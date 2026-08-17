# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Regressions for the bounded adaptive beam re-decode lane."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from local_transcript_max.config import PipelineConfig, load_config, validate_adaptive_selection
from local_transcript_max.errors import LTMError
from local_transcript_max.qa.adaptive import policy
from local_transcript_max.qa.adaptive.decision import (
    DECISION_REASONS,
    SpanEvaluationInput,
    evaluate_span,
)
from local_transcript_max.qa.adaptive.evidence import candidate_evidence, failed_evidence
from local_transcript_max.qa.adaptive.lane import asr_result_sha256, run_adaptive_redecode
from local_transcript_max.qa.adaptive.plan import (
    canonical_plan_bytes,
    load_sealed_plan,
    seal_plan,
)
from local_transcript_max.qa.adaptive.publish import (
    AdaptiveDerivatives,
    privacy_safe_summary,
    publish_adaptive_lane,
)
from local_transcript_max.qa.adaptive.stitch import SpanPromotion, apply_selected_spans
from local_transcript_max.qa.heuristics import run_qa
from local_transcript_max.schemas import (
    AlignedWord,
    ASRResult,
    ASRSegment,
    DiarizationTurn,
    ModelMetadata,
    QAReport,
    Transcript,
)
from local_transcript_max.utils import read_json

# --------------------------------------------------------------------------
# Fixtures and stubs
# --------------------------------------------------------------------------

SUSPECT_TEXT = "we we we we we we we we"
# Assembled at runtime so the literal never appears in a source distribution,
# which forbids home-shaped absolute paths in shipped files.
PRIVATE_PATH_MARKER = "/" + "Users" + "/example/secret"
CONTROL_TEXT = "we should meet on tuesday"
WIDE_TEXT = "we should not meet on tuesday"


def _segment(
    index: int,
    start: float,
    end: float,
    text: str,
    *,
    avg_logprob: float | None = None,
    compression_ratio: float | None = 1.0,
    no_speech_prob: float | None = 0.1,
) -> ASRSegment:
    return ASRSegment(
        id=index,
        start=start,
        end=end,
        text=text,
        words=[
            AlignedWord(
                text=token,
                start=round(start + offset * (end - start) / max(1, len(text.split())), 4),
                end=round(
                    start + (offset + 1) * (end - start) / max(1, len(text.split())),
                    4,
                ),
                alignment_source="whisper",
            )
            for offset, token in enumerate(text.split())
        ],
        avg_logprob=avg_logprob,
        compression_ratio=compression_ratio,
        no_speech_prob=no_speech_prob,
    )


def first_pass_result() -> ASRResult:
    """A first pass whose middle segment is flagged as low confidence."""

    return ASRResult(
        segments=[
            _segment(0, 0.0, 4.0, "hello everyone thanks for joining", avg_logprob=-0.2),
            _segment(1, 4.0, 8.0, SUSPECT_TEXT, avg_logprob=-2.0, compression_ratio=1.2),
            _segment(2, 8.0, 12.0, "anyway that is all for today", avg_logprob=-0.3),
        ],
        language="en",
        model=ModelMetadata(
            role="asr",
            model_id="stub-asr",
            revision="stub-revision",
            revision_verified=True,
            present=True,
        ),
        config={"backend": "stub-asr", "device": "cpu"},
    )


def first_pass_transcript(
    result: ASRResult,
    *,
    speakers: tuple[str, ...] = ("SPEAKER_00",),
) -> Transcript:
    words: list[AlignedWord] = []
    for segment in result.segments:
        for word in segment.words:
            speaker = speakers[0] if len(speakers) == 1 else _speaker_for(word.start, speakers)
            words.append(word.model_copy(update={"speaker": speaker}))
    turns = [
        DiarizationTurn(start=0.0, end=12.0, speaker=speaker, source="stub")
        for speaker in speakers
    ]
    return Transcript(
        input_path="sample.wav",
        language=result.language,
        segments=result.segments,
        words=words,
        diarization_turns=turns,
        exclusive_diarization_turns=turns,
    )


def _speaker_for(start: float | None, speakers: tuple[str, ...]) -> str:
    if start is None:
        return speakers[0]
    return speakers[0] if start < 6.0 else speakers[1]


class StubASR:
    """Returns a programmed window result per beam width."""

    name = "stub-asr"

    def __init__(self, by_beam: dict[int, ASRResult | Exception]) -> None:
        self.by_beam = by_beam
        self.calls: list[tuple[Path, int, dict[str, Any]]] = []

    def transcribe(self, audio_path: Path, config: PipelineConfig) -> ASRResult:
        beam = config.asr.beam_size
        self.calls.append(
            (
                audio_path,
                beam,
                json.loads(config.model_dump_json()),
            )
        )
        outcome = self.by_beam[beam]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome.model_copy(deep=True)


class StubAligner:
    """Emits per-word alignment with a controllable usable-word share."""

    def __init__(self, coverage_by_text: dict[str, float] | None = None) -> None:
        self.coverage_by_text = coverage_by_text or {}

    def align(
        self,
        audio_path: Path,
        asr_result: ASRResult,
        config: PipelineConfig,
    ) -> list[AlignedWord]:
        text = " ".join(segment.text.strip() for segment in asr_result.segments)
        coverage = self.coverage_by_text.get(text, 1.0)
        tokens = [token for token in text.split() if token]
        usable = int(round(coverage * len(tokens)))
        words: list[AlignedWord] = []
        for index, token in enumerate(tokens):
            if index < usable:
                words.append(
                    AlignedWord(
                        text=token,
                        start=float(index) * 0.2,
                        end=float(index) * 0.2 + 0.19,
                        alignment_source="forced",
                    )
                )
            else:
                words.append(AlignedWord(text=token, alignment_source="missing"))
        return words


