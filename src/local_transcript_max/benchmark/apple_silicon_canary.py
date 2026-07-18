# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Pure evidence models and promotion policy for Apple Silicon diarization canaries.

This module deliberately does not execute models, inspect media, or select a runtime
device.  It evaluates already-collected local evidence.  A comparison configuration
hash must be calculated from a canonical configuration in which only the diarization
device experiment dimension has been replaced by a stable sentinel.  Full source
configuration hashes remain attached to each run.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from enum import StrEnum
from statistics import median
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_COMPARISON_EPSILON = 1e-12
_REQUIRED_MODEL_BACKENDS = {
    "asr": "mlx-whisper",
    "alignment": "whisperx",
    "diarization": "pyannote",
}
_REQUIRED_RUNTIME_PACKAGES = frozenset(
    {
        "mlx",
        "mlx-metal",
        "mlx-whisper",
        "torch",
        "torchaudio",
        "whisperx",
        "pyannote.audio",
    }
)
_REQUIRED_COMPLETED_ARTIFACTS = frozenset(
    {
        "completion_timing.json",
        "run_metadata.json",
        "transcript.raw.json",
        "alignment_report.json",
        "diarization.json",
        "qa_report.json",
        "speaker_validation_report.json",
    }
)


class CanaryOutcome(StrEnum):
    """Exhaustive decision outcomes for the MPS diarization evidence gate."""

    CPU_RETAINED = "cpu_retained"
    MPS_PROMOTED = "mps_promoted"
    MPS_EXPLICIT_OVERRIDE = "mps_explicit_override"
    BENCHMARK_INCONCLUSIVE = "benchmark_inconclusive"
    MPS_FAILED = "mps_failed"
    MPS_FELL_BACK_TO_CPU = "mps_fell_back_to_cpu"
    QA_REGRESSION = "qa_regression"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class CanaryVariant(StrEnum):
    """The one controlled comparison dimension."""

    CPU_BASELINE = "cpu_baseline"
    MPS_CANDIDATE = "mps_candidate"


class CanaryRunPhase(StrEnum):
    """Lifecycle phase used to keep cold and warmup runs out of warm medians."""

    COLD_START = "cold_start"
    WARMUP = "warmup"
    MEASURED_WARM = "measured_warm"


class CanaryRunStatus(StrEnum):
    """Run completion state; non-completions remain in the evidence artifact."""

    COMPLETED = "completed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class _CanaryModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class ModelVersionBinding(_CanaryModel):
    """Immutable identity of one model/backend participating in the canary."""

    role: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    revision: str | None = Field(default=None, min_length=1)
    revision_verified: bool = True
    checkpoint_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    backend: str = Field(min_length=1)
    backend_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _provenance_is_explicit(self) -> Self:
        if self.revision_verified and self.revision is None:
            raise ValueError("verified model bindings require a revision")
        if not self.revision_verified and self.checkpoint_sha256 is None:
            raise ValueError(
                "unverified-revision model bindings require an exact checkpoint hash"
            )
        return self


