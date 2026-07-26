# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""MLX Whisper ASR backend with local-only model path enforcement."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import time
import wave
from pathlib import Path
from typing import Any

from local_transcript_max.config import ModelEntry, PipelineConfig, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError, ModelCacheError, OfflineModeError
from local_transcript_max.models import (
    ModelStatus,
    model_checkpoint_sha256,
    status_for,
    status_for_path,
)
from local_transcript_max.schemas import (
    AlignedWord,
    ASRResult,
    ASRSegment,
    ModelMetadata,
    StageTiming,
    VADRegion,
)

MLX_SAMPLE_RATE_WARNING = "mlx_vad_windowed_requires_seekable_wav"
MLX_INTERNAL_BOUNDARY_CONTEXT_SECONDS = 1.0


class MlxWhisperBackend:
    name = "mlx-whisper"

    def __init__(self) -> None:
        self._checkpoint_binding: tuple[Path, str] | None = None

    def clear_runtime_cache(self) -> None:
        """Release the pinned upstream process model holder when explicitly asked."""

        self._checkpoint_binding = None
        holder_module = sys.modules.get("mlx_whisper.transcribe")
        holder = getattr(holder_module, "ModelHolder", None)
        if holder is None:
            return
        holder.model = None
        holder.model_path = None
        if hasattr(holder, "_ltm_runtime_key"):
            holder._ltm_runtime_key = None

    def transcribe(
        self,
        audio_path: Path,
        config: PipelineConfig,
        *,
        vad_regions: list[VADRegion] | None = None,
    ) -> ASRResult:
        if importlib.util.find_spec("mlx_whisper") is None:
            raise BackendUnavailableError("mlx-whisper is not installed. Run: uv sync --extra mlx")

        import mlx_whisper

        entry = mlx_model_entry(config)
        model_path = resolve_mlx_model_path(config)
        status = mlx_model_status(config)
        checkpoint_sha256 = self._checkpoint_sha256(entry, model_path)
        initial_prompt = config.asr.initial_prompt
        if config.asr.glossary_file:
            initial_prompt = (
                (initial_prompt or "") + "\n" + config.asr.glossary_file.read_text(encoding="utf-8")
            )
        warnings = unsupported_option_warnings(config)
        transcribe_kwargs: dict[str, Any] = {
            "path_or_hf_repo": str(model_path),
            "verbose": None,
            "language": config.asr.language,
            "task": config.asr.task,
            "best_of": config.asr.best_of,
            "temperature": tuple(config.asr.temperature)
            if isinstance(config.asr.temperature, list)
            else config.asr.temperature,
            "compression_ratio_threshold": config.asr.compression_ratio_threshold,
            "logprob_threshold": config.asr.logprob_threshold,
            "no_speech_threshold": config.asr.no_speech_threshold,
            "condition_on_previous_text": config.asr.condition_on_previous_text,
            "initial_prompt": initial_prompt,
            "word_timestamps": config.asr.word_timestamps,
            "fp16": config.asr.mlx_fp16,
        }

        runtime_package = getattr(mlx_whisper, "__path__", None) is not None
        if runtime_package:
            reset_mlx_peak_memory(load_if_needed=True)
            mlx_memory_before = mlx_runtime_evidence()
        else:
            mlx_memory_before = mlx_runtime_evidence(load_if_needed=False)
        model_timing = prepare_mlx_model(
            model_path,
            fp16=config.asr.mlx_fp16,
        )
        synchronize_mlx()
        inference_started = time.perf_counter()
        if config.asr.mlx_vad_windowed:
            payload, window_warnings = transcribe_vad_windows(
                mlx_whisper,
                audio_path,
                transcribe_kwargs,
                vad_regions or [],
                window_seconds=config.asr.mlx_vad_window_seconds,
                padding_seconds=config.asr.mlx_vad_window_padding_seconds,
            )
            warnings.extend(window_warnings)
            inference_mode = "mlx-vad-windowed" if payload.get("vad_windowed") else "mlx"
        else:
            payload = mlx_whisper.transcribe(str(audio_path), **transcribe_kwargs)
            inference_mode = "mlx"
        synchronize_mlx()
        inference_duration = time.perf_counter() - inference_started
        mlx_memory_after = mlx_runtime_evidence(
            load_if_needed=runtime_package,
        )
        model_load_value = model_timing["model_load_seconds"]
        model_load_duration = (
            float(model_load_value)
            if isinstance(model_load_value, int | float) and not isinstance(model_load_value, bool)
            else 0.0
        )
        duration = inference_duration + model_load_duration
        segments, raw_segments, timestamp_warnings = segments_from_mlx_payload(
            payload,
            require_word_timestamps=config.asr.word_timestamps,
        )
        warnings.extend(timestamp_warnings)

        return ASRResult(
            segments=segments,
            language=_str_or_none(payload.get("language")) if isinstance(payload, dict) else None,
            raw={
                "segments": raw_segments,
                "mlx": (
                    {key: value for key, value in payload.items() if key != "segments"}
                    if isinstance(payload, dict)
                    else {}
                ),
            },
            model=ModelMetadata(
                role="asr-mlx",
                model_id=entry.model_id,
                local_path=str(model_path),
                revision=entry.revision if status.revision_verified else None,
                revision_verified=status.revision_verified,
                checkpoint_sha256=checkpoint_sha256,
                gated=entry.gated,
                present=True,
            ),
            config={
                **config.asr.model_dump(mode="json"),
                "backend": "mlx-whisper",
                "device": "mlx",
                "compute_type": mlx_compute_type(config),
                "inference_mode": inference_mode,
                "model_path": str(model_path),
                "runtime_downloads_disabled": True,
                "runtime_device": mlx_memory_after.get("default_device"),
                "metal_available": mlx_memory_after.get("metal_available"),
                "memory_before": mlx_memory_before,
                "memory_after": mlx_memory_after,
                "vad_window_count": len(payload.get("vad_windows", []))
                if isinstance(payload, dict)
                else 0,
                "timing": {
                    **model_timing,
                    "inference_seconds": inference_duration,
                    "lifecycle": "mlx_whisper_process_model_holder",
                },
            },
            warnings=sorted(set(warnings)),
            timing=StageTiming(name="asr", duration_seconds=duration),
        )

    def _checkpoint_sha256(self, entry: ModelEntry, model_path: Path) -> str:
        resolved = model_path.expanduser().resolve()
        if self._checkpoint_binding is None or self._checkpoint_binding[0] != resolved:
            self._checkpoint_binding = (
                resolved,
                model_checkpoint_sha256(entry, resolved),
            )
        return self._checkpoint_binding[1]


