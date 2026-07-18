# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from local_transcript_max.benchmark.apple_silicon_canary import (
    CanaryOutcome,
    CanaryRunStatus,
    CanaryVariant,
)
from local_transcript_max.benchmark.apple_silicon_runner import (
    AppleSiliconCanaryOptions,
    AppleSiliconCanaryRequest,
    CanaryRuntimeBackends,
    _canary_configs,
    run_apple_silicon_canary,
)
from local_transcript_max.config import PipelineConfig, resolve_model_entry
from local_transcript_max.configuration_evidence import (
    SOURCE_CONFIG_SHA256_SCOPE,
    SOURCE_CONFIGURATION_SCHEMA,
    configuration_evidence,
)
from local_transcript_max.errors import LTMError
from local_transcript_max.models import model_checkpoint_sha256
from local_transcript_max.runtime_policy import HostCapabilities
from local_transcript_max.utils import sha256_file, write_json

_REVISION = "4329325714cba150e1e4a2a52a772ea6671bd31e"
_VERSIONS = {
    "mlx": "0.31.2",
    "mlx-metal": "0.31.2",
    "mlx-whisper": "0.4.3",
    "torch": "2.8.0",
    "torchaudio": "2.8.0",
    "whisperx": "3.8.6",
    "pyannote.audio": "4.0.4",
}


def _host(**updates: Any) -> HostCapabilities:
    values: dict[str, Any] = {
        "system": "Darwin",
        "process_architecture": "arm64",
        "hardware_architecture": "arm64",
        "macos_version": "26.5",
        "is_apple_silicon": True,
        "is_native_arm64": True,
        "is_native_apple_silicon": True,
        "is_rosetta": False,
        "torch_available": True,
        "cuda_available": False,
        "mps_built": True,
        "mps_available": True,
        "mlx_available": True,
        "mlx_whisper_available": True,
    }
    values.update(updates)
    return HostCapabilities.model_validate(values)


def _config(tmp_path: Path) -> PipelineConfig:
    config = PipelineConfig(model_cache=tmp_path / "models")
    config.asr.mlx_model_path = tmp_path / "models" / "mlx"
    config.alignment.language = "en"
    return config


def _request(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    explicit_mps_override: bool = False,
) -> AppleSiliconCanaryRequest:
    return AppleSiliconCanaryRequest(
        input_file=tmp_path / "private-input.wav",
        output_dir=tmp_path / "canary",
        config=_config(tmp_path),
        speaker_count=2,
        options=AppleSiliconCanaryOptions(
            dry_run=dry_run,
            explicit_mps_override=explicit_mps_override,
        ),
    )


def _version(package: str) -> str | None:
    return _VERSIONS.get(package)


def _backend_factory(variant: CanaryVariant) -> CanaryRuntimeBackends:
    return CanaryRuntimeBackends(
        asr=f"{variant.value}-asr",
        alignment=f"{variant.value}-alignment",
        diarization=f"{variant.value}-diarization",
    )


def _condition() -> dict[str, Any]:
    return {
        "available": True,
        "source": "AC Power",
        "battery_percent": 80,
        "charging": True,
        "low_power_mode": False,
        "thermal_state": "nominal",
        "thermal_pressure": "nominal",
    }


