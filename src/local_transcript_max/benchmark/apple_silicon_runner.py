# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Executable, local-only Apple Silicon CPU-versus-MPS canary harness.

The runner controls one experimental dimension: pyannote diarization uses CPU
for the baseline and MPS for the candidate.  MLX Whisper FP16 ASR, WhisperX
forced alignment on CPU, input media, model revisions, speaker constraints,
code, and host bindings are otherwise identical.

Canary summary and decision artifacts intentionally contain hashes, counts,
timings, device evidence, and defect identifiers without transcript text. Each
nested pipeline run retains its normal private transcript, QA, and debug
artifacts and must remain local.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from local_transcript_max.code_provenance import current_code_revision
from local_transcript_max.config import PipelineConfig, resolve_model_entry
from local_transcript_max.configuration_evidence import (
    SOURCE_CONFIG_SHA256_SCOPE,
    SOURCE_CONFIGURATION_SCHEMA,
    configuration_evidence,
)
from local_transcript_max.errors import LTMError
from local_transcript_max.model_lifecycle import release_runtime_models
from local_transcript_max.models import model_checkpoint_sha256
from local_transcript_max.platform_security import ensure_private_directory
from local_transcript_max.runtime_policy import (
    HostCapabilities,
    collect_host_capabilities,
)
from local_transcript_max.schemas import CompletionTimingReport
from local_transcript_max.utils import read_json, sha256_file, write_json

from .apple_silicon_canary import (
    AlignmentQAEvidence,
    AppleSiliconCanaryEvidence,
    ArtifactHashBinding,
    CanaryBinding,
    CanaryDecision,
    CanaryGatePolicy,
    CanaryRun,
    CanaryRunPhase,
    CanaryRunStatus,
    CanaryTimingEvidence,
    CanaryVariant,
    DeviceEvidence,
    ModelVersionBinding,
    PipelineDeviceEvidence,
    RuntimeVersionBinding,
    SpeakerQAEvidence,
    evaluate_mps_diarization_gate,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
_OFFLINE_ENVIRONMENT = {
    "LTM_OFFLINE": "1",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "PYANNOTE_METRICS_ENABLED": "0",
    "DO_NOT_TRACK": "1",
}
_PREPARATION_TIMINGS = ("preflight", "media_probe", "audio_preprocessing", "vad")
_SAFE_FALSE_ENV_VALUES = {"", "0", "false", "no", "off"}
_CANARY_RUNTIME_PACKAGES = (
    "mlx",
    "mlx-metal",
    "mlx-whisper",
    "torch",
    "torchaudio",
    "whisperx",
    "pyannote.audio",
)
_CANARY_MPS_EXPERIMENT_ENVIRONMENT = (
    "PYTORCH_DEBUG_MPS_ALLOCATOR",
    "PYTORCH_MPS_LOG_PROFILE_INFO",
    "PYTORCH_MPS_TRACE_SIGNPOSTS",
    "PYTORCH_MPS_HIGH_WATERMARK_RATIO",
    "PYTORCH_MPS_LOW_WATERMARK_RATIO",
    "PYTORCH_MPS_FAST_MATH",
    "PYTORCH_MPS_PREFER_METAL",
)
_CANARY_THREAD_ENVIRONMENT = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "OPENBLAS_NUM_THREADS",
)


class _RunnerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AppleSiliconCanaryOptions(_RunnerModel):
    """Execution controls and conservative promotion policy."""

    warmup_runs: int = Field(default=1, ge=1)
    measured_runs: int = Field(default=3, ge=3)
    alignment_model: str = Field(default="en", min_length=1)
    explicit_mps_override: bool = False
    dry_run: bool = False
    policy: CanaryGatePolicy = Field(default_factory=CanaryGatePolicy)


class AppleSiliconCanaryRequest(_RunnerModel):
    """Public request model suitable for CLI construction."""

    input_file: Path
    output_dir: Path
    config: PipelineConfig
    speaker_count: int = Field(gt=0, le=128)
    roster_path: Path | None = None
    options: AppleSiliconCanaryOptions = Field(
        default_factory=AppleSiliconCanaryOptions
    )


class PlannedModelBinding(_RunnerModel):
    """Model identity available without loading a package or model."""

    role: str
    model_id: str
    revision: str | None = None
    revision_verified: bool = True
    checkpoint_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    backend: str
    backend_version: str | None = None


class PlannedRuntimeVersion(_RunnerModel):
    package: str
    version: str | None = None


class CanaryVariantPlan(_RunnerModel):
    variant: CanaryVariant
    diarization_device: Literal["cpu", "mps"]
    source_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    run_directories: tuple[str, ...]


