# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""pyannote community diarization backend with gated model handling."""

from __future__ import annotations

import importlib.util
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from local_transcript_max.config import PipelineConfig, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError, ModelCacheError
from local_transcript_max.models import require_model
from local_transcript_max.schemas import (
    DiarizationResult,
    DiarizationTurn,
    ModelMetadata,
    StageTiming,
)

PYANNOTE_MPS_EXPERIMENTAL_WARNING = (
    "pyannote_mps_experimental: requested=mps actual=mps "
    "reason=torch_mps_diarization_requires_same_media_benchmark_validation"
)


class PyannoteDiarizationBackend:
    name = "pyannote"

    def __init__(self) -> None:
        self._pipeline_cache: dict[
            tuple[str, str | None],
            tuple[Any, str | None, tuple[str, ...]],
        ] = {}
        self._audio_cache: dict[tuple[str, int, int], dict[str, object]] = {}

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
        device = resolve_diarization_device(config.diarization.device)
        pipeline, actual_device, device_warnings = self._pipeline(
            model_path=model_path,
            requested_device=device,
            pipeline_factory=Pipeline,
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
        diarization = pipeline(diarization_input, **kwargs)
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
        return DiarizationResult(
            turns=turns,
            exclusive_turns=exclusive,
            raw={"kwargs": serializable_constraints},
            model=ModelMetadata(
                role="diarization",
                model_id=entry.model_id,
                local_path=str(model_path),
                gated=entry.gated,
                present=True,
            ),
            metadata={
                "backend": "pyannote",
                "device": actual_device,
                "requested_device": config.diarization.device,
                "speaker_constraints": serializable_constraints,
                "overlap_support": True,
                "exclusive_diarization_support": bool(exclusive),
                "pyannote_metrics_enabled": os.getenv("PYANNOTE_METRICS_ENABLED"),
                "progress_hook": telemetry_hook.summary() if telemetry_hook is not None else None,
            },
            warnings=list(device_warnings),
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
                actual_device = "cpu"
                warnings.append(
                    "pyannote_device_fallback:"
                    f" requested={requested_device} actual=cpu reason={exc.__class__.__name__}"
                )
        result = (pipeline, actual_device, tuple(warnings))
        self._pipeline_cache[key] = result
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


def ensure_pyannote_privacy_defaults() -> None:
    os.environ["PYANNOTE_METRICS_ENABLED"] = "false"


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
