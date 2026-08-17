# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Orchestration for the bounded adaptive beam re-decode lane.

One immutable audio window per reconciled span is decoded twice — beam 5 as the
comparison control and beam 10 as the challenger — with every other decoding
field held identical. Beam width is the single deliberate difference.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from local_transcript_max import __version__
from local_transcript_max.asr.base import ASRBackend
from local_transcript_max.code_provenance import current_code_revision
from local_transcript_max.config import PipelineConfig
from local_transcript_max.configuration_evidence import configuration_evidence
from local_transcript_max.errors import LTMError
from local_transcript_max.platform_security import ensure_private_directory
from local_transcript_max.redaction import privacy_safe_obj, redact_obj
from local_transcript_max.schemas import AlignedWord, ASRResult, QAReport, Transcript
from local_transcript_max.utils import sha256_file

from ..redecode import extract_audio_window, select_redecode_spans
from .decision import SpanDecision, SpanEvaluationInput, evaluate_span
from .evidence import (
    CandidateEvidence,
    candidate_evidence,
    candidate_text,
    failed_evidence,
    sha256_text,
)
from .plan import (
    AdaptiveRedecodePlan,
    ModelProvenance,
    PlanLimits,
    PlanSummary,
    SpanPlan,
    StageDisposition,
    plan_sha256,
    seal_plan,
)
from .policy import ABSOLUTE_MAXIMUM_CANDIDATES_PER_SPAN, ABSOLUTE_MAXIMUM_SPANS
from .stitch import SpanPromotion, apply_selected_spans

ADAPTIVE_CANDIDATES_SCHEMA_VERSION = "adaptive_redecode_candidates.v1"

CandidateRole = Literal["control", "wide"]


@dataclass
class AdaptiveRedecodeOutcome:
    """Everything the lane produced, ready for transactional publication."""

    plan: AdaptiveRedecodePlan
    sealed_plan: dict[str, Any]
    candidates_artifact: dict[str, Any]
    selected_result: ASRResult | None
    promotions: list[SpanPromotion] = field(default_factory=list)

    @property
    def promoted(self) -> bool:
        return self.selected_result is not None


@dataclass(frozen=True)
class _ReconciledSpan:
    index: int
    reason_codes: tuple[str, ...]
    original_start: float
    original_end: float
    replace_start: float
    replace_end: float
    padded_start: float
    padded_end: float
    replaced_segment_ids: tuple[int, ...]
    source_text: str
    speaker_labels: tuple[str, ...]
    skip_reason: str | None = None

    @property
    def window_duration_seconds(self) -> float:
        return round(max(0.0, self.padded_end - self.padded_start), 6)