class AppleSiliconCanaryPlan(_RunnerModel):
    """Deterministic plan emitted before execution and returned by dry-run."""

    schema_version: Literal["apple_silicon_canary_plan.v1"] = (
        "apple_silicon_canary_plan.v1"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    execution_required: bool
    input_media_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    comparison_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    speaker_constraints_sha256: str = Field(pattern=_SHA256_PATTERN)
    machine_fingerprint_sha256: str = Field(pattern=_SHA256_PATTERN)
    runtime_environment_sha256: str = Field(pattern=_SHA256_PATTERN)
    code_revision: str
    pytorch_enable_mps_fallback: Literal["unset", "disabled"]
    initial_host_condition: dict[str, Any]
    models: tuple[PlannedModelBinding, ...]
    runtime_versions: tuple[PlannedRuntimeVersion, ...]
    cold_start_runs: Literal[1] = 1
    warmup_runs: int
    measured_runs: int
    variants: tuple[CanaryVariantPlan, CanaryVariantPlan]
    invariants: tuple[str, ...]


class AppleSiliconRunnerRunManifest(_RunnerModel):
    """Individual run plus runner-only bindings not present in the pure gate."""

    schema_version: Literal["apple_silicon_canary_runner_run.v1"] = (
        "apple_silicon_canary_runner_run.v1"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run: CanaryRun
    pytorch_enable_mps_fallback: Literal["unset", "disabled"]
    host_condition_before: dict[str, Any]
    host_condition_after: dict[str, Any]
    pyannote_component_devices: dict[str, Any] | list[Any] | None = None


class AppleSiliconCanaryResult(_RunnerModel):
    """Dry-run plan or completed evidence and decision."""

    schema_version: Literal["apple_silicon_canary_result.v1"] = (
        "apple_silicon_canary_result.v1"
    )
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: Literal["planned", "completed"]
    plan: AppleSiliconCanaryPlan
    evidence: AppleSiliconCanaryEvidence | None = None
    decision: CanaryDecision | None = None


@dataclass(frozen=True)
class CanaryRuntimeBackends:
    """Backend instances reused within one variant to make warm runs meaningful."""

    asr: object
    alignment: object
    diarization: object


class TranscribeCallable(Protocol):
    def __call__(
        self,
        input_file: Path,
        out_dir: Path,
        config: PipelineConfig,
        **kwargs: Any,
    ) -> object: ...


class SpeakerValidationCallable(Protocol):
    def __call__(self, job_dir: Path, expected_speakers: int) -> object: ...


BackendFactory = Callable[[CanaryVariant], CanaryRuntimeBackends]
VersionResolver = Callable[[str], str | None]
HostConditionProbe = Callable[[], Mapping[str, Any]]


def run_apple_silicon_canary(
    request: AppleSiliconCanaryRequest,
    *,
    transcribe: TranscribeCallable | None = None,
    speaker_validator: SpeakerValidationCallable | None = None,
    backend_factory: BackendFactory | None = None,
    capabilities: HostCapabilities | None = None,
    version_resolver: VersionResolver | None = None,
    code_revision: str | None = None,
    condition_probe: HostConditionProbe | None = None,
) -> AppleSiliconCanaryResult:
    """Plan or execute a bound, offline Apple Silicon diarization canary.

    Execution never downloads models.  The normal pipeline preflight must find
    every package and exact local cache before inference begins.
    """

    host = capabilities or collect_host_capabilities()
    _validate_host(host)
    fallback_environment = _mps_fallback_environment()
    if fallback_environment == "enabled":
        raise LTMError(
            "Apple Silicon canary evidence is invalid while "
            "PYTORCH_ENABLE_MPS_FALLBACK is enabled; unset it or set it to 0."
        )
    canonical_environment = _canonical_environment_snapshot()
    _validate_canonical_environment(canonical_environment)
    runtime_environment_sha256 = _hash_json(canonical_environment)

    resolved_revision = code_revision or current_code_revision()
    probe = condition_probe or _default_host_condition_probe
    initial_host_condition = _host_condition(probe)
    baseline_config, candidate_config = _canary_configs(request)
    comparison_hash = _comparison_config_hash(baseline_config, candidate_config)
    speaker_hash = _speaker_constraints_hash(
        request.speaker_count,
        request.roster_path,
        require_file=not request.options.dry_run,
    )
    machine_hash = _machine_fingerprint_hash(
        host,
        fallback_environment,
        initial_host_condition,
    )
    resolver = version_resolver or _installed_version
    planned_models = _planned_models(
        baseline_config,
        request.options.alignment_model,
        resolver=resolver,
        require_versions=not request.options.dry_run,
    )
    planned_runtime_versions = _planned_runtime_versions(
        resolver,
        require_versions=not request.options.dry_run,
    )
    input_hash: str | None = None
    if not request.options.dry_run:
        input_path = _validated_input(request.input_file)
        input_hash = sha256_file(input_path)

    plan = _build_plan(
        request,
        baseline_config,
        candidate_config,
        comparison_hash=comparison_hash,
        speaker_hash=speaker_hash,
        machine_hash=machine_hash,
        runtime_environment_sha256=runtime_environment_sha256,
        code_revision=resolved_revision,
        fallback_environment=fallback_environment,
        initial_host_condition=initial_host_condition,
        planned_models=planned_models,
        planned_runtime_versions=planned_runtime_versions,
        input_hash=input_hash,
    )
    if request.options.dry_run:
        return AppleSiliconCanaryResult(status="planned", plan=plan)
    if input_hash is None:  # pragma: no cover - narrowed by the dry-run branch
        raise LTMError("Apple Silicon canary input binding was not created.")
    execution_input = _validated_input(request.input_file)

    output_dir = _prepare_output_root(request.output_dir)
    write_json(output_dir / "canary_plan.json", plan.model_dump(mode="json"))
    model_bindings = tuple(
        ModelVersionBinding.model_validate(model.model_dump())
        for model in planned_models
    )
    runtime_version_bindings = tuple(
        RuntimeVersionBinding.model_validate(row.model_dump())
        for row in planned_runtime_versions
    )
    binding = CanaryBinding(
        input_media_sha256=input_hash,
        comparison_config_sha256=comparison_hash,
        speaker_constraints_sha256=speaker_hash,
        machine_fingerprint_sha256=machine_hash,
        runtime_environment_sha256=runtime_environment_sha256,
        code_revision=resolved_revision,
        models=model_bindings,
        runtime_versions=runtime_version_bindings,
    )

    transcribe_callable = transcribe or _default_transcribe()
    validator_callable = speaker_validator or _default_speaker_validator()
    factory = backend_factory or _default_backend_factory
    runtimes = {
        CanaryVariant.CPU_BASELINE: factory(CanaryVariant.CPU_BASELINE),
        CanaryVariant.MPS_CANDIDATE: factory(CanaryVariant.MPS_CANDIDATE),
    }
    configs = {
        CanaryVariant.CPU_BASELINE: baseline_config,
        CanaryVariant.MPS_CANDIDATE: candidate_config,
    }
    runs: dict[CanaryVariant, list[CanaryRun]] = {
        CanaryVariant.CPU_BASELINE: [],
        CanaryVariant.MPS_CANDIDATE: [],
    }

    schedule = _execution_schedule(request.options)
    with _offline_environment():
        for variant, phase, ordinal in schedule:
            _assert_mps_fallback_disabled()
            runtime = runtimes[variant]
            if phase is CanaryRunPhase.COLD_START:
                release_runtime_models(
                    (runtime.asr, runtime.alignment, runtime.diarization)
                )
            host_condition_before = _host_condition(probe)
            run_id = _run_id(variant, phase, ordinal)
            run_dir = output_dir / _relative_run_directory(
                variant,
                phase,
                ordinal,
            )
            run, component_devices = _execute_run(
                input_file=execution_input,
                run_dir=run_dir,
                config=configs[variant],
                variant=variant,
                phase=phase,
                run_id=run_id,
                binding=binding,
                expected_source_config_sha256=_config_hash(configs[variant]),
                expected_code_revision=resolved_revision,
                expected_models=model_bindings,
                expected_input_sha256=input_hash,
                speaker_count=request.speaker_count,
                runtime=runtime,
                transcribe=transcribe_callable,
                speaker_validator=validator_callable,
                fallback_environment=fallback_environment,
            )
            host_condition_after = _host_condition(probe)
            run = _apply_host_condition_gate(
                run,
                reference=initial_host_condition,
                before=host_condition_before,
                after=host_condition_after,
            )
            runs[variant].append(run)
            write_json(
                run_dir / "canary_run_manifest.json",
                AppleSiliconRunnerRunManifest(
                    run=run,
                    pytorch_enable_mps_fallback=fallback_environment,
                    host_condition_before=host_condition_before,
                    host_condition_after=host_condition_after,
                    pyannote_component_devices=component_devices,
                ).model_dump(mode="json"),
            )

    _assert_mps_fallback_disabled()
    if sha256_file(_validated_input(request.input_file)) != input_hash:
        raise LTMError(
            "Input media changed during the Apple Silicon canary; evidence was not finalized."
        )
    _validate_mlx_checkpoint_unchanged(baseline_config, model_bindings)
    evidence = AppleSiliconCanaryEvidence(
        baseline_runs=tuple(runs[CanaryVariant.CPU_BASELINE]),
        candidate_runs=tuple(runs[CanaryVariant.MPS_CANDIDATE]),
        explicit_mps_override=request.options.explicit_mps_override,
    )
    decision = evaluate_mps_diarization_gate(evidence, request.options.policy)
    result = AppleSiliconCanaryResult(
        status="completed",
        plan=plan,
        evidence=evidence,
        decision=decision,
    )
    write_json(
        output_dir / "canary_evidence.json",
        evidence.model_dump(mode="json"),
    )
    write_json(
        output_dir / "canary_decision.json",
        decision.model_dump(mode="json"),
    )
    write_json(
        output_dir / "canary_result.json",
        result.model_dump(mode="json"),
    )
    return result


def _validate_mlx_checkpoint_unchanged(
    config: PipelineConfig,
    expected_models: tuple[ModelVersionBinding, ...],
) -> None:
    from local_transcript_max.asr.mlx_whisper_backend import mlx_model_status

    expected = next(model for model in expected_models if model.role == "asr")
    status = mlx_model_status(config)
    if not status.present:
        raise LTMError(
            "The bound MLX checkpoint became unavailable during the canary."
        )
    actual = model_checkpoint_sha256(status.entry, status.path)
    if actual != expected.checkpoint_sha256:
        raise LTMError(
            "The local MLX checkpoint changed during the canary; evidence was not finalized."
        )


def _validate_host(host: HostCapabilities) -> None:
    if not host.is_native_apple_silicon or host.is_rosetta:
        raise LTMError(
            "The Apple Silicon canary requires native arm64 macOS execution without Rosetta."
        )
    if not host.mps_built or not host.mps_available:
        raise LTMError(
            "The Apple Silicon canary requires a PyTorch build with an available MPS backend."
        )


def _canary_configs(
    request: AppleSiliconCanaryRequest,
) -> tuple[PipelineConfig, PipelineConfig]:
    base = PipelineConfig.model_validate(request.config.model_dump(mode="python"))
    base.offline = True
    base.asr.backend = "mlx-whisper"
    base.asr.device = "auto"
    base.asr.mlx_fp16 = True
    base.asr.temperature = 0.0
    base.asr.initial_prompt = None
    base.asr.prefix = None
    base.asr.glossary_file = None
    base.alignment.enabled = True
    base.alignment.backend = "forced"
    base.alignment.device = "cpu"
    alignment_entry = resolve_model_entry(
        "alignment",
        request.options.alignment_model,
    )
    if alignment_entry is None:
        raise LTMError(
            "Apple Silicon canary alignment model must resolve through the "
            "reviewed alignment manifest."
        )
    base.alignment.model = alignment_entry.key
    base.alignment.language = alignment_entry.key
    base.diarization.enabled = True
    base.diarization.backend = "pyannote"
    base.diarization.num_speakers = request.speaker_count
    base.diarization.min_speakers = None
    base.diarization.max_speakers = None
    base.qa.enabled = True
    base.qa.targeted_redecode = False
    base.agentic_diarization.enabled = False
    base.redecode.mode = "off"
    base.redecode.write_report = False
    base.speaker_id.mode = "off"
    base.speaker_identity.mode = "off"
    base.cleanup.mode = "off"
    base.runtime.model_lifecycle = "persistent"

    baseline = PipelineConfig.model_validate(base.model_dump(mode="python"))
    baseline.diarization.device = "cpu"
    candidate = PipelineConfig.model_validate(base.model_dump(mode="python"))
    candidate.diarization.device = "mps"
    return baseline, candidate


def _comparison_config_hash(
    baseline: PipelineConfig,
    candidate: PipelineConfig,
) -> str:
    baseline_payload = baseline.model_dump(mode="json")
    candidate_payload = candidate.model_dump(mode="json")
    baseline_payload["diarization"]["device"] = "<canary-device>"
    candidate_payload["diarization"]["device"] = "<canary-device>"
    if baseline_payload != candidate_payload:
        raise LTMError(
            "Canary variants differ outside the controlled diarization device field."
        )
    return _hash_json(baseline_payload)


def _speaker_constraints_hash(
    speaker_count: int,
    roster_path: Path | None,
    *,
    require_file: bool,
) -> str:
    roster_sha256: str | None = None
    if roster_path is not None:
        expanded = roster_path.expanduser()
        if require_file:
            roster = _validated_regular_file(expanded, "roster")
            _validate_roster_constraint(roster, speaker_count)
            roster_sha256 = sha256_file(roster)
        elif expanded.exists() and expanded.is_file() and not expanded.is_symlink():
            roster = expanded.resolve()
            _validate_roster_constraint(roster, speaker_count)
            roster_sha256 = sha256_file(roster)
    return _hash_json(
        {
            "exact_speaker_count": speaker_count,
            "roster_sha256": roster_sha256,
        }
    )


def _validate_roster_constraint(path: Path, speaker_count: int) -> None:
    from local_transcript_max.speaker_id.identity_models import SpeakerRoster

    try:
        roster = SpeakerRoster.model_validate(_read_artifact(path))
    except Exception as exc:
        raise LTMError(
            "Apple Silicon canary roster is not a valid private speaker roster."
        ) from exc
    expected = sum(participant.expected for participant in roster.participants)
    if expected != speaker_count or roster.expected_speaker_count not in {
        None,
        speaker_count,
    }:
        raise LTMError(
            "Apple Silicon canary roster does not match --num-speakers."
        )


def _machine_fingerprint_hash(
    host: HostCapabilities,
    fallback_environment: str,
    initial_host_condition: Mapping[str, Any],
) -> str:
    return _hash_json(
        {
            "host_capabilities": host.model_dump(mode="json"),
            "pytorch_enable_mps_fallback": fallback_environment,
            "initial_host_condition": dict(initial_host_condition),
        }
    )


def _planned_models(
    config: PipelineConfig,
    alignment_model: str,
    *,
    resolver: VersionResolver,
    require_versions: bool,
) -> tuple[PlannedModelBinding, ...]:
    specifications = (
        (
            "asr",
            "asr-mlx",
            config.asr.mlx_model,
            "mlx-whisper",
            "mlx-whisper",
        ),
        (
            "alignment",
            "alignment",
            alignment_model,
            "whisperx",
            "whisperx",
        ),
        (
            "diarization",
            "diarization",
            config.diarization.model,
            "pyannote",
            "pyannote.audio",
        ),
    )
    models: list[PlannedModelBinding] = []
    for role, manifest_role, selection, backend, package in specifications:
        entry = resolve_model_entry(manifest_role, selection)
        if entry is None or not entry.revision:
            raise LTMError(
                f"Apple Silicon canary model binding is unavailable for {role}={selection!r}."
            )
        version = resolver(package)
        if require_versions and not version:
            raise LTMError(
                f"Apple Silicon canary requires installed package {package!r}; "
                "dry-run remains available without it."
            )
        revision: str | None = entry.revision
        revision_verified = True
        checkpoint_sha256: str | None = None
        if role == "asr":
            from local_transcript_max.asr.mlx_whisper_backend import mlx_model_status

            status = mlx_model_status(config)
            revision_verified = status.revision_verified
            revision = entry.revision if revision_verified else None
            if require_versions:
                if not status.present:
                    raise LTMError(
                        "Apple Silicon canary requires a complete local MLX model "
                        f"checkpoint: {status.problem_summary(config.model_cache)}"
                    )
                checkpoint_sha256 = model_checkpoint_sha256(entry, status.path)
        models.append(
            PlannedModelBinding(
                role=role,
                model_id=entry.model_id,
                revision=revision,
                revision_verified=revision_verified,
                checkpoint_sha256=checkpoint_sha256,
                backend=backend,
                backend_version=version,
            )
        )
    return tuple(sorted(models, key=lambda model: model.role))


def _planned_runtime_versions(
    resolver: VersionResolver,
    *,
    require_versions: bool,
) -> tuple[PlannedRuntimeVersion, ...]:
    rows: list[PlannedRuntimeVersion] = []
    for package in _CANARY_RUNTIME_PACKAGES:
        version = resolver(package)
        if require_versions and not version:
            raise LTMError(
                f"Apple Silicon canary requires installed runtime package {package!r}."
            )
        rows.append(PlannedRuntimeVersion(package=package, version=version))
    return tuple(rows)


def _build_plan(
    request: AppleSiliconCanaryRequest,
    baseline: PipelineConfig,
    candidate: PipelineConfig,
    *,
    comparison_hash: str,
    speaker_hash: str,
    machine_hash: str,
    runtime_environment_sha256: str,
    code_revision: str,
    fallback_environment: Literal["unset", "disabled"],
    initial_host_condition: dict[str, Any],
    planned_models: tuple[PlannedModelBinding, ...],
    planned_runtime_versions: tuple[PlannedRuntimeVersion, ...],
    input_hash: str | None,
) -> AppleSiliconCanaryPlan:
    options = request.options
    return AppleSiliconCanaryPlan(
        execution_required=not options.dry_run,
        input_media_sha256=input_hash,
        comparison_config_sha256=comparison_hash,
        speaker_constraints_sha256=speaker_hash,
        machine_fingerprint_sha256=machine_hash,
        runtime_environment_sha256=runtime_environment_sha256,
        code_revision=code_revision,
        pytorch_enable_mps_fallback=fallback_environment,
        initial_host_condition=initial_host_condition,
        models=planned_models,
        runtime_versions=planned_runtime_versions,
        cold_start_runs=1,
        warmup_runs=options.warmup_runs,
        measured_runs=options.measured_runs,
        variants=(
            _variant_plan(
                CanaryVariant.CPU_BASELINE,
                baseline,
                options,
            ),
            _variant_plan(
                CanaryVariant.MPS_CANDIDATE,
                candidate,
                options,
            ),
        ),
        invariants=(
            "local_offline_only",
            "native_apple_silicon_arm64",
            "pytorch_implicit_mps_fallback_disabled",
            "same_input_media_hash",
            "same_models_and_revisions",
            "same_exact_speaker_count_and_roster_hash",
            "mlx_whisper_fp16_asr",
            "whisperx_forced_alignment_cpu",
            "only_pyannote_diarization_device_varies",
            "cold_start_runs_excluded_from_measured_medians",
            "warmup_runs_excluded_from_measured_medians",
        ),
    )


def _variant_plan(
    variant: CanaryVariant,
    config: PipelineConfig,
    options: AppleSiliconCanaryOptions,
) -> CanaryVariantPlan:
    device: Literal["cpu", "mps"] = (
        "cpu" if variant is CanaryVariant.CPU_BASELINE else "mps"
    )
    directories = (
        _relative_run_directory(variant, CanaryRunPhase.COLD_START, 1),
    ) + tuple(
        _relative_run_directory(variant, CanaryRunPhase.WARMUP, index)
        for index in range(1, options.warmup_runs + 1)
    ) + tuple(
        _relative_run_directory(variant, CanaryRunPhase.MEASURED_WARM, index)
        for index in range(1, options.measured_runs + 1)
    )
    return CanaryVariantPlan(
        variant=variant,
        diarization_device=device,
        source_config_sha256=_config_hash(config),
        run_directories=directories,
    )


def _execution_schedule(
    options: AppleSiliconCanaryOptions,
) -> tuple[tuple[CanaryVariant, CanaryRunPhase, int], ...]:
    rows: list[tuple[CanaryVariant, CanaryRunPhase, int]] = []
    for phase, count in (
        (CanaryRunPhase.COLD_START, 1),
        (CanaryRunPhase.WARMUP, options.warmup_runs),
        (CanaryRunPhase.MEASURED_WARM, options.measured_runs),
    ):
        for ordinal in range(1, count + 1):
            order = (
                (
                    CanaryVariant.CPU_BASELINE,
                    CanaryVariant.MPS_CANDIDATE,
                )
                if ordinal % 2
                else (
                    CanaryVariant.MPS_CANDIDATE,
                    CanaryVariant.CPU_BASELINE,
                )
            )
            rows.extend((variant, phase, ordinal) for variant in order)
    return tuple(rows)


def _execute_run(
    *,
    input_file: Path,
    run_dir: Path,
    config: PipelineConfig,
    variant: CanaryVariant,
    phase: CanaryRunPhase,
    run_id: str,
    binding: CanaryBinding,
    expected_source_config_sha256: str,
    expected_code_revision: str,
    expected_models: tuple[ModelVersionBinding, ...],
    expected_input_sha256: str,
    speaker_count: int,
    runtime: CanaryRuntimeBackends,
    transcribe: TranscribeCallable,
    speaker_validator: SpeakerValidationCallable,
    fallback_environment: Literal["unset", "disabled"],
) -> tuple[CanaryRun, dict[str, Any] | list[Any] | None]:
    planned_devices = _planned_devices(variant)
    try:
        transcribe(
            input_file,
            run_dir,
            config,
            asr_backend=runtime.asr,
            alignment_backend=runtime.alignment,
            diarization_backend=runtime.diarization,
            precheck_models=True,
            debug_report_path=run_dir / "debug_report.json",
        )
    except Exception as exc:
        run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        ensure_private_directory(run_dir)
        return (
            CanaryRun(
                run_id=run_id,
                variant=variant,
                phase=phase,
                status=CanaryRunStatus.FAILED,
                binding=binding,
                source_config_sha256=expected_source_config_sha256,
                devices=planned_devices,
                status_detail=f"transcription_failed:{exc.__class__.__name__}",
            ),
            None,
        )

    try:
        speaker_validator(run_dir, speaker_count)
    except Exception as exc:
        return (
            CanaryRun(
                run_id=run_id,
                variant=variant,
                phase=phase,
                status=CanaryRunStatus.FAILED,
                binding=binding,
                source_config_sha256=expected_source_config_sha256,
                devices=_artifact_devices_or_planned(run_dir, planned_devices),
                status_detail=f"speaker_validation_failed:{exc.__class__.__name__}",
            ),
            None,
        )

    component_devices: dict[str, Any] | list[Any] | None = None
    try:
        run_metadata = _read_artifact(run_dir / "run_metadata.json")
        completion_timing = _read_artifact(
            run_dir / "completion_timing.json"
        )
        transcript_raw = _read_artifact(run_dir / "transcript.raw.json")
        alignment_report = _read_artifact(run_dir / "alignment_report.json")
        diarization_report = _read_artifact(run_dir / "diarization.json")
        qa_report = _read_artifact(run_dir / "qa_report.json")
        speaker_report = _read_artifact(
            run_dir / "speaker_validation_report.json"
        )
        artifact_bindings = _artifact_hash_bindings(
            run_dir,
            (
                "completion_timing.json",
                "run_metadata.json",
                "transcript.raw.json",
                "alignment_report.json",
                "diarization.json",
                "qa_report.json",
                "speaker_validation_report.json",
            ),
        )
        _validate_run_binding(
            run_metadata,
            config=config,
            expected_source_config_sha256=expected_source_config_sha256,
            expected_code_revision=expected_code_revision,
            expected_models=expected_models,
            expected_runtime_versions=binding.runtime_versions,
            expected_runtime_environment_sha256=(
                binding.runtime_environment_sha256
            ),
            expected_input_sha256=expected_input_sha256,
            fallback_environment=fallback_environment,
        )
        _validate_backend_lifecycle(
            transcript_raw,
            alignment_report,
            diarization_report,
            phase=phase,
        )
        devices = _device_evidence(run_metadata, diarization_report, variant)
        component_devices = _component_device_metadata(run_metadata)
        _validate_component_devices(
            component_devices,
            variant=variant,
            diarization=devices.diarization,
        )
        timings = _timing_evidence(
            run_metadata,
            completion_timing,
            run_metadata_sha256=sha256_file(run_dir / "run_metadata.json"),
        )
        peak_memory = _peak_memory_bytes(completion_timing)
        alignment_qa = _alignment_qa(alignment_report, qa_report)
        speaker_qa = _speaker_qa(qa_report, speaker_report, speaker_count)
    except Exception as exc:
        return (
            CanaryRun(
                run_id=run_id,
                variant=variant,
                phase=phase,
                status=CanaryRunStatus.INCONCLUSIVE,
                binding=binding,
                source_config_sha256=expected_source_config_sha256,
                devices=_artifact_devices_or_planned(run_dir, planned_devices),
                status_detail=f"evidence_invalid:{exc.__class__.__name__}",
            ),
            component_devices,
        )

    return (
        CanaryRun(
            run_id=run_id,
            variant=variant,
            phase=phase,
            status=CanaryRunStatus.COMPLETED,
            binding=binding,
            source_config_sha256=expected_source_config_sha256,
            processing_timestamp=_processing_timestamp(run_metadata),
            artifacts=artifact_bindings,
            devices=devices,
            timings=timings,
            peak_memory_bytes=peak_memory,
            alignment_qa=alignment_qa,
            speaker_qa=speaker_qa,
        ),
        component_devices,
    )


def _validate_run_binding(
    metadata: Mapping[str, Any],
    *,
    config: PipelineConfig,
    expected_source_config_sha256: str,
    expected_code_revision: str,
    expected_models: tuple[ModelVersionBinding, ...],
    expected_runtime_versions: tuple[RuntimeVersionBinding, ...],
    expected_runtime_environment_sha256: str,
    expected_input_sha256: str,
    fallback_environment: Literal["unset", "disabled"],
) -> None:
    expected_configuration, complete_config_sha256 = configuration_evidence(config)
    if complete_config_sha256 != expected_source_config_sha256:
        raise ValueError("runner configuration hash mismatch")
    if metadata.get("source_configuration_schema") != SOURCE_CONFIGURATION_SCHEMA:
        raise ValueError("source configuration privacy schema mismatch")
    if metadata.get("source_config_sha256_scope") != SOURCE_CONFIG_SHA256_SCOPE:
        raise ValueError("source configuration hash scope mismatch")
    if metadata.get("source_configuration") != expected_configuration:
        raise ValueError("privacy-safe source configuration projection mismatch")
    if metadata.get("source_config_sha256") != expected_source_config_sha256:
        raise ValueError("source configuration hash mismatch")
    if metadata.get("input_file_hash") != expected_input_sha256:
        raise ValueError("input media hash mismatch")
    if metadata.get("code_revision") != expected_code_revision:
        raise ValueError("code revision mismatch")
    if metadata.get("asr_backend") != "mlx-whisper":
        raise ValueError("ASR backend mismatch")
    if metadata.get("diarization_backend") != "pyannote":
        raise ValueError("diarization backend mismatch")
    if not str(metadata.get("alignment_backend") or "").startswith("whisperx"):
        raise ValueError("forced alignment backend mismatch")
    if metadata.get("alignment_actual_device") != "cpu":
        raise ValueError("alignment did not execute on CPU")

    runtime_environment = metadata.get("runtime_environment")
    if not isinstance(runtime_environment, Mapping):
        raise ValueError("runtime environment evidence missing")
    mlx_environment = runtime_environment.get("mlx")
    if not isinstance(mlx_environment, Mapping):
        raise ValueError("MLX runtime evidence missing")
    if mlx_environment.get("loaded") is not True:
        raise ValueError("MLX runtime was not loaded")
    if mlx_environment.get("default_device") != "mlx:gpu":
        raise ValueError("MLX runtime default device was not GPU")
    if mlx_environment.get("metal_available") is not True:
        raise ValueError("MLX Metal runtime was not available")
    if (
        _hash_json(_observed_environment_snapshot(metadata))
        != expected_runtime_environment_sha256
    ):
        raise ValueError("runtime thread/MPS environment binding mismatch")

    dependency_versions = metadata.get("dependency_versions")
    if not isinstance(dependency_versions, Mapping):
        raise ValueError("dependency version evidence missing")
    for runtime_expected in expected_runtime_versions:
        if (
            dependency_versions.get(runtime_expected.package)
            != runtime_expected.version
        ):
            raise ValueError(
                f"runtime package version mismatch: {runtime_expected.package}"
            )
    package_by_backend = {
        "mlx-whisper": "mlx-whisper",
        "whisperx": "whisperx",
        "pyannote": "pyannote.audio",
    }
    for model_expected in expected_models:
        package = package_by_backend[model_expected.backend]
        if dependency_versions.get(package) != model_expected.backend_version:
            raise ValueError(f"backend version mismatch: {package}")

    observed_models = metadata.get("models")
    if not isinstance(observed_models, list):
        raise ValueError("model evidence missing")
    expected_ids = {
        model.role: model.model_id
        for model in expected_models
        if model.role in {"asr", "alignment", "diarization"}
    }
    actual_ids: dict[str, str] = {}
    for row in observed_models:
        if not isinstance(row, Mapping):
            continue
        role = str(row.get("role") or "")
        normalized_role = "asr" if role in {"asr", "asr-mlx"} else role
        model_id = row.get("model_id")
        if normalized_role in expected_ids and isinstance(model_id, str):
            actual_ids[normalized_role] = model_id
            expected_model = next(
                model for model in expected_models if model.role == normalized_role
            )
            if row.get("revision") != expected_model.revision:
                raise ValueError(f"model revision mismatch: {normalized_role}")
            if row.get("revision_verified") is not expected_model.revision_verified:
                raise ValueError(
                    f"model revision verification mismatch: {normalized_role}"
                )
            if row.get("checkpoint_sha256") != expected_model.checkpoint_sha256:
                raise ValueError(f"model checkpoint mismatch: {normalized_role}")
    if actual_ids != expected_ids:
        raise ValueError("model identifier binding mismatch")

    environment_overrides = metadata.get("environment_overrides")
    if isinstance(environment_overrides, Mapping):
        observed_fallback = environment_overrides.get(
            "PYTORCH_ENABLE_MPS_FALLBACK"
        )
        if _env_value_enables_fallback(observed_fallback):
            raise ValueError("hidden PyTorch MPS fallback was enabled")
    if fallback_environment not in {"unset", "disabled"}:
        raise ValueError("invalid PyTorch MPS fallback binding")


def _processing_timestamp(metadata: Mapping[str, Any]) -> datetime:
    value = metadata.get("processing_timestamp")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("processing timestamp evidence missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("processing timestamp evidence invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("processing timestamp must include a UTC offset")
    return parsed


def _artifact_hash_bindings(
    run_dir: Path,
    names: tuple[str, ...],
) -> tuple[ArtifactHashBinding, ...]:
    return tuple(
        ArtifactHashBinding(name=name, sha256=sha256_file(run_dir / name))
        for name in sorted(names)
    )


def _validate_backend_lifecycle(
    transcript_raw: Mapping[str, Any],
    alignment_report: Mapping[str, Any],
    diarization_report: Mapping[str, Any],
    *,
    phase: CanaryRunPhase,
) -> None:
    asr_config = _mapping(transcript_raw.get("config"))
    asr_timing = _mapping(asr_config.get("timing"))
    alignment_metadata = _mapping(alignment_report.get("metadata"))
    diarization_metadata = _mapping(diarization_report.get("metadata"))
    diarization_timing = _mapping(diarization_metadata.get("timing"))
    lifecycle = {
        "asr": asr_timing.get("cold_start"),
        "alignment": alignment_metadata.get("cold_start"),
        "diarization": diarization_timing.get("cold_start"),
    }
    if any(not isinstance(value, bool) for value in lifecycle.values()):
        raise ValueError("backend cold-start telemetry missing")
    if phase is not CanaryRunPhase.COLD_START:
        cold = [name for name, value in lifecycle.items() if value is True]
        if cold:
            raise ValueError(
                "warm canary phase unexpectedly performed cold model loads: "
                + ", ".join(cold)
            )


def _device_evidence(
    metadata: Mapping[str, Any],
    diarization_report: Mapping[str, Any],
    variant: CanaryVariant,
) -> PipelineDeviceEvidence:
    expected_diarization = (
        "cpu" if variant is CanaryVariant.CPU_BASELINE else "mps"
    )
    asr = DeviceEvidence(
        requested=_required_text(
            metadata.get("asr_requested_device"),
            "asr requested device",
        ),
        resolved=_required_text(
            metadata.get("asr_resolved_device"),
            "asr resolved device",
        ),
        actual=_required_text(
            metadata.get("asr_actual_device") or metadata.get("device"),
            "asr actual device",
        ),
    )
    if asr.requested != "auto" or asr.resolved != "mlx" or asr.actual != "mlx:gpu":
        raise ValueError("ASR did not execute through the verified MLX GPU lane")
    alignment_fallback_reason = _optional_text(
        metadata.get("alignment_fallback_reason")
    )
    alignment = DeviceEvidence(
        requested=_required_text(
            metadata.get("alignment_requested_device"),
            "alignment requested device",
        ),
        resolved=_required_text(
            metadata.get("alignment_resolved_device"),
            "alignment resolved device",
        ),
        actual=_required_text(
            metadata.get("alignment_actual_device")
            or metadata.get("alignment_device"),
            "alignment actual device",
        ),
        fallback_occurred=alignment_fallback_reason is not None,
        fallback_reason=alignment_fallback_reason,
    )
    diarization_fallback = bool(metadata.get("diarization_device_fallback"))
    diarization_fallback_reason = _optional_text(
        metadata.get("diarization_fallback_reason")
    )
    if diarization_fallback and diarization_fallback_reason is None:
        diarization_fallback_reason = "structured_fallback_reason_missing"
    diarization_metadata = _mapping(diarization_report.get("metadata"))
    diarization = DeviceEvidence(
        requested=_required_text(
            metadata.get("diarization_requested_device"),
            "diarization requested device",
        ),
        resolved=_required_text(
            metadata.get("diarization_resolved_device"),
            "diarization resolved device",
        ),
        actual=_required_text(
            metadata.get("diarization_actual_device"),
            "diarization actual device",
        ),
        fallback_occurred=diarization_fallback,
        fallback_reason=diarization_fallback_reason,
        synchronized_before=(
            diarization_metadata.get("mps_synchronized_before")
            if isinstance(
                diarization_metadata.get("mps_synchronized_before"),
                bool,
            )
            else None
        ),
        synchronized_after=(
            diarization_metadata.get("mps_synchronized_after")
            if isinstance(
                diarization_metadata.get("mps_synchronized_after"),
                bool,
            )
            else None
        ),
    )
    if diarization.requested != expected_diarization:
        raise ValueError("diarization request does not match canary variant")
    return PipelineDeviceEvidence(
        asr=asr,
        alignment=alignment,
        diarization=diarization,
    )


def _planned_devices(variant: CanaryVariant) -> PipelineDeviceEvidence:
    diarization = "cpu" if variant is CanaryVariant.CPU_BASELINE else "mps"
    return PipelineDeviceEvidence(
        asr=DeviceEvidence(requested="auto", resolved="mlx"),
        alignment=DeviceEvidence(requested="cpu", resolved="cpu"),
        diarization=DeviceEvidence(
            requested=diarization,
            resolved=diarization,
        ),
    )


def _artifact_devices_or_planned(
    run_dir: Path,
    planned: PipelineDeviceEvidence,
) -> PipelineDeviceEvidence:
    path = run_dir / "run_metadata.json"
    if not path.exists():
        return planned
    try:
        payload = _read_artifact(path)
        variant = (
            CanaryVariant.MPS_CANDIDATE
            if planned.diarization.requested == "mps"
            else CanaryVariant.CPU_BASELINE
        )
        diarization_path = run_dir / "diarization.json"
        diarization_payload = (
            _read_artifact(diarization_path)
            if diarization_path.exists()
            else {}
        )
        return _device_evidence(payload, diarization_payload, variant)
    except Exception:
        return planned


def _timing_evidence(
    metadata: Mapping[str, Any],
    completion: Mapping[str, Any],
    *,
    run_metadata_sha256: str,
) -> CanaryTimingEvidence:
    completion_report = CompletionTimingReport.model_validate(dict(completion))
    if completion_report.run_metadata_sha256 != run_metadata_sha256:
        raise ValueError(
            "completion timing does not match the bound run metadata"
        )
    rows = metadata.get("timings")
    if not isinstance(rows, list):
        raise ValueError("timing rows missing")
    timings: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        duration = row.get("duration_seconds")
        if (
            isinstance(name, str)
            and isinstance(duration, int | float)
            and not isinstance(duration, bool)
            and duration >= 0
        ):
            timings[name] = float(duration)
    preparation = sum(_required_timing(timings, name) for name in _PREPARATION_TIMINGS)
    observed_total = completion_report.observed_end_to_end_seconds
    if observed_total <= 0:
        raise ValueError("positive observed end-to-end timing missing")
    return CanaryTimingEvidence(
        preparation_seconds=preparation,
        asr_seconds=_required_timing(timings, "asr"),
        alignment_seconds=_required_timing(timings, "alignment"),
        diarization_seconds=_required_timing(timings, "diarization", positive=True),
        total_seconds=observed_total,
    )


def _peak_memory_bytes(metadata: Mapping[str, Any]) -> int:
    usage = metadata.get("resource_usage")
    if not isinstance(usage, Mapping):
        raise ValueError("resource usage missing")
    for name in ("rss_peak_sampled_bytes", "rss_peak_process_lifetime_bytes"):
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    raise ValueError("positive peak memory evidence missing")


def _alignment_qa(
    alignment_report: Mapping[str, Any],
    qa_report: Mapping[str, Any],
) -> AlignmentQAEvidence:
    summary = _mapping(qa_report.get("summary"))
    source_percentages = _mapping(alignment_report.get("source_percentages"))
    forced = _percentage_rate(
        alignment_report.get("forced_aligned_word_percentage"),
        source_percentages.get("forced"),
        summary.get("forced_alignment_percentage"),
    )
    estimated = _percentage_rate(
        alignment_report.get("estimated_word_percentage"),
        source_percentages.get("estimated"),
        summary.get("estimated_alignment_percentage"),
    )
    missing = _required_nonnegative_int(
        alignment_report.get("missing_timestamp_count"),
        "missing timestamp count",
    )
    nonmonotonic = _required_nonnegative_int(
        alignment_report.get("nonmonotonic_timestamp_count")
        if alignment_report.get("nonmonotonic_timestamp_count") is not None
        else summary.get("non_monotonic_timestamp_count"),
        "nonmonotonic timestamp count",
    )
    return AlignmentQAEvidence(
        forced_word_coverage=forced,
        estimated_word_coverage=estimated,
        missing_timestamp_count=missing,
        nonmonotonic_timestamp_count=nonmonotonic,
    )


def _speaker_qa(
    qa_report: Mapping[str, Any],
    speaker_report: Mapping[str, Any],
    speaker_count: int,
) -> SpeakerQAEvidence:
    summary = _mapping(qa_report.get("summary"))
    expected = _required_nonnegative_int(
        speaker_report.get("expected_speakers"),
        "validated requested speaker count",
    )
    if expected != speaker_count:
        raise ValueError("speaker validation constraint mismatch")
    detected = _required_nonnegative_int(
        summary.get("detected_speaker_count")
        if summary.get("detected_speaker_count") is not None
        else speaker_report.get("unique_speakers"),
        "detected speaker count",
    )
    validated = _required_nonnegative_int(
        speaker_report.get("unique_speakers"),
        "validated speaker count",
    )
    uncertain_rate = _percentage_rate(
        summary.get("percent_words_uncertain"),
        speaker_report.get("percent_words_marked_uncertain"),
    )
    assigned_rate = _percentage_rate(
        summary.get("percent_words_assigned_to_speakers"),
        speaker_report.get("percent_words_assigned_to_speakers"),
    )
    flapping = _unit_rate(
        speaker_report.get("speaker_flapping_score")
        if speaker_report.get("speaker_flapping_score") is not None
        else summary.get("speaker_flapping_score"),
        "speaker flapping score",
    )
    flags = qa_report.get("flags")
    critical: set[str] = set()
    major: set[str] = set()
    if isinstance(flags, list):
        for row in flags:
            if not isinstance(row, Mapping):
                continue
            code = _defect_id(row.get("code"))
            severity = row.get("severity")
            if code and severity == "error":
                critical.add(code)
            elif code and severity == "warning":
                major.add(code)
    if bool(speaker_report.get("speaker_count_mismatch")):
        major.add("speaker_count_mismatch")
    return SpeakerQAEvidence(
        requested_speaker_count=speaker_count,
        detected_speaker_count=detected,
        validated_speaker_count=validated,
        uncertain_speaker_rate=uncertain_rate,
        unassigned_speaker_rate=max(0.0, min(1.0, 1.0 - assigned_rate)),
        speaker_flapping=flapping,
        critical_defects=tuple(sorted(critical)),
        major_defects=tuple(sorted(major)),
    )


def _component_device_metadata(
    metadata: Mapping[str, Any],
) -> dict[str, Any] | list[Any] | None:
    direct = metadata.get("diarization_component_devices")
    if isinstance(direct, dict | list):
        return direct
    device_policy = metadata.get("device_policy")
    if isinstance(device_policy, Mapping):
        diarization = device_policy.get("diarization")
        if isinstance(diarization, Mapping):
            nested = diarization.get("component_devices")
            if isinstance(nested, dict | list):
                return nested
    return None


def _validate_component_devices(
    components: dict[str, Any] | list[Any] | None,
    *,
    variant: CanaryVariant,
    diarization: DeviceEvidence,
) -> None:
    if components is None:
        raise ValueError("pyannote component device evidence missing")
    rows: list[tuple[str, Any]] = []
    if isinstance(components, dict):
        rows = [(str(name), value) for name, value in components.items()]
    else:
        rows = [(str(index), value) for index, value in enumerate(components)]
    neural_rows = 0
    required_neural_components = {"segmentation", "embedding"}
    observed_required_components: set[str] = set()
    expected = "cpu" if variant is CanaryVariant.CPU_BASELINE else "mps"
    for name, value in rows:
        actual: str | None
        verification: str | None = None
        if isinstance(value, str):
            actual = value
            neural = name in {
                "neural_pipeline",
                "segmentation",
                "embedding",
                "segmentation_model",
                "embedding_model",
            }
            fallback = False
        elif isinstance(value, Mapping):
            kind = str(value.get("kind") or "").strip().lower()
            explicit_neural = value.get("neural")
            neural = (
                bool(explicit_neural)
                if isinstance(explicit_neural, bool)
                else kind == "neural"
                or (
                    not kind
                    and name
                    in {
                        "neural_pipeline",
                        "segmentation",
                        "embedding",
                        "segmentation_model",
                        "embedding_model",
                    }
                )
            )
            actual = _optional_text(
                value.get("actual_device") or value.get("device")
            )
            fallback = bool(
                value.get("fallback_occurred") or value.get("device_fallback")
            )
            verification = _optional_text(value.get("verification"))
        else:
            continue
        if not neural:
            continue
        neural_rows += 1
        if name in required_neural_components:
            observed_required_components.add(name)
        if actual != expected:
            raise ValueError(f"pyannote neural component device mismatch: {name}")
        if fallback:
            raise ValueError(f"pyannote neural component fallback: {name}")
        if (
            name in required_neural_components
            and verification
            != "component_device_attribute_and_successful_pipeline_call"
        ):
            raise ValueError(
                f"pyannote neural component verification missing: {name}"
            )
    if not neural_rows:
        raise ValueError("pyannote component evidence contains no neural components")
    if observed_required_components != required_neural_components:
        missing = sorted(required_neural_components - observed_required_components)
        raise ValueError(
            "pyannote component evidence missing required neural components: "
            + ", ".join(missing)
        )
    if variant is CanaryVariant.MPS_CANDIDATE and (
        diarization.actual != "mps" or diarization.fallback_occurred
    ):
        raise ValueError("candidate neural execution was not decisive MPS")


def _read_artifact(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required regular artifact missing: {path.name}")
    if path.stat().st_size > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"artifact exceeds size limit: {path.name}")
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"artifact must contain an object: {path.name}")
    return payload


def _validated_input(path: Path) -> Path:
    return _validated_regular_file(path.expanduser(), "input media")


def _validated_regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise LTMError(f"Apple Silicon canary {label} must be a regular, non-symlink file.")
    return path.resolve()


def _prepare_output_root(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.exists() or expanded.is_symlink():
        raise LTMError(
            "Apple Silicon canary output directory must not already exist; "
            "choose a fresh path so evidence cannot be reused."
        )
    expanded.mkdir(parents=True, mode=0o700)
    ensure_private_directory(expanded)
    return expanded.resolve()


def _relative_run_directory(
    variant: CanaryVariant,
    phase: CanaryRunPhase,
    ordinal: int,
) -> str:
    variant_dir = (
        "cpu_baseline"
        if variant is CanaryVariant.CPU_BASELINE
        else "mps_candidate"
    )
    phase_name = {
        CanaryRunPhase.COLD_START: "cold-start",
        CanaryRunPhase.WARMUP: "warmup",
        CanaryRunPhase.MEASURED_WARM: "measured",
    }[phase]
    return f"runs/{variant_dir}/{phase_name}-{ordinal:03d}"


def _run_id(
    variant: CanaryVariant,
    phase: CanaryRunPhase,
    ordinal: int,
) -> str:
    prefix = "cpu" if variant is CanaryVariant.CPU_BASELINE else "mps"
    phase_name = {
        CanaryRunPhase.COLD_START: "cold-start",
        CanaryRunPhase.WARMUP: "warmup",
        CanaryRunPhase.MEASURED_WARM: "measured",
    }[phase]
    return f"{prefix}-{phase_name}-{ordinal:03d}"


def _config_hash(config: PipelineConfig) -> str:
    return _hash_json(config.model_dump(mode="json"))


def _hash_json(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _installed_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _default_transcribe() -> TranscribeCallable:
    from local_transcript_max.pipeline import transcribe_file

    return cast(TranscribeCallable, transcribe_file)


def _default_speaker_validator() -> SpeakerValidationCallable:
    from local_transcript_max.validation.speakers import validate_speaker_artifacts

    return cast(SpeakerValidationCallable, validate_speaker_artifacts)


def _default_backend_factory(variant: CanaryVariant) -> CanaryRuntimeBackends:
    del variant
    from local_transcript_max.alignment.forced_alignment import (
        WhisperXForcedAlignment,
    )
    from local_transcript_max.asr.mlx_whisper_backend import MlxWhisperBackend
    from local_transcript_max.diarization.pyannote_backend import (
        PyannoteDiarizationBackend,
    )

    return CanaryRuntimeBackends(
        asr=MlxWhisperBackend(),
        alignment=WhisperXForcedAlignment(),
        diarization=PyannoteDiarizationBackend(),
    )


def _default_host_condition_probe() -> Mapping[str, Any]:
    from local_transcript_max.diagnostics import power_state

    return power_state()


def _host_condition(probe: HostConditionProbe) -> dict[str, Any]:
    """Retain only privacy-safe power and thermal fields from a local probe."""

    try:
        observed = probe()
    except Exception:
        observed = {}
    allowed = (
        "available",
        "source",
        "battery_percent",
        "charging",
        "low_power_mode",
        "power_mode",
        "thermal_state",
        "thermal_pressure",
        "serious_thermal_pressure",
    )
    return {
        key: value
        for key in allowed
        if (value := observed.get(key)) is None
        or isinstance(value, str | int | float | bool)
    }


def _apply_host_condition_gate(
    run: CanaryRun,
    *,
    reference: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> CanaryRun:
    if run.status is not CanaryRunStatus.COMPLETED:
        return run
    problem = _host_condition_problem(reference, before, after)
    if problem is None:
        return run
    return run.model_copy(
        update={
            "status": CanaryRunStatus.INCONCLUSIVE,
            "status_detail": problem,
        }
    )


def _host_condition_problem(
    reference: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> str | None:
    if any(condition.get("available") is not True for condition in (reference, before, after)):
        return "host_power_condition_unavailable"
    if _serious_thermal_condition(before) or _serious_thermal_condition(after):
        return "serious_thermal_state_invalidated_run"
    for key in ("source", "thermal_state", "thermal_pressure"):
        values = (reference.get(key), before.get(key), after.get(key))
        if any(value is None for value in values) or len(set(values)) != 1:
            return f"host_condition_changed:{key}"
    low_power_values = tuple(
        condition.get("low_power_mode") for condition in (reference, before, after)
    )
    power_mode_values = tuple(
        condition.get("power_mode") for condition in (reference, before, after)
    )
    if all(value is not None for value in low_power_values):
        if len(set(low_power_values)) != 1:
            return "host_condition_changed:low_power_mode"
    elif all(value is not None for value in power_mode_values):
        if len(set(power_mode_values)) != 1:
            return "host_condition_changed:power_mode"
    else:
        return "host_power_mode_unavailable"
    return None


def _serious_thermal_condition(condition: Mapping[str, Any]) -> bool:
    if condition.get("serious_thermal_pressure") is True:
        return True
    for key in ("thermal_state", "thermal_pressure"):
        value = condition.get(key)
        if isinstance(value, str) and value.strip().lower() in {
            "serious",
            "critical",
            "heavy",
        }:
            return True
        if isinstance(value, int) and not isinstance(value, bool) and value >= 2:
            return True
    return False


@contextmanager
def _offline_environment() -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in _OFFLINE_ENVIRONMENT}
    try:
        os.environ.update(_OFFLINE_ENVIRONMENT)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _mps_fallback_environment() -> Literal["unset", "disabled", "enabled"]:
    value = os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK")
    if value is None:
        return "unset"
    if value.strip().lower() in _SAFE_FALSE_ENV_VALUES:
        return "disabled"
    return "enabled"


def _canonical_environment_snapshot() -> dict[str, str | None]:
    return {
        name: os.environ.get(name)
        for name in (
            *_CANARY_MPS_EXPERIMENT_ENVIRONMENT,
            *_CANARY_THREAD_ENVIRONMENT,
        )
    }


def _validate_canonical_environment(
    environment: Mapping[str, str | None],
) -> None:
    enabled_experiments = [
        name
        for name in _CANARY_MPS_EXPERIMENT_ENVIRONMENT
        if (environment.get(name) or "").strip()
    ]
    if enabled_experiments:
        raise LTMError(
            "Apple Silicon canonical canary requires these experimental MPS "
            "environment variables to be unset: "
            + ", ".join(enabled_experiments)
        )


def _observed_environment_snapshot(
    metadata: Mapping[str, Any],
) -> dict[str, str | None]:
    overrides = _mapping(metadata.get("environment_overrides"))
    runtime_environment = _mapping(metadata.get("runtime_environment"))
    thread_environment = _mapping(runtime_environment.get("thread_environment"))
    return {
        **{
            name: _optional_text(overrides.get(name))
            for name in _CANARY_MPS_EXPERIMENT_ENVIRONMENT
        },
        **{
            name: _optional_text(thread_environment.get(name))
            for name in _CANARY_THREAD_ENVIRONMENT
        },
    }


def _assert_mps_fallback_disabled() -> None:
    if _mps_fallback_environment() == "enabled":
        raise LTMError(
            "PYTORCH_ENABLE_MPS_FALLBACK changed during the canary; "
            "evidence was not finalized."
        )


def _env_value_enables_fallback(value: object) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() not in _SAFE_FALSE_ENV_VALUES


def _required_timing(
    timings: Mapping[str, float],
    name: str,
    *,
    positive: bool = False,
) -> float:
    if name not in timings:
        raise ValueError(f"required timing missing: {name}")
    value = timings[name]
    if positive and value <= 0:
        raise ValueError(f"required timing must be positive: {name}")
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _required_text(value: object, label: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{label} missing")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} invalid")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    raise ValueError(f"{label} missing or invalid")


def _percentage_rate(*values: object) -> float:
    for value in values:
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and 0 <= float(value) <= 100
        ):
            return float(value) / 100.0
    raise ValueError("percentage evidence missing or invalid")


def _unit_rate(value: object, label: str) -> float:
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and 0 <= float(value) <= 1
    ):
        return float(value)
    raise ValueError(f"{label} missing or invalid")


def _defect_id(value: object) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", text.lower()).strip("_")
    return normalized or None