class _FakeTranscriber:
    def __init__(
        self,
        *,
        candidate_fallback: bool = False,
        mismatch_source_hash: bool = False,
        candidate_component_cpu: bool = False,
        mlx_metal_available: bool = True,
        include_component_devices: bool = True,
    ) -> None:
        self.candidate_fallback = candidate_fallback
        self.mismatch_source_hash = mismatch_source_hash
        self.candidate_component_cpu = candidate_component_cpu
        self.mlx_metal_available = mlx_metal_available
        self.include_component_devices = include_component_devices
        self.calls: list[tuple[Path, PipelineConfig, dict[str, Any]]] = []

    def __call__(
        self,
        input_file: Path,
        out_dir: Path,
        config: PipelineConfig,
        **kwargs: Any,
    ) -> object:
        self.calls.append((out_dir, config, kwargs))
        out_dir.mkdir(parents=True)
        device = config.diarization.device
        candidate = device == "mps"
        fallback = candidate and self.candidate_fallback
        actual_device = "cpu" if fallback else device
        source_configuration, source_hash = configuration_evidence(config)
        if self.mismatch_source_hash:
            source_hash = "0" * 64
        component_device = (
            "cpu" if candidate and self.candidate_component_cpu else actual_device
        )
        entry = resolve_model_entry("asr-mlx", config.asr.mlx_model)
        assert entry is not None
        model_path = config.asr.mlx_model_path
        assert model_path is not None
        checkpoint_sha256 = model_checkpoint_sha256(entry, model_path)
        cold_start = "cold-start" in out_dir.name
        write_json(
            out_dir / "run_metadata.json",
            {
                "package_version": "0.2.0",
                "code_revision": _REVISION,
                "source_config_sha256": source_hash,
                "source_config_sha256_scope": SOURCE_CONFIG_SHA256_SCOPE,
                "source_configuration": source_configuration,
                "source_configuration_schema": SOURCE_CONFIGURATION_SCHEMA,
                "input_file_hash": sha256_file(input_file),
                "processing_timestamp": "2026-07-18T12:00:00+00:00",
                "models": [
                    {
                        "role": "asr-mlx",
                        "model_id": "mlx-community/whisper-large-v3-mlx",
                        "revision": None,
                        "revision_verified": False,
                        "checkpoint_sha256": checkpoint_sha256,
                    },
                    {
                        "role": "alignment",
                        "model_id": "facebook/wav2vec2-base-960h",
                        "revision": "22aad52d435eb6dbaf354bdad9b0da84ce7d6156",
                        "revision_verified": True,
                        "checkpoint_sha256": None,
                    },
                    {
                        "role": "diarization",
                        "model_id": "pyannote/speaker-diarization-community-1",
                        "revision": "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee",
                        "revision_verified": True,
                        "checkpoint_sha256": None,
                    },
                ],
                "asr_backend": "mlx-whisper",
                "diarization_backend": "pyannote",
                "alignment_backend": "whisperx-forced",
                "asr_requested_device": "auto",
                "asr_resolved_device": "mlx",
                "asr_actual_device": "mlx:gpu",
                "alignment_requested_device": "cpu",
                "alignment_resolved_device": "cpu",
                "alignment_actual_device": "cpu",
                "alignment_device": "cpu",
                "alignment_fallback_reason": None,
                "diarization_requested_device": device,
                "diarization_resolved_device": device,
                "diarization_actual_device": actual_device,
                "diarization_device_fallback": fallback,
                "diarization_fallback_reason": (
                    "NotImplementedError" if fallback else None
                ),
                "diarization_component_devices": (
                    {
                        "segmentation": {
                            "kind": "neural",
                            "actual_device": component_device,
                            "fallback_occurred": fallback,
                            "verification": (
                                "component_device_attribute_and_successful_pipeline_call"
                            ),
                        },
                        "embedding": {
                            "kind": "neural",
                            "actual_device": component_device,
                            "fallback_occurred": fallback,
                            "verification": (
                                "component_device_attribute_and_successful_pipeline_call"
                            ),
                        },
                    }
                    if self.include_component_devices
                    else None
                ),
                "dependency_versions": dict(_VERSIONS),
                "environment_overrides": {},
                "runtime_environment": {
                    "mlx": {
                        "loaded": True,
                        "default_device": "mlx:gpu",
                        "metal_available": self.mlx_metal_available,
                    },
                    "thread_environment": {
                        name: os.getenv(name)
                        for name in (
                            "OMP_NUM_THREADS",
                            "MKL_NUM_THREADS",
                            "VECLIB_MAXIMUM_THREADS",
                            "OPENBLAS_NUM_THREADS",
                        )
                    },
                },
                "resource_usage": {"rss_peak_sampled_bytes": 900_000_000},
                "timings": [
                    {"name": "preflight", "duration_seconds": 0.2},
                    {"name": "media_probe", "duration_seconds": 0.1},
                    {"name": "audio_preprocessing", "duration_seconds": 0.4},
                    {"name": "vad", "duration_seconds": 0.3},
                    {"name": "asr", "duration_seconds": 5.0},
                    {"name": "alignment", "duration_seconds": 2.0},
                    {
                        "name": "diarization",
                        "duration_seconds": 8.0 if candidate else 10.0,
                    },
                    {
                        "name": "total",
                        "duration_seconds": 18.0 if candidate else 20.0,
                    },
                ],
            },
        )
        write_json(
            out_dir / "completion_timing.json",
            {
                "schema_version": "completion_timing.v1",
                "run_metadata_sha256": sha256_file(
                    out_dir / "run_metadata.json"
                ),
                "pipeline_core_seconds": 17.0 if candidate else 19.0,
                "observed_end_to_end_seconds": 18.0 if candidate else 20.0,
                "real_time_factor": 0.18 if candidate else 0.20,
                "stage_durations": {
                    "invocation_setup": 0.25,
                    "run_metadata_preparation_and_publish": 0.25,
                    "speaker_identity": 0.0,
                    "debug_report_publish": 0.0,
                },
                "resource_usage": {
                    "rss_peak_sampled_bytes": 900_000_000,
                },
                "measurement_scope": (
                    "through_debug_publish_before_completion_artifact.v1"
                ),
                "excluded_operations": [
                    "completion_timing_artifact_publish",
                    "completion_progress_callback",
                ],
            },
        )
        write_json(
            out_dir / "transcript.raw.json",
            {"segments": [], "config": {"timing": {"cold_start": cold_start}}},
        )
        write_json(
            out_dir / "alignment_report.json",
            {
                "source_percentages": {"forced": 99.0, "estimated": 1.0},
                "missing_timestamp_count": 0,
                "nonmonotonic_timestamp_count": 0,
                "metadata": {"cold_start": cold_start},
            },
        )
        write_json(
            out_dir / "diarization.json",
            {
                "metadata": {
                    "mps_synchronized_before": candidate,
                    "mps_synchronized_after": candidate,
                    "timing": {"cold_start": cold_start},
                }
            },
        )
        write_json(
            out_dir / "qa_report.json",
            {
                "flags": [],
                "summary": {
                    "detected_speaker_count": 2,
                    "percent_words_uncertain": 5.0,
                    "percent_words_assigned_to_speakers": 98.0,
                    "speaker_flapping_score": 0.1,
                    "non_monotonic_timestamp_count": 0,
                },
            },
        )
        return object()


