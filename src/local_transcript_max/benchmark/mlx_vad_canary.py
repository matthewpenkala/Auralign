# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Pure promotion gate for MLX whole-file versus VAD-windowed evidence.

The module deliberately performs no transcription and reads no private artifacts.
Callers construct privacy-minimal, hash-bound evidence from completed local runs.
The gate distinguishes a real quality/performance failure from evidence that is
merely insufficient or not safely comparable.
"""

from __future__ import annotations

from enum import StrEnum
from statistics import median
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CODE_REVISION_PATTERN = (
    r"^(?:[0-9a-f]{40}|[0-9a-f]{40}\+source-sha256:[0-9a-f]{64})$"
)
_RUN_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_EPSILON = 1e-12
_WINDOWING_DELTA = "asr.mlx_vad_windowed"


class MlxVadCanaryOutcome(StrEnum):
    """Exhaustive outcome of the fail-closed evidence gate."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class MlxVadVariant(StrEnum):
    """The sole controlled experiment dimension."""

    WHOLE_FILE = "whole_file"
    VAD_WINDOWED = "vad_windowed"


class MlxVadRunStatus(StrEnum):
    """Whether a measured run produced complete comparable evidence."""

    COMPLETED = "completed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class MlxVadRunPhase(StrEnum):
    """Only post-warmup measured runs may participate in this pure gate."""

    MEASURED_WARM = "measured_warm"


class MlxVadFailureCode(StrEnum):
    """Privacy-safe failure classes; never retain paths or transcript prose."""

    TRANSCRIPTION_FAILED = "transcription_failed"
    ARTIFACT_INCOMPLETE = "artifact_incomplete"
    BINDING_MISMATCH = "binding_mismatch"
    METRIC_UNAVAILABLE = "metric_unavailable"
    HOST_CONDITION_UNSTABLE = "host_condition_unstable"
    WINDOWED_NOT_EXECUTED = "windowed_not_executed"
    REVIEW_INCOMPLETE = "review_incomplete"


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class MlxVadTimestampEvidence(_EvidenceModel):
    """Word/segment timing invariants for one completed transcript."""

    word_count: int = Field(gt=0)
    segment_count: int = Field(gt=0)
    missing_timestamp_count: int = Field(ge=0)
    nonmonotonic_timestamp_count: int = Field(ge=0)
    out_of_bounds_timestamp_count: int = Field(ge=0)
    nonpositive_duration_count: int = Field(ge=0)

    @property
    def valid(self) -> bool:
        return (
            self.missing_timestamp_count == 0
            and self.nonmonotonic_timestamp_count == 0
            and self.out_of_bounds_timestamp_count == 0
            and self.nonpositive_duration_count == 0
        )


class MlxVadTextEvidence(_EvidenceModel):
    """Reference and repetition metrics without retaining transcript text."""

    wer: float = Field(ge=0.0)
    cer: float = Field(ge=0.0)
    substitutions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    insertions: int = Field(ge=0)
    adjacent_repeated_ngram_token_count: int = Field(ge=0)
    expected_boundary_count: int = Field(ge=0)
    audited_boundary_count: int = Field(ge=0)
    boundary_changed_word_count: int = Field(ge=0)
    boundary_lost_word_count: int = Field(ge=0)
    boundary_duplicate_word_count: int = Field(ge=0)
    boundary_timestamp_gap_count: int = Field(ge=0)
    boundary_timestamp_overlap_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _boundary_coverage_is_possible(self) -> Self:
        if self.audited_boundary_count > self.expected_boundary_count:
            raise ValueError("audited boundary count cannot exceed expected count")
        return self