def prepare_mlx_model(
    model_path: Path,
    *,
    fp16: bool,
    mlx_core: Any | None = None,
    model_holder: Any | None = None,
) -> dict[str, object]:
    """Warm the version-pinned MLX model holder and separate load from inference.

    Older or mocked ``mlx-whisper`` surfaces may not expose ``ModelHolder``.
    In that case the public transcribe call remains authoritative and load time
    is honestly reported as inseparable.
    """

    if mlx_core is None or model_holder is None:
        try:
            model_holder_module = importlib.import_module("mlx_whisper.transcribe")
            model_holder = model_holder_module.ModelHolder
            mlx_core = importlib.import_module("mlx.core")
        except (ImportError, AttributeError):
            return {
                "model_load_seconds": None,
                "model_load_separable": False,
                "cold_start": None,
            }
    dtype = mlx_core.float16 if fp16 else mlx_core.float32
    key = (str(model_path), "float16" if fp16 else "float32")
    previous_key = getattr(model_holder, "_ltm_runtime_key", None)
    if model_holder.model is not None and previous_key != key:
        # Upstream 0.4.x keys only by path. A precision change must not reuse a
        # model instantiated with the previous dtype. An unbound existing
        # holder is also unsafe because its construction precision is unknown.
        model_holder.model = None
        model_holder.model_path = None
    cold_start = model_holder.model is None or model_holder.model_path != str(model_path)
    started = time.perf_counter()
    model_holder.get_model(str(model_path), dtype=dtype)
    synchronize = getattr(mlx_core, "synchronize", None)
    if callable(synchronize):
        synchronize()
    model_load_seconds = time.perf_counter() - started
    model_holder._ltm_runtime_key = key
    return {
        "model_load_seconds": model_load_seconds,
        "model_load_separable": True,
        "cold_start": cold_start,
    }