def _speaker_validator(job_dir: Path, expected_speakers: int) -> object:
    write_json(
        job_dir / "speaker_validation_report.json",
        {
            "expected_speakers": expected_speakers,
            "unique_speakers": 2,
            "speaker_count_mismatch": False,
            "speaker_flapping_score": 0.1,
            "percent_words_assigned_to_speakers": 98.0,
            "percent_words_marked_uncertain": 5.0,
        },
    )
    return object()


def _execute(
    request: AppleSiliconCanaryRequest,
    transcriber: _FakeTranscriber,
    *,
    condition_probe: Any = _condition,
) -> Any:
    model_path = request.config.asr.mlx_model_path
    assert model_path is not None
    model_path.mkdir(parents=True, exist_ok=True)
    (model_path / "config.json").write_text("{}", encoding="utf-8")
    (model_path / "weights.npz").write_bytes(b"fake model checkpoint")
    return run_apple_silicon_canary(
        request,
        transcribe=transcriber,
        speaker_validator=_speaker_validator,
        backend_factory=_backend_factory,
        capabilities=_host(),
        version_resolver=_version,
        code_revision=_REVISION,
        condition_probe=condition_probe,
    )


def test_dry_run_requires_no_media_models_packages_or_output_directory(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, dry_run=True)

    result = run_apple_silicon_canary(
        request,
        capabilities=_host(mlx_available=False, mlx_whisper_available=False),
        version_resolver=lambda _package: None,
        code_revision=_REVISION,
        condition_probe=_condition,
    )

    assert result.status == "planned"
    assert result.evidence is None
    assert result.decision is None
    assert result.plan.execution_required is False
    assert result.plan.input_media_sha256 is None
    assert result.plan.cold_start_runs == 1
    assert result.plan.warmup_runs == 1
    assert result.plan.measured_runs == 3
    assert result.plan.initial_host_condition["thermal_state"] == "nominal"
    assert result.plan.models[0].backend_version is None
    assert not request.output_dir.exists()
    baseline, candidate = result.plan.variants
    assert baseline.source_config_sha256 != candidate.source_config_sha256
    assert baseline.run_directories[0] == "runs/cpu_baseline/cold-start-001"
    assert candidate.run_directories[-1] == "runs/mps_candidate/measured-003"