def window_result(
    text: str,
    *,
    avg_logprob: float,
    compression_ratio: float = 1.0,
    no_speech_prob: float = 0.1,
    start: float = 0.0,
    end: float = 5.0,
    language: str | None = "en",
) -> ASRResult:
    return ASRResult(
        segments=[
            _segment(
                0,
                start,
                end,
                text,
                avg_logprob=avg_logprob,
                compression_ratio=compression_ratio,
                no_speech_prob=no_speech_prob,
            )
        ],
        language=language,
        model=ModelMetadata(
            role="asr",
            model_id="stub-asr",
            revision="stub-revision",
            revision_verified=True,
            present=True,
        ),
        config={"backend": "stub-asr", "device": "cpu"},
    )


def adaptive_config(tmp_path: Path) -> PipelineConfig:
    config = load_config(cwd=tmp_path)
    config.redecode.mode = "low-confidence"
    config.redecode.selection_policy = "adaptive-safe"
    validate_adaptive_selection(config)
    return config


@pytest.fixture
def stub_window(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    extracted: list[tuple[float, float]] = []

    def fake_extract(source: Path, output: Path, *, start: float, end: float) -> None:
        extracted.append((start, end))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(f"window:{start:.3f}:{end:.3f}".encode())

    monkeypatch.setattr(
        "local_transcript_max.qa.adaptive.lane.extract_audio_window",
        fake_extract,
    )
    return extracted


def run_lane(
    tmp_path: Path,
    *,
    control: ASRResult | Exception,
    wide: ASRResult | Exception,
    config: PipelineConfig | None = None,
    aligner: StubAligner | None = None,
    speakers: tuple[str, ...] = ("SPEAKER_00",),
    transcript: Transcript | None = None,
) -> tuple[Any, StubASR]:
    config = config or adaptive_config(tmp_path)
    canonical = tmp_path / "canonical.wav"
    canonical.write_bytes(b"canonical-audio-bytes")
    result = first_pass_result()
    resolved = transcript if transcript is not None else first_pass_transcript(
        result, speakers=speakers
    )
    backend = StubASR(
        {
            config.redecode.beam_size: control,
            config.redecode.adaptive_wide_beam_size: wide,
        }
    )
    outcome = run_adaptive_redecode(
        canonical,
        resolved,
        run_qa(resolved),
        config,
        backend,
        first_pass_result=result,
        alignment_backend=aligner or StubAligner(),
        audio_duration_seconds=12.0,
    )
    return outcome, backend


# --------------------------------------------------------------------------
# 1. Beam 10 wins on independent evidence and is selected
# --------------------------------------------------------------------------


def test_wide_beam_selected_when_independent_evidence_improves(
    tmp_path: Path,
    stub_window: list[tuple[float, float]],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )

    span = outcome.plan.spans[0]
    assert span.decision.decision_reason == "promoted_wide_beam"
    assert span.decision.selected == "wide"
    assert span.applied is True
    assert outcome.selected_result is not None
    assert "we should meet today" in " ".join(
        segment.text for segment in outcome.selected_result.segments
    )
    assert stub_window == [(3.0, 9.0)]


# --------------------------------------------------------------------------
# 2. Better raw model score but worse independent evidence is rejected
# --------------------------------------------------------------------------


def test_wide_beam_rejected_when_alignment_or_repetition_regresses(
    tmp_path: Path,
    stub_window: list[tuple[float, float]],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.9, compression_ratio=1.0),
        wide=window_result(
            "we should should should meet today",
            avg_logprob=-0.1,
            compression_ratio=1.6,
        ),
    )

    span = outcome.plan.spans[0]
    assert span.decision.selected == "source"
    assert span.decision.decision_reason == "abstained_protected_regression"
    assert any(reason.startswith("regression:") for reason in span.decision.failure_reasons)
    assert outcome.selected_result is None


def test_higher_model_score_alone_never_promotes(tmp_path: Path, stub_window: list[Any]) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.9),
        wide=window_result("we shall meet today", avg_logprob=-0.1),
    )

    span = outcome.plan.spans[0]
    assert span.decision.selected == "source"
    assert span.decision.independent_support_count == 0
    assert span.decision.decision_reason in {
        "abstained_insufficient_independent_support",
        "abstained_protected_material_ambiguity",
    }


# --------------------------------------------------------------------------
# 3. Ties and ambiguity preserve the source
# --------------------------------------------------------------------------


def test_identical_candidates_preserve_the_first_pass(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    identical = window_result("we should meet today", avg_logprob=-0.4)
    outcome, _ = run_lane(tmp_path, control=identical, wide=identical.model_copy(deep=True))

    span = outcome.plan.spans[0]
    assert span.decision.decision_reason == "abstained_no_wide_beam_disagreement"
    assert outcome.selected_result is None


# --------------------------------------------------------------------------
# 4. Wide-beam inference failure is safe
# --------------------------------------------------------------------------


def test_wide_beam_failure_preserves_the_source(tmp_path: Path, stub_window: list[Any]) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=RuntimeError("private failure at " + PRIVATE_PATH_MARKER + " token=do-not-serialize"),
    )

    span = outcome.plan.spans[0]
    assert span.decision.decision_reason == "abstained_candidate_failed"
    assert outcome.selected_result is None
    serialized = canonical_plan_bytes(outcome.plan).decode("utf-8")
    assert "do-not-serialize" not in serialized
    assert PRIVATE_PATH_MARKER not in serialized


# --------------------------------------------------------------------------
# 5. Both candidates bind identical audio and configuration except beam width
# --------------------------------------------------------------------------


def test_both_candidates_share_audio_and_configuration_except_beam(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, backend = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=window_result("we should meet tomorrow", avg_logprob=-0.2),
    )

    assert len(backend.calls) == 2
    (control_path, control_beam, control_config), (wide_path, wide_beam, wide_config) = (
        backend.calls
    )
    assert control_path == wide_path
    assert control_path.read_bytes() == wide_path.read_bytes() if control_path.exists() else True
    assert (control_beam, wide_beam) == (5, 10)
    differing = {
        key
        for key in set(control_config["asr"]) | set(wide_config["asr"])
        if control_config["asr"].get(key) != wide_config["asr"].get(key)
    }
    assert differing == {"beam_size"}
    assert {
        key
        for key in set(control_config) | set(wide_config)
        if control_config.get(key) != wide_config.get(key)
    } == {"asr"}
    span = outcome.plan.spans[0]
    assert {candidate.beam_size for candidate in span.candidates} == {5, 10}
    assert len({candidate.role for candidate in span.candidates}) == 2
    assert len(span.window_audio_sha256) == 64