def synchronize_mlx() -> None:
    """Materialize lazy MLX work at a benchmark boundary when MLX is loaded."""

    try:
        mx = sys.modules.get("mlx.core")
        if mx is None:
            return
        synchronize = getattr(mx, "synchronize", None)
        if callable(synchronize):
            synchronize()
    except (ImportError, RuntimeError):
        return


def reset_mlx_peak_memory(*, load_if_needed: bool = False) -> None:
    try:
        mx = importlib.import_module("mlx.core") if load_if_needed else sys.modules.get("mlx.core")
        if mx is None:
            return
        reset = getattr(mx, "reset_peak_memory", None)
        if callable(reset):
            reset()
    except (ImportError, RuntimeError):
        return


def mlx_runtime_evidence(*, load_if_needed: bool = True) -> dict[str, object]:
    """Return overlap-aware MLX device and allocator counters.

    Diagnostics can set ``load_if_needed=False`` so merely inspecting a
    faster-whisper/CPU run does not initialize a second accelerator runtime.
    """

    try:
        mx = importlib.import_module("mlx.core") if load_if_needed else sys.modules.get("mlx.core")
        if mx is None:
            return {
                "available": importlib.util.find_spec("mlx") is not None,
                "loaded": False,
                "default_device": None,
                "metal_available": None,
                "active_memory_bytes": None,
                "peak_memory_bytes": None,
                "cache_memory_bytes": None,
                "measurement_note": (
                    "MLX is not loaded in this process; runtime device and allocator "
                    "evidence are unavailable until the MLX backend starts."
                ),
            }
        device = mx.default_device()
        device_type = getattr(getattr(device, "type", None), "name", None)
        metal = getattr(mx, "metal", None)
        metal_available = bool(
            metal is not None
            and callable(getattr(metal, "is_available", None))
            and metal.is_available()
        )
        return {
            "available": True,
            "loaded": True,
            "default_device": (f"mlx:{device_type}" if device_type else str(device)),
            "metal_available": metal_available,
            "active_memory_bytes": _call_optional_int(mx, "get_active_memory"),
            "peak_memory_bytes": _call_optional_int(mx, "get_peak_memory"),
            "cache_memory_bytes": _call_optional_int(mx, "get_cache_memory"),
            "measurement_note": (
                "MLX allocator counters overlap conceptually with process RSS; do not sum them."
            ),
        }
    except (ImportError, RuntimeError):
        return {
            "available": False,
            "loaded": False,
            "default_device": None,
            "metal_available": False,
            "active_memory_bytes": None,
            "peak_memory_bytes": None,
            "cache_memory_bytes": None,
            "measurement_note": "MLX runtime counters unavailable.",
        }


def _call_optional_int(module: Any, name: str) -> int | None:
    function = getattr(module, name, None)
    if not callable(function):
        return None
    try:
        return int(function())
    except (RuntimeError, TypeError, ValueError):
        return None


def mlx_model_entry(config: PipelineConfig) -> ModelEntry:
    entry = resolve_model_entry("asr-mlx", config.asr.mlx_model)
    if entry is None:
        raise ModelCacheError(f"Unknown MLX ASR model '{config.asr.mlx_model}'.")
    return entry


def mlx_model_status(config: PipelineConfig) -> ModelStatus:
    entry = mlx_model_entry(config)
    if config.asr.mlx_model_path is not None:
        return status_for_path(entry, config.asr.mlx_model_path)
    return status_for(entry, config.model_cache)