def test_request_rejects_missing_exact_speaker_count(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        AppleSiliconCanaryRequest.model_validate(
            {
                "input_file": tmp_path / "input.wav",
                "output_dir": tmp_path / "out",
                "config": _config(tmp_path),
            }
        )


def test_runner_rejects_roster_count_that_does_not_match_exact_speaker_count(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path, dry_run=True)
    roster = tmp_path / "private-roster.json"
    write_json(
        roster,
        {
            "schema_version": "speaker_identity_roster.v1",
            "expected_speaker_count": 1,
            "participants": [{"name": "Private participant", "expected": True}],
        },
    )
    request.roster_path = roster

    with pytest.raises(LTMError, match="does not match --num-speakers"):
        run_apple_silicon_canary(
            request,
            capabilities=_host(),
            version_resolver=lambda _package: None,
            code_revision=_REVISION,
            condition_probe=_condition,
        )


@pytest.mark.parametrize(
    "selection",
    ("en", "english", "auto", "facebook/wav2vec2-base-960h"),
)
def test_canary_canonicalizes_alignment_selector_and_language(
    tmp_path: Path,
    selection: str,
) -> None:
    request = _request(tmp_path, dry_run=True)
    request.options.alignment_model = selection
    request.config.alignment.language = None

    baseline, candidate = _canary_configs(request)

    assert baseline.alignment.model == "en"
    assert baseline.alignment.language == "en"
    assert candidate.alignment.model == "en"
    assert candidate.alignment.language == "en"


@pytest.mark.parametrize(
    "host",
    [
        _host(system="Linux", is_apple_silicon=False, is_native_apple_silicon=False),
        _host(
            process_architecture="x86_64",
            is_native_arm64=False,
            is_native_apple_silicon=False,
            is_rosetta=True,
        ),
        _host(mps_available=False),
    ],
)
def test_runner_fails_closed_on_ineligible_host(
    tmp_path: Path,
    host: HostCapabilities,
) -> None:
    with pytest.raises(LTMError, match="native arm64|available MPS"):
        run_apple_silicon_canary(
            _request(tmp_path, dry_run=True),
            capabilities=host,
            version_resolver=lambda _package: None,
            code_revision=_REVISION,
        )


def test_runner_rejects_hidden_pytorch_mps_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")

    with pytest.raises(LTMError, match="PYTORCH_ENABLE_MPS_FALLBACK"):
        run_apple_silicon_canary(
            _request(tmp_path, dry_run=True),
            capabilities=_host(),
            version_resolver=lambda _package: None,
            code_revision=_REVISION,
        )


def test_runner_rejects_noncanonical_mps_experiment_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTORCH_MPS_FAST_MATH", "1")

    with pytest.raises(LTMError, match="PYTORCH_MPS_FAST_MATH"):
        run_apple_silicon_canary(
            _request(tmp_path, dry_run=True),
            capabilities=_host(),
            version_resolver=lambda _package: None,
            code_revision=_REVISION,
            condition_probe=_condition,
        )


def test_runner_executes_bound_warmups_and_measured_runs_and_promotes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.config.qa.enabled = False
    request.config.qa.targeted_redecode = True
    request.input_file.write_bytes(b"private media bytes")
    transcriber = _FakeTranscriber()

    result = _execute(request, transcriber)

    assert result.status == "completed"
    assert result.evidence is not None
    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.MPS_PROMOTED
    assert result.decision.diarization_speedup_ratio == pytest.approx(0.2)
    assert len(result.evidence.baseline_runs) == 5
    assert len(result.evidence.candidate_runs) == 5
    assert all(
        run.status is CanaryRunStatus.COMPLETED
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )
    assert len(transcriber.calls) == 10
    assert {
        call[1].diarization.device for call in transcriber.calls
    } == {"cpu", "mps"}
    assert all(call[1].offline for call in transcriber.calls)
    assert all(call[1].asr.backend == "mlx-whisper" for call in transcriber.calls)
    assert all(call[1].asr.mlx_fp16 for call in transcriber.calls)
    assert all(call[1].asr.temperature == 0.0 for call in transcriber.calls)
    assert all(call[1].asr.initial_prompt is None for call in transcriber.calls)
    assert all(call[1].asr.glossary_file is None for call in transcriber.calls)
    assert all(call[1].alignment.device == "cpu" for call in transcriber.calls)
    assert all(call[1].diarization.num_speakers == 2 for call in transcriber.calls)
    assert all(call[1].qa.enabled for call in transcriber.calls)
    assert all(not call[1].qa.targeted_redecode for call in transcriber.calls)
    assert all(
        call[1].runtime.model_lifecycle == "persistent"
        for call in transcriber.calls
    )
    assert all(
        run.processing_timestamp is not None and len(run.artifacts) == 7
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )
    asr_binding = next(
        model
        for model in result.evidence.baseline_runs[0].binding.models
        if model.role == "asr"
    )
    assert asr_binding.revision is None
    assert asr_binding.revision_verified is False
    assert asr_binding.checkpoint_sha256 is not None
    assert {
        row.package for row in result.evidence.baseline_runs[0].binding.runtime_versions
    } >= {"mlx", "mlx-metal", "torch", "torchaudio"}
    assert (
        request.output_dir
        / "runs"
        / "mps_candidate"
        / "measured-003"
        / "canary_run_manifest.json"
    ).is_file()
    assert (request.output_dir / "canary_evidence.json").is_file()
    assert (request.output_dir / "canary_decision.json").is_file()
    assert (request.output_dir / "canary_result.json").is_file()


def test_explicit_override_cannot_hide_candidate_cpu_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path, explicit_mps_override=True)
    request.input_file.write_bytes(b"private media bytes")

    result = _execute(request, _FakeTranscriber(candidate_fallback=True))

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.MPS_FELL_BACK_TO_CPU
    assert result.decision.automatic_promotion_eligible is False