# --------------------------------------------------------------------------
# 6. Window stitching neither duplicates nor drops words
# --------------------------------------------------------------------------


def test_stitching_drops_padded_context_and_keeps_neighbours_intact() -> None:
    source = first_pass_result()
    candidate = ASRResult(
        segments=[
            # Padded context that belongs to the previous, unchanged segment.
            _segment(0, 0.0, 1.0, "thanks for joining", avg_logprob=-0.2),
            _segment(1, 1.0, 5.0, "we should meet today", avg_logprob=-0.2),
            # Padded context belonging to the following, unchanged segment.
            _segment(2, 5.0, 6.0, "anyway that is", avg_logprob=-0.2),
        ],
        language="en",
    )
    derived = apply_selected_spans(
        source,
        [
            SpanPromotion(
                span_index=1,
                replaced_segment_ids=(1,),
                replace_start=4.0,
                replace_end=8.0,
                window_offset_seconds=3.0,
                candidate=candidate,
            )
        ],
    )

    texts = [segment.text for segment in derived.segments]
    assert texts == [
        "hello everyone thanks for joining",
        "we should meet today",
        "anyway that is all for today",
    ]
    assert [segment.id for segment in derived.segments] == [0, 1, 2]


def test_stitching_rejects_boundary_duplication() -> None:
    source = first_pass_result()
    candidate = ASRResult(
        segments=[_segment(0, 1.0, 5.0, "joining we should meet today", avg_logprob=-0.2)],
        language="en",
    )
    # "joining" repeats the last word of the untouched previous segment.
    with pytest.raises(LTMError, match="duplicated words") as failure:
        apply_selected_spans(
            source,
            [
                SpanPromotion(
                    span_index=1,
                    replaced_segment_ids=(1,),
                    replace_start=4.0,
                    replace_end=8.0,
                    window_offset_seconds=3.0,
                    candidate=ASRResult(
                        segments=[
                            _segment(
                                0, 1.0, 5.0, "joining joining we should meet", avg_logprob=-0.2
                            )
                        ],
                        language="en",
                    ),
                )
            ],
        )
    # The refusal is transcript-content free: it counts the repeats instead of
    # quoting them, so the message stays safe to log.
    message = str(failure.value)
    assert "joining" not in message
    assert "1 adjacent repeat pairs" in message
    assert candidate.segments


def test_stitching_rejects_a_candidate_with_no_text_inside_the_span() -> None:
    source = first_pass_result()
    candidate = ASRResult(
        segments=[_segment(0, 0.0, 0.5, "context only", avg_logprob=-0.2)],
        language="en",
    )
    with pytest.raises(LTMError, match="no text inside its authorized source span"):
        apply_selected_spans(
            source,
            [
                SpanPromotion(
                    span_index=1,
                    replaced_segment_ids=(1,),
                    replace_start=4.0,
                    replace_end=8.0,
                    window_offset_seconds=3.0,
                    candidate=candidate,
                )
            ],
        )


# --------------------------------------------------------------------------
# 7. Overlapping QA spans reconcile deterministically
# --------------------------------------------------------------------------


def test_overlapping_spans_reconcile_to_one_window(
    tmp_path: Path,
    stub_window: list[tuple[float, float]],
) -> None:
    config = adaptive_config(tmp_path)
    result = first_pass_result()
    transcript = first_pass_transcript(result)
    report = QAReport(
        flags=[
            flag
            for flag in run_qa(transcript).flags
            if flag.code == "low_avg_logprob"
        ]
    )
    report.flags.append(report.flags[0].model_copy(update={"start": 5.0, "end": 7.0}))
    canonical = tmp_path / "canonical.wav"
    canonical.write_bytes(b"canonical-audio-bytes")
    backend = StubASR(
        {
            5: window_result("we should meet today", avg_logprob=-0.4),
            10: window_result("we should meet today", avg_logprob=-0.4),
        }
    )
    outcome = run_adaptive_redecode(
        canonical,
        transcript,
        report,
        config,
        backend,
        first_pass_result=result,
        alignment_backend=StubAligner(),
        audio_duration_seconds=12.0,
    )

    assert len(outcome.plan.spans) == 1
    assert stub_window == [(3.0, 9.0)]


def test_reconciliation_is_order_independent_and_repeatable(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    first, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=window_result("we should meet today", avg_logprob=-0.4),
    )
    second, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=window_result("we should meet today", avg_logprob=-0.4),
    )
    assert canonical_plan_bytes(first.plan) == canonical_plan_bytes(second.plan)


# --------------------------------------------------------------------------
# 8. Invalid or non-monotonic timestamps fail closed
# --------------------------------------------------------------------------


def test_non_monotonic_candidate_timestamps_fail_closed(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    broken = ASRResult(
        segments=[
            _segment(0, 3.0, 5.0, "we should meet today", avg_logprob=-0.1),
            _segment(1, 0.5, 2.0, "and then leave", avg_logprob=-0.1),
        ],
        language="en",
    )
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.9),
        wide=broken,
    )

    span = outcome.plan.spans[0]
    assert span.decision.decision_reason == "abstained_hard_gate_failed"
    assert "wide:timestamps_monotonic" in span.decision.failure_reasons
    assert outcome.selected_result is None


def test_candidate_outside_the_window_fails_closed(tmp_path: Path, stub_window: list[Any]) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.9),
        wide=window_result("we should meet today at noon", avg_logprob=-0.1, start=0.0, end=99.0),
    )

    span = outcome.plan.spans[0]
    assert "wide:timestamps_within_window" in span.decision.failure_reasons
    assert span.decision.selected == "source"


# --------------------------------------------------------------------------
# 9. Protected-material ambiguity abstains
# --------------------------------------------------------------------------