def resolve_mlx_model_path(config: PipelineConfig) -> Path:
    entry = mlx_model_entry(config)
    if not entry.production_selectable:
        raise ModelCacheError(
            f"MLX ASR model {entry.key} is unavailable in the production product. "
            f"Classification: {entry.product_classification}. "
            f"Blocker: {entry.blocker or 'release-critical model authority is incomplete'}"
        )
    status = mlx_model_status(config)
    if status.present:
        return status.path
    message = (
        f"MLX ASR model {status.entry.key} is not ready.\n"
        f"Model ID: {status.entry.model_id}\n"
        f"Expected: {status.path}\n"
        f"Problem: {status.problem_summary(config.model_cache)}\n"
        "Runtime MLX downloads are disabled; provide a complete local MLX model directory."
    )
    if config.offline:
        raise OfflineModeError("Offline mode is enabled, but " + message)
    raise ModelCacheError(message)


def mlx_compute_type(config: PipelineConfig) -> str:
    return "float16" if config.asr.mlx_fp16 else "float32"


def transcribe_vad_windows(
    mlx_whisper: Any,
    audio_path: Path,
    transcribe_kwargs: dict[str, Any],
    vad_regions: list[VADRegion],
    *,
    window_seconds: float,
    padding_seconds: float,
) -> tuple[dict[str, Any], list[str]]:
    vad_provenance = {
        "sources": sorted(
            {
                region.source
                for region in vad_regions
                if isinstance(region.source, str) and region.source.strip()
            }
        ),
        "window_seconds": window_seconds,
        "region_padding_seconds": padding_seconds,
        "internal_boundary_context_seconds": internal_boundary_context_seconds(
            window_seconds=window_seconds,
            padding_seconds=padding_seconds,
        ),
    }
    windows = merge_vad_regions(
        vad_regions,
        window_seconds=window_seconds,
        padding_seconds=padding_seconds,
        audio_duration_seconds=wav_duration_seconds(audio_path),
    )
    warnings: list[str] = []
    if not windows:
        warnings.append("mlx_vad_windowed_no_regions_fell_back_full")
        payload = mlx_whisper.transcribe(str(audio_path), **transcribe_kwargs)
        if not isinstance(payload, dict):
            raise BackendUnavailableError("mlx-whisper returned an unexpected non-dict payload.")
        payload = dict(payload)
        payload["vad_windows"] = []
        payload["vad_windowed"] = False
        payload["vad_provenance"] = vad_provenance
        return payload, warnings

    combined_segments: list[dict[str, Any]] = []
    chunk_payloads: list[dict[str, Any]] = []
    audio_samples, sample_rate = read_wav_float32(audio_path)
    for index, window in enumerate(windows):
        start_sample = max(0, int(round(window["start"] * sample_rate)))
        end_sample = min(
            len(audio_samples),
            max(start_sample + 1, int(round(window["end"] * sample_rate))),
        )
        chunk = audio_samples[start_sample:end_sample]
        payload = mlx_whisper.transcribe(chunk, **transcribe_kwargs)
        if not isinstance(payload, dict):
            raise BackendUnavailableError("mlx-whisper returned an unexpected non-dict payload.")
        chunk_segments = payload.get("segments", [])
        if not isinstance(chunk_segments, list):
            raise BackendUnavailableError("mlx-whisper returned an unexpected segments payload.")
        raw_segment_count = sum(1 for item in chunk_segments if isinstance(item, dict))
        emitted_segment_count = 0
        context_word_count = 0
        for segment in chunk_segments:
            if isinstance(segment, dict):
                shifted = offset_mlx_segment(segment, window["start"])
                emitted = retain_mlx_segment_for_window(
                    shifted,
                    window,
                    inclusive_end=(
                        index == len(windows) - 1
                        or windows[index + 1]["ownership_start"] > window["ownership_end"]
                    ),
                )
                raw_words = segment.get("words")
                emitted_words = None if emitted is None else emitted.get("words")
                if isinstance(raw_words, list):
                    context_word_count += len(raw_words) - (
                        len(emitted_words) if isinstance(emitted_words, list) else 0
                    )
                if emitted is not None:
                    combined_segments.append(emitted)
                    emitted_segment_count += 1
        chunk_payloads.append(
            {
                "window_index": index,
                "decode_start": window["start"],
                "decode_end": window["end"],
                "ownership_start": window["ownership_start"],
                "ownership_end": window["ownership_end"],
                "boundary_context_seconds": window["boundary_context_seconds"],
                "raw_segment_count": raw_segment_count,
                "emitted_segment_count": emitted_segment_count,
                "context_word_count_not_emitted": context_word_count,
                "language": payload.get("language"),
                "audio_representation": "in_memory_float32",
            }
        )
    return {
        "segments": resequence_segments(combined_segments),
        "language": first_non_empty_language(chunk_payloads),
        "vad_windows": windows,
        "vad_windowed": True,
        "vad_provenance": vad_provenance,
        "vad_window_qa": {
            "ownership_intervals_non_overlapping": ownership_intervals_are_non_overlapping(windows),
            "context_words_not_emitted": sum(
                int(item["context_word_count_not_emitted"]) for item in chunk_payloads
            ),
            "emitted_segment_count": len(combined_segments),
            "whole_file_comparison_performed": False,
        },
        "chunk_payloads": chunk_payloads,
    }, warnings