class MlxVadPerformanceEvidence(_EvidenceModel):
    """Comparable ASR duration and separate process/MLX memory evidence."""

    asr_seconds: float = Field(gt=0.0)
    total_seconds: float = Field(gt=0.0)
    peak_rss_bytes: int = Field(gt=0)
    peak_mlx_allocator_bytes: int = Field(gt=0)
    model_cold_start: Literal[False] = False
    asr_timing_scope: Literal["pipeline_stage.asr.v1"] = "pipeline_stage.asr.v1"
    total_timing_scope: Literal["completion_timing.observed_end_to_end.v1"] = (
        "completion_timing.observed_end_to_end.v1"
    )
    rss_timing_scope: Literal[
        "completion_timing.rss_peak_process_lifetime.v1"
    ] = "completion_timing.rss_peak_process_lifetime.v1"
    mlx_memory_scope: Literal[
        "transcript_raw.config.memory_after.peak_memory_bytes.v1"
    ] = "transcript_raw.config.memory_after.peak_memory_bytes.v1"

    @model_validator(mode="after")
    def _total_contains_asr_stage(self) -> Self:
        if self.total_seconds + _EPSILON < self.asr_seconds:
            raise ValueError("end-to-end duration cannot be shorter than ASR duration")
        return self


class MlxVadModelBinding(_EvidenceModel):
    """Exact MLX checkpoint and backend identity used by one measured run."""

    model_id: str = Field(min_length=1, max_length=200)
    revision: str = Field(min_length=1, max_length=200)
    checkpoint_sha256: str = Field(pattern=_SHA256_PATTERN)
    backend: Literal["mlx-whisper"] = "mlx-whisper"
    backend_version: str = Field(min_length=1, max_length=100)
    mlx_version: str = Field(min_length=1, max_length=100)


class MlxVadDeviceEvidence(_EvidenceModel):
    """Observed execution evidence, not configuration intent."""

    requested_backend: Literal["mlx-whisper"] = "mlx-whisper"
    actual_device: Literal["mlx"] = "mlx"
    runtime_device: Literal["mlx:gpu"] = "mlx:gpu"
    metal_available: Literal[True] = True


class MlxVadRunEvidence(_EvidenceModel):
    """One privacy-minimal completed-run manifest."""

    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    variant: MlxVadVariant
    phase: MlxVadRunPhase = MlxVadRunPhase.MEASURED_WARM
    sequence_index: int = Field(ge=0)
    status: MlxVadRunStatus = MlxVadRunStatus.COMPLETED
    status_reason: MlxVadFailureCode | None = None
    input_media_sha256: str = Field(pattern=_SHA256_PATTERN)
    code_revision: str = Field(pattern=_CODE_REVISION_PATTERN)
    plan_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    machine_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    host_condition_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    model: MlxVadModelBinding
    device: MlxVadDeviceEvidence
    canonical_audio_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    vad_artifact_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    reference_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    source_config_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    comparison_config_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    configuration_evidence_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    raw_transcript_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    canonical_transcript_result_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    run_metadata_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    completion_timing_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    quality_evidence_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    boundary_evidence_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    mlx_vad_windowed: bool
    inference_mode: Literal["mlx", "mlx-vad-windowed"] | None = None
    raw_mlx_vad_windowed: bool | None = None
    vad_window_count: int | None = Field(default=None, ge=0)
    ownership_intervals_non_overlapping: bool | None = None
    whole_file_fallback_occurred: bool = False
    timestamps: MlxVadTimestampEvidence | None = None
    text: MlxVadTextEvidence | None = None
    performance: MlxVadPerformanceEvidence | None = None

    @model_validator(mode="after")
    def _run_contract_is_coherent(self) -> Self:
        if self.status is MlxVadRunStatus.COMPLETED:
            if self.status_reason is not None:
                raise ValueError("completed runs cannot have status_reason")
            required_artifacts = (
                self.canonical_audio_sha256,
                self.vad_artifact_sha256,
                self.reference_sha256,
                self.source_config_sha256,
                self.comparison_config_sha256,
                self.configuration_evidence_sha256,
                self.raw_transcript_sha256,
                self.canonical_transcript_result_sha256,
                self.run_metadata_sha256,
                self.completion_timing_sha256,
                self.quality_evidence_sha256,
                self.boundary_evidence_sha256,
            )
            if any(value is None for value in required_artifacts):
                raise ValueError(
                    "completed runs require every metric-bearing artifact hash"
                )
            if self.timestamps is None or self.text is None or self.performance is None:
                raise ValueError(
                    "completed runs require timestamp, text, and performance evidence"
                )
        elif self.status_reason is None:
            raise ValueError("failed or inconclusive runs require status_reason")

        if self.variant is MlxVadVariant.WHOLE_FILE:
            if self.mlx_vad_windowed:
                raise ValueError("whole-file runs require mlx_vad_windowed=false")
            if self.whole_file_fallback_occurred:
                raise ValueError("whole-file baseline runs cannot report fallback")
            if self.status is MlxVadRunStatus.COMPLETED and self.inference_mode != "mlx":
                raise ValueError("whole-file runs require inference_mode=mlx")
            if self.status is MlxVadRunStatus.COMPLETED and self.raw_mlx_vad_windowed:
                raise ValueError("whole-file runs require raw.mlx.vad_windowed=false")
            if self.status is MlxVadRunStatus.COMPLETED and self.vad_window_count != 0:
                raise ValueError("whole-file runs cannot report VAD windows")
            if (
                self.status is MlxVadRunStatus.COMPLETED
                and self.ownership_intervals_non_overlapping is not None
            ):
                raise ValueError("whole-file runs cannot report window ownership evidence")
        else:
            if not self.mlx_vad_windowed:
                raise ValueError("windowed runs require mlx_vad_windowed=true")
            if (
                self.status is MlxVadRunStatus.COMPLETED
                and not self.whole_file_fallback_occurred
                and self.inference_mode != "mlx-vad-windowed"
            ):
                raise ValueError("windowed runs require inference_mode=mlx-vad-windowed")
            if (
                self.status is MlxVadRunStatus.COMPLETED
                and not self.whole_file_fallback_occurred
                and self.raw_mlx_vad_windowed is not True
            ):
                raise ValueError("windowed runs require raw.mlx.vad_windowed=true")
            if (
                self.status is MlxVadRunStatus.COMPLETED
                and not self.whole_file_fallback_occurred
                and (self.vad_window_count is None or self.vad_window_count <= 1)
            ):
                raise ValueError(
                    "windowed runs require multiple executed VAD windows"
                )
            if (
                self.status is MlxVadRunStatus.COMPLETED
                and not self.whole_file_fallback_occurred
                and self.ownership_intervals_non_overlapping is not True
            ):
                raise ValueError("windowed runs require non-overlapping ownership evidence")
            if (
                self.status is MlxVadRunStatus.COMPLETED
                and not self.whole_file_fallback_occurred
                and self.text is not None
                and self.vad_window_count is not None
                and self.text.expected_boundary_count
                != self.vad_window_count - 1
            ):
                raise ValueError(
                    "expected boundary count must equal executed VAD windows minus one"
                )
        return self