def run_adaptive_redecode(
    canonical_wav: Path,
    transcript: Transcript,
    qa_report: QAReport,
    config: PipelineConfig,
    asr_backend: ASRBackend,
    *,
    first_pass_result: ASRResult,
    alignment_backend: object | None = None,
    audio_duration_seconds: float | None = None,
) -> AdaptiveRedecodeOutcome:
    """Run the adaptive lane and return its plan plus any selected ASR result."""

    if config.redecode.selection_policy != "adaptive-safe":
        raise LTMError("run_adaptive_redecode requires redecode.selection_policy=adaptive-safe.")
    if config.redecode.mode == "off":
        raise LTMError("run_adaptive_redecode requires a re-decode mode that selects spans.")

    control_beam, wide_beam = config.redecode.beam_ladder
    spans = _reconcile_spans(
        transcript,
        qa_report,
        config,
        first_pass_result,
        audio_duration_seconds=audio_duration_seconds,
    )
    safe_configuration, configuration_sha256 = configuration_evidence(config)
    canonical_audio_sha256 = sha256_file(canonical_wav) if canonical_wav.exists() else None
    if canonical_audio_sha256 is None:
        raise LTMError("Adaptive re-decode requires the canonical audio artifact.")

    span_plans: list[SpanPlan] = []
    promotions: list[SpanPromotion] = []
    sensitive_spans: list[dict[str, Any]] = []
    wide_decode_count = 0

    with tempfile.TemporaryDirectory(prefix="ltm-adaptive-") as window_root:
        window_dir = ensure_private_directory(Path(window_root))
        for span in spans:
            if span.skip_reason is not None:
                span_plans.append(_skipped_span_plan(span, control_beam, wide_beam))
                sensitive_spans.append(_sensitive_span(span, {}))
                continue
            window = window_dir / f"adaptive_span_{span.index:03d}.wav"
            try:
                extract_audio_window(
                    canonical_wav,
                    window,
                    start=span.padded_start,
                    end=span.padded_end,
                )
                window_audio_sha256 = sha256_file(window)
            except Exception:
                span_plans.append(
                    _skipped_span_plan(
                        span,
                        control_beam,
                        wide_beam,
                        skip_reason="window_extraction_failed",
                    )
                )
                sensitive_spans.append(_sensitive_span(span, {}))
                continue

            decoded: dict[CandidateRole, ASRResult | None] = {}
            evidences: dict[CandidateRole, CandidateEvidence] = {}
            for role, beam in (("control", control_beam), ("wide", wide_beam)):
                role_typed = cast(CandidateRole, role)
                result, evidence = _decode_and_measure(
                    window,
                    config,
                    asr_backend,
                    alignment_backend,
                    role=role_typed,
                    beam_size=beam,
                    window_duration_seconds=span.window_duration_seconds,
                )
                decoded[role_typed] = result
                evidences[role_typed] = evidence
                if role_typed == "wide" and result is not None:
                    wide_decode_count += 1

            control_evidence = evidences["control"]
            wide_evidence = evidences["wide"]
            decision = evaluate_span(
                SpanEvaluationInput(
                    source_text=span.source_text,
                    control_text=candidate_text(decoded["control"]),
                    wide_text=candidate_text(decoded["wide"]),
                    expected_language=first_pass_result.language,
                    speaker_count=len(span.speaker_labels),
                    minimum_alignment_coverage=config.redecode.adaptive_min_alignment_coverage,
                    minimum_logprob_margin=config.redecode.adaptive_min_logprob_margin,
                    protected_margin_multiplier=(
                        config.redecode.adaptive_protected_margin_multiplier
                    ),
                ),
                control_evidence,
                wide_evidence,
            )
            wide_result = decoded["wide"]
            applied = decision.selected == "wide" and wide_result is not None
            if applied and wide_result is not None:
                promotions.append(
                    SpanPromotion(
                        span_index=span.index,
                        replaced_segment_ids=span.replaced_segment_ids,
                        replace_start=span.replace_start,
                        replace_end=span.replace_end,
                        window_offset_seconds=span.padded_start,
                        candidate=wide_result,
                    )
                )
            span_plans.append(
                SpanPlan(
                    index=span.index,
                    reason_codes=list(span.reason_codes),
                    original_start=span.original_start,
                    original_end=span.original_end,
                    padded_start=span.padded_start,
                    padded_end=span.padded_end,
                    window_duration_seconds=span.window_duration_seconds,
                    window_audio_sha256=window_audio_sha256,
                    source_text_sha256=sha256_text(span.source_text),
                    source_word_count=len(span.source_text.split()),
                    speaker_labels=list(span.speaker_labels),
                    candidates=[control_evidence, wide_evidence],
                    decision=decision,
                    applied=applied,
                    applied_start=span.replace_start if applied else None,
                    applied_end=span.replace_end if applied else None,
                    replaced_segment_ids=list(span.replaced_segment_ids) if applied else [],
                )
            )
            sensitive_spans.append(
                _sensitive_span(
                    span,
                    {
                        role: candidate_text(decoded[cast(CandidateRole, role)])
                        for role in ("control", "wide")
                    },
                )
            )

    selected_result: ASRResult | None = None
    if promotions:
        selected_result = apply_selected_spans(first_pass_result, promotions)

    plan = AdaptiveRedecodePlan(
        producer_version=__version__,
        source_revision=current_code_revision(),
        beam_ladder=[control_beam, wide_beam],
        backend=str(getattr(asr_backend, "name", type(asr_backend).__name__)),
        runtime_device=_optional_str(
            first_pass_result.config.get("runtime_device")
            or first_pass_result.config.get("actual_device")
            or first_pass_result.config.get("device")
        ),
        inference_mode=_optional_str(first_pass_result.config.get("inference_mode")),
        model=_model_provenance(first_pass_result),
        canonical_audio_sha256=canonical_audio_sha256,
        first_pass_asr_sha256=asr_result_sha256(first_pass_result),
        first_pass_language=first_pass_result.language,
        configuration_sha256=configuration_sha256,
        asr_configuration=_safe_asr_configuration(safe_configuration),
        limits=PlanLimits(
            maximum_spans=min(config.redecode.max_spans, ABSOLUTE_MAXIMUM_SPANS),
            maximum_span_seconds=config.redecode.adaptive_max_span_seconds,
            maximum_total_window_seconds=config.redecode.adaptive_max_total_window_seconds,
            maximum_candidates_per_span=ABSOLUTE_MAXIMUM_CANDIDATES_PER_SPAN,
            maximum_context_seconds_each_side=config.redecode.pad_seconds,
            maximum_plan_bytes=config.redecode.adaptive_max_plan_bytes,
            maximum_attempts_per_span=1,
        ),
        spans=span_plans,
        summary=_summarize(span_plans, wide_decode_count),
        stage_dispositions=_stage_dispositions(bool(promotions)),
        selected_asr_sha256=(
            asr_result_sha256(selected_result) if selected_result is not None else None
        ),
    )
    sealed = seal_plan(plan, maximum_bytes=config.redecode.adaptive_max_plan_bytes)
    candidates_artifact = cast(
        dict[str, Any],
        redact_obj(
            {
                "schema_version": ADAPTIVE_CANDIDATES_SCHEMA_VERSION,
                "selection_policy": "adaptive-safe",
                "plan_sha256": sealed["plan_sha256"],
                "beam_ladder": [control_beam, wide_beam],
                "spans": sensitive_spans,
            }
        ),
    )
    return AdaptiveRedecodeOutcome(
        plan=plan,
        sealed_plan=sealed,
        candidates_artifact=candidates_artifact,
        selected_result=selected_result,
        promotions=promotions,
    )


