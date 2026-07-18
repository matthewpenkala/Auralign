# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""pyannote community diarization backend with gated model handling."""

from __future__ import annotations

import importlib.util
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from local_transcript_max.config import PipelineConfig, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError, ModelCacheError
from local_transcript_max.models import require_model
from local_transcript_max.runtime_policy import resolve_runtime_policy
from local_transcript_max.schemas import (
    DiarizationResult,
    DiarizationTurn,
    ModelMetadata,
    StageTiming,
)

PYANNOTE_MPS_EXPERIMENTAL_WARNING = (
    "pyannote_mps_experimental: requested=mps "
    "reason=torch_mps_diarization_requires_same_media_benchmark_validation"
)

FallbackStage = Literal["move", "inference"]


@dataclass(frozen=True)
class _PipelineDeviceState:
    fallback_stage: FallbackStage | None = None
    fallback_reason: str | None = None


class PyannoteDiarizationBackend:
    name = "pyannote"

    def __init__(self) -> None:
        self._pipeline_cache: dict[
            tuple[str, str | None],
            tuple[Any, str | None, tuple[str, ...]],
        ] = {}
        self._pipeline_device_states: dict[
            tuple[str, str | None],
            _PipelineDeviceState,
        ] = {}
        self._audio_cache: dict[tuple[str, int, int], dict[str, object]] = {}

    def clear_runtime_cache(self) -> None:
        """Release pipeline and decoded-audio references for per-job lifecycle."""

        self._pipeline_cache.clear()
        self._pipeline_device_states.clear()
        self._audio_cache.clear()

    def diarize(self, audio_path: Path, config: PipelineConfig) -> DiarizationResult:
        return self._diarize(audio_path, config, progress=None)

    def diarize_with_progress(
        self,
        audio_path: Path,
        config: PipelineConfig,
        progress: Callable[[dict[str, Any]], None],
    ) -> DiarizationResult:
        return self._diarize(audio_path, config, progress=progress)

    def _diarize(
        self,
        audio_path: Path,
        config: PipelineConfig,
        *,
        progress: Callable[[dict[str, Any]], None] | None,
    ) -> DiarizationResult:
        if not config.diarization.enabled:
            return DiarizationResult(turns=[], raw={"disabled": True})
        ensure_pyannote_privacy_defaults()
        if importlib.util.find_spec("pyannote.audio") is None:
            raise BackendUnavailableError(
                "pyannote.audio is not installed. Run: uv sync --extra diarization"
            )
        from pyannote.audio import Pipeline

        entry = resolve_model_entry("diarization", config.diarization.model)
        if entry is None:
            raise ModelCacheError(f"Unknown diarization model '{config.diarization.model}'.")
        model_path = require_model(
            "diarization", config.diarization.model, config.model_cache, offline=config.offline
        )
        start = time.perf_counter()
        device = resolve_runtime_policy(config).diarization.resolved_device
        pipeline_cache_key = (str(model_path), device)
        pipeline_was_cached = pipeline_cache_key in self._pipeline_cache
        model_load_started = time.perf_counter()
        pipeline, actual_device, device_warnings = self._pipeline(
            model_path=model_path,
            requested_device=device,
            pipeline_factory=Pipeline,
        )
        model_load_seconds = (
            0.0
            if pipeline_was_cached
            else time.perf_counter() - model_load_started
        )
        device_state = self._pipeline_device_states.get(
            (str(model_path), device),
            _PipelineDeviceState(),
        )
        fallback_stage = device_state.fallback_stage
        fallback_reason = device_state.fallback_reason
        mps_inference_attempted = False
        mps_synchronized_before = False
        mps_synchronized_after = False
        mps_attempt_seconds: float | None = None
        cpu_retry_seconds: float | None = None
        cpu_fallback_model_load_seconds: float | None = None
        warnings = list(device_warnings)
        pytorch_mps_fallback_enabled = mps_cpu_fallback_environment_enabled()
        if device == "mps" and pytorch_mps_fallback_enabled:
            warnings.append(
                "pyannote_mps_hidden_fallback_enabled: "
                "PYTORCH_ENABLE_MPS_FALLBACK permits per-operation CPU fallback; "
                "this run is not decisive MPS benchmark evidence"
            )
        kwargs: dict[str, Any] = {}
        if config.diarization.num_speakers is not None:
            kwargs["num_speakers"] = config.diarization.num_speakers
        if config.diarization.min_speakers is not None:
            kwargs["min_speakers"] = config.diarization.min_speakers
        if config.diarization.max_speakers is not None:
            kwargs["max_speakers"] = config.diarization.max_speakers
        telemetry_hook = PyannoteProgressTelemetryHook(progress) if progress else None
        if telemetry_hook is not None:
            kwargs["hook"] = telemetry_hook
        diarization_input = self._audio(audio_path)
        inference_started = time.perf_counter()
        executed_pipeline = pipeline
        if device == "mps" and actual_device != "cpu":
            mps_inference_attempted = True
            mps_synchronized_before = synchronize_torch_mps()
            mps_started = time.perf_counter()
            try:
                diarization = pipeline(diarization_input, **kwargs)
            except Exception as exc:
                mps_synchronized_after = synchronize_torch_mps()
                mps_attempt_seconds = time.perf_counter() - mps_started
                if not is_mps_compatibility_error(exc):
                    raise
                cpu_load_started = time.perf_counter()
                cpu_pipeline, cpu_device, cpu_warnings = self._pipeline(
                    model_path=model_path,
                    requested_device="cpu",
                    pipeline_factory=Pipeline,
                )
                cpu_fallback_model_load_seconds = time.perf_counter() - cpu_load_started
                if cpu_device != "cpu":
                    raise BackendUnavailableError(
                        "pyannote did not prepare the required clean CPU fallback pipeline"
                    ) from exc
                # Retry exactly once. Any CPU inference error must propagate.
                cpu_retry_started = time.perf_counter()
                diarization = cpu_pipeline(diarization_input, **kwargs)
                executed_pipeline = cpu_pipeline
                cpu_retry_seconds = time.perf_counter() - cpu_retry_started
                actual_device = "cpu"
                fallback_stage = "inference"
                fallback_reason = exc.__class__.__name__
                warnings.extend(cpu_warnings)
                warnings.append(
                    device_fallback_warning(
                        requested_device="mps",
                        stage=fallback_stage,
                        reason=fallback_reason,
                    )
                )
                cached_result = (cpu_pipeline, actual_device, tuple(dict.fromkeys(warnings)))
                cache_key = (str(model_path), device)
                self._pipeline_cache[cache_key] = cached_result
                self._pipeline_device_states[cache_key] = _PipelineDeviceState(
                    fallback_stage=fallback_stage,
                    fallback_reason=fallback_reason,
                )
            else:
                mps_synchronized_after = synchronize_torch_mps()
                mps_attempt_seconds = time.perf_counter() - mps_started
                actual_device = "mps"
        else:
            diarization = pipeline(diarization_input, **kwargs)
        inference_seconds = time.perf_counter() - inference_started
        serializable_constraints = {key: value for key, value in kwargs.items() if key != "hook"}
        primary_annotation = getattr(diarization, "speaker_diarization", diarization)
        turns = mark_overlaps(annotation_to_turns(primary_annotation, source="pyannote"))
        exclusive = []
        if hasattr(diarization, "exclusive_speaker_diarization"):
            exclusive = annotation_to_turns(
                diarization.exclusive_speaker_diarization,
                source="pyannote",
            )
        duration = time.perf_counter() - start
        component_devices = pyannote_neural_component_devices(
            executed_pipeline,
            fallback_occurred=fallback_stage is not None,
        )
        return DiarizationResult(
            turns=turns,
            exclusive_turns=exclusive,
            raw={"kwargs": serializable_constraints},
            model=ModelMetadata(
                role="diarization",
                model_id=entry.model_id,
                local_path=str(model_path),
                revision=entry.revision,
                revision_verified=True,
                gated=entry.gated,
                present=True,
            ),
            metadata={
                "backend": "pyannote",
                "device": actual_device,
                "requested_device": config.diarization.device,
                "resolved_device": device,
                "actual_device": actual_device,
                "component_devices": {
                    **component_devices,
                    "clustering_and_postprocessing": {
                        "kind": "non_neural",
                        "actual_device": "cpu",
                    },
                    "overall": (
                        "mixed"
                        if actual_device in {"cuda", "mps"}
                        else actual_device
                    ),
                },
                "mps_inference_attempted": mps_inference_attempted,
                "mps_synchronized_before": mps_synchronized_before,
                "mps_synchronized_after": mps_synchronized_after,
                "pytorch_mps_fallback_enabled": pytorch_mps_fallback_enabled,
                "device_fallback": fallback_stage is not None,
                "fallback_stage": fallback_stage,
                "fallback_reason": fallback_reason,
                "speaker_constraints": serializable_constraints,
                "overlap_support": True,
                "exclusive_diarization_support": bool(exclusive),
                "pyannote_metrics_enabled": os.getenv("PYANNOTE_METRICS_ENABLED"),
                "progress_hook": telemetry_hook.summary() if telemetry_hook is not None else None,
                "timing": {
                    "model_load_seconds": model_load_seconds,
                    "inference_seconds": inference_seconds,
                    "cold_start": not pipeline_was_cached,
                    "mps_attempt_seconds": mps_attempt_seconds,
                    "cpu_fallback_model_load_seconds": cpu_fallback_model_load_seconds,
                    "cpu_retry_seconds": cpu_retry_seconds,
                },
            },
            warnings=list(dict.fromkeys(warnings)),
            timing=StageTiming(name="diarization", duration_seconds=duration),
        )

    def _pipeline(
        self,
        *,
        model_path: Path,
        requested_device: str | None,
        pipeline_factory: Any,
    ) -> tuple[Any, str | None, tuple[str, ...]]:
        key = (str(model_path), requested_device)
        cached = self._pipeline_cache.get(key)
        if cached is not None:
            return cached
        pipeline_obj = pipeline_factory.from_pretrained(str(model_path))
        if pipeline_obj is None:
            raise BackendUnavailableError(f"Could not load pyannote pipeline from {model_path}")
        pipeline: Any = pipeline_obj
        actual_device = requested_device
        warnings: list[str] = []
        if requested_device == "mps":
            warnings.append(PYANNOTE_MPS_EXPERIMENTAL_WARNING)
        if requested_device:
            try:
                import torch

                pipeline.to(torch.device(requested_device))
            except Exception as exc:
                if requested_device in {"cpu", "cuda"}:
                    raise BackendUnavailableError(
                        "pyannote could not move the pinned local pipeline to the required "
                        f"{requested_device} device: {exc.__class__.__name__}"
                    ) from exc
                if requested_device != "mps" or not is_mps_compatibility_error(exc):
                    raise
                # A failed MPS transfer may leave only part of a pipeline moved.
                # Discard it and construct a clean CPU instance rather than
                # relabeling the possibly mixed-device object as CPU.
                del pipeline
                cpu_pipeline = pipeline_factory.from_pretrained(str(model_path))
                if cpu_pipeline is None:
                    raise BackendUnavailableError(
                        f"Could not load clean CPU pyannote pipeline from {model_path}"
                    ) from exc
                try:
                    cpu_pipeline.to(torch.device("cpu"))
                except Exception as cpu_exc:
                    raise BackendUnavailableError(
                        "pyannote could not move a fresh fallback pipeline to the required "
                        f"cpu device: {cpu_exc.__class__.__name__}"
                    ) from cpu_exc
                pipeline = cpu_pipeline
                actual_device = "cpu"
                fallback_reason = exc.__class__.__name__
                warnings.append(
                    device_fallback_warning(
                        requested_device=requested_device,
                        stage="move",
                        reason=fallback_reason,
                    )
                )
                cpu_key = (str(model_path), "cpu")
                cpu_result = (pipeline, "cpu", ())
                self._pipeline_cache[cpu_key] = cpu_result
                self._pipeline_device_states[cpu_key] = _PipelineDeviceState()
                self._pipeline_device_states[key] = _PipelineDeviceState(
                    fallback_stage="move",
                    fallback_reason=fallback_reason,
                )
            else:
                # Moving to MPS prepares a candidate pipeline. It is not
                # evidence of actual MPS execution until inference succeeds.
                if requested_device == "mps":
                    actual_device = None
        result = (pipeline, actual_device, tuple(warnings))
        self._pipeline_cache[key] = result
        self._pipeline_device_states.setdefault(key, _PipelineDeviceState())
        return result

    def _audio(self, audio_path: Path) -> dict[str, object]:
        path = audio_path.expanduser().resolve()
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        cached = self._audio_cache.get(key)
        if cached is not None:
            return cached
        audio = load_audio_for_pyannote(path)
        self._audio_cache.clear()
        self._audio_cache[key] = audio
        return audio