class ArtifactHashBinding(_CanaryModel):
    """Content identity for a private nested pipeline artifact."""

    name: str = Field(min_length=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class RuntimeVersionBinding(_CanaryModel):
    """Exact framework package version used by the measured process."""

    package: str = Field(min_length=1)
    version: str = Field(min_length=1)


class CanaryBinding(_CanaryModel):
    """Privacy-minimal identity shared by runs that are safe to compare.

    ``comparison_config_sha256`` binds the canonical configuration with the
    CPU-versus-MPS diarization device field normalized.  Every other configuration
    value, including speaker constraints, must remain represented by the hash.
    """

    input_media_sha256: str = Field(pattern=_SHA256_PATTERN)
    comparison_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    speaker_constraints_sha256: str = Field(pattern=_SHA256_PATTERN)
    machine_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_environment_sha256: str = Field(pattern=_SHA256_PATTERN)
    code_revision: str = Field(min_length=1)
    models: tuple[ModelVersionBinding, ...] = Field(min_length=1)
    runtime_versions: tuple[RuntimeVersionBinding, ...] = Field(min_length=1)
    asr_backend: Literal["mlx-whisper"] = "mlx-whisper"
    asr_precision: Literal["fp16"] = "fp16"
    alignment_backend: Literal["whisperx"] = "whisperx"
    forced_alignment_enabled: Literal[True] = True
    diarization_backend: Literal["pyannote"] = "pyannote"

    @field_validator("models")
    @classmethod
    def _canonical_models(
        cls,
        value: tuple[ModelVersionBinding, ...],
    ) -> tuple[ModelVersionBinding, ...]:
        identities = [(model.role, model.model_id) for model in value]
        if len(set(identities)) != len(identities):
            raise ValueError("model roles and identifiers must be unique")
        roles = [model.role for model in value]
        if (
            len(value) != len(_REQUIRED_MODEL_BACKENDS)
            or len(set(roles)) != len(roles)
            or set(roles) != set(_REQUIRED_MODEL_BACKENDS)
        ):
            raise ValueError(
                "canary model bindings require exact asr, alignment, and "
                "diarization roles"
            )
        for model in value:
            if model.backend != _REQUIRED_MODEL_BACKENDS[model.role]:
                raise ValueError(
                    f"canary model backend mismatch for role {model.role}"
                )
        return tuple(
            sorted(
                value,
                key=lambda model: (
                    model.role,
                    model.model_id,
                    model.revision or "",
                    model.backend,
                    model.backend_version,
                ),
            )
        )

    @field_validator("runtime_versions")
    @classmethod
    def _canonical_runtime_versions(
        cls,
        value: tuple[RuntimeVersionBinding, ...],
    ) -> tuple[RuntimeVersionBinding, ...]:
        packages = [row.package for row in value]
        if len(packages) != len(set(packages)):
            raise ValueError("runtime package bindings must be unique")
        if set(packages) != _REQUIRED_RUNTIME_PACKAGES:
            raise ValueError(
                "canary runtime bindings require the exact reviewed package set"
            )
        return tuple(sorted(value, key=lambda row: row.package))


class DeviceEvidence(_CanaryModel):
    """Requested, resolved, and observed device state for one pipeline stage."""

    requested: str = Field(min_length=1)
    resolved: str | None = Field(default=None, min_length=1)
    actual: str | None = Field(default=None, min_length=1)
    fallback_occurred: bool = False
    fallback_reason: str | None = Field(default=None, min_length=1)
    synchronized_before: bool | None = None
    synchronized_after: bool | None = None

    @model_validator(mode="after")
    def _fallback_is_structured(self) -> Self:
        if self.fallback_occurred and self.fallback_reason is None:
            raise ValueError("fallback_reason is required when fallback_occurred is true")
        if not self.fallback_occurred and self.fallback_reason is not None:
            raise ValueError("fallback_reason requires fallback_occurred=true")
        return self


class PipelineDeviceEvidence(_CanaryModel):
    """Independent device evidence for ASR, alignment, and diarization."""

    asr: DeviceEvidence
    alignment: DeviceEvidence
    diarization: DeviceEvidence


class CanaryTimingEvidence(_CanaryModel):
    """Comparable monotonic wall-clock durations retained for each run."""

    preparation_seconds: float = Field(ge=0.0)
    asr_seconds: float = Field(ge=0.0)
    alignment_seconds: float = Field(ge=0.0)
    diarization_seconds: float = Field(gt=0.0)
    total_seconds: float = Field(gt=0.0)

    @model_validator(mode="after")
    def _total_covers_each_stage(self) -> Self:
        largest_stage = max(
            self.preparation_seconds,
            self.asr_seconds,
            self.alignment_seconds,
            self.diarization_seconds,
        )
        if self.total_seconds + _COMPARISON_EPSILON < largest_stage:
            raise ValueError("total_seconds cannot be shorter than an individual stage")
        return self


class AlignmentQAEvidence(_CanaryModel):
    """Stable alignment metrics used by the promotion policy."""

    forced_word_coverage: float = Field(ge=0.0, le=1.0)
    estimated_word_coverage: float = Field(ge=0.0, le=1.0)
    missing_timestamp_count: int = Field(ge=0)
    nonmonotonic_timestamp_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _coverage_is_possible(self) -> Self:
        if (
            self.forced_word_coverage
            + self.estimated_word_coverage
            > 1.0 + _COMPARISON_EPSILON
        ):
            raise ValueError("forced and estimated word coverage cannot exceed 1")
        return self


class SpeakerQAEvidence(_CanaryModel):
    """Stable speaker metrics and severe defect identities for one run."""

    requested_speaker_count: int = Field(gt=0)
    detected_speaker_count: int = Field(ge=0)
    validated_speaker_count: int = Field(ge=0)
    uncertain_speaker_rate: float = Field(ge=0.0, le=1.0)
    unassigned_speaker_rate: float = Field(ge=0.0, le=1.0)
    speaker_flapping: float = Field(ge=0.0, le=1.0)
    critical_defects: tuple[str, ...] = ()
    major_defects: tuple[str, ...] = ()

    @field_validator("critical_defects", "major_defects")
    @classmethod
    def _canonical_defects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not defect.strip() for defect in value):
            raise ValueError("QA defect identifiers cannot be blank")
        if len(set(value)) != len(value):
            raise ValueError("QA defect identifiers must be unique within a severity")
        return tuple(sorted(value))