def asr_result_sha256(result: ASRResult) -> str:
    """Hash an ASR result through its canonical serialization."""

    encoded = json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decode_and_measure(
    window: Path,
    config: PipelineConfig,
    asr_backend: ASRBackend,
    alignment_backend: object | None,
    *,
    role: CandidateRole,
    beam_size: int,
    window_duration_seconds: float,
) -> tuple[ASRResult | None, CandidateEvidence]:
    """Decode the identical window bytes at one beam width and measure it."""

    candidate_config = _candidate_config(config, beam_size)
    try:
        result = asr_backend.transcribe(window, candidate_config)
    except Exception:
        # The failure text can carry private paths and backend internals, so the
        # reason code stays generic and the span preserves the first pass.
        return None, failed_evidence(
            role=role,
            beam_size=beam_size,
            failure_reason="inference_failed",
            window_duration_seconds=window_duration_seconds,
        )
    alignment_words, alignment_failure = _align_window(
        window,
        result,
        candidate_config,
        alignment_backend,
    )
    return result, candidate_evidence(
        result,
        role=role,
        beam_size=beam_size,
        window_duration_seconds=window_duration_seconds,
        alignment_words=alignment_words,
        alignment_failure_reason=alignment_failure,
    )


def _candidate_config(config: PipelineConfig, beam_size: int) -> PipelineConfig:
    """Clone the effective configuration, changing only the beam width."""

    candidate = copy.deepcopy(config)
    candidate.asr.beam_size = beam_size
    candidate.asr.temperature = config.redecode.temperature
    return candidate


def _align_window(
    window: Path,
    result: ASRResult,
    config: PipelineConfig,
    alignment_backend: object | None,
) -> tuple[list[AlignedWord] | None, str | None]:
    """Align a candidate against the same window audio it was decoded from."""

    backend = alignment_backend
    if backend is None:
        # Mirror the pipeline's own selection so the window is judged by the
        # same independent aligner the full file used.
        from local_transcript_max.alignment.forced_alignment import WhisperXForcedAlignment

        backend = WhisperXForcedAlignment()
    align = getattr(backend, "align", None)
    if align is None:
        return None, "alignment_backend_unavailable"
    try:
        return list(align(window, result, config)), None
    except Exception:
        return None, "alignment_failed"