def merge_vad_regions(
    regions: list[VADRegion],
    *,
    window_seconds: float,
    padding_seconds: float,
    audio_duration_seconds: float,
) -> list[dict[str, float]]:
    if window_seconds < 1.0:
        raise BackendUnavailableError("MLX VAD window seconds must be at least 1 second.")
    ordered = sorted(
        (
            {
                "start": max(0.0, region.start - padding_seconds),
                "end": min(audio_duration_seconds, region.end + padding_seconds),
            }
            for region in regions
            if region.end > region.start
        ),
        key=lambda item: (item["start"], item["end"]),
    )
    windows: list[dict[str, float]] = []
    current: dict[str, float] | None = None
    for region in ordered:
        if current is None:
            current = dict(region)
            continue
        overlaps_or_touches = region["start"] <= current["end"]
        fits_current_window = region["end"] - current["start"] <= window_seconds
        if overlaps_or_touches or fits_current_window:
            current["end"] = max(current["end"], region["end"])
            continue
        if current["end"] > current["start"]:
            windows.append(dict(current))
        current = dict(region)
    if current is not None and current["end"] > current["start"]:
        windows.append(dict(current))
    return [
        round_window(piece)
        for window in windows
        for piece in split_long_vad_window(
            window,
            window_seconds=window_seconds,
            context_seconds=internal_boundary_context_seconds(
                window_seconds=window_seconds,
                padding_seconds=padding_seconds,
            ),
        )
    ]


def split_long_vad_window(
    window: dict[str, float],
    *,
    window_seconds: float,
    context_seconds: float = MLX_INTERNAL_BOUNDARY_CONTEXT_SECONDS,
) -> list[dict[str, float]]:
    """Bound one VAD region with context and non-overlapping emission ownership."""

    start = window["start"]
    end = window["end"]
    if window_seconds < 1.0:
        raise BackendUnavailableError("MLX VAD window seconds must be at least 1 second.")
    if context_seconds < 0 or context_seconds * 2 >= window_seconds:
        raise BackendUnavailableError(
            "MLX VAD boundary context must be non-negative and less than half the window duration."
        )
    if end - start <= window_seconds:
        return [
            {
                "start": start,
                "end": end,
                "ownership_start": start,
                "ownership_end": end,
                "boundary_context_seconds": 0.0,
            }
        ]

    pieces: list[dict[str, float]] = []
    ownership_start = start
    while ownership_start < end:
        decode_start = max(start, ownership_start - context_seconds)
        decode_end = min(end, decode_start + window_seconds)
        ownership_end = end if decode_end >= end else decode_end - context_seconds
        if ownership_end <= ownership_start:
            raise BackendUnavailableError("MLX VAD boundary context left no owned audio interval.")
        pieces.append(
            {
                "start": decode_start,
                "end": decode_end,
                "ownership_start": ownership_start,
                "ownership_end": ownership_end,
                "boundary_context_seconds": context_seconds,
            }
        )
        ownership_start = ownership_end
    return pieces


def round_window(window: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 3) for key, value in window.items()}


def internal_boundary_context_seconds(
    *,
    window_seconds: float,
    padding_seconds: float,
) -> float:
    """Choose a safe internal context while keeping each decoded chunk bounded."""

    return min(
        max(MLX_INTERNAL_BOUNDARY_CONTEXT_SECONDS, padding_seconds),
        window_seconds / 4.0,
    )