def test_protected_material_difference_requires_stronger_support(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(CONTROL_TEXT, avg_logprob=-0.9, compression_ratio=1.4),
        wide=window_result(WIDE_TEXT, avg_logprob=-0.4, compression_ratio=1.3),
    )

    span = outcome.plan.spans[0]
    assert "negation" in span.decision.protected_classes
    assert span.decision.protected_support_required is True
    assert span.decision.decision_reason in {
        "abstained_protected_material_ambiguity",
        "abstained_score_margin_below_threshold",
    }
    assert outcome.selected_result is None


def test_protected_class_detection_covers_the_documented_material() -> None:
    assert "negation" in policy.protected_classes("we will go", "we will not go")
    assert "quantity" in policy.protected_classes("pay 15 dollars", "pay 50 dollars")
    assert "date_or_time" in policy.protected_classes("due monday", "due friday")
    assert "proper_noun" in policy.protected_classes("call Dana", "call Dana Smith")
    assert "modality" in policy.protected_classes("it will work", "it might work")
    assert "attribution" in policy.protected_classes("he said yes", "he yes")
    assert "contraction" in policy.protected_classes("we are ready", "we're ready")
    assert policy.protected_classes("same words here", "same words here") == ()


# --------------------------------------------------------------------------
# 10. No-speech and hallucinated candidates are rejected
# --------------------------------------------------------------------------


def test_no_speech_candidate_is_rejected(tmp_path: Path, stub_window: list[Any]) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.9),
        wide=window_result("thanks for watching", avg_logprob=-0.1, no_speech_prob=0.97),
    )

    span = outcome.plan.spans[0]
    assert "wide:no_speech_probability" in span.decision.failure_reasons
    assert span.decision.selected == "source"


def test_repetition_loop_candidate_is_rejected(tmp_path: Path, stub_window: list[Any]) -> None:
    loop = " ".join(["okay"] * 12)
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.9),
        wide=window_result(loop, avg_logprob=-0.05, compression_ratio=1.2),
    )

    span = outcome.plan.spans[0]
    assert "wide:repetition_ratio" in span.decision.failure_reasons
    assert span.decision.selected == "source"


# --------------------------------------------------------------------------
# 11. A genuine source error may be corrected
# --------------------------------------------------------------------------


def test_genuine_source_error_is_corrected_with_strong_evidence(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    aligner = StubAligner(
        coverage_by_text={
            "we we we we we we we we": 0.55,
            "we should meet in the morning": 1.0,
        }
    )
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.3, compression_ratio=2.1),
        wide=window_result(
            "we should meet in the morning",
            avg_logprob=-0.25,
            compression_ratio=1.1,
        ),
        aligner=aligner,
    )

    span = outcome.plan.spans[0]
    assert span.decision.decision_reason == "promoted_wide_beam"
    assert span.decision.independent_support_count >= 1
    assert outcome.selected_result is not None


# --------------------------------------------------------------------------
# 12. Application cannot cross a speaker boundary
# --------------------------------------------------------------------------


def test_span_crossing_a_speaker_boundary_abstains(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.3, compression_ratio=2.1),
        wide=window_result("we should meet today", avg_logprob=-0.2, compression_ratio=1.0),
        speakers=("SPEAKER_00", "SPEAKER_01"),
    )

    span = outcome.plan.spans[0]
    assert span.decision.decision_reason == "abstained_crosses_speaker_boundary"
    assert outcome.selected_result is None


def test_promotions_may_not_overlap_a_previously_applied_span() -> None:
    source = first_pass_result()
    promotion = SpanPromotion(
        span_index=1,
        replaced_segment_ids=(1,),
        replace_start=4.0,
        replace_end=8.0,
        window_offset_seconds=3.0,
        candidate=window_result("we should meet today", avg_logprob=-0.2, start=1.0, end=5.0),
    )
    with pytest.raises(LTMError, match="overlap"):
        apply_selected_spans(source, [promotion, promotion])


# --------------------------------------------------------------------------
# 13. Dependent stages rerun; reuse is recorded
# --------------------------------------------------------------------------