def _reconcile_spans(
    transcript: Transcript,
    qa_report: QAReport,
    config: PipelineConfig,
    first_pass_result: ASRResult,
    *,
    audio_duration_seconds: float | None,
) -> list[_ReconciledSpan]:
    """Merge QA spans, bind them to whole first-pass segments, and bound them."""

    raw_spans = select_redecode_spans(
        transcript,
        qa_report,
        mode=config.redecode.mode,
        max_spans=min(config.redecode.max_spans, ABSOLUTE_MAXIMUM_SPANS),
        pad_seconds=config.redecode.pad_seconds,
    )
    bound: list[_ReconciledSpan] = []
    consumed: set[int] = set()
    budget = config.redecode.adaptive_max_total_window_seconds
    spent = 0.0
    index = 0
    for raw in raw_spans:
        original_start = float(raw["original_start"])
        original_end = float(raw["original_end"])
        # Strict overlap: a segment that only touches the flagged boundary is
        # not part of the suspicious span and must not be replaced.
        segments = [
            segment
            for segment in first_pass_result.segments
            if segment.end > original_start and segment.start < original_end
        ]
        if not segments:
            continue
        segment_ids = tuple(segment.id for segment in segments)
        if consumed & set(segment_ids):
            # An earlier reconciled span already owns this text. Overlapping
            # replacements are never permitted, so this one is dropped.
            continue
        index += 1
        replace_start = min(segment.start for segment in segments)
        replace_end = max(segment.end for segment in segments)
        padded_start = round(max(0.0, replace_start - config.redecode.pad_seconds), 6)
        padded_end = round(replace_end + config.redecode.pad_seconds, 6)
        if audio_duration_seconds is not None:
            padded_end = round(min(padded_end, float(audio_duration_seconds)), 6)
        if padded_end <= padded_start:
            continue
        span = _ReconciledSpan(
            index=index,
            reason_codes=tuple(sorted(str(code) for code in raw.get("reason_codes", []))),
            original_start=round(original_start, 6),
            original_end=round(original_end, 6),
            replace_start=round(replace_start, 6),
            replace_end=round(replace_end, 6),
            padded_start=padded_start,
            padded_end=padded_end,
            replaced_segment_ids=segment_ids,
            source_text=" ".join(
                segment.text.strip() for segment in segments if segment.text.strip()
            ),
            speaker_labels=_speaker_labels(transcript, replace_start, replace_end),
        )
        duration = span.window_duration_seconds
        if duration > config.redecode.adaptive_max_span_seconds:
            bound.append(_with_skip(span, "span_exceeds_duration_limit"))
            continue
        if spent + duration > budget:
            bound.append(_with_skip(span, "total_window_budget_exhausted"))
            continue
        spent += duration
        consumed.update(segment_ids)
        bound.append(span)
    return bound


def _with_skip(span: _ReconciledSpan, reason: str) -> _ReconciledSpan:
    return _ReconciledSpan(
        index=span.index,
        reason_codes=span.reason_codes,
        original_start=span.original_start,
        original_end=span.original_end,
        replace_start=span.replace_start,
        replace_end=span.replace_end,
        padded_start=span.padded_start,
        padded_end=span.padded_end,
        replaced_segment_ids=span.replaced_segment_ids,
        source_text=span.source_text,
        speaker_labels=span.speaker_labels,
        skip_reason=reason,
    )


def _speaker_labels(transcript: Transcript, start: float, end: float) -> tuple[str, ...]:
    labels = {
        word.speaker
        for word in transcript.words
        if word.speaker
        and word.start is not None
        and word.end is not None
        and word.end >= start
        and word.start <= end
    }
    return tuple(sorted(labels))