def ownership_intervals_are_non_overlapping(
    windows: list[dict[str, float]],
) -> bool:
    return all(
        previous["ownership_end"] <= following["ownership_start"]
        for previous, following in zip(windows, windows[1:], strict=False)
    )


def wav_duration_seconds(audio_path: Path) -> float:
    try:
        with wave.open(str(audio_path), "rb") as source:
            rate = source.getframerate()
            if rate <= 0:
                return 0.0
            return source.getnframes() / rate
    except wave.Error as exc:
        raise BackendUnavailableError(MLX_SAMPLE_RATE_WARNING) from exc


def read_wav_float32(audio_path: Path) -> tuple[Any, int]:
    """Decode the canonical PCM WAV once for all opt-in MLX VAD windows."""

    try:
        np = importlib.import_module("numpy")

        with wave.open(str(audio_path), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise BackendUnavailableError(MLX_SAMPLE_RATE_WARNING)
            rate = source.getframerate()
            if rate != 16_000:
                raise BackendUnavailableError(MLX_SAMPLE_RATE_WARNING)
            frames = source.readframes(source.getnframes())
        samples = np.frombuffer(frames, dtype="<i2").astype(np.float32)
        samples /= 32768.0
        return samples, rate
    except (ImportError, wave.Error) as exc:
        raise BackendUnavailableError(MLX_SAMPLE_RATE_WARNING) from exc


def offset_mlx_segment(segment: dict[str, Any], offset: float) -> dict[str, Any]:
    shifted = dict(segment)
    shifted["start"] = _float_value(shifted.get("start")) + offset
    shifted["end"] = _float_value(shifted.get("end")) + offset
    words = shifted.get("words")
    if isinstance(words, list):
        shifted_words: list[dict[str, Any]] = []
        for word in words:
            if not isinstance(word, dict):
                continue
            shifted_word = dict(word)
            if shifted_word.get("start") is not None:
                shifted_word["start"] = _float_value(shifted_word.get("start")) + offset
            if shifted_word.get("end") is not None:
                shifted_word["end"] = _float_value(shifted_word.get("end")) + offset
            shifted_words.append(shifted_word)
        shifted["words"] = shifted_words
    return shifted


def retain_mlx_segment_for_window(
    segment: dict[str, Any],
    window: dict[str, float],
    *,
    inclusive_end: bool,
) -> dict[str, Any] | None:
    """Emit each context-decoded word from exactly one ownership interval."""

    segment_start = _float_value(segment.get("start"))
    segment_end = _float_value(segment.get("end"))
    segment_midpoint = (segment_start + segment_end) / 2.0
    words = segment.get("words")
    if isinstance(words, list) and words:
        retained_words: list[dict[str, Any]] = []
        for word in words:
            if not isinstance(word, dict):
                continue
            start = _optional_float(word.get("start"))
            end = _optional_float(word.get("end"))
            if start is not None and end is not None:
                midpoint = (start + end) / 2.0
            elif start is not None:
                midpoint = start
            elif end is not None:
                midpoint = end
            else:
                midpoint = segment_midpoint
            if ownership_contains(
                midpoint,
                window,
                inclusive_end=inclusive_end,
            ):
                retained_words.append(word)
        if not retained_words:
            return None
        emitted = dict(segment)
        emitted["words"] = retained_words
        emitted["text"] = text_from_mlx_words(retained_words)
        retained_starts = [
            value
            for word in retained_words
            if (value := _optional_float(word.get("start"))) is not None
        ]
        retained_ends = [
            value
            for word in retained_words
            if (value := _optional_float(word.get("end"))) is not None
        ]
        if retained_starts:
            emitted["start"] = min(retained_starts)
        if retained_ends:
            emitted["end"] = max(retained_ends)
        return emitted

    if ownership_contains(
        segment_midpoint,
        window,
        inclusive_end=inclusive_end,
    ):
        return segment
    return None


def ownership_contains(
    timestamp: float,
    window: dict[str, float],
    *,
    inclusive_end: bool,
) -> bool:
    start = window["ownership_start"]
    end = window["ownership_end"]
    return timestamp >= start and (timestamp <= end if inclusive_end else timestamp < end)


def text_from_mlx_words(words: list[dict[str, Any]]) -> str:
    tokens = [str(word.get("word") or "") for word in words]
    if any(token[:1].isspace() for token in tokens):
        return "".join(tokens).strip()
    return " ".join(token.strip() for token in tokens if token.strip())


def resequence_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        segments,
        key=lambda item: (
            _float_value(item.get("start")),
            _float_value(item.get("end")),
        ),
    )
    for index, segment in enumerate(ordered):
        segment["id"] = index
    return ordered


