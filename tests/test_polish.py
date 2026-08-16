# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

import local_transcript_max.polish as polish_module
from local_transcript_max.cleanup import (
    APPLE_FOUNDATION_MODEL_ID,
    APPLE_PUNCTUATION_WINDOW_WORDS,
    SOURCE_GRAMMAR_SCORER_VERSION,
    WINDOW_AGGREGATE_SCORER_VERSION,
    _build_presentation_decoder_words,
    _decoded_surface_quality,
    _presentation_context_transcript,
    _source_and_grammar_scorer,
    project_punctuation_cleanup,
    scorer_output_digest,
    source_grammar_configuration_digest,
    window_aggregate_configuration_digest,
)
from local_transcript_max.config import PipelineConfig
from local_transcript_max.export.markdown_export import render_markdown
from local_transcript_max.export.srt_export import render_srt
from local_transcript_max.export.vtt_export import render_vtt
from local_transcript_max.polish import (
    PolishError,
    compose_completed_job_derivatives,
    polish_completed_job,
)
from local_transcript_max.presentation import (
    BoundarySupport,
    EvidenceRole,
    PresentationProposal,
    ScorerOutput,
    ScorerProvenance,
    TrailingPunctuation,
    apply_presentation_plan,
    decode_presentation,
    deserialize_presentation_plan,
    lexical_text_digest,
    serialize_presentation_plan,
)
from local_transcript_max.presentation_sat import (
    SaTBoundaryScorer,
    SaTMode,
    sat_score_receipt_sha256,
    sat_score_receipt_to_dict,
)
from local_transcript_max.schemas import AlignedWord, DiarizationTurn, SpeakerTurn, Transcript
from local_transcript_max.semantic_cleanup import (
    CanonicalWord,
    EditProvenance,
    LexicalCleanupPolicy,
    SemanticAbstentionReason,
    SemanticProposalBatch,
    canonical_evidence_digest,
    deserialize_semantic_cleanup_plan,
    propose_safe_speech_artifact_edits,
    reconcile_semantic_proposal_batches,
    serialize_semantic_cleanup_plan,
    serialize_semantic_reconciliation_result,
)
from local_transcript_max.speaker_id.enrollment import rename_speaker
from local_transcript_max.speaker_id.identity_io import deterministic_json_bytes
from local_transcript_max.speaker_id.identity_models import (
    DeliverableFormat,
    Participant,
    ResolverMode,
    ResolverPolicy,
    SpeakerRoster,
)
from local_transcript_max.speaker_id.identity_resolver import (
    ResolveIdentityOptions,
    resolve_identities,
)


def _word(text: str, start: float, end: float, speaker: str) -> AlignedWord:
    return AlignedWord(
        text=text,
        start=start,
        end=end,
        speaker=speaker,
        alignment_source="forced",
        assignment_source="exclusive_diarization",
    )