def _skipped_span_plan(
    span: _ReconciledSpan,
    control_beam: int,
    wide_beam: int,
    *,
    skip_reason: str | None = None,
) -> SpanPlan:
    reason = skip_reason or span.skip_reason or "span_skipped"
    duration = span.window_duration_seconds
    return SpanPlan(
        index=span.index,
        reason_codes=list(span.reason_codes),
        original_start=span.original_start,
        original_end=span.original_end,
        padded_start=span.padded_start,
        padded_end=span.padded_end,
        window_duration_seconds=duration,
        window_audio_sha256=sha256_text(""),
        source_text_sha256=sha256_text(span.source_text),
        source_word_count=len(span.source_text.split()),
        speaker_labels=list(span.speaker_labels),
        candidates=[
            failed_evidence(
                role="control",
                beam_size=control_beam,
                failure_reason=reason,
                window_duration_seconds=duration,
            ),
            failed_evidence(
                role="wide",
                beam_size=wide_beam,
                failure_reason=reason,
                window_duration_seconds=duration,
            ),
        ],
        decision=SpanDecision(
            selected="source",
            decision_reason="abstained_candidate_failed",
            failure_reasons=[reason],
        ),
    )


def _sensitive_span(span: _ReconciledSpan, texts: dict[str, str]) -> dict[str, Any]:
    return {
        "index": span.index,
        "original_start": span.original_start,
        "original_end": span.original_end,
        "padded_start": span.padded_start,
        "padded_end": span.padded_end,
        "reason_codes": list(span.reason_codes),
        "source_text": span.source_text,
        "source_text_sha256": sha256_text(span.source_text),
        "candidates": [
            {
                "role": role,
                "text": texts.get(role, ""),
                "text_sha256": sha256_text(texts.get(role, "")),
            }
            for role in ("control", "wide")
        ],
    }


def _summarize(span_plans: list[SpanPlan], wide_decode_count: int) -> PlanSummary:
    reason_counts: dict[str, int] = {}
    protected_counts: dict[str, int] = {}
    failed = 0
    for span in span_plans:
        reason = span.decision.decision_reason
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        for protected in span.decision.protected_classes:
            protected_counts[protected] = protected_counts.get(protected, 0) + 1
        failed += sum(1 for candidate in span.candidates if candidate.status == "failed")
    promoted = sum(1 for span in span_plans if span.applied)
    return PlanSummary(
        span_count=len(span_plans),
        promoted_span_count=promoted,
        abstained_span_count=len(span_plans) - promoted,
        failed_candidate_count=failed,
        decision_reason_counts=dict(sorted(reason_counts.items())),
        protected_class_counts=dict(sorted(protected_counts.items())),
        total_window_seconds=round(
            sum(span.window_duration_seconds for span in span_plans), 6
        ),
        wide_beam_decode_count=wide_decode_count,
    )


def _stage_dispositions(promoted: bool) -> list[StageDisposition]:
    """Record every reuse and rerun decision explicitly."""

    reused = "text_independent_inputs_unchanged"
    rerun = "depends_on_selected_asr_text"
    dispositions = [
        StageDisposition(stage="media_preparation", disposition="reused", reason=reused),
        StageDisposition(stage="vad", disposition="reused", reason=reused),
        StageDisposition(stage="diarization", disposition="reused", reason=reused),
        StageDisposition(stage="speaker_embedding", disposition="reused", reason=reused),
    ]
    if not promoted:
        return dispositions
    dispositions.extend(
        [
            StageDisposition(stage="forced_alignment", disposition="rerun", reason=rerun),
            StageDisposition(stage="word_timing", disposition="rerun", reason=rerun),
            StageDisposition(stage="word_speaker_fusion", disposition="rerun", reason=rerun),
            StageDisposition(stage="qa", disposition="rerun", reason=rerun),
            StageDisposition(stage="exports", disposition="rerun", reason=rerun),
        ]
    )
    return dispositions


def _model_provenance(result: ASRResult) -> ModelProvenance | None:
    model = result.model
    if model is None:
        return None
    return ModelProvenance(
        role=model.role,
        model_id=model.model_id,
        revision=model.revision,
        revision_verified=model.revision_verified,
        checkpoint_sha256=model.checkpoint_sha256,
        gated=model.gated,
        present=model.present,
    )


def _safe_asr_configuration(safe_configuration: dict[str, Any]) -> dict[str, Any]:
    asr = safe_configuration.get("asr")
    projected = privacy_safe_obj(asr if isinstance(asr, dict) else {})
    return projected if isinstance(projected, dict) else {}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


__all__ = [
    "ADAPTIVE_CANDIDATES_SCHEMA_VERSION",
    "AdaptiveRedecodeOutcome",
    "asr_result_sha256",
    "plan_sha256",
    "run_adaptive_redecode",
]