def pyannote_neural_component_devices(
    pipeline: object,
    *,
    fallback_occurred: bool,
) -> dict[str, dict[str, object]]:
    """Read the actual device retained by each Community-1 neural component."""

    rows: dict[str, dict[str, object]] = {}
    for name, attribute in (
        ("segmentation", "_segmentation"),
        ("embedding", "_embedding"),
    ):
        component = getattr(pipeline, attribute, None)
        device = getattr(component, "device", None)
        actual_device = str(device).split(":", 1)[0].lower() if device is not None else None
        rows[name] = {
            "kind": "neural",
            "actual_device": actual_device,
            "fallback_occurred": fallback_occurred,
            "verification": (
                "component_device_attribute_and_successful_pipeline_call"
                if actual_device is not None
                else "component_device_unavailable"
            ),
        }
    return rows


def ensure_pyannote_privacy_defaults() -> None:
    os.environ["PYANNOTE_METRICS_ENABLED"] = "false"


def mps_cpu_fallback_environment_enabled() -> bool:
    value = os.getenv("PYTORCH_ENABLE_MPS_FALLBACK", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def synchronize_torch_mps() -> bool:
    """Synchronize queued MPS work at a timing boundary when supported."""

    try:
        import torch

        synchronize = getattr(getattr(torch, "mps", None), "synchronize", None)
        if not callable(synchronize):
            return False
        synchronize()
        return True
    except (ImportError, RuntimeError):
        return False


def device_fallback_warning(
    *,
    requested_device: str,
    stage: FallbackStage,
    reason: str,
) -> str:
    return (
        "pyannote_device_fallback:"
        f" requested={requested_device} actual=cpu stage={stage} reason={reason}"
    )


def is_mps_compatibility_error(exc: Exception) -> bool:
    """Recognize bounded Torch MPS unsupported-operation failures."""

    if not isinstance(exc, (RuntimeError, NotImplementedError)):
        return False
    message = " ".join(str(exc).lower().split())
    markers = (
        "not implemented for the mps device",
        "not currently implemented for the mps device",
        "not implemented for 'mps'",
        "mps does not support",
        "mps backend does not support",
        "unsupported operation on mps",
        "placeholder storage has not been allocated on mps device",
    )
    return any(marker in message for marker in markers)


class PyannoteProgressTelemetryHook:
    _STEP_RANGES = {
        "segmentation": (0.0, 50.0),
        "embeddings": (50.0, 99.0),
    }

    def __init__(self, progress: Callable[[dict[str, Any]], None]) -> None:
        self.progress = progress
        self.last_fraction = 0.0
        self.event_count = 0
        self.steps: set[str] = set()

    def __call__(
        self,
        step_name: str,
        step_artifact: object | None = None,
        file: object | None = None,
        total: int | None = None,
        completed: int | None = None,
    ) -> None:
        del step_artifact, file
        fraction = self._fraction(step_name, total, completed)
        if fraction < self.last_fraction:
            fraction = self.last_fraction
        self.last_fraction = fraction
        self.steps.add(step_name)
        self.event_count += 1
        self.progress(
            {
                "step": step_name,
                "completed": completed,
                "total": total,
                "fraction": round(fraction, 6),
            }
        )

    def _fraction(self, step_name: str, total: int | None, completed: int | None) -> float:
        if total is None or completed is None or total <= 0:
            return self.last_fraction
        start, end = self._STEP_RANGES.get(step_name, (0.0, 99.0))
        percent = start + min(completed / total, 1.0) * (end - start)
        return percent / 100.0

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "event_count": self.event_count,
            "steps": sorted(self.steps),
            "last_fraction": round(self.last_fraction, 6),
        }