def test_publication_reruns_dependent_stages_and_records_reuse(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    assert outcome.selected_result is not None
    reran: list[str] = []

    def regenerate(selected: ASRResult) -> AdaptiveDerivatives:
        reran.append("alignment")
        transcript = first_pass_transcript(selected)
        return AdaptiveDerivatives(
            selected_result=selected,
            selected_transcript=transcript,
            qa_report=run_qa(transcript),
            alignment_report={"backend": "stub"},
        )

    out_dir = tmp_path / "job"
    out_dir.mkdir()
    report = publish_adaptive_lane(
        out_dir,
        outcome,
        first_pass_qa=QAReport(),
        regenerate=regenerate,
    )

    assert reran == ["alignment"]
    lane = out_dir / "adaptive"
    assert (lane / "transcript.selected.raw.json").exists()
    assert (lane / "transcript.selected.json").exists()
    assert (lane / "redecode_plan.json").exists()
    assert (lane / "qa_report.selected.json").exists()
    assert report["valid"] is True
    dispositions = {
        item.stage: item.disposition for item in outcome.plan.stage_dispositions
    }
    assert dispositions["diarization"] == "reused"
    assert dispositions["forced_alignment"] == "rerun"
    assert dispositions["qa"] == "rerun"
    assert set(report["regenerated_stages"]) >= {"forced_alignment", "qa", "exports"}


def test_publication_is_transactional_when_a_dependent_stage_regresses(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    assert outcome.selected_result is not None

    def regenerate(selected: ASRResult) -> AdaptiveDerivatives:
        transcript = first_pass_transcript(selected)
        broken = transcript.model_copy(
            update={
                "words": [
                    word.model_copy(update={"start": None, "end": None})
                    for word in transcript.words
                ]
            }
        )
        return AdaptiveDerivatives(
            selected_result=selected,
            selected_transcript=broken,
            qa_report=run_qa(broken),
            alignment_report={},
        )

    out_dir = tmp_path / "job"
    out_dir.mkdir()
    with pytest.raises(LTMError, match="validation failed"):
        publish_adaptive_lane(
            out_dir,
            outcome,
            first_pass_qa=QAReport(),
            regenerate=regenerate,
        )

    assert not (out_dir / "adaptive").exists()
    assert list(out_dir.iterdir()) == []


# --------------------------------------------------------------------------
# 14. Candidate-only mode stays behaviourally compatible
# --------------------------------------------------------------------------


def test_candidate_only_remains_the_default_and_is_unchanged(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    assert config.redecode.selection_policy == "candidate-only"
    assert config.redecode.beam_size == 5
    assert config.redecode.beam_ladder == (5,)

    config.redecode.mode = "low-confidence"
    with pytest.raises(LTMError, match="adaptive-safe"):
        run_adaptive_redecode(
            tmp_path / "canonical.wav",
            first_pass_transcript(first_pass_result()),
            QAReport(),
            config,
            StubASR({}),
            first_pass_result=first_pass_result(),
        )


def test_disabled_adaptive_lane_leaves_max_output_unchanged(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    config = load_config(cwd=tmp_path)
    config.redecode.mode = "low-confidence"
    assert config.redecode.selection_policy == "candidate-only"
    out_dir = tmp_path / "job"
    out_dir.mkdir()
    assert not (out_dir / "adaptive").exists()


# --------------------------------------------------------------------------
# 15. Byte-stable canonical serialization
# --------------------------------------------------------------------------


def test_repeated_runs_produce_byte_stable_plans(tmp_path: Path, stub_window: list[Any]) -> None:
    outcomes = [
        run_lane(
            tmp_path,
            control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
            wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
        )[0]
        for _ in range(2)
    ]
    assert canonical_plan_bytes(outcomes[0].plan) == canonical_plan_bytes(outcomes[1].plan)
    assert outcomes[0].sealed_plan["plan_sha256"] == outcomes[1].sealed_plan["plan_sha256"]
    assert asr_result_sha256(outcomes[0].selected_result) == asr_result_sha256(
        outcomes[1].selected_result
    )


# --------------------------------------------------------------------------
# 16. Tampering is rejected
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"canonical_audio_sha256": "0" * 64}, id="audio"),
        pytest.param({"first_pass_asr_sha256": "0" * 64}, id="first_pass"),
        pytest.param({"configuration_sha256": "0" * 64}, id="configuration"),
        pytest.param({"source_revision": "tampered"}, id="source_revision"),
        pytest.param({"selected_asr_sha256": "0" * 64}, id="selected"),
        pytest.param({"backend": "other-backend"}, id="backend"),
        pytest.param({"first_pass_language": "de"}, id="language"),
    ],
)
def test_tampered_plan_fields_are_rejected(
    tmp_path: Path,
    stub_window: list[Any],
    mutation: dict[str, Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    sealed = copy.deepcopy(outcome.sealed_plan)
    sealed["plan"].update(mutation)

    with pytest.raises(LTMError, match="digest does not match"):
        load_sealed_plan(sealed, maximum_bytes=4_194_304)


def test_tampered_candidate_metrics_and_decisions_are_rejected(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    sealed = copy.deepcopy(outcome.sealed_plan)
    sealed["plan"]["spans"][0]["candidates"][1]["avg_logprob"] = 0.0
    with pytest.raises(LTMError, match="digest does not match"):
        load_sealed_plan(sealed, maximum_bytes=4_194_304)

    sealed = copy.deepcopy(outcome.sealed_plan)
    sealed["plan"]["spans"][0]["decision"]["selected"] = "source"
    with pytest.raises(LTMError, match="digest does not match"):
        load_sealed_plan(sealed, maximum_bytes=4_194_304)


def test_untampered_plan_round_trips(tmp_path: Path, stub_window: list[Any]) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    replayed = load_sealed_plan(outcome.sealed_plan, maximum_bytes=4_194_304)
    assert canonical_plan_bytes(replayed) == canonical_plan_bytes(outcome.plan)


def test_replaying_the_plan_reproduces_every_decision(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    config = adaptive_config(tmp_path)
    outcome, _ = run_lane(
        tmp_path,
        config=config,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    plan = load_sealed_plan(outcome.sealed_plan, maximum_bytes=4_194_304)
    candidates = read_json_payload(outcome.candidates_artifact)

    for span in plan.spans:
        sensitive = next(item for item in candidates["spans"] if item["index"] == span.index)
        texts = {item["role"]: item["text"] for item in sensitive["candidates"]}
        control, wide = span.candidates
        replayed = evaluate_span(
            SpanEvaluationInput(
                source_text=sensitive["source_text"],
                control_text=texts["control"],
                wide_text=texts["wide"],
                expected_language=plan.first_pass_language,
                speaker_count=len(span.speaker_labels),
                minimum_alignment_coverage=config.redecode.adaptive_min_alignment_coverage,
                minimum_logprob_margin=config.redecode.adaptive_min_logprob_margin,
                protected_margin_multiplier=(
                    config.redecode.adaptive_protected_margin_multiplier
                ),
            ),
            control,
            wide,
        )
        assert replayed.selected == span.decision.selected
        assert replayed.decision_reason == span.decision.decision_reason
        assert replayed.model_dump(mode="json") == span.decision.model_dump(mode="json")


def read_json_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


# --------------------------------------------------------------------------
# 17. Unknown fields, stale schemas, duplicates, and oversized plans fail closed
# --------------------------------------------------------------------------


def test_unknown_and_stale_plan_fields_fail_closed(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )

    extended = copy.deepcopy(outcome.sealed_plan)
    extended["plan"]["surprise_field"] = True
    with pytest.raises(LTMError, match="strict schema validation"):
        load_sealed_plan(extended, maximum_bytes=4_194_304)

    stale = copy.deepcopy(outcome.sealed_plan)
    stale["plan"]["schema_version"] = "adaptive_redecode_plan.v0"
    with pytest.raises(LTMError, match="strict schema validation"):
        load_sealed_plan(stale, maximum_bytes=4_194_304)

    opaque = copy.deepcopy(outcome.sealed_plan)
    opaque["unexpected"] = "payload"
    with pytest.raises(LTMError, match="unexpected shape"):
        load_sealed_plan(opaque, maximum_bytes=4_194_304)

    sealed_stale = copy.deepcopy(outcome.sealed_plan)
    sealed_stale["sealed_schema_version"] = "adaptive_redecode_sealed_plan.v0"
    with pytest.raises(LTMError, match="unsupported sealed schema"):
        load_sealed_plan(sealed_stale, maximum_bytes=4_194_304)

    with pytest.raises(LTMError, match="artifact size limit"):
        load_sealed_plan(outcome.sealed_plan, maximum_bytes=4_096)

    with pytest.raises(LTMError, match="artifact size limit"):
        seal_plan(outcome.plan, maximum_bytes=1_024)


def test_duplicate_and_overlapping_plan_spans_fail_closed(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    duplicated = outcome.plan.model_copy(
        update={"spans": [outcome.plan.spans[0], outcome.plan.spans[0]]}
    )
    with pytest.raises(LTMError, match="duplicate span"):
        load_sealed_plan(seal_plan(duplicated, maximum_bytes=4_194_304), maximum_bytes=4_194_304)


def test_unknown_decision_reason_fails_closed(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    """A reason code outside this policy version's vocabulary is not auditable."""

    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    assert outcome.plan.spans[0].decision.decision_reason in DECISION_REASONS

    tampered = copy.deepcopy(outcome.sealed_plan)
    tampered["plan"]["spans"][0]["decision"]["decision_reason"] = "promoted_because_i_said_so"
    with pytest.raises(LTMError, match="strict schema validation"):
        load_sealed_plan(tampered, maximum_bytes=4_194_304)


def test_publication_refuses_to_overwrite_an_existing_lane(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=window_result("we should meet today", avg_logprob=-0.4),
    )
    out_dir = tmp_path / "job"
    (out_dir / "adaptive").mkdir(parents=True)
    with pytest.raises(LTMError, match="refusing to overwrite"):
        publish_adaptive_lane(out_dir, outcome, first_pass_qa=QAReport(), regenerate=None)


def test_promoted_selection_without_a_regenerator_fails_closed(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
        wide=window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
    )
    out_dir = tmp_path / "job"
    out_dir.mkdir()
    with pytest.raises(LTMError, match="dependent-stage regenerator"):
        publish_adaptive_lane(out_dir, outcome, first_pass_qa=QAReport(), regenerate=None)
    assert not (out_dir / "adaptive").exists()


# --------------------------------------------------------------------------
# 18. Offline, secret, and privacy contracts
# --------------------------------------------------------------------------


def test_privacy_safe_summary_excludes_candidate_text_and_paths(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(CONTROL_TEXT, avg_logprob=-0.9),
        wide=window_result(WIDE_TEXT, avg_logprob=-0.2),
    )
    summary = json.dumps(privacy_safe_summary(outcome))

    assert "should" not in summary
    assert str(tmp_path) not in summary
    assert "uncalibrated" in summary
    assert outcome.plan.spans[0].window_audio_sha256 in summary

    sensitive = json.dumps(outcome.candidates_artifact)
    assert "meet" in sensitive


def test_plan_never_serializes_candidate_transcript_text(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result(CONTROL_TEXT, avg_logprob=-0.9),
        wide=window_result(WIDE_TEXT, avg_logprob=-0.2),
    )
    serialized = canonical_plan_bytes(outcome.plan).decode("utf-8")
    assert "tuesday" not in serialized
    assert "should" not in serialized


def test_adaptive_lane_publishes_a_private_directory(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    from local_transcript_max.platform_security import private_path_is_secure

    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=window_result("we should meet today", avg_logprob=-0.4),
    )
    out_dir = tmp_path / "job"
    out_dir.mkdir()
    publish_adaptive_lane(out_dir, outcome, first_pass_qa=QAReport(), regenerate=None)
    assert private_path_is_secure(out_dir / "adaptive", directory=True)
    assert not any(child.name.startswith(".adaptive-staging-") for child in out_dir.iterdir())


# --------------------------------------------------------------------------
# 19. Resource bounds
# --------------------------------------------------------------------------


def test_span_duration_limit_skips_without_decoding(
    tmp_path: Path,
    stub_window: list[tuple[float, float]],
) -> None:
    config = adaptive_config(tmp_path)
    config.redecode.adaptive_max_span_seconds = 1.0
    outcome, backend = run_lane(
        tmp_path,
        config=config,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=window_result("we should meet today", avg_logprob=-0.2),
    )

    assert backend.calls == []
    assert stub_window == []
    span = outcome.plan.spans[0]
    assert span.decision.failure_reasons == ["span_exceeds_duration_limit"]
    assert span.decision.selected == "source"


def test_total_window_budget_stops_further_decoding(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    config = adaptive_config(tmp_path)
    config.redecode.adaptive_max_total_window_seconds = 1.0
    outcome, backend = run_lane(
        tmp_path,
        config=config,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=window_result("we should meet today", avg_logprob=-0.2),
    )

    assert backend.calls == []
    assert outcome.plan.spans[0].decision.failure_reasons == ["total_window_budget_exhausted"]


def test_beam_and_span_bounds_are_enforced_by_configuration() -> None:
    with pytest.raises(ValidationError):
        PipelineConfig(redecode={"adaptive_wide_beam_size": 11})
    adaptive = {"selection_policy": "adaptive-safe", "mode": "low-confidence"}
    with pytest.raises(ValidationError, match="strictly increase"):
        PipelineConfig(redecode={**adaptive, "beam_size": 10, "adaptive_wide_beam_size": 10})
    with pytest.raises(ValidationError, match="strictly increase"):
        PipelineConfig(redecode={**adaptive, "beam_size": 8, "adaptive_wide_beam_size": 6})
    with pytest.raises(ValidationError, match="requires a re-decode mode"):
        PipelineConfig(redecode={"selection_policy": "adaptive-safe"})
    with pytest.raises(ValidationError, match="temperature"):
        PipelineConfig(
            redecode={
                "selection_policy": "adaptive-safe",
                "mode": "low-confidence",
                "temperature": 0.2,
            }
        )
    with pytest.raises(ValidationError):
        PipelineConfig(redecode={"max_spans": 101})


def test_shipped_adaptive_example_configuration_validates() -> None:
    """The documented opt-in example must satisfy every adaptive requirement."""

    repo_root = Path(__file__).resolve().parents[1]
    example = repo_root / "examples" / "config.max-adaptive-redecode.yaml"
    config = load_config("max", config_file=example)

    assert config.redecode.selection_policy == "adaptive-safe"
    assert config.redecode.beam_ladder == (5, 10)
    assert config.redecode.mode != "off"
    assert config.redecode.temperature == 0.0
    assert config.qa.enabled
    assert config.alignment.enabled
    assert config.alignment.backend in {"auto", "forced"}
    assert config.asr.backend == "faster-whisper"
    assert config.cleanup.mode == "off"
    assert config.speaker_identity.mode == "off"
    # Redundant with construction, but it pins the exact fail-closed contract
    # the example is documented to satisfy.
    validate_adaptive_selection(config)


def test_adaptive_selection_requires_supporting_evidence_and_backends() -> None:
    base: dict[str, Any] = {
        "redecode": {"selection_policy": "adaptive-safe", "mode": "low-confidence"}
    }
    with pytest.raises(ValidationError, match="qa.enabled"):
        PipelineConfig(**base, qa={"enabled": False})
    with pytest.raises(ValidationError, match="requires forced alignment"):
        PipelineConfig(**base, alignment={"backend": "off"})
    with pytest.raises(ValidationError, match="not independent evidence"):
        PipelineConfig(**base, alignment={"backend": "fallback"})
    with pytest.raises(ValidationError, match="requires forced alignment"):
        PipelineConfig(**base, alignment={"enabled": False})
    with pytest.raises(ValidationError, match="faster-whisper"):
        PipelineConfig(**base, asr={"backend": "mlx-whisper", "beam_size": 1})
    with pytest.raises(ValidationError, match="cleanup"):
        PipelineConfig(**base, cleanup={"mode": "local-llm", "allow_remote_cleanup": True})
    with pytest.raises(ValidationError, match="speaker-identity"):
        PipelineConfig(**base, speaker_identity={"mode": "review"})


# --------------------------------------------------------------------------
# 20. Evidence primitives
# --------------------------------------------------------------------------


def test_failed_evidence_is_never_promotable() -> None:
    control = failed_evidence(
        role="control",
        beam_size=5,
        failure_reason="inference_failed",
        window_duration_seconds=4.0,
    )
    wide = candidate_evidence(
        window_result("we should meet today", avg_logprob=-0.1),
        role="wide",
        beam_size=10,
        window_duration_seconds=6.0,
        alignment_words=StubAligner().align(
            Path("window.wav"),
            window_result("we should meet today", avg_logprob=-0.1),
            PipelineConfig(),
        ),
    )
    decision = evaluate_span(
        SpanEvaluationInput(
            source_text=SUSPECT_TEXT,
            control_text="",
            wide_text="we should meet today",
            expected_language="en",
            speaker_count=1,
            minimum_alignment_coverage=0.9,
            minimum_logprob_margin=0.05,
            protected_margin_multiplier=3.0,
        ),
        control,
        wide,
    )
    assert decision.selected == "source"
    assert decision.decision_reason == "abstained_candidate_failed"


def test_missing_alignment_evidence_abstains(tmp_path: Path, stub_window: list[Any]) -> None:
    class BrokenAligner:
        def align(self, *args: Any, **kwargs: Any) -> list[AlignedWord]:
            raise RuntimeError("alignment unavailable")

    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.9),
        wide=window_result("we should meet tomorrow", avg_logprob=-0.1),
        aligner=BrokenAligner(),  # type: ignore[arg-type]
    )
    span = outcome.plan.spans[0]
    assert span.decision.decision_reason == "abstained_missing_alignment_evidence"


def test_low_alignment_coverage_fails_the_hard_gate(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.9),
        wide=window_result("we should meet tomorrow", avg_logprob=-0.1),
        aligner=StubAligner(coverage_by_text={"we should meet tomorrow": 0.4}),
    )
    span = outcome.plan.spans[0]
    assert "wide:alignment_coverage" in span.decision.failure_reasons
    assert span.decision.selected == "source"


def test_repetition_ratio_separates_loops_from_emphasis() -> None:
    assert policy.repetition_ratio("no no no no no no") > 0.9
    assert policy.repetition_ratio("that is really really important to us") < 0.3
    assert policy.repetition_ratio("") == 0.0


def test_written_plan_artifact_is_canonical_on_disk(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    outcome, _ = run_lane(
        tmp_path,
        control=window_result("we should meet today", avg_logprob=-0.4),
        wide=window_result("we should meet today", avg_logprob=-0.4),
    )
    out_dir = tmp_path / "job"
    out_dir.mkdir()
    publish_adaptive_lane(out_dir, outcome, first_pass_qa=QAReport(), regenerate=None)
    written = read_json(out_dir / "adaptive" / "redecode_plan.json")
    replayed = load_sealed_plan(written, maximum_bytes=4_194_304)
    assert canonical_plan_bytes(replayed) == canonical_plan_bytes(outcome.plan)


# --------------------------------------------------------------------------
# End-to-end pipeline integration
# --------------------------------------------------------------------------


class PipelineStubAligner:
    """Lays forced-alignment words evenly inside each source segment."""

    name = "stub-forced"
    last_device = "cpu"

    def align(
        self,
        audio_path: Path,
        asr_result: ASRResult,
        config: PipelineConfig,
    ) -> list[AlignedWord]:
        words: list[AlignedWord] = []
        for segment in asr_result.segments:
            tokens = [token for token in segment.text.split() if token]
            if not tokens:
                continue
            step = (segment.end - segment.start) / len(tokens)
            for index, token in enumerate(tokens):
                start = segment.start + index * step
                words.append(
                    AlignedWord(
                        text=token,
                        start=round(start, 4),
                        end=round(start + step * 0.9, 4),
                        alignment_source="forced",
                    )
                )
        return words


class PipelineStubASR:
    """First pass on the whole file; programmed window results per beam width."""

    name = "stub-asr"

    def __init__(self, by_beam: dict[int, ASRResult]) -> None:
        self.by_beam = by_beam
        self.window_calls = 0

    def transcribe(self, audio_path: Path, config: PipelineConfig) -> ASRResult:
        if audio_path.name.startswith("adaptive_span_"):
            self.window_calls += 1
            return self.by_beam[config.asr.beam_size].model_copy(deep=True)
        return first_pass_result()


class PipelineStubDiarization:
    name = "stub-diarization"

    def diarize(self, audio_path: Path, config: PipelineConfig):
        turns = [DiarizationTurn(start=0.0, end=12.0, speaker="SPEAKER_00", source="stub")]
        from local_transcript_max.schemas import DiarizationResult

        return DiarizationResult(
            turns=turns,
            exclusive_turns=turns,
            metadata={"backend": "stub", "device": "cpu", "requested_device": "cpu"},
        )


def _write_silence(path: Path, seconds: float = 12.0) -> None:
    import math
    import wave

    rate = 16000
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = [
            int(0.1 * 32767 * math.sin(2 * math.pi * 220 * index / rate)).to_bytes(
                2, "little", signed=True
            )
            for index in range(int(rate * seconds))
        ]
        handle.writeframes(b"".join(frames))


def _pipeline_config(tmp_path: Path) -> PipelineConfig:
    config = load_config(cwd=tmp_path)
    config.speaker_id.mode = "off"
    config.alignment.backend = "auto"
    config.redecode.mode = "low-confidence"
    config.redecode.selection_policy = "adaptive-safe"
    validate_adaptive_selection(config)
    return config


def test_pipeline_publishes_the_derived_lane_and_preserves_the_first_pass(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    from local_transcript_max.pipeline import transcribe_file

    media = tmp_path / "sample.wav"
    _write_silence(media)
    out_dir = tmp_path / "out"
    backend = PipelineStubASR(
        {
            5: window_result(SUSPECT_TEXT, avg_logprob=-1.4, compression_ratio=2.0),
            10: window_result("we should meet today", avg_logprob=-0.3, compression_ratio=1.0),
        }
    )
    transcript = transcribe_file(
        media,
        out_dir,
        _pipeline_config(tmp_path),
        asr_backend=backend,
        alignment_backend=PipelineStubAligner(),
        diarization_backend=PipelineStubDiarization(),
        precheck_models=False,
    )

    # The first pass stays exactly as decoded.
    assert SUSPECT_TEXT in " ".join(segment.text for segment in transcript.segments)
    assert SUSPECT_TEXT in (out_dir / "transcript.txt").read_text(encoding="utf-8")

    lane = out_dir / "adaptive"
    assert lane.is_dir()
    selected = read_json(lane / "transcript.selected.json")
    selected_text = " ".join(segment["text"] for segment in selected["segments"])
    assert "we should meet today" in selected_text
    assert SUSPECT_TEXT not in selected_text
    assert (lane / "transcript.srt").exists()
    assert (lane / "qa_report.selected.json").exists()
    validation = read_json(lane / "adaptive_validation_report.json")
    assert validation["valid"] is True
    plan = load_sealed_plan(read_json(lane / "redecode_plan.json"), maximum_bytes=4_194_304)
    assert plan.summary.promoted_span_count == 1
    assert plan.selected_asr_sha256 is not None
    # Every derived word carries a speaker from the reused diarization.
    assert {word["speaker"] for word in selected["words"]} == {"SPEAKER_00"}


def test_pipeline_without_adaptive_selection_produces_no_derived_lane(
    tmp_path: Path,
    stub_window: list[Any],
) -> None:
    from local_transcript_max.pipeline import transcribe_file

    media = tmp_path / "sample.wav"
    _write_silence(media)
    out_dir = tmp_path / "out"
    config = load_config(cwd=tmp_path)
    config.speaker_id.mode = "off"
    config.alignment.backend = "auto"
    backend = PipelineStubASR({5: window_result("unused", avg_logprob=-0.1)})

    transcribe_file(
        media,
        out_dir,
        config,
        asr_backend=backend,
        alignment_backend=PipelineStubAligner(),
        diarization_backend=PipelineStubDiarization(),
        precheck_models=False,
    )

    assert not (out_dir / "adaptive").exists()
    assert backend.window_calls == 0
    assert not (out_dir / "transcript.redecode_candidates.json").exists()


# --------------------------------------------------------------------------
# Bounded synthetic evaluation harness
# --------------------------------------------------------------------------


def test_synthetic_evaluation_matches_every_policy_expectation() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from evaluate_adaptive_redecode import DEFAULT_CORPUS, evaluate_corpus

    corpus = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))
    report = evaluate_corpus(corpus)

    assert report["evidence_class"] == "synthetic_development_fixture"
    assert report["held_out_quality_evidence"] is False
    assert report["policy_expectation_mismatches"] == []
    assert report["false_promotion_count"] == 0
    assert report["missed_correction_count"] == 0
    assert report["deterministic_replay"] is True
    # The premise this lane exists for: a wider beam everywhere is not reliably
    # better, so the global beam-10 arm may be worse than the beam-5 baseline.
    assert report["arms"]["global_beam_10"]["wer"] > report["arms"]["beam_5"]["wer"]
    assert report["arms"]["adaptive_5_to_10"]["wer"] <= report["arms"]["beam_5"]["wer"]


def test_existing_candidate_only_configs_with_a_wide_beam_still_load() -> None:
    """The ladder rule must not retroactively reject a legacy re-decode beam.

    ``redecode.beam_size`` has always accepted 1-20. Enforcing the adaptive
    ladder in candidate-only mode would break every existing configuration whose
    single re-decode beam is already 10 or wider.
    """

    for beam in (10, 12, 20):
        config = PipelineConfig(redecode={"mode": "low-confidence", "beam_size": beam})
        assert config.redecode.selection_policy == "candidate-only"
        assert config.redecode.beam_size == beam
        assert config.redecode.beam_ladder == (beam,)