class CanaryRun(_CanaryModel):
    """One preserved cold, warmup, or measured run manifest."""

    schema_version: Literal["apple_silicon_canary_run.v1"] = (
        "apple_silicon_canary_run.v1"
    )
    run_id: str = Field(min_length=1)
    variant: CanaryVariant
    phase: CanaryRunPhase
    status: CanaryRunStatus
    binding: CanaryBinding
    source_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    processing_timestamp: datetime | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    artifacts: tuple[ArtifactHashBinding, ...] = ()
    devices: PipelineDeviceEvidence
    timings: CanaryTimingEvidence | None = None
    peak_memory_bytes: int | None = Field(default=None, gt=0)
    alignment_qa: AlignmentQAEvidence | None = None
    speaker_qa: SpeakerQAEvidence | None = None
    status_detail: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _completion_evidence_is_complete(self) -> Self:
        if self.status is CanaryRunStatus.COMPLETED:
            if self.status_detail is not None:
                raise ValueError("completed runs cannot have status_detail")
            required = {
                "timings": self.timings,
                "peak_memory_bytes": self.peak_memory_bytes,
                "alignment_qa": self.alignment_qa,
                "speaker_qa": self.speaker_qa,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError(
                    "completed runs require complete evidence: " + ", ".join(missing)
                )
            if self.processing_timestamp is None:
                raise ValueError("completed runs require a processing timestamp")
            artifact_names = {artifact.name for artifact in self.artifacts}
            if artifact_names != _REQUIRED_COMPLETED_ARTIFACTS:
                raise ValueError(
                    "completed runs require exact private artifact hash bindings"
                )
            if len(artifact_names) != len(self.artifacts):
                raise ValueError("completed run artifact names must be unique")
            for stage_name, device in (
                ("asr", self.devices.asr),
                ("alignment", self.devices.alignment),
                ("diarization", self.devices.diarization),
            ):
                if device.resolved is None or device.actual is None:
                    raise ValueError(
                        f"completed runs require resolved and actual {stage_name} devices"
                    )
        elif self.status_detail is None:
            raise ValueError("failed and inconclusive runs require status_detail")
        return self


class AppleSiliconCanaryEvidence(_CanaryModel):
    """All individual manifests for one CPU-baseline versus MPS-candidate canary."""

    schema_version: Literal["apple_silicon_canary_evidence.v1"] = (
        "apple_silicon_canary_evidence.v1"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    baseline_runs: tuple[CanaryRun, ...]
    candidate_runs: tuple[CanaryRun, ...]
    explicit_mps_override: bool = False

    @model_validator(mode="after")
    def _roles_and_ids_are_unambiguous(self) -> Self:
        if any(
            run.variant is not CanaryVariant.CPU_BASELINE for run in self.baseline_runs
        ):
            raise ValueError("baseline_runs may contain only cpu_baseline runs")
        if any(
            run.variant is not CanaryVariant.MPS_CANDIDATE
            for run in self.candidate_runs
        ):
            raise ValueError("candidate_runs may contain only mps_candidate runs")
        run_ids = [run.run_id for run in (*self.baseline_runs, *self.candidate_runs)]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("run_id values must be unique across the canary")
        return self


class CanaryGatePolicy(_CanaryModel):
    """Configurable conservative tolerances for automatic MPS promotion."""

    minimum_measured_runs: int = Field(default=3, ge=3)
    minimum_warmup_runs: int = Field(default=1, ge=0)
    minimum_diarization_speedup_ratio: float = Field(default=0.10, ge=0.0, lt=1.0)
    maximum_total_time_regression_ratio: float = Field(default=0.05, ge=0.0)
    maximum_missing_timestamp_increase: float = Field(default=0.0, ge=0.0)
    maximum_nonmonotonic_timestamp_increase: float = Field(default=0.0, ge=0.0)
    maximum_forced_coverage_regression: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_estimated_coverage_increase: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_uncertain_speaker_rate_increase: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
    )
    maximum_unassigned_speaker_rate_increase: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
    )
    maximum_speaker_flapping_increase: float = Field(default=0.0, ge=0.0, le=1.0)
    speaker_count_tolerance: int = Field(default=0, ge=0)


class CanaryMetricMedians(_CanaryModel):
    """Medians calculated only from successful measured-warm runs."""

    preparation_seconds: float
    asr_seconds: float
    alignment_seconds: float
    diarization_seconds: float
    total_seconds: float
    peak_memory_bytes: float
    forced_word_coverage: float
    estimated_word_coverage: float
    missing_timestamp_count: float
    nonmonotonic_timestamp_count: float
    uncertain_speaker_rate: float
    unassigned_speaker_rate: float
    speaker_flapping: float
    requested_speaker_count: float
    detected_speaker_count: float
    validated_speaker_count: float
    critical_defect_count: float
    major_defect_count: float


class CanaryAggregate(_CanaryModel):
    """Audit-friendly aggregation that explicitly identifies excluded runs."""

    measured_run_ids: tuple[str, ...]
    warmup_run_ids: tuple[str, ...]
    cold_start_run_ids: tuple[str, ...]
    excluded_run_ids: tuple[str, ...]
    failed_run_ids: tuple[str, ...]
    inconclusive_run_ids: tuple[str, ...]
    medians: CanaryMetricMedians | None
    critical_defects: tuple[str, ...]
    major_defects: tuple[str, ...]