def _write_completed_job(root: Path, monkeypatch: pytest.MonkeyPatch) -> Transcript:
    monkeypatch.setenv("LTM_SPEAKER_RENAME_DIR", str(root.parent / "renames"))
    root.mkdir()
    words = [
        _word("i", 0.0, 0.2, "SPEAKER_00"),
        _word("met", 0.2, 0.4, "SPEAKER_00"),
        _word("natalie", 0.4, 0.7, "SPEAKER_00"),
        _word("michael", 0.8, 1.1, "SPEAKER_01"),
        _word("replied", 1.1, 1.4, "SPEAKER_01"),
    ]
    transcript = Transcript(
        input_path="synthetic.wav",
        words=words,
        diarization_turns=[
            DiarizationTurn(start=0.0, end=0.7, speaker="SPEAKER_00"),
            DiarizationTurn(start=0.8, end=1.4, speaker="SPEAKER_01"),
        ],
        exclusive_diarization_turns=[
            DiarizationTurn(start=0.0, end=0.7, speaker="SPEAKER_00"),
            DiarizationTurn(start=0.8, end=1.4, speaker="SPEAKER_01"),
        ],
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=0.7,
                text="i met natalie",
                words=words[:3],
            ),
            SpeakerTurn(
                speaker="SPEAKER_01",
                start=0.8,
                end=1.4,
                text="michael replied",
                words=words[3:],
            ),
        ],
    )
    (root / "canonical.wav").write_bytes(b"synthetic canonical audio")
    (root / "transcript.json").write_bytes(deterministic_json_bytes(transcript))
    (root / "transcript.srt").write_text(render_srt(transcript), encoding="utf-8")
    (root / "transcript.vtt").write_text(render_vtt(transcript), encoding="utf-8")
    (root / "transcript.md").write_text(render_markdown(transcript), encoding="utf-8")
    (root / "transcript.txt").write_text(
        "SPEAKER_00: i met natalie\n\nSPEAKER_01: michael replied\n",
        encoding="utf-8",
    )
    segments = io.StringIO(newline="")
    writer = csv.DictWriter(segments, fieldnames=["speaker", "start", "end", "text"])
    writer.writeheader()
    for turn in transcript.speaker_turns:
        writer.writerow(
            {
                "speaker": turn.speaker,
                "start": turn.start,
                "end": turn.end,
                "text": turn.text,
            }
        )
    (root / "transcript.segments.csv").write_text(segments.getvalue(), encoding="utf-8")
    (root / "alignment_report.json").write_text("{}\n", encoding="utf-8")
    (root / "run_metadata.json").write_text(
        json.dumps(
            {
                "input_file_hash": "a" * 64,
                "processing_timestamp": "2026-08-12T00:00:00Z",
                "code_revision": "test-source",
                "canonical_audio_metadata": {"duration_seconds": 1.4},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    roster = SpeakerRoster(
        expected_speaker_count=2,
        participants=[Participant(name="Roger"), Participant(name="Matthew")],
    )
    roster_path = root.parent / "roster.json"
    roster_path.write_bytes(deterministic_json_bytes(roster))
    rename_speaker("polish-test", "SPEAKER_00", "Roger", job_dir=root)
    rename_speaker("polish-test", "SPEAKER_01", "Matthew", job_dir=root)
    resolve_identities(
        root,
        ResolveIdentityOptions(
            policy=ResolverPolicy(
                mode=ResolverMode.APPLY_SAFE,
                deliverable_format=DeliverableFormat.ALL,
            ),
            known_speakers=2,
            roster_path=roster_path,
            write_identity_mapped_exports=True,
        ),
    )
    return transcript


def _config() -> PipelineConfig:
    config = PipelineConfig(offline=True)
    config.cleanup.mode = "apple-foundation"
    config.cleanup.scope = "punctuation"
    config.cleanup.required_complete = True
    config.cleanup.write_diff = True
    config.cleanup.proper_nouns = ["Natalie", "Michael"]
    return PipelineConfig.model_validate(config.model_dump(mode="python"))


def _accepted_cleanup(transcript: Transcript, out_dir: Path, _config: PipelineConfig):
    canonical_words_list: list[CanonicalWord] = []
    for turn_index, turn in enumerate(transcript.speaker_turns):
        for word in turn.words:
            canonical_words_list.append(
                CanonicalWord(
                    word_index=len(canonical_words_list),
                    surface=word.text,
                    speaker_id=turn.speaker,
                    start_seconds=word.start,
                    end_seconds=word.end,
                    container_index=turn_index,
                )
            )
    canonical_words = tuple(canonical_words_list)
    semantic_proposals = propose_safe_speech_artifact_edits(canonical_words)
    semantic_batches = tuple(
        SemanticProposalBatch.from_proposals(
            batch_id=f"fixture.semantic.{index}",
            canonical_words=canonical_words,
            provenance=proposal.provenance,
            proposals=(proposal,),
        )
        for index, proposal in enumerate(semantic_proposals)
    ) or (
        SemanticProposalBatch.abstain(
            batch_id="fixture.semantic",
            canonical_words=canonical_words,
            provenance=EditProvenance(
                "fixture.semantic",
                "1.0",
                canonical_evidence_digest(canonical_words),
            ),
            reason=SemanticAbstentionReason.NO_SUPPORTED_EDIT,
        ),
    )
    semantic_result = reconcile_semantic_proposal_batches(
        canonical_words,
        semantic_batches,
        policy=LexicalCleanupPolicy.safe_default(),
    )
    semantic_plan = semantic_result.plan
    decoder_words = _build_presentation_decoder_words(
        transcript,
        semantic_plan.clean_surfaces,
        source_word_indices=semantic_plan.clean_to_source_indices,
    )
    proposals = tuple(
        PresentationProposal(
            word_index=index,
            lexical_signature=word.lexical_signature,
            lexical_text_digest=lexical_text_digest(word.lexical_text),
            trailing_punctuation=(
                TrailingPunctuation.PERIOD if index in {2, 4} else TrailingPunctuation.NONE
            ),
            punctuation_probability=0.99,
            capitalization_probability=0.99 if index in {0, 2, 3} else 0.01,
            sentence_break_probability=0.99 if index in {2, 4} else 0.01,
            paragraph_break_probability=0.01,
        )
        for index, word in enumerate(decoder_words)
    )
    sat_result = SaTBoundaryScorer(
        out_dir / "missing-sat-model",
        out_dir / "missing-sat-tokenizer",
    ).score_with_receipt(
        decoder_words,
        mode=SaTMode.OPTIONAL,
        signal_weight=0.75,
    )
    source_configuration_digest = source_grammar_configuration_digest(
        proper_nouns=_config.cleanup.proper_nouns,
        word_count=len(decoder_words),
    )
    source_output = _source_and_grammar_scorer(
        decoder_words,
        semantic_plan.clean_surfaces,
        proper_nouns=_config.cleanup.proper_nouns,
    )
    window_evidence_digest = "e" * 64
    aggregate_configuration_digest = window_aggregate_configuration_digest(
        mode="apple-foundation",
        model=APPLE_FOUNDATION_MODEL_ID,
        window_size=APPLE_PUNCTUATION_WINDOW_WORDS,
        window_count=1,
        evidence_digest=window_evidence_digest,
    )
    aggregate_output = ScorerOutput(
        ScorerProvenance(
            "apple-foundation.window-aggregate",
            WINDOW_AGGREGATE_SCORER_VERSION,
            aggregate_configuration_digest,
        ),
        proposals,
    )
    presentation_plan = decode_presentation(
        decoder_words,
        (source_output, aggregate_output, sat_result.output),
    )
    sat_receipt = sat_score_receipt_to_dict(sat_result.receipt)
    sat_receipt["receipt_sha256"] = sat_score_receipt_sha256(sat_result.receipt)
    presented_surfaces = apply_presentation_plan(decoder_words, presentation_plan)
    surface_quality = _decoded_surface_quality(
        _presentation_context_transcript(
            transcript,
            semantic_plan.clean_to_source_indices,
            semantic_plan.clean_surfaces,
        ),
        semantic_plan.clean_surfaces,
        presented_surfaces,
        presentation_plan,
        proper_nouns=_config.cleanup.proper_nouns,
    )
    cleaned = project_punctuation_cleanup(transcript, " ".join(presented_surfaces))
    (out_dir / "transcript.cleaned.json").write_bytes(deterministic_json_bytes(cleaned))
    (out_dir / "cleanup_diff.json").write_text(
        '{"accepted":true,"reason":"accepted"}\n', encoding="utf-8"
    )
    plan_bytes = serialize_presentation_plan(presentation_plan)
    (out_dir / "presentation_plan.json").write_bytes(plan_bytes)
    semantic_bytes = serialize_semantic_cleanup_plan(semantic_plan) + b"\n"
    (out_dir / "semantic_cleanup_plan.json").write_bytes(semantic_bytes)
    semantic_receipt = serialize_semantic_reconciliation_result(semantic_result) + b"\n"
    (out_dir / "semantic_cleanup_receipt.json").write_bytes(semantic_receipt)

    def bind_receipt(payload: dict[str, object]) -> dict[str, object]:
        payload["receipt_sha256"] = hashlib.sha256(deterministic_json_bytes(payload)).hexdigest()
        return payload

    source_receipt = bind_receipt(
        {
            "schema_version": "auralign.presentation.scorer-receipt.v1",
            "status": "succeeded",
            "abstention_reason": None,
            "scorer_id": "auralign.source-grammar-features",
            "scorer_version": SOURCE_GRAMMAR_SCORER_VERSION,
            "configuration_digest": source_configuration_digest,
            "presentation_input_digest": presentation_plan.input_digest,
            "proposal_count": 5,
            "output_digest": scorer_output_digest(source_output),
        }
    )
    aggregate_receipt = bind_receipt(
        {
            "schema_version": "auralign.presentation.scorer-receipt.v1",
            "status": "succeeded",
            "abstention_reason": None,
            "scorer_id": "apple-foundation.window-aggregate",
            "scorer_version": WINDOW_AGGREGATE_SCORER_VERSION,
            "configuration_digest": aggregate_configuration_digest,
            "presentation_input_digest": presentation_plan.input_digest,
            "proposal_count": 5 * len(TrailingPunctuation),
            "output_digest": scorer_output_digest(aggregate_output),
            "backend_mode": "apple-foundation",
            "backend_model": APPLE_FOUNDATION_MODEL_ID,
            "window_size_words": APPLE_PUNCTUATION_WINDOW_WORDS,
            "window_count": 1,
            "window_evidence_digest": window_evidence_digest,
            "evidenced_word_count": 5,
            "neutral_gap_word_count": 0,
            "evidenced_word_indices_sha256": hashlib.sha256(
                deterministic_json_bytes(list(range(5)))
            ).hexdigest(),
        }
    )
    sat_generic_receipt = bind_receipt(
        {
            "schema_version": "auralign.presentation.scorer-receipt.v1",
            "status": "abstained",
            "abstention_reason": "missing_model_directory",
            "scorer_id": sat_result.output.provenance.scorer_id,
            "scorer_version": sat_result.output.provenance.scorer_version,
            "configuration_digest": sat_result.output.provenance.configuration_digest,
            "presentation_input_digest": presentation_plan.input_digest,
            "proposal_count": 0,
            "output_digest": sat_result.receipt.output_digest,
            "source_receipt_sha256": sat_score_receipt_sha256(sat_result.receipt),
        }
    )
    return {
        "enabled": True,
        "accepted": True,
        "request": {
            "backend": "apple-foundation",
            "locality": "on-device-system-model",
            "model": "com.apple.system-language-model.default",
            "sdk_version": "0.2.1",
            "window_count": 1,
            "window_size_words": APPLE_PUNCTUATION_WINDOW_WORDS,
            "accepted_window_count": 1,
            "fallback_window_count": 0,
            "fallback_reasons": {},
            "decoder_version": presentation_plan.decoder_version,
            "decoder_tie_break_policy": presentation_plan.tie_break_policy,
            "presentation_plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
            "presentation_plan_word_count": 5,
            "presentation_input_digest": presentation_plan.input_digest,
            "hard_constraint_application_count": (
                presentation_plan.hard_constraint_application_count
            ),
            "hard_constraint_violation_count": 0,
            "scorer_count": 3,
            "proposal_source_registry": [
                {"key": "source-grammar-features"},
                {"key": "cleanup-backend-indexed-windows"},
                {"key": "sat-local"},
                {"key": "word-token-classifier"},
            ],
            "proposal_source_receipts": [
                source_receipt,
                aggregate_receipt,
                sat_generic_receipt,
            ],
            "sat_receipt": sat_receipt,
            "word_model_receipt": {
                "schema_version": "auralign.presentation-word-model.routing.v1",
                "status": "off",
                "backend": "none",
                "reason_code": "no_approved_checkpoint",
                "authoritative_text": False,
                "network_access": False,
            },
            "quality_profile": "semantically-faithful-clean-v1",
            "semantic_cleanup_version": "auralign.semantic-cleanup.v1",
            "semantic_cleanup_plan_sha256": hashlib.sha256(
                semantic_bytes.rstrip(b"\n")
            ).hexdigest(),
            "semantic_reconciliation_sha256": hashlib.sha256(
                semantic_receipt.rstrip(b"\n")
            ).hexdigest(),
            "semantic_proposal_receipt_count": len(semantic_result.proposal_receipts),
            "semantic_abstention_receipt_count": len(semantic_result.abstention_receipts),
            "canonical_semantic_evidence_digest": semantic_plan.canonical_digest,
            "semantic_edit_count": 0,
            "semantic_rejected_proposal_count": 0,
            "semantic_clean_word_count": 5,
            "surface_quality": surface_quality,
        },
        "diff": {
            "accepted": True,
            "reason": "accepted",
            "original_word_count": 5,
            "cleaned_word_count": 5,
        },
    }


def test_polish_composes_complete_cleanup_and_global_identity_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    source_before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in job.iterdir()
        if path.is_file()
    }
    evidence = tmp_path / "recovery_provenance.json"
    evidence.write_text('{"preserved":true}\n', encoding="utf-8")
    monkeypatch.setattr(polish_module, "run_cleanup", _accepted_cleanup)
    out = tmp_path / "polished"

    manifest = polish_completed_job(job, out, _config(), evidence_paths=[evidence])

    assert manifest["validation"]["valid"] is True
    assert manifest["validation"]["word_count"] == 5
    assert manifest["cleanup"]["fallback_window_count"] == 0
    assert manifest["cleanup"]["model"] == "com.apple.system-language-model.default"
    assert manifest["cleanup"]["locality"] == "on-device-system-model"
    assert manifest["cleanup"]["backend"]["backend"] == "apple-foundation"
    assert manifest["cleanup"]["schema_version"] == "auralign_cleanup_receipt.v2"
    assert manifest["cleanup"]["sat_receipt"]["status"] == "abstained"
    assert manifest["cleanup"]["sat_receipt"]["schema_version"].endswith(".v2")
    assert len(manifest["cleanup"]["proposal_source_receipts"]) == 3
    assert manifest["cleanup"]["word_model_receipt"]["status"] == "off"
    assert set(manifest["cleanup"]["derived_transcript_hashes"]) == {
        path.name for path in out.glob("transcript.polished.identity_mapped.*")
    }
    assert (out / "derived_manifest.json").is_file()
    assert (out / "validation_report.json").is_file()
    retained_cleanup_bytes = (out / "cleanup_report.json").read_bytes()
    retained_cleanup = json.loads(retained_cleanup_bytes)
    semantic_plan_bytes = (out / "semantic_cleanup_plan.json").read_bytes()
    semantic_receipt_bytes = (out / "semantic_cleanup_receipt.json").read_bytes()
    semantic_receipt = json.loads(semantic_receipt_bytes)
    semantic_plan = deserialize_semantic_cleanup_plan(semantic_plan_bytes[:-1])
    presentation_plan = deserialize_presentation_plan((out / "presentation_plan.json").read_bytes())
    decoder_words = _build_presentation_decoder_words(
        canonical,
        semantic_plan.clean_surfaces,
        source_word_indices=semantic_plan.clean_to_source_indices,
    )
    assert retained_cleanup == manifest["cleanup"]
    assert (
        retained_cleanup
        == json.loads((out / "derived_manifest.json").read_text(encoding="utf-8"))["cleanup"]
    )
    assert (
        retained_cleanup
        == json.loads((out / "validation_report.json").read_text(encoding="utf-8"))["cleanup"]
    )
    assert retained_cleanup["window_size_words"] == APPLE_PUNCTUATION_WINDOW_WORDS
    assert (
        retained_cleanup["semantic_reconciliation_sha256"]
        == hashlib.sha256(semantic_receipt_bytes[:-1]).hexdigest()
    )
    assert retained_cleanup["semantic_proposal_receipt_count"] == len(
        semantic_receipt["proposal_receipts"]
    )
    assert retained_cleanup["semantic_abstention_receipt_count"] == len(
        semantic_receipt["abstention_receipts"]
    )
    assert manifest["outputs"]["cleanup_report.json"] == {
        "sha256": hashlib.sha256(retained_cleanup_bytes).hexdigest(),
        "size_bytes": len(retained_cleanup_bytes),
    }
    polish_module._validate_semantic_receipt_binding(
        retained_cleanup,
        semantic_receipt_bytes,
        semantic_plan_bytes,
    )
    polish_module._validate_proposal_source_evidence(
        retained_cleanup,
        _config(),
        decoder_words=decoder_words,
        presentation_plan=presentation_plan,
    )
    for path in out.glob("transcript.polished.identity_mapped.*"):
        assert re.search(r"\bSPEAKER_\d+\b", path.read_text(encoding="utf-8")) is None
    srt = (out / "transcript.polished.identity_mapped.srt").read_text(encoding="utf-8")
    assert "Roger: I met Natalie" in srt
    assert "Matthew: Michael replied" in srt
    payload = json.loads(
        (out / "transcript.polished.identity_mapped.json").read_text(encoding="utf-8")
    )
    flattened = [word for turn in payload["speaker_turns"] for word in turn["words"]]
    assert [word["text"] for word in flattened] == [
        "I",
        "met",
        "Natalie",
        "Michael",
        "replied",
    ]
    assert [(word["start"], word["end"]) for word in flattened] == [
        (word.start, word.end) for word in canonical.words
    ]
    assert all(word["alignment_source"] == "forced" for word in flattened)
    assert source_before == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in job.iterdir()
        if path.is_file()
    }


@pytest.mark.parametrize(
    ("field", "expected_error"),
    [
        ("semantic_reconciliation_sha256", "receipt hash"),
        ("semantic_proposal_receipt_count", "proposal receipt coverage"),
        ("semantic_abstention_receipt_count", "abstention receipt coverage"),
    ],
)
@pytest.mark.parametrize("mutation", ["omitted", "tampered"])
def test_compact_cleanup_receipt_rejects_semantic_binding_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    expected_error: str,
    mutation: str,
) -> None:
    canonical = _write_completed_job(tmp_path / "job", monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    compact = polish_module._cleanup_receipt(report, config)
    candidate = dict(compact)
    if mutation == "omitted":
        candidate.pop(field)
    elif field == "semantic_reconciliation_sha256":
        candidate[field] = "0" * 64
    else:
        value = candidate[field]
        assert isinstance(value, int)
        candidate[field] = value + 1

    with pytest.raises(PolishError, match=expected_error):
        polish_module._validate_semantic_receipt_binding(
            candidate,
            (cleanup_dir / "semantic_cleanup_receipt.json").read_bytes(),
            (cleanup_dir / "semantic_cleanup_plan.json").read_bytes(),
        )


@pytest.mark.parametrize("mutation", ["omitted", "tampered"])
def test_compact_cleanup_receipt_rejects_window_size_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    canonical = _write_completed_job(tmp_path / "job", monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    compact = polish_module._cleanup_receipt(report, config)
    candidate = dict(compact)
    if mutation == "omitted":
        candidate.pop("window_size_words")
    else:
        value = candidate["window_size_words"]
        assert isinstance(value, int)
        candidate["window_size_words"] = value + 1
    semantic_plan = deserialize_semantic_cleanup_plan(
        (cleanup_dir / "semantic_cleanup_plan.json").read_bytes()[:-1]
    )
    decoder_words = _build_presentation_decoder_words(
        canonical,
        semantic_plan.clean_surfaces,
        source_word_indices=semantic_plan.clean_to_source_indices,
    )
    presentation_plan = deserialize_presentation_plan(
        (cleanup_dir / "presentation_plan.json").read_bytes()
    )

    with pytest.raises(PolishError, match="source or aggregate proposal evidence"):
        polish_module._validate_proposal_source_evidence(
            candidate,
            config,
            decoder_words=decoder_words,
            presentation_plan=presentation_plan,
        )


def test_polish_reapplies_typed_plan_and_rejects_surface_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    report = _accepted_cleanup(canonical, cleanup_dir, _config())
    semantic_plan = polish_module._validate_semantic_plan_binding(
        report,
        (cleanup_dir / "semantic_cleanup_plan.json").read_bytes(),
    )
    presentation_plan = polish_module._validate_presentation_plan_binding(
        report,
        (cleanup_dir / "presentation_plan.json").read_bytes(),
    )
    cleaned = polish_module._load_transcript(cleanup_dir / "transcript.cleaned.json")
    first_turn = cleaned.speaker_turns[0]
    tampered_first_word = first_turn.words[0].model_copy(update={"text": "I!!!"})
    tampered_words = [tampered_first_word, *first_turn.words[1:]]
    tampered = cleaned.model_copy(
        update={
            "speaker_turns": [
                first_turn.model_copy(
                    update={
                        "text": " ".join(word.text for word in tampered_words),
                        "words": tampered_words,
                    }
                ),
                *cleaned.speaker_turns[1:],
            ]
        },
        deep=True,
    )

    with pytest.raises(PolishError, match="bound presentation plan"):
        polish_module._validate_cleaned_transcript(
            canonical,
            tampered,
            _config(),
            semantic_plan,
            presentation_plan,
        )


def test_polish_rejects_turn_text_that_disagrees_with_bound_words(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    report = _accepted_cleanup(canonical, cleanup_dir, _config())
    semantic_plan = polish_module._validate_semantic_plan_binding(
        report,
        (cleanup_dir / "semantic_cleanup_plan.json").read_bytes(),
    )
    presentation_plan = polish_module._validate_presentation_plan_binding(
        report,
        (cleanup_dir / "presentation_plan.json").read_bytes(),
    )
    cleaned = polish_module._load_transcript(cleanup_dir / "transcript.cleaned.json")
    tampered = cleaned.model_copy(
        update={
            "speaker_turns": [
                cleaned.speaker_turns[0].model_copy(
                    update={"text": "I met Natalie. Injected text."}
                ),
                cleaned.speaker_turns[1],
            ],
            "cleaned_text": "I met Natalie. Injected text.\nMichael replied.",
        },
        deep=True,
    )

    with pytest.raises(PolishError, match="turn text"):
        polish_module._validate_cleaned_transcript(
            canonical,
            tampered,
            _config(),
            semantic_plan,
            presentation_plan,
        )


def test_polish_rejects_parallel_word_representation_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    report = _accepted_cleanup(canonical, cleanup_dir, _config())
    semantic_plan = polish_module._validate_semantic_plan_binding(
        report,
        (cleanup_dir / "semantic_cleanup_plan.json").read_bytes(),
    )
    presentation_plan = polish_module._validate_presentation_plan_binding(
        report,
        (cleanup_dir / "presentation_plan.json").read_bytes(),
    )
    cleaned = polish_module._load_transcript(cleanup_dir / "transcript.cleaned.json")
    tampered = cleaned.model_copy(
        update={
            "words": [
                cleaned.words[0].model_copy(update={"text": "Injected"}),
                *cleaned.words[1:],
            ]
        },
        deep=True,
    )

    with pytest.raises(PolishError, match="top-level word representation"):
        polish_module._validate_cleaned_transcript(
            canonical,
            tampered,
            _config(),
            semantic_plan,
            presentation_plan,
        )


def test_polish_reapplication_rejects_canonically_equivalent_unicode_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    accented = canonical.words[2].model_copy(update={"text": "café"})
    canonical = canonical.model_copy(
        update={
            "words": [*canonical.words[:2], accented, *canonical.words[3:]],
            "speaker_turns": [
                canonical.speaker_turns[0].model_copy(
                    update={
                        "text": "i met café",
                        "words": [*canonical.speaker_turns[0].words[:2], accented],
                    }
                ),
                canonical.speaker_turns[1],
            ],
        },
        deep=True,
    )
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    report = _accepted_cleanup(canonical, cleanup_dir, _config())
    semantic_plan = polish_module._validate_semantic_plan_binding(
        report,
        (cleanup_dir / "semantic_cleanup_plan.json").read_bytes(),
    )
    presentation_plan = polish_module._validate_presentation_plan_binding(
        report,
        (cleanup_dir / "presentation_plan.json").read_bytes(),
    )
    cleaned = polish_module._load_transcript(cleanup_dir / "transcript.cleaned.json")
    first_turn = cleaned.speaker_turns[0]
    decomposed = first_turn.words[2].model_copy(update={"text": "Cafe\u0301."})
    tampered = cleaned.model_copy(
        update={
            "speaker_turns": [
                first_turn.model_copy(
                    update={
                        "text": "I met Cafe\u0301.",
                        "words": [*first_turn.words[:2], decomposed],
                    }
                ),
                cleaned.speaker_turns[1],
            ]
        },
        deep=True,
    )

    with pytest.raises(PolishError, match="bound presentation plan"):
        polish_module._validate_cleaned_transcript(
            canonical,
            tampered,
            _config(),
            semantic_plan,
            presentation_plan,
        )


def test_polish_rejects_tampered_semantic_reconciliation_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    report = _accepted_cleanup(canonical, cleanup_dir, _config())
    plan_bytes = (cleanup_dir / "semantic_cleanup_plan.json").read_bytes()
    receipt = json.loads(
        (cleanup_dir / "semantic_cleanup_receipt.json").read_text(encoding="utf-8")
    )
    receipt["plan"]["clean_surfaces"][0] = "Tampered"
    tampered = (
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )

    with pytest.raises(PolishError, match="does not embed the accepted plan"):
        polish_module._validate_semantic_receipt_binding(
            report,
            tampered,
            plan_bytes,
        )


def test_polish_rejects_rehashed_nested_semantic_receipt_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    report = _accepted_cleanup(canonical, cleanup_dir, _config())
    plan_bytes = (cleanup_dir / "semantic_cleanup_plan.json").read_bytes()
    receipt = json.loads(
        (cleanup_dir / "semantic_cleanup_receipt.json").read_text(encoding="utf-8")
    )
    receipt["abstention_receipts"][0]["canonical_digest"] = "f" * 64
    tampered = (
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )
    report["request"]["semantic_reconciliation_sha256"] = hashlib.sha256(tampered[:-1]).hexdigest()

    with pytest.raises(PolishError, match="typed canonical schema"):
        polish_module._validate_semantic_receipt_binding(
            report,
            tampered,
            plan_bytes,
        )


def test_complete_cleanup_rejects_scorer_receipt_policy_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    report["request"]["sat_receipt"]["mode"] = "required"

    with pytest.raises(PolishError, match="SaT receipt"):
        polish_module._require_complete_cleanup(report, canonical, config)


def test_complete_cleanup_rejects_forged_sat_receipt_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    report["request"]["sat_receipt"]["word_count"] = 999

    with pytest.raises(PolishError, match="hash binding"):
        polish_module._require_complete_cleanup(report, canonical, config)


def test_complete_cleanup_rejects_duplicate_sat_proposal_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    receipts = report["request"]["proposal_source_receipts"]
    sat_generic = next(
        receipt
        for receipt in receipts
        if receipt["scorer_id"]
        not in {
            "auralign.source-grammar-features",
            "apple-foundation.window-aggregate",
        }
    )
    receipts.append(dict(sat_generic))

    with pytest.raises(PolishError, match="duplicated"):
        polish_module._require_complete_cleanup(report, canonical, config)


def test_complete_cleanup_rejects_unregistered_scorer_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    extra: dict[str, object] = {
        "schema_version": "auralign.presentation.scorer-receipt.v1",
        "status": "succeeded",
        "abstention_reason": None,
        "scorer_id": "unregistered.extra-scorer",
        "scorer_version": "1.0",
        "configuration_digest": "e" * 64,
        "presentation_input_digest": report["request"]["presentation_input_digest"],
        "proposal_count": 5,
        "output_digest": "f" * 64,
    }
    extra["receipt_sha256"] = hashlib.sha256(deterministic_json_bytes(extra)).hexdigest()
    report["request"]["proposal_source_receipts"].append(extra)

    with pytest.raises(PolishError, match="unexpected proposal-source"):
        polish_module._require_complete_cleanup(report, canonical, config)


def test_complete_cleanup_rejects_scorer_count_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    report["request"]["scorer_count"] = 2

    with pytest.raises(PolishError, match="scorer count"):
        polish_module._require_complete_cleanup(report, canonical, config)


@pytest.mark.parametrize(
    ("scorer_id", "field", "replacement"),
    (
        ("auralign.source-grammar-features", "configuration_digest", "f" * 64),
        ("apple-foundation.window-aggregate", "backend_model", "wrong-model"),
        ("apple-foundation.window-aggregate", "window_evidence_digest", "f" * 64),
    ),
)
def test_complete_cleanup_rejects_rehashed_scorer_configuration_drift(
    scorer_id: str,
    field: str,
    replacement: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    receipt = next(
        item
        for item in report["request"]["proposal_source_receipts"]
        if item["scorer_id"] == scorer_id
    )
    receipt[field] = replacement
    receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(deterministic_json_bytes(receipt)).hexdigest()

    with pytest.raises(PolishError, match="source or aggregate proposal evidence"):
        polish_module._require_complete_cleanup(report, canonical, config)


@pytest.mark.parametrize(
    "scorer_id",
    ("auralign.source-grammar-features", "apple-foundation.window-aggregate"),
)
def test_complete_cleanup_requires_source_and_aggregate_receipts(
    scorer_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    report["request"]["proposal_source_receipts"] = [
        receipt
        for receipt in report["request"]["proposal_source_receipts"]
        if receipt["scorer_id"] != scorer_id
    ]

    with pytest.raises(PolishError, match="source or aggregate"):
        polish_module._require_complete_cleanup(report, canonical, config)


def test_complete_cleanup_rejects_tampered_aggregate_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    aggregate = next(
        receipt
        for receipt in report["request"]["proposal_source_receipts"]
        if receipt["scorer_id"] == "apple-foundation.window-aggregate"
    )
    aggregate["proposal_count"] = 1

    with pytest.raises(PolishError, match="receipt hash"):
        polish_module._require_complete_cleanup(report, canonical, config)


@pytest.mark.parametrize(
    ("tampered_field", "expected_error"),
    [
        ("input_digest", "input digests"),
        ("output_digest", "does not match execution evidence"),
    ],
)
def test_polish_rebinds_sat_receipt_to_decoder_input_and_generic_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered_field: str,
    expected_error: str,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    request = report["request"]
    sat_receipt = request["sat_receipt"]
    sat_receipt[tampered_field] = "0" * 64
    receipt_payload = dict(sat_receipt)
    receipt_payload.pop("receipt_sha256")
    sat_receipt["receipt_sha256"] = hashlib.sha256(
        deterministic_json_bytes(receipt_payload)
    ).hexdigest()
    generic_sat = next(
        receipt
        for receipt in request["proposal_source_receipts"]
        if receipt["scorer_id"]
        not in {
            "auralign.source-grammar-features",
            "apple-foundation.window-aggregate",
        }
    )
    generic_sat["source_receipt_sha256"] = sat_receipt["receipt_sha256"]
    generic_payload = dict(generic_sat)
    generic_payload.pop("receipt_sha256")
    generic_sat["receipt_sha256"] = hashlib.sha256(
        deterministic_json_bytes(generic_payload)
    ).hexdigest()
    decoder_words = _build_presentation_decoder_words(
        canonical,
        tuple(word.text for word in canonical.words),
        source_word_indices=tuple((index,) for index in range(len(canonical.words))),
    )

    with pytest.raises(PolishError, match=expected_error):
        polish_module._validate_proposal_source_evidence(
            request,
            config,
            decoder_words=decoder_words,
        )


def test_polish_recomputes_source_scorer_output_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    request = report["request"]
    source_receipt = next(
        receipt
        for receipt in request["proposal_source_receipts"]
        if receipt["scorer_id"] == "auralign.source-grammar-features"
    )
    source_receipt["output_digest"] = "0" * 64
    receipt_payload = dict(source_receipt)
    receipt_payload.pop("receipt_sha256")
    source_receipt["receipt_sha256"] = hashlib.sha256(
        deterministic_json_bytes(receipt_payload)
    ).hexdigest()
    decoder_words = _build_presentation_decoder_words(
        canonical,
        tuple(word.text for word in canonical.words),
        source_word_indices=tuple((index,) for index in range(len(canonical.words))),
    )

    with pytest.raises(PolishError, match="Source scorer output digest"):
        polish_module._validate_proposal_source_evidence(
            request,
            config,
            decoder_words=decoder_words,
        )


def test_complete_cleanup_allows_receipt_bound_negative_local_margin_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = _write_completed_job(tmp_path / "job", monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    quality = report["request"]["surface_quality"]
    quality["locally_adverse_added_strong_stop_count"] = 4
    quality["typed_supported_added_strong_stop_count"] += 4

    polish_module._require_complete_cleanup(report, canonical, config)


def test_polish_recomputes_surface_quality_instead_of_trusting_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = _write_completed_job(tmp_path / "job", monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    request = report["request"]
    request["surface_quality"]["locally_adverse_added_strong_stop_count"] += 1
    semantic_plan = deserialize_semantic_cleanup_plan(
        (cleanup_dir / "semantic_cleanup_plan.json").read_bytes().rstrip(b"\n")
    )
    presentation_plan = deserialize_presentation_plan(
        (cleanup_dir / "presentation_plan.json").read_bytes()
    )

    with pytest.raises(PolishError, match="surface-quality report does not match exact replay"):
        polish_module._validate_surface_quality_replay(
            request,
            canonical,
            semantic_plan,
            presentation_plan,
            config,
        )


def test_polish_rejects_rehashed_plan_that_forges_sat_boundary_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    request = report["request"]
    presentation_plan = deserialize_presentation_plan(
        (cleanup_dir / "presentation_plan.json").read_bytes()
    )
    sat_output = next(
        output
        for output in presentation_plan.scorer_outputs
        if output.provenance.scorer_id
        not in {
            "auralign.source-grammar-features",
            "apple-foundation.window-aggregate",
        }
    )
    aggregate_output = next(
        output
        for output in presentation_plan.scorer_outputs
        if output.provenance.scorer_id == "apple-foundation.window-aggregate"
    )
    forged = replace(
        presentation_plan,
        decisions=(
            presentation_plan.decisions[0],
            replace(
                presentation_plan.decisions[1],
                trailing_punctuation=TrailingPunctuation.PERIOD,
                sentence_break=True,
                boundary_support=BoundarySupport(
                    exact_mark_provenance=(aggregate_output.provenance,),
                    boundary_provenance=(sat_output.provenance,),
                ),
            ),
            *presentation_plan.decisions[2:],
        ),
    )
    forged_bytes = serialize_presentation_plan(forged)
    request["presentation_plan_sha256"] = hashlib.sha256(forged_bytes).hexdigest()
    bound_forgery = polish_module._validate_presentation_plan_binding(report, forged_bytes)
    semantic_plan = deserialize_semantic_cleanup_plan(
        (cleanup_dir / "semantic_cleanup_plan.json").read_bytes().rstrip(b"\n")
    )
    decoder_words = _build_presentation_decoder_words(
        canonical,
        semantic_plan.clean_surfaces,
        source_word_indices=semantic_plan.clean_to_source_indices,
    )

    polish_module._validate_proposal_source_evidence(
        request,
        config,
        decoder_words=decoder_words,
        presentation_plan=bound_forgery,
    )
    with pytest.raises(PolishError, match="exact surface-quality replay"):
        polish_module._validate_surface_quality_replay(
            request,
            canonical,
            semantic_plan,
            bound_forgery,
            config,
        )


def test_polish_rejects_rehashed_plan_with_changed_evidence_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canonical = _write_completed_job(tmp_path / "job", monkeypatch)
    cleanup_dir = tmp_path / "cleanup"
    cleanup_dir.mkdir()
    config = _config()
    report = _accepted_cleanup(canonical, cleanup_dir, config)
    request = report["request"]
    plan = deserialize_presentation_plan((cleanup_dir / "presentation_plan.json").read_bytes())
    aggregate_index = next(
        index
        for index, output in enumerate(plan.scorer_outputs)
        if output.provenance.scorer_id == "apple-foundation.window-aggregate"
    )
    aggregate = plan.scorer_outputs[aggregate_index]
    changed_aggregate = replace(
        aggregate,
        proposals=tuple(
            replace(proposal, evidence_roles=(EvidenceRole.BOUNDARY_ONLY,))
            for proposal in aggregate.proposals
        ),
    )
    outputs = list(plan.scorer_outputs)
    outputs[aggregate_index] = changed_aggregate
    forged = replace(plan, scorer_outputs=tuple(outputs))
    forged_bytes = serialize_presentation_plan(forged)
    request["presentation_plan_sha256"] = hashlib.sha256(forged_bytes).hexdigest()
    bound_forgery = polish_module._validate_presentation_plan_binding(report, forged_bytes)
    semantic_plan = deserialize_semantic_cleanup_plan(
        (cleanup_dir / "semantic_cleanup_plan.json").read_bytes().rstrip(b"\n")
    )
    decoder_words = _build_presentation_decoder_words(
        canonical,
        semantic_plan.clean_surfaces,
        source_word_indices=semantic_plan.clean_to_source_indices,
    )

    with pytest.raises(PolishError, match="exact receipt digest"):
        polish_module._validate_proposal_source_evidence(
            request,
            config,
            decoder_words=decoder_words,
            presentation_plan=bound_forgery,
        )


def test_polish_fails_closed_without_publishing_on_rejected_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    _write_completed_job(job, monkeypatch)

    def rejected(_transcript: Transcript, _out: Path, _config: PipelineConfig):
        return {
            "enabled": True,
            "accepted": False,
            "request": {
                "window_count": 1,
                "accepted_window_count": 0,
                "fallback_window_count": 1,
            },
        }

    monkeypatch.setattr(polish_module, "run_cleanup", rejected)
    out = tmp_path / "polished"

    with pytest.raises(PolishError, match="accepted"):
        polish_completed_job(job, out, _config())

    assert not out.exists()
    assert not list(tmp_path.glob(".polished.staging.*"))


def test_semantic_plan_binding_rejects_noncanonical_trailing_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = _write_completed_job(tmp_path / "job", monkeypatch)
    report = _accepted_cleanup(transcript, tmp_path, _config())
    plan_bytes = (tmp_path / "semantic_cleanup_plan.json").read_bytes()

    polish_module._validate_semantic_plan_binding(report, plan_bytes)
    with pytest.raises(PolishError, match="canonical final newline"):
        polish_module._validate_semantic_plan_binding(report, plan_bytes + b"\n")
    with pytest.raises(PolishError, match="canonical final newline"):
        polish_module._validate_semantic_plan_binding(report, plan_bytes.rstrip(b"\n"))


def test_polish_refuses_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    _write_completed_job(job, monkeypatch)
    out = tmp_path / "polished"
    out.mkdir()

    with pytest.raises(PolishError, match="must not already exist"):
        polish_completed_job(job, out, _config())


def test_polish_rehashes_sources_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    _write_completed_job(job, monkeypatch)
    monkeypatch.setattr(polish_module, "run_cleanup", _accepted_cleanup)
    original = polish_module._source_hashes
    calls = 0

    def changed(source: Path) -> dict[str, str]:
        nonlocal calls
        calls += 1
        hashes = original(source)
        if calls > 1:
            hashes["transcript.json"] = "0" * 64
        return hashes

    monkeypatch.setattr(polish_module, "_source_hashes", changed)
    out = tmp_path / "polished"

    with pytest.raises(PolishError, match="changed during polishing"):
        polish_completed_job(job, out, _config())

    assert not out.exists()


def test_polish_fails_if_production_source_changes_during_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    _write_completed_job(job, monkeypatch)
    monkeypatch.setattr(polish_module, "run_cleanup", _accepted_cleanup)
    revisions = iter(("source-before", "source-after"))
    monkeypatch.setattr(
        polish_module,
        "current_code_revision",
        lambda: next(revisions),
    )
    out = tmp_path / "polished"

    with pytest.raises(PolishError, match="Production source changed"):
        polish_completed_job(job, out, _config())

    assert not out.exists()
    assert not list(tmp_path.glob(".polished.staging.*"))


def test_proper_noun_validation_includes_possessives() -> None:
    assert polish_module._validate_proper_noun_casing(
        "Katie Katie's Katie’s",
        ["Katie"],
    ) == {"Katie": 3}
    with pytest.raises(PolishError, match="proper-noun casing"):
        polish_module._validate_proper_noun_casing("katie's", ["Katie"])


def test_unattributed_turn_overrides_nested_word_cluster_labels() -> None:
    transcript = Transcript(
        input_path="synthetic.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                mapped_name="Unattributed",
                start=0.0,
                end=0.4,
                text="short fragment",
                words=[
                    _word("short", 0.0, 0.2, "SPEAKER_00"),
                    AlignedWord(
                        text="fragment",
                        start=0.2,
                        end=0.4,
                        speaker=None,
                        alignment_source="forced",
                        assignment_source="no_diarization_coverage",
                    ),
                ],
            )
        ],
    )

    payload = polish_module._display_json_payload(transcript, [])
    rows = list(csv.DictReader(io.StringIO(polish_module._render_words_csv(transcript, []))))

    assert [word["speaker"] for word in payload["speaker_turns"][0]["words"]] == [
        "Unattributed",
        "Unattributed",
    ]
    assert [row["speaker"] for row in rows] == ["Unattributed", "Unattributed"]


def test_semantic_word_removal_renders_consistently_across_every_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    first_turn = canonical.speaker_turns[0]
    second_turn = canonical.speaker_turns[1]
    cleaned_first_words = [
        first_turn.words[0].model_copy(update={"text": ""}, deep=True),
        first_turn.words[1].model_copy(update={"text": "met"}, deep=True),
        first_turn.words[2].model_copy(update={"text": "Natalie."}, deep=True),
    ]
    cleaned_second_words = [
        second_turn.words[0].model_copy(update={"text": "Michael"}, deep=True),
        second_turn.words[1].model_copy(update={"text": "replied."}, deep=True),
    ]
    cleaned = canonical.model_copy(
        update={
            "speaker_turns": [
                first_turn.model_copy(
                    update={"text": "met Natalie.", "words": cleaned_first_words},
                    deep=True,
                ),
                second_turn.model_copy(
                    update={
                        "text": "Michael replied.",
                        "words": cleaned_second_words,
                    },
                    deep=True,
                ),
            ]
        },
        deep=True,
    )
    identity_report = polish_module._load_identity_report(job / "speaker_identity_report.json")
    decisions = [*identity_report.cluster_decisions, *identity_report.turn_decisions]
    outputs = polish_module._render_polished_family(
        canonical=canonical,
        cleaned=cleaned,
        decisions=decisions,
        config=_config(),
    )

    validation = polish_module._validate_outputs(
        outputs=outputs,
        canonical=canonical,
        cleaned=cleaned,
        decisions=decisions,
        config=_config(),
    )
    json_payload = json.loads(outputs["transcript.polished.identity_mapped.json"].decode("utf-8"))
    json_words = [word for turn in json_payload["speaker_turns"] for word in turn["words"]]
    csv_words = list(
        csv.DictReader(
            io.StringIO(outputs["transcript.polished.identity_mapped.words.csv"].decode("utf-8"))
        )
    )

    assert validation["word_count"] == 4
    assert [word["canonical_word_index"] for word in json_words] == [1, 2, 3, 4]
    assert [int(word["canonical_word_index"]) for word in csv_words] == [1, 2, 3, 4]
    for name in (
        "transcript.polished.identity_mapped.srt",
        "transcript.polished.identity_mapped.vtt",
        "transcript.polished.identity_mapped.txt",
        "transcript.polished.identity_mapped.md",
        "transcript.polished.identity_mapped.segments.csv",
    ):
        rendered = outputs[name].decode("utf-8")
        assert "met Natalie." in rendered
        assert " i met " not in f" {rendered.casefold()} "


def test_lexical_projection_rejects_casefold_expansion() -> None:
    canonical_word = _word("ß", 0.0, 0.5, "A")
    canonical = Transcript(
        input_path="synthetic.wav",
        language="de",
        speaker_turns=[
            SpeakerTurn(
                speaker="A",
                start=0.0,
                end=0.5,
                text="ß",
                words=[canonical_word],
            )
        ],
    )
    with pytest.raises(ValueError, match="lexical_word_changed_at_index_0"):
        project_punctuation_cleanup(canonical, "SS")


@pytest.mark.parametrize(
    "artifact_name",
    [
        "transcript.polished.identity_mapped.srt",
        "transcript.polished.identity_mapped.vtt",
        "transcript.polished.identity_mapped.txt",
        "transcript.polished.identity_mapped.md",
        "transcript.polished.identity_mapped.json",
        "transcript.polished.identity_mapped.segments.csv",
        "transcript.polished.identity_mapped.words.csv",
    ],
)
def test_polished_validation_rejects_tampering_in_every_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    cleaned = project_punctuation_cleanup(
        canonical,
        "I met Natalie. Michael replied.",
    )
    identity_report = polish_module._load_identity_report(job / "speaker_identity_report.json")
    decisions = [*identity_report.cluster_decisions, *identity_report.turn_decisions]
    config = _config()
    outputs = polish_module._render_polished_family(
        canonical=canonical,
        cleaned=cleaned,
        decisions=decisions,
        config=config,
    )
    mutated = dict(outputs)
    mutated[artifact_name] = mutated[artifact_name].replace(b"Roger", b"Wrong", 1)

    with pytest.raises(PolishError, match="shared display plan"):
        polish_module._validate_outputs(
            outputs=mutated,
            canonical=canonical,
            cleaned=cleaned,
            decisions=decisions,
            config=config,
        )


def test_cleanup_then_identity_and_identity_then_cleanup_compose_identically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = tmp_path / "job"
    canonical = _write_completed_job(job, monkeypatch)
    config = _config()
    monkeypatch.setattr(polish_module, "run_cleanup", _accepted_cleanup)

    polish_first = tmp_path / "polish-first"
    polish_completed_job(job, polish_first, config)

    cleanup_report = _accepted_cleanup(canonical, job, config)
    (job / "cleanup_report.json").write_bytes(deterministic_json_bytes(cleanup_report))
    cleanup_first = tmp_path / "cleanup-first"
    compose_completed_job_derivatives(job, cleanup_first, config)

    polish_family = {
        path.name: path.read_bytes()
        for path in polish_first.glob("transcript.polished.identity_mapped.*")
    }
    cleanup_family = {
        path.name: path.read_bytes()
        for path in cleanup_first.glob("transcript.polished.identity_mapped.*")
    }
    assert cleanup_family == polish_family