class MlxVadWarmupEvidence(_EvidenceModel):
    """Excluded warmup manifest bound to the same private experiment plan."""

    run_id: str = Field(pattern=_RUN_ID_PATTERN)
    variant: MlxVadVariant
    status: Literal["completed"] = "completed"
    plan_binding_sha256: str = Field(pattern=_SHA256_PATTERN)
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)


class MlxVadCanaryEvidence(_EvidenceModel):
    """All measured manifests and the normalized one-field comparison contract."""

    schema_version: Literal["mlx_vad_canary_evidence.v1"] = (
        "mlx_vad_canary_evidence.v1"
    )
    baseline_runs: tuple[MlxVadRunEvidence, ...]
    candidate_runs: tuple[MlxVadRunEvidence, ...]
    warmup_runs: tuple[MlxVadWarmupEvidence, ...]
    config_delta_paths: tuple[str, ...] = (_WINDOWING_DELTA,)
    reference_reviewed: bool = False

    @field_validator("config_delta_paths")
    @classmethod
    def _only_windowing_may_change(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != (_WINDOWING_DELTA,):
            raise ValueError(
                "MLX VAD comparison permits exactly asr.mlx_vad_windowed to change"
            )
        return value

    @model_validator(mode="after")
    def _variants_and_ids_are_unambiguous(self) -> Self:
        if any(
            run.variant is not MlxVadVariant.WHOLE_FILE for run in self.baseline_runs
        ):
            raise ValueError("baseline_runs may contain only whole_file runs")
        if any(
            run.variant is not MlxVadVariant.VAD_WINDOWED
            for run in self.candidate_runs
        ):
            raise ValueError("candidate_runs may contain only vad_windowed runs")
        measured_runs = (*self.baseline_runs, *self.candidate_runs)
        run_ids = [run.run_id for run in measured_runs]
        run_ids.extend(run.run_id for run in self.warmup_runs)
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("run_id values must be unique across the canary")
        warmup_variants = {run.variant for run in self.warmup_runs}
        if warmup_variants != {
            MlxVadVariant.WHOLE_FILE,
            MlxVadVariant.VAD_WINDOWED,
        }:
            raise ValueError("at least one completed warmup is required per variant")
        plan_bindings = {run.plan_binding_sha256 for run in measured_runs}
        plan_bindings.update(
            run.plan_binding_sha256 for run in self.warmup_runs
        )
        if len(plan_bindings) != 1:
            raise ValueError("warmup and measured runs require one experiment plan binding")
        ordered = sorted(
            measured_runs,
            key=lambda run: run.sequence_index,
        )
        sequence_indexes = [run.sequence_index for run in ordered]
        if len(sequence_indexes) != len(set(sequence_indexes)):
            raise ValueError("sequence_index values must be unique across the canary")
        if any(
            previous.variant is following.variant
            for previous, following in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError("measured runs must alternate whole-file and windowed variants")
        return self


class MlxVadCanaryPolicy(_EvidenceModel):
    """Conservative automatic-promotion thresholds."""

    minimum_measured_runs: int = Field(default=3, ge=3)
    minimum_material_improvement_ratio: float = Field(
        default=0.10,
        ge=0.0,
        lt=1.0,
    )
    maximum_performance_regression_ratio: float = Field(default=0.05, ge=0.0)
    maximum_wer_regression: float = Field(default=0.0, ge=0.0)
    maximum_cer_regression: float = Field(default=0.0, ge=0.0)


class MlxVadAggregate(_EvidenceModel):
    """Medians and worst-case values over completed measured runs."""

    measured_run_count: int = Field(ge=0)
    run_ids: tuple[str, ...]
    median_asr_seconds: float | None
    maximum_asr_seconds: float | None
    median_total_seconds: float | None
    maximum_total_seconds: float | None
    median_peak_rss_bytes: float | None
    maximum_peak_rss_bytes: int | None
    median_peak_mlx_allocator_bytes: float | None
    maximum_peak_mlx_allocator_bytes: int | None
    median_wer: float | None
    maximum_wer: float | None
    median_cer: float | None
    maximum_cer: float | None
    maximum_insertions: int | None
    maximum_deletions: int | None
    maximum_repeated_ngram_tokens: int | None
    boundary_changed_word_count: int
    boundary_lost_word_count: int
    boundary_duplicate_word_count: int
    boundary_timestamp_gap_count: int
    boundary_timestamp_overlap_count: int


class MlxVadCanaryDecision(_EvidenceModel):
    """Serializable decision with explicit PASS, FAIL, or INCONCLUSIVE."""

    schema_version: Literal["mlx_vad_canary_decision.v1"] = (
        "mlx_vad_canary_decision.v1"
    )
    outcome: MlxVadCanaryOutcome
    automatic_promotion_eligible: bool
    reasons: tuple[str, ...]
    asr_speedup_ratio: float | None
    peak_rss_improvement_ratio: float | None
    peak_mlx_allocator_improvement_ratio: float | None
    baseline: MlxVadAggregate
    candidate: MlxVadAggregate
    policy: MlxVadCanaryPolicy
    evidence: MlxVadCanaryEvidence


def evaluate_mlx_vad_gate(
    evidence: MlxVadCanaryEvidence,
    policy: MlxVadCanaryPolicy | None = None,
) -> MlxVadCanaryDecision:
    """Evaluate already-collected evidence without reading media or running models."""

    active_policy = policy or MlxVadCanaryPolicy()
    baseline = _aggregate(evidence.baseline_runs)
    candidate = _aggregate(evidence.candidate_runs)
    speedup = _relative_improvement(
        baseline.median_asr_seconds,
        candidate.median_asr_seconds,
    )
    memory_improvement = _relative_improvement(
        baseline.median_peak_rss_bytes,
        candidate.median_peak_rss_bytes,
    )
    mlx_memory_improvement = _relative_improvement(
        baseline.median_peak_mlx_allocator_bytes,
        candidate.median_peak_mlx_allocator_bytes,
    )

    hard_failure = _hard_failure_reasons(evidence)
    if hard_failure:
        outcome = (
            MlxVadCanaryOutcome.FAIL
            if any(
                reason.startswith(
                    ("candidate_run_failed:", "candidate_whole_file_fallback:")
                )
                for reason in hard_failure
            )
            else MlxVadCanaryOutcome.INCONCLUSIVE
        )
        return _decision(
            outcome,
            hard_failure,
            speedup,
            memory_improvement,
            mlx_memory_improvement,
            baseline,
            candidate,
            active_policy,
            evidence,
        )

    comparability = _comparability_reasons(evidence, baseline, candidate, active_policy)
    if comparability:
        return _decision(
            MlxVadCanaryOutcome.INCONCLUSIVE,
            comparability,
            speedup,
            memory_improvement,
            mlx_memory_improvement,
            baseline,
            candidate,
            active_policy,
            evidence,
        )

    candidate_timestamp_failures = tuple(
        f"candidate_timestamp_invalid:{run.run_id}"
        for run in evidence.candidate_runs
        if run.status is MlxVadRunStatus.COMPLETED
        and run.timestamps is not None
        and not run.timestamps.valid
    )
    quality_failures = (
        *candidate_timestamp_failures,
        *_quality_failure_reasons(baseline, candidate, active_policy),
    )
    performance_failures = _performance_failure_reasons(
        baseline,
        candidate,
        active_policy,
        speedup,
        memory_improvement,
        mlx_memory_improvement,
    )
    failures = (*quality_failures, *performance_failures)
    if failures:
        return _decision(
            MlxVadCanaryOutcome.FAIL,
            failures,
            speedup,
            memory_improvement,
            mlx_memory_improvement,
            baseline,
            candidate,
            active_policy,
            evidence,
        )

    return _decision(
        MlxVadCanaryOutcome.PASS,
        (
            "same_bound_input_canonical_vad_reference_code_models_runtime",
            "exact_single_windowing_config_delta",
            "repeated_measured_evidence_satisfied",
            "timestamps_complete_bounded_positive_and_monotonic",
            "no_text_or_boundary_regression",
            "material_runtime_or_memory_improvement",
            "per_media_pass_only_requires_representative_suite_and_trusted_collector",
        ),
        speedup,
        memory_improvement,
        mlx_memory_improvement,
        baseline,
        candidate,
        active_policy,
        evidence,
    )


def _hard_failure_reasons(evidence: MlxVadCanaryEvidence) -> tuple[str, ...]:
    reasons = [
        f"baseline_run_failed:{run.run_id}"
        for run in evidence.baseline_runs
        if run.status is MlxVadRunStatus.FAILED
    ]
    reasons.extend(
        f"baseline_run_inconclusive:{run.run_id}"
        for run in evidence.baseline_runs
        if run.status is MlxVadRunStatus.INCONCLUSIVE
    )
    reasons.extend(
        f"candidate_run_failed:{run.run_id}"
        for run in evidence.candidate_runs
        if run.status is MlxVadRunStatus.FAILED
    )
    reasons.extend(
        f"candidate_run_inconclusive:{run.run_id}"
        for run in evidence.candidate_runs
        if run.status is MlxVadRunStatus.INCONCLUSIVE
    )
    reasons.extend(
        f"candidate_whole_file_fallback:{run.run_id}"
        for run in evidence.candidate_runs
        if run.whole_file_fallback_occurred
    )
    return tuple(reasons)


def _comparability_reasons(
    evidence: MlxVadCanaryEvidence,
    baseline: MlxVadAggregate,
    candidate: MlxVadAggregate,
    policy: MlxVadCanaryPolicy,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if baseline.measured_run_count < policy.minimum_measured_runs:
        reasons.append(
            "insufficient_baseline_runs:"
            f"{baseline.measured_run_count}<{policy.minimum_measured_runs}"
        )
    if candidate.measured_run_count < policy.minimum_measured_runs:
        reasons.append(
            "insufficient_candidate_runs:"
            f"{candidate.measured_run_count}<{policy.minimum_measured_runs}"
        )
    completed = tuple(
        run
        for run in (*evidence.baseline_runs, *evidence.candidate_runs)
        if run.status is MlxVadRunStatus.COMPLETED
    )
    for field in (
        "input_media_sha256",
        "canonical_audio_sha256",
        "vad_artifact_sha256",
        "reference_sha256",
        "code_revision",
        "plan_binding_sha256",
        "machine_binding_sha256",
        "host_condition_binding_sha256",
        "comparison_config_sha256",
        "model",
        "device",
    ):
        if len({getattr(run, field) for run in completed}) > 1:
            reasons.append(f"binding_mismatch:{field}")
    for label, runs in (
        ("baseline", evidence.baseline_runs),
        ("candidate", evidence.candidate_runs),
    ):
        completed_runs = [
            run for run in runs if run.status is MlxVadRunStatus.COMPLETED
        ]
        if len({run.source_config_sha256 for run in completed_runs}) > 1:
            reasons.append(f"{label}_source_config_changed_between_runs")
        if len(
            {
                run.canonical_transcript_result_sha256
                for run in completed_runs
            }
        ) > 1:
            reasons.append(f"{label}_transcript_not_reproducible")
    baseline_configs = {
        run.source_config_sha256
        for run in evidence.baseline_runs
        if run.status is MlxVadRunStatus.COMPLETED
    }
    candidate_configs = {
        run.source_config_sha256
        for run in evidence.candidate_runs
        if run.status is MlxVadRunStatus.COMPLETED
    }
    if baseline_configs and candidate_configs and baseline_configs == candidate_configs:
        reasons.append("source_config_hash_did_not_change_for_windowing_variant")
    if not evidence.reference_reviewed:
        reasons.append("reference_not_reviewed")
    completed_sequence = sorted(completed, key=lambda run: run.sequence_index)
    if any(run.phase is not MlxVadRunPhase.MEASURED_WARM for run in completed_sequence):
        reasons.append("non_warm_run_included")
    baseline_invalid = [
        run.run_id
        for run in evidence.baseline_runs
        if run.status is MlxVadRunStatus.COMPLETED
        and run.timestamps is not None
        and not run.timestamps.valid
    ]
    reasons.extend(f"baseline_timestamp_invalid:{run_id}" for run_id in baseline_invalid)
    reasons.extend(
        f"candidate_boundary_audit_incomplete:{run.run_id}"
        for run in evidence.candidate_runs
        if run.status is MlxVadRunStatus.COMPLETED
        and run.text is not None
        and run.text.audited_boundary_count
        != run.text.expected_boundary_count
    )
    return tuple(reasons)


def _quality_failure_reasons(
    baseline: MlxVadAggregate,
    candidate: MlxVadAggregate,
    policy: MlxVadCanaryPolicy,
) -> tuple[str, ...]:
    reasons: list[str] = []
    candidate_invalid = candidate.boundary_changed_word_count
    candidate_invalid += candidate.boundary_lost_word_count
    candidate_invalid += candidate.boundary_duplicate_word_count
    candidate_invalid += candidate.boundary_timestamp_gap_count
    candidate_invalid += candidate.boundary_timestamp_overlap_count
    if candidate_invalid:
        reasons.append("candidate_boundary_audit_failed")
    if (
        candidate.maximum_wer is not None
        and baseline.maximum_wer is not None
        and _greater(
            candidate.maximum_wer,
            baseline.maximum_wer + policy.maximum_wer_regression,
        )
    ):
        reasons.append("candidate_worst_case_wer_regressed")
    if (
        candidate.median_wer is not None
        and baseline.median_wer is not None
        and _greater(
            candidate.median_wer,
            baseline.median_wer + policy.maximum_wer_regression,
        )
    ):
        reasons.append("candidate_median_wer_regressed")
    if (
        candidate.maximum_cer is not None
        and baseline.maximum_cer is not None
        and _greater(
            candidate.maximum_cer,
            baseline.maximum_cer + policy.maximum_cer_regression,
        )
    ):
        reasons.append("candidate_worst_case_cer_regressed")
    if (
        candidate.median_cer is not None
        and baseline.median_cer is not None
        and _greater(
            candidate.median_cer,
            baseline.median_cer + policy.maximum_cer_regression,
        )
    ):
        reasons.append("candidate_median_cer_regressed")
    for field, reason in (
        ("maximum_insertions", "candidate_insertions_regressed"),
        ("maximum_deletions", "candidate_deletions_regressed"),
        ("maximum_repeated_ngram_tokens", "candidate_repetition_regressed"),
    ):
        baseline_value = getattr(baseline, field)
        candidate_value = getattr(candidate, field)
        if (
            baseline_value is not None
            and candidate_value is not None
            and candidate_value > baseline_value
        ):
            reasons.append(reason)
    return tuple(reasons)


def _performance_failure_reasons(
    baseline: MlxVadAggregate,
    candidate: MlxVadAggregate,
    policy: MlxVadCanaryPolicy,
    speedup: float | None,
    memory_improvement: float | None,
    mlx_memory_improvement: float | None,
) -> tuple[str, ...]:
    values = (
        baseline.median_asr_seconds,
        baseline.maximum_asr_seconds,
        baseline.median_total_seconds,
        baseline.maximum_total_seconds,
        baseline.median_peak_rss_bytes,
        baseline.maximum_peak_rss_bytes,
        baseline.median_peak_mlx_allocator_bytes,
        baseline.maximum_peak_mlx_allocator_bytes,
        candidate.median_asr_seconds,
        candidate.maximum_asr_seconds,
        candidate.median_total_seconds,
        candidate.maximum_total_seconds,
        candidate.median_peak_rss_bytes,
        candidate.maximum_peak_rss_bytes,
        candidate.median_peak_mlx_allocator_bytes,
        candidate.maximum_peak_mlx_allocator_bytes,
    )
    if any(value is None for value in values):
        return ("missing_performance_evidence",)
    (
        baseline_median_time,
        baseline_max_time,
        baseline_median_total,
        baseline_max_total,
        baseline_median_rss,
        baseline_max_rss,
        baseline_median_mlx,
        baseline_max_mlx,
        candidate_median_time,
        candidate_max_time,
        candidate_median_total,
        candidate_max_total,
        candidate_median_rss,
        candidate_max_rss,
        candidate_median_mlx,
        candidate_max_mlx,
    ) = values
    assert baseline_median_time is not None
    assert baseline_max_time is not None
    assert baseline_median_total is not None
    assert baseline_max_total is not None
    assert baseline_median_rss is not None
    assert baseline_max_rss is not None
    assert baseline_median_mlx is not None
    assert baseline_max_mlx is not None
    assert candidate_median_time is not None
    assert candidate_max_time is not None
    assert candidate_median_total is not None
    assert candidate_max_total is not None
    assert candidate_median_rss is not None
    assert candidate_max_rss is not None
    assert candidate_median_mlx is not None
    assert candidate_max_mlx is not None
    reasons: list[str] = []
    allowed_ratio = 1.0 + policy.maximum_performance_regression_ratio
    if _greater(candidate_median_time, baseline_median_time * allowed_ratio):
        reasons.append("candidate_median_asr_time_regressed")
    if _greater(candidate_max_time, baseline_max_time * allowed_ratio):
        reasons.append("candidate_asr_time_outlier_exceeded_baseline_envelope")
    if _greater(candidate_median_total, baseline_median_total * allowed_ratio):
        reasons.append("candidate_median_total_time_regressed")
    if _greater(candidate_max_total, baseline_max_total * allowed_ratio):
        reasons.append("candidate_total_time_outlier_exceeded_baseline_envelope")
    if _greater(candidate_median_rss, baseline_median_rss * allowed_ratio):
        reasons.append("candidate_median_peak_rss_regressed")
    if _greater(float(candidate_max_rss), float(baseline_max_rss) * allowed_ratio):
        reasons.append("candidate_peak_rss_outlier_exceeded_baseline_envelope")
    if _greater(candidate_median_mlx, baseline_median_mlx * allowed_ratio):
        reasons.append("candidate_median_peak_mlx_allocator_regressed")
    if _greater(float(candidate_max_mlx), float(baseline_max_mlx) * allowed_ratio):
        reasons.append(
            "candidate_peak_mlx_allocator_outlier_exceeded_baseline_envelope"
        )
    material_speedup = (
        speedup is not None
        and speedup + _EPSILON >= policy.minimum_material_improvement_ratio
    )
    material_memory = (
        memory_improvement is not None
        and memory_improvement + _EPSILON
        >= policy.minimum_material_improvement_ratio
    )
    material_mlx_memory = (
        mlx_memory_improvement is not None
        and mlx_memory_improvement + _EPSILON
        >= policy.minimum_material_improvement_ratio
    )
    if not (material_speedup or material_memory or material_mlx_memory):
        reasons.append("no_material_runtime_or_memory_improvement")
    return tuple(reasons)


def _aggregate(runs: tuple[MlxVadRunEvidence, ...]) -> MlxVadAggregate:
    completed = tuple(
        run for run in runs if run.status is MlxVadRunStatus.COMPLETED
    )
    performances = [run.performance for run in completed if run.performance is not None]
    texts = [run.text for run in completed if run.text is not None]
    return MlxVadAggregate(
        measured_run_count=len(completed),
        run_ids=tuple(run.run_id for run in completed),
        median_asr_seconds=_median([row.asr_seconds for row in performances]),
        maximum_asr_seconds=_maximum([row.asr_seconds for row in performances]),
        median_total_seconds=_median([row.total_seconds for row in performances]),
        maximum_total_seconds=_maximum([row.total_seconds for row in performances]),
        median_peak_rss_bytes=_median(
            [float(row.peak_rss_bytes) for row in performances]
        ),
        maximum_peak_rss_bytes=max(
            (row.peak_rss_bytes for row in performances),
            default=None,
        ),
        median_peak_mlx_allocator_bytes=_median(
            [float(row.peak_mlx_allocator_bytes) for row in performances]
        ),
        maximum_peak_mlx_allocator_bytes=max(
            (row.peak_mlx_allocator_bytes for row in performances),
            default=None,
        ),
        median_wer=_median([row.wer for row in texts]),
        maximum_wer=_maximum([row.wer for row in texts]),
        median_cer=_median([row.cer for row in texts]),
        maximum_cer=_maximum([row.cer for row in texts]),
        maximum_insertions=max((row.insertions for row in texts), default=None),
        maximum_deletions=max((row.deletions for row in texts), default=None),
        maximum_repeated_ngram_tokens=max(
            (row.adjacent_repeated_ngram_token_count for row in texts),
            default=None,
        ),
        boundary_changed_word_count=sum(
            row.boundary_changed_word_count for row in texts
        ),
        boundary_lost_word_count=sum(row.boundary_lost_word_count for row in texts),
        boundary_duplicate_word_count=sum(
            row.boundary_duplicate_word_count for row in texts
        ),
        boundary_timestamp_gap_count=sum(
            row.boundary_timestamp_gap_count for row in texts
        ),
        boundary_timestamp_overlap_count=sum(
            row.boundary_timestamp_overlap_count for row in texts
        ),
    )


def _decision(
    outcome: MlxVadCanaryOutcome,
    reasons: tuple[str, ...],
    speedup: float | None,
    memory_improvement: float | None,
    mlx_memory_improvement: float | None,
    baseline: MlxVadAggregate,
    candidate: MlxVadAggregate,
    policy: MlxVadCanaryPolicy,
    evidence: MlxVadCanaryEvidence,
) -> MlxVadCanaryDecision:
    return MlxVadCanaryDecision(
        outcome=outcome,
        automatic_promotion_eligible=False,
        reasons=reasons,
        asr_speedup_ratio=speedup,
        peak_rss_improvement_ratio=memory_improvement,
        peak_mlx_allocator_improvement_ratio=mlx_memory_improvement,
        baseline=baseline,
        candidate=candidate,
        policy=policy,
        evidence=evidence,
    )


def _relative_improvement(
    baseline: float | None,
    candidate: float | None,
) -> float | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (baseline - candidate) / baseline


def _median(values: list[float]) -> float | None:
    return float(median(values)) if values else None


def _maximum(values: list[float]) -> float | None:
    return max(values, default=None)


def _greater(left: float, right: float) -> bool:
    return left > right + _EPSILON