def test_mismatched_source_binding_is_preserved_as_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")

    result = _execute(request, _FakeTranscriber(mismatch_source_hash=True))

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert result.evidence is not None
    assert all(
        run.status is CanaryRunStatus.INCONCLUSIVE
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )
    assert all(
        run.status_detail == "evidence_invalid:ValueError"
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )


def test_candidate_neural_component_cpu_evidence_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")

    result = _execute(request, _FakeTranscriber(candidate_component_cpu=True))

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert result.evidence is not None
    assert all(
        run.status is CanaryRunStatus.INCONCLUSIVE
        for run in result.evidence.candidate_runs
    )


def test_missing_pyannote_component_evidence_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")

    result = _execute(request, _FakeTranscriber(include_component_devices=False))

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert result.evidence is not None
    assert all(
        run.status is CanaryRunStatus.INCONCLUSIVE
        and run.status_detail == "evidence_invalid:ValueError"
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )


def test_partial_pyannote_component_evidence_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")

    class PartialComponentTranscriber(_FakeTranscriber):
        def __call__(
            self,
            input_file: Path,
            out_dir: Path,
            config: PipelineConfig,
            **kwargs: Any,
        ) -> object:
            result = super().__call__(
                input_file,
                out_dir,
                config,
                **kwargs,
            )
            metadata_path = out_dir / "run_metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            del metadata["diarization_component_devices"]["embedding"]
            write_json(metadata_path, metadata)
            return result

    result = _execute(request, PartialComponentTranscriber())

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert result.evidence is not None
    assert all(
        run.status is CanaryRunStatus.INCONCLUSIVE
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )


def test_missing_mlx_metal_evidence_is_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")

    result = _execute(request, _FakeTranscriber(mlx_metal_available=False))

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert result.evidence is not None
    assert all(
        run.status is CanaryRunStatus.INCONCLUSIVE
        and run.status_detail == "evidence_invalid:ValueError"
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )


def test_serious_thermal_state_invalidates_completed_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")

    result = _execute(
        request,
        _FakeTranscriber(),
        condition_probe=lambda: {
            **_condition(),
            "thermal_state": "serious",
        },
    )

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert result.evidence is not None
    assert all(
        run.status is CanaryRunStatus.INCONCLUSIVE
        and run.status_detail == "serious_thermal_state_invalidated_run"
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )
    manifest = json.loads(
        (
            request.output_dir
            / "runs"
            / "cpu_baseline"
            / "measured-001"
            / "canary_run_manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["host_condition_before"]["thermal_state"] == "serious"
    assert manifest["host_condition_after"]["thermal_state"] == "serious"


def test_stable_power_mode_is_sufficient_when_low_power_flag_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")

    result = _execute(
        request,
        _FakeTranscriber(),
        condition_probe=lambda: {
            **_condition(),
            "low_power_mode": None,
            "power_mode": 2,
        },
    )

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.MPS_PROMOTED


def test_power_source_change_invalidates_canary_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")
    calls = 0

    def changing_condition() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            **_condition(),
            "source": "AC Power" if calls == 1 else "Battery Power",
        }

    result = _execute(
        request,
        _FakeTranscriber(),
        condition_probe=changing_condition,
    )

    assert result.decision is not None
    assert result.decision.outcome is CanaryOutcome.BENCHMARK_INCONCLUSIVE
    assert result.evidence is not None
    assert all(
        run.status is CanaryRunStatus.INCONCLUSIVE
        and run.status_detail == "host_condition_changed:source"
        for run in (
            *result.evidence.baseline_runs,
            *result.evidence.candidate_runs,
        )
    )


def test_existing_output_path_is_rejected_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)
    request = _request(tmp_path)
    request.input_file.write_bytes(b"private media bytes")
    request.output_dir.mkdir()

    with pytest.raises(LTMError, match="must not already exist"):
        _execute(request, _FakeTranscriber())