class CanaryDecision(_CanaryModel):
    """Complete, serializable result of the pure evidence-gate evaluation."""

    schema_version: Literal["apple_silicon_canary_decision.v1"] = (
        "apple_silicon_canary_decision.v1"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    outcome: CanaryOutcome
    automatic_promotion_eligible: bool
    reasons: tuple[str, ...]
    diarization_speedup_ratio: float | None
    total_time_regression_ratio: float | None
    baseline: CanaryAggregate
    candidate: CanaryAggregate
    policy: CanaryGatePolicy
    evidence: AppleSiliconCanaryEvidence


def aggregate_canary_runs(runs: tuple[CanaryRun, ...]) -> CanaryAggregate:
    """Aggregate successful measured-warm runs without dropping any run identity."""

    measured = tuple(
        run
        for run in runs
        if run.phase is CanaryRunPhase.MEASURED_WARM
        and run.status is CanaryRunStatus.COMPLETED
    )
    warmups = tuple(run.run_id for run in runs if run.phase is CanaryRunPhase.WARMUP)
    cold_starts = tuple(
        run.run_id for run in runs if run.phase is CanaryRunPhase.COLD_START
    )
    excluded = tuple(
        run.run_id
        for run in runs
        if run.phase in {CanaryRunPhase.WARMUP, CanaryRunPhase.COLD_START}
    )
    failed = tuple(
        run.run_id for run in runs if run.status is CanaryRunStatus.FAILED
    )
    inconclusive = tuple(
        run.run_id for run in runs if run.status is CanaryRunStatus.INCONCLUSIVE
    )

    critical_defects = tuple(
        sorted(
            {
                defect
                for run in measured
                for defect in _required_speaker_qa(run).critical_defects
            }
        )
    )
    major_defects = tuple(
        sorted(
            {
                defect
                for run in measured
                for defect in _required_speaker_qa(run).major_defects
            }
        )
    )
    medians = _metric_medians(measured) if measured else None
    return CanaryAggregate(
        measured_run_ids=tuple(run.run_id for run in measured),
        warmup_run_ids=warmups,
        cold_start_run_ids=cold_starts,
        excluded_run_ids=excluded,
        failed_run_ids=failed,
        inconclusive_run_ids=inconclusive,
        medians=medians,
        critical_defects=critical_defects,
        major_defects=major_defects,
    )


def evaluate_mps_diarization_gate(
    evidence: AppleSiliconCanaryEvidence,
    policy: CanaryGatePolicy | None = None,
) -> CanaryDecision:
    """Evaluate a CPU-versus-MPS diarization comparison without side effects."""

    active_policy = policy or CanaryGatePolicy()
    baseline = aggregate_canary_runs(evidence.baseline_runs)
    candidate = aggregate_canary_runs(evidence.candidate_runs)
    speedup = _relative_improvement(
        _median_value(baseline, "diarization_seconds"),
        _median_value(candidate, "diarization_seconds"),
    )
    total_regression = _relative_regression(
        _median_value(baseline, "total_seconds"),
        _median_value(candidate, "total_seconds"),
    )

    hard_outcome, hard_reasons = _hard_failure_outcome(evidence)
    if hard_outcome is not None:
        return _decision(
            hard_outcome,
            hard_reasons,
            False,
            speedup,
            total_regression,
            baseline,
            candidate,
            active_policy,
            evidence,
        )

    automatic_outcome, reasons = _automatic_gate_outcome(
        evidence,
        baseline,
        candidate,
        active_policy,
        speedup,
        total_regression,
    )
    eligible = automatic_outcome is CanaryOutcome.MPS_PROMOTED
    overrideable_outcomes = {
        CanaryOutcome.MPS_PROMOTED,
        CanaryOutcome.CPU_RETAINED,
        CanaryOutcome.QA_REGRESSION,
    }
    if (
        evidence.explicit_mps_override
        and automatic_outcome in overrideable_outcomes
        and _has_observed_mps_candidate(evidence)
    ):
        reasons = (
            *reasons,
            f"explicit_mps_override_recorded:automatic_outcome={automatic_outcome.value}",
        )
        return _decision(
            CanaryOutcome.MPS_EXPLICIT_OVERRIDE,
            reasons,
            False,
            speedup,
            total_regression,
            baseline,
            candidate,
            active_policy,
            evidence,
        )
    return _decision(
        automatic_outcome,
        reasons,
        eligible,
        speedup,
        total_regression,
        baseline,
        candidate,
        active_policy,
        evidence,
    )


def _hard_failure_outcome(
    evidence: AppleSiliconCanaryEvidence,
) -> tuple[CanaryOutcome | None, tuple[str, ...]]:
    alignment_violations = [
        run.run_id
        for run in (*evidence.baseline_runs, *evidence.candidate_runs)
        if not _device_fixed_to(run.devices.alignment, "cpu")
    ]
    if alignment_violations:
        return (
            CanaryOutcome.INSUFFICIENT_EVIDENCE,
            tuple(f"alignment_not_fixed_to_cpu:{run_id}" for run_id in alignment_violations),
        )

    failed_baselines = [
        run.run_id
        for run in evidence.baseline_runs
        if run.status is CanaryRunStatus.FAILED
    ]
    if failed_baselines:
        return (
            CanaryOutcome.BENCHMARK_INCONCLUSIVE,
            tuple(f"baseline_run_failed:{run_id}" for run_id in failed_baselines),
        )

    fallback_runs = [
        run.run_id
        for run in evidence.candidate_runs
        if run.devices.diarization.fallback_occurred
        or (
            run.devices.diarization.requested == "mps"
            and run.devices.diarization.actual == "cpu"
        )
    ]
    if fallback_runs:
        return (
            CanaryOutcome.MPS_FELL_BACK_TO_CPU,
            tuple(f"candidate_diarization_fallback:{run_id}" for run_id in fallback_runs),
        )

    failed_candidates = [
        run.run_id
        for run in evidence.candidate_runs
        if run.status is CanaryRunStatus.FAILED
    ]
    if failed_candidates:
        return (
            CanaryOutcome.MPS_FAILED,
            tuple(f"candidate_run_failed:{run_id}" for run_id in failed_candidates),
        )
    return None, ()


def _automatic_gate_outcome(
    evidence: AppleSiliconCanaryEvidence,
    baseline: CanaryAggregate,
    candidate: CanaryAggregate,
    policy: CanaryGatePolicy,
    speedup: float | None,
    total_regression: float | None,
) -> tuple[CanaryOutcome, tuple[str, ...]]:
    inconclusive_runs = [
        run.run_id
        for run in (*evidence.baseline_runs, *evidence.candidate_runs)
        if run.status is CanaryRunStatus.INCONCLUSIVE
    ]
    failed_baselines = [
        run.run_id
        for run in evidence.baseline_runs
        if run.status is CanaryRunStatus.FAILED
    ]
    if inconclusive_runs or failed_baselines:
        reasons = [
            *(f"run_inconclusive:{run_id}" for run_id in inconclusive_runs),
            *(f"baseline_run_failed:{run_id}" for run_id in failed_baselines),
        ]
        return CanaryOutcome.BENCHMARK_INCONCLUSIVE, tuple(reasons)

    evidence_reasons = _insufficient_evidence_reasons(
        evidence,
        baseline,
        candidate,
        policy,
    )
    if evidence_reasons:
        return CanaryOutcome.INSUFFICIENT_EVIDENCE, evidence_reasons

    baseline_medians = _required_medians(baseline)
    candidate_medians = _required_medians(candidate)
    qa_reasons = _qa_regression_reasons(
        evidence,
        baseline,
        candidate,
        baseline_medians,
        candidate_medians,
        policy,
    )
    if qa_reasons:
        return CanaryOutcome.QA_REGRESSION, qa_reasons

    performance_reasons: list[str] = []
    baseline_measured = _completed_measured_runs(evidence.baseline_runs)
    candidate_measured = _completed_measured_runs(evidence.candidate_runs)
    if baseline_measured:
        baseline_total_ceiling = max(
            _required_timings(run).total_seconds for run in baseline_measured
        ) * (1.0 + policy.maximum_total_time_regression_ratio)
        for run in candidate_measured:
            candidate_total = _required_timings(run).total_seconds
            if _strictly_greater(candidate_total, baseline_total_ceiling):
                performance_reasons.append(
                    f"candidate_total_time_unstable:{run.run_id}:"
                    f"actual={candidate_total:.6f},"
                    f"allowed={baseline_total_ceiling:.6f}"
                )
    if speedup is None or _strictly_less(
        speedup,
        policy.minimum_diarization_speedup_ratio,
    ):
        performance_reasons.append(
            "diarization_speedup_below_threshold:"
            f"actual={_format_optional(speedup)},"
            f"required={policy.minimum_diarization_speedup_ratio:.6f}"
        )
    if total_regression is None or _strictly_greater(
        total_regression,
        policy.maximum_total_time_regression_ratio,
    ):
        performance_reasons.append(
            "total_time_regression_exceeded:"
            f"actual={_format_optional(total_regression)},"
            f"allowed={policy.maximum_total_time_regression_ratio:.6f}"
        )
    if performance_reasons:
        return CanaryOutcome.CPU_RETAINED, tuple(performance_reasons)

    return (
        CanaryOutcome.MPS_PROMOTED,
        (
            "same_bound_evidence",
            "alignment_fixed_to_cpu",
            "candidate_actual_device_mps_without_fallback",
            "minimum_measured_runs_satisfied",
            "diarization_speedup_threshold_satisfied",
            "end_to_end_and_qa_tolerances_satisfied",
        ),
    )


def _insufficient_evidence_reasons(
    evidence: AppleSiliconCanaryEvidence,
    baseline: CanaryAggregate,
    candidate: CanaryAggregate,
    policy: CanaryGatePolicy,
) -> tuple[str, ...]:
    reasons: list[str] = []
    all_runs = (*evidence.baseline_runs, *evidence.candidate_runs)
    if not evidence.baseline_runs:
        reasons.append("missing_cpu_baseline_runs")
    if not evidence.candidate_runs:
        reasons.append("missing_mps_candidate_runs")
    if all_runs:
        reference_binding = all_runs[0].binding
        reasons.extend(
            f"comparison_binding_mismatch:{run.run_id}"
            for run in all_runs[1:]
            if run.binding != reference_binding
        )

    for name, runs in (
        ("baseline", evidence.baseline_runs),
        ("candidate", evidence.candidate_runs),
    ):
        source_hashes = {run.source_config_sha256 for run in runs}
        if len(source_hashes) > 1:
            reasons.append(f"{name}_source_config_changed_between_runs")

    if len(baseline.measured_run_ids) < policy.minimum_measured_runs:
        reasons.append(
            "insufficient_baseline_measured_runs:"
            f"{len(baseline.measured_run_ids)}<{policy.minimum_measured_runs}"
        )
    if len(candidate.measured_run_ids) < policy.minimum_measured_runs:
        reasons.append(
            "insufficient_candidate_measured_runs:"
            f"{len(candidate.measured_run_ids)}<{policy.minimum_measured_runs}"
        )
    baseline_warmups = _completed_warmup_count(evidence.baseline_runs)
    candidate_warmups = _completed_warmup_count(evidence.candidate_runs)
    if baseline_warmups < policy.minimum_warmup_runs:
        reasons.append(
            f"insufficient_baseline_warmups:{baseline_warmups}<{policy.minimum_warmup_runs}"
        )
    if candidate_warmups < policy.minimum_warmup_runs:
        reasons.append(
            f"insufficient_candidate_warmups:{candidate_warmups}<{policy.minimum_warmup_runs}"
        )

    completed_baselines = _completed_runs(evidence.baseline_runs)
    completed_candidates = _completed_runs(evidence.candidate_runs)
    reasons.extend(
        _stage_device_reasons(
            completed_baselines,
            expected_diarization="cpu",
            label="baseline",
        )
    )
    reasons.extend(
        _stage_device_reasons(
            completed_candidates,
            expected_diarization="mps",
            label="candidate",
        )
    )
    completed_runs = (*completed_baselines, *completed_candidates)
    if completed_runs:
        asr_devices = {
            (
                run.devices.asr.requested,
                run.devices.asr.resolved,
                run.devices.asr.actual,
            )
            for run in completed_runs
        }
        if len(asr_devices) > 1:
            reasons.append("asr_device_changed_across_variants")
    return tuple(reasons)


def _stage_device_reasons(
    runs: tuple[CanaryRun, ...],
    *,
    expected_diarization: Literal["cpu", "mps"],
    label: str,
) -> list[str]:
    reasons: list[str] = []
    for run in runs:
        if not _device_fixed_to(run.devices.diarization, expected_diarization):
            reasons.append(
                f"{label}_diarization_not_{expected_diarization}:{run.run_id}"
            )
        if run.devices.asr.fallback_occurred:
            reasons.append(f"{label}_asr_fallback:{run.run_id}")
        if (
            run.devices.asr.resolved != "mlx"
            or run.devices.asr.actual != "mlx:gpu"
        ):
            reasons.append(f"{label}_asr_not_verified_mlx_gpu:{run.run_id}")
        if expected_diarization == "mps" and (
            run.devices.diarization.synchronized_before is not True
            or run.devices.diarization.synchronized_after is not True
        ):
            reasons.append(f"{label}_diarization_not_synchronized:{run.run_id}")
    if runs:
        asr_devices = {
            (
                run.devices.asr.requested,
                run.devices.asr.resolved,
                run.devices.asr.actual,
            )
            for run in runs
        }
        if len(asr_devices) > 1:
            reasons.append(f"{label}_asr_device_changed_between_runs")
    return reasons


def _qa_regression_reasons(
    evidence: AppleSiliconCanaryEvidence,
    baseline: CanaryAggregate,
    candidate: CanaryAggregate,
    baseline_medians: CanaryMetricMedians,
    candidate_medians: CanaryMetricMedians,
    policy: CanaryGatePolicy,
) -> tuple[str, ...]:
    reasons: list[str] = []

    _append_if_above(
        reasons,
        "missing_timestamps_regressed",
        candidate_medians.missing_timestamp_count,
        baseline_medians.missing_timestamp_count
        + policy.maximum_missing_timestamp_increase,
    )
    _append_if_above(
        reasons,
        "nonmonotonic_timestamps_regressed",
        candidate_medians.nonmonotonic_timestamp_count,
        baseline_medians.nonmonotonic_timestamp_count
        + policy.maximum_nonmonotonic_timestamp_increase,
    )
    _append_if_below(
        reasons,
        "forced_alignment_coverage_regressed",
        candidate_medians.forced_word_coverage,
        baseline_medians.forced_word_coverage
        - policy.maximum_forced_coverage_regression,
    )
    _append_if_above(
        reasons,
        "estimated_alignment_coverage_regressed",
        candidate_medians.estimated_word_coverage,
        baseline_medians.estimated_word_coverage
        + policy.maximum_estimated_coverage_increase,
    )
    _append_if_above(
        reasons,
        "uncertain_speaker_rate_regressed",
        candidate_medians.uncertain_speaker_rate,
        baseline_medians.uncertain_speaker_rate
        + policy.maximum_uncertain_speaker_rate_increase,
    )
    _append_if_above(
        reasons,
        "unassigned_speaker_rate_regressed",
        candidate_medians.unassigned_speaker_rate,
        baseline_medians.unassigned_speaker_rate
        + policy.maximum_unassigned_speaker_rate_increase,
    )
    _append_if_above(
        reasons,
        "speaker_flapping_regressed",
        candidate_medians.speaker_flapping,
        baseline_medians.speaker_flapping
        + policy.maximum_speaker_flapping_increase,
    )

    baseline_measured = _completed_measured_runs(evidence.baseline_runs)
    candidate_measured = _completed_measured_runs(evidence.candidate_runs)
    baseline_alignment = tuple(
        _required_alignment_qa(run) for run in baseline_measured
    )
    baseline_speaker = tuple(_required_speaker_qa(run) for run in baseline_measured)
    if baseline_alignment and baseline_speaker:
        for run in candidate_measured:
            alignment_qa = _required_alignment_qa(run)
            speaker_qa = _required_speaker_qa(run)
            per_run_checks = (
                (
                    "missing_timestamps_regressed",
                    alignment_qa.missing_timestamp_count,
                    max(item.missing_timestamp_count for item in baseline_alignment)
                    + policy.maximum_missing_timestamp_increase,
                    "above",
                ),
                (
                    "nonmonotonic_timestamps_regressed",
                    alignment_qa.nonmonotonic_timestamp_count,
                    max(
                        item.nonmonotonic_timestamp_count
                        for item in baseline_alignment
                    )
                    + policy.maximum_nonmonotonic_timestamp_increase,
                    "above",
                ),
                (
                    "forced_alignment_coverage_regressed",
                    alignment_qa.forced_word_coverage,
                    min(item.forced_word_coverage for item in baseline_alignment)
                    - policy.maximum_forced_coverage_regression,
                    "below",
                ),
                (
                    "estimated_alignment_coverage_regressed",
                    alignment_qa.estimated_word_coverage,
                    max(item.estimated_word_coverage for item in baseline_alignment)
                    + policy.maximum_estimated_coverage_increase,
                    "above",
                ),
                (
                    "uncertain_speaker_rate_regressed",
                    speaker_qa.uncertain_speaker_rate,
                    max(item.uncertain_speaker_rate for item in baseline_speaker)
                    + policy.maximum_uncertain_speaker_rate_increase,
                    "above",
                ),
                (
                    "unassigned_speaker_rate_regressed",
                    speaker_qa.unassigned_speaker_rate,
                    max(item.unassigned_speaker_rate for item in baseline_speaker)
                    + policy.maximum_unassigned_speaker_rate_increase,
                    "above",
                ),
                (
                    "speaker_flapping_regressed",
                    speaker_qa.speaker_flapping,
                    max(item.speaker_flapping for item in baseline_speaker)
                    + policy.maximum_speaker_flapping_increase,
                    "above",
                ),
            )
            for code, actual, limit, direction in per_run_checks:
                failed = (
                    _strictly_greater(float(actual), float(limit))
                    if direction == "above"
                    else _strictly_less(float(actual), float(limit))
                )
                if failed:
                    reasons.append(
                        f"{code}:{run.run_id}:actual={float(actual):.6f},"
                        f"allowed={float(limit):.6f}"
                    )

    for run in _completed_measured_runs(
        (*evidence.baseline_runs, *evidence.candidate_runs)
    ):
        speaker_qa = _required_speaker_qa(run)
        detected_delta = abs(
            speaker_qa.detected_speaker_count - speaker_qa.requested_speaker_count
        )
        validated_delta = abs(
            speaker_qa.validated_speaker_count - speaker_qa.requested_speaker_count
        )
        if detected_delta > policy.speaker_count_tolerance:
            reasons.append(f"detected_speaker_count_unacceptable:{run.run_id}")
        if validated_delta > policy.speaker_count_tolerance:
            reasons.append(f"validated_speaker_count_unacceptable:{run.run_id}")

    new_critical = sorted(set(candidate.critical_defects) - set(baseline.critical_defects))
    new_major = sorted(set(candidate.major_defects) - set(baseline.major_defects))
    reasons.extend(f"new_critical_qa_defect:{defect}" for defect in new_critical)
    reasons.extend(f"new_major_qa_defect:{defect}" for defect in new_major)
    return tuple(reasons)


def _metric_medians(runs: tuple[CanaryRun, ...]) -> CanaryMetricMedians:
    timings = tuple(_required_timings(run) for run in runs)
    alignment = tuple(_required_alignment_qa(run) for run in runs)
    speaker = tuple(_required_speaker_qa(run) for run in runs)
    return CanaryMetricMedians(
        preparation_seconds=_median(item.preparation_seconds for item in timings),
        asr_seconds=_median(item.asr_seconds for item in timings),
        alignment_seconds=_median(item.alignment_seconds for item in timings),
        diarization_seconds=_median(item.diarization_seconds for item in timings),
        total_seconds=_median(item.total_seconds for item in timings),
        peak_memory_bytes=_median(_required_peak_memory(run) for run in runs),
        forced_word_coverage=_median(item.forced_word_coverage for item in alignment),
        estimated_word_coverage=_median(item.estimated_word_coverage for item in alignment),
        missing_timestamp_count=_median(item.missing_timestamp_count for item in alignment),
        nonmonotonic_timestamp_count=_median(
            item.nonmonotonic_timestamp_count for item in alignment
        ),
        uncertain_speaker_rate=_median(item.uncertain_speaker_rate for item in speaker),
        unassigned_speaker_rate=_median(item.unassigned_speaker_rate for item in speaker),
        speaker_flapping=_median(item.speaker_flapping for item in speaker),
        requested_speaker_count=_median(item.requested_speaker_count for item in speaker),
        detected_speaker_count=_median(item.detected_speaker_count for item in speaker),
        validated_speaker_count=_median(item.validated_speaker_count for item in speaker),
        critical_defect_count=_median(len(item.critical_defects) for item in speaker),
        major_defect_count=_median(len(item.major_defects) for item in speaker),
    )


def _required_timings(run: CanaryRun) -> CanaryTimingEvidence:
    if run.timings is None:
        raise ValueError(f"completed measured run lacks timings: {run.run_id}")
    return run.timings


def _required_alignment_qa(run: CanaryRun) -> AlignmentQAEvidence:
    if run.alignment_qa is None:
        raise ValueError(f"completed measured run lacks alignment QA: {run.run_id}")
    return run.alignment_qa


def _required_speaker_qa(run: CanaryRun) -> SpeakerQAEvidence:
    if run.speaker_qa is None:
        raise ValueError(f"completed measured run lacks speaker QA: {run.run_id}")
    return run.speaker_qa


def _required_peak_memory(run: CanaryRun) -> int:
    if run.peak_memory_bytes is None:
        raise ValueError(f"completed measured run lacks peak memory: {run.run_id}")
    return run.peak_memory_bytes


def _required_medians(aggregate: CanaryAggregate) -> CanaryMetricMedians:
    if aggregate.medians is None:
        raise ValueError("evidence checks should reject an aggregate without measured medians")
    return aggregate.medians


def _median(values: Iterable[float | int]) -> float:
    return float(median(values))


def _median_value(
    aggregate: CanaryAggregate,
    field: Literal["diarization_seconds", "total_seconds"],
) -> float | None:
    if aggregate.medians is None:
        return None
    return float(getattr(aggregate.medians, field))


def _relative_improvement(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline <= 0.0:
        return None
    return (baseline - candidate) / baseline


def _relative_regression(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline <= 0.0:
        return None
    return (candidate - baseline) / baseline


def _completed_runs(runs: tuple[CanaryRun, ...]) -> tuple[CanaryRun, ...]:
    return tuple(run for run in runs if run.status is CanaryRunStatus.COMPLETED)


def _completed_measured_runs(runs: tuple[CanaryRun, ...]) -> tuple[CanaryRun, ...]:
    return tuple(
        run
        for run in runs
        if run.status is CanaryRunStatus.COMPLETED
        and run.phase is CanaryRunPhase.MEASURED_WARM
    )


def _completed_warmup_count(runs: tuple[CanaryRun, ...]) -> int:
    return sum(
        run.status is CanaryRunStatus.COMPLETED
        and run.phase is CanaryRunPhase.WARMUP
        for run in runs
    )


def _device_fixed_to(device: DeviceEvidence, expected: str) -> bool:
    observed = (device.requested, device.resolved, device.actual)
    return all(value is None or value == expected for value in observed)


def _has_observed_mps_candidate(evidence: AppleSiliconCanaryEvidence) -> bool:
    return bool(evidence.candidate_runs) and all(
        run.status is CanaryRunStatus.COMPLETED
        and run.devices.diarization.actual == "mps"
        and not run.devices.diarization.fallback_occurred
        and run.devices.diarization.synchronized_before is True
        and run.devices.diarization.synchronized_after is True
        for run in evidence.candidate_runs
    )


def _append_if_above(
    reasons: list[str],
    code: str,
    actual: float,
    maximum: float,
) -> None:
    if _strictly_greater(actual, maximum):
        reasons.append(f"{code}:actual={actual:.6f},allowed={maximum:.6f}")


def _append_if_below(
    reasons: list[str],
    code: str,
    actual: float,
    minimum: float,
) -> None:
    if _strictly_less(actual, minimum):
        reasons.append(f"{code}:actual={actual:.6f},required={minimum:.6f}")


def _strictly_greater(left: float, right: float) -> bool:
    return left > right and not math.isclose(
        left,
        right,
        rel_tol=1e-12,
        abs_tol=_COMPARISON_EPSILON,
    )


def _strictly_less(left: float, right: float) -> bool:
    return left < right and not math.isclose(
        left,
        right,
        rel_tol=1e-12,
        abs_tol=_COMPARISON_EPSILON,
    )


def _format_optional(value: float | None) -> str:
    return "missing" if value is None else f"{value:.6f}"


def _decision(
    outcome: CanaryOutcome,
    reasons: tuple[str, ...],
    eligible: bool,
    speedup: float | None,
    total_regression: float | None,
    baseline: CanaryAggregate,
    candidate: CanaryAggregate,
    policy: CanaryGatePolicy,
    evidence: AppleSiliconCanaryEvidence,
) -> CanaryDecision:
    return CanaryDecision(
        outcome=outcome,
        automatic_promotion_eligible=eligible,
        reasons=reasons,
        diarization_speedup_ratio=speedup,
        total_time_regression_ratio=total_regression,
        baseline=baseline,
        candidate=candidate,
        policy=policy,
        evidence=evidence,
    )