def annotation_to_turns(annotation: object, *, source: str) -> list[DiarizationTurn]:
    turns: list[DiarizationTurn] = []
    for segment, _track, label in annotation.itertracks(yield_label=True):  # type: ignore[attr-defined]
        turns.append(
            DiarizationTurn(
                start=float(segment.start),
                end=float(segment.end),
                speaker=str(label),
                source=source,
            )
        )
    return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))


def mark_overlaps(turns: list[DiarizationTurn]) -> list[DiarizationTurn]:
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    overlap_indexes: set[int] = set()
    for index, left in enumerate(ordered):
        for other_index, right in enumerate(ordered[index + 1 :], start=index + 1):
            if right.start >= left.end:
                break
            if min(left.end, right.end) > max(left.start, right.start):
                overlap_indexes.add(index)
                overlap_indexes.add(other_index)
    return [
        turn.model_copy(update={"overlap": True}) if index in overlap_indexes else turn
        for index, turn in enumerate(ordered)
    ]


def resolve_diarization_device(requested: str) -> str | None:
    if requested in {"cpu", "cuda", "mps"}:
        return requested
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    except Exception:
        return "cpu"


def load_audio_for_pyannote(audio_path: Path) -> dict[str, object]:
    import torchaudio

    waveform, sample_rate = torchaudio.load(str(audio_path))
    return {"waveform": waveform, "sample_rate": sample_rate}