def first_non_empty_language(chunk_payloads: list[dict[str, Any]]) -> str | None:
    for payload in chunk_payloads:
        language = payload.get("language")
        if language:
            return str(language)
    return None


def unsupported_option_warnings(config: PipelineConfig) -> list[str]:
    warnings: list[str] = []
    if config.asr.prefix:
        warnings.append("mlx_prefix_unsupported")
    if config.asr.vad_filter:
        warnings.append("mlx_vad_filter_unsupported")
    if config.asr.batched:
        warnings.append("mlx_batched_unsupported")
    if config.asr.cpu_threads is not None:
        warnings.append("mlx_cpu_threads_ignored")
    if config.asr.num_workers is not None:
        warnings.append("mlx_num_workers_ignored")
    if config.asr.beam_size != 1:
        warnings.append("mlx_beam_search_unsupported")
    if config.asr.patience is not None:
        warnings.append("mlx_patience_ignored")
    return warnings


def segments_from_mlx_payload(
    payload: Any,
    *,
    require_word_timestamps: bool,
) -> tuple[list[ASRSegment], list[dict[str, Any]], list[str]]:
    if not isinstance(payload, dict):
        raise BackendUnavailableError("mlx-whisper returned an unexpected non-dict payload.")
    raw_items = payload.get("segments", [])
    if not isinstance(raw_items, list):
        raise BackendUnavailableError("mlx-whisper returned an unexpected segments payload.")
    segments: list[ASRSegment] = []
    raw_segments: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        raw_words = raw_words_from_mlx(item.get("words"))
        words = words_from_mlx(raw_words)
        text = str(item.get("text", "")).strip()
        if require_word_timestamps and text and not words:
            warnings.append("missing_word_timestamps")
        raw = {
            "id": item.get("id", index),
            "start": item.get("start", 0.0),
            "end": item.get("end", 0.0),
            "text": item.get("text", ""),
            "words": raw_words,
            "avg_logprob": item.get("avg_logprob"),
            "no_speech_prob": item.get("no_speech_prob"),
            "compression_ratio": item.get("compression_ratio"),
            "temperature": item.get("temperature"),
        }
        raw_segments.append(raw)
        segments.append(
            ASRSegment(
                id=_int_value(raw["id"]),
                start=_float_value(raw["start"]),
                end=_float_value(raw["end"]),
                text=text,
                words=words,
                avg_logprob=_optional_float(raw["avg_logprob"]),
                no_speech_prob=_optional_float(raw["no_speech_prob"]),
                compression_ratio=_optional_float(raw["compression_ratio"]),
            )
        )
    return segments, raw_segments, warnings


def raw_words_from_mlx(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    words: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        words.append(
            {
                "word": item.get("word", ""),
                "start": item.get("start"),
                "end": item.get("end"),
                "probability": item.get("probability"),
            }
        )
    return words


def words_from_mlx(raw_words: list[dict[str, Any]]) -> list[AlignedWord]:
    words: list[AlignedWord] = []
    for word in raw_words:
        text = str(word.get("word", "")).strip()
        if not text:
            continue
        words.append(
            AlignedWord(
                text=text,
                start=_optional_float(word.get("start")),
                end=_optional_float(word.get("end")),
                confidence=_optional_float(word.get("probability")),
                alignment_source="whisper",
            )
        )
    return words


def _int_value(value: object) -> int:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_value(value: object) -> float:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
