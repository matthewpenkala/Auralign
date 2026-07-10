# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""MLX Whisper ASR backend with local-only model path enforcement."""

from __future__ import annotations

import importlib.util
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

from local_transcript_max.config import ModelEntry, PipelineConfig, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError, ModelCacheError, OfflineModeError
from local_transcript_max.models import ModelStatus, status_for, status_for_path
from local_transcript_max.schemas import (
    AlignedWord,
    ASRResult,
    ASRSegment,
    ModelMetadata,
    StageTiming,
    VADRegion,
)

MLX_SAMPLE_RATE_WARNING = "mlx_vad_windowed_requires_seekable_wav"


class MlxWhisperBackend:
    name = "mlx-whisper"

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

        start = time.perf_counter()
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
            inference_mode = (
                "mlx-vad-windowed" if payload.get("vad_windowed") else "mlx"
            )
        else:
            payload = mlx_whisper.transcribe(str(audio_path), **transcribe_kwargs)
            inference_mode = "mlx"
        duration = time.perf_counter() - start
        segments, raw_segments, timestamp_warnings = segments_from_mlx_payload(
            payload,
            require_word_timestamps=config.asr.word_timestamps,
        )
        warnings.extend(timestamp_warnings)

        return ASRResult(
            segments=segments,
            language=_str_or_none(payload.get("language")) if isinstance(payload, dict) else None,
            raw={"segments": raw_segments, "mlx": payload if isinstance(payload, dict) else {}},
            model=ModelMetadata(
                role="asr-mlx",
                model_id=entry.model_id,
                local_path=str(model_path),
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
                "vad_window_count": len(payload.get("vad_windows", []))
                if isinstance(payload, dict)
                else 0,
            },
            warnings=sorted(set(warnings)),
            timing=StageTiming(name="asr", duration_seconds=duration),
        )


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
        return payload, warnings

    combined_segments: list[dict[str, Any]] = []
    chunk_payloads: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ltm-mlx-vad-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        for index, window in enumerate(windows):
            chunk_path = tmp_dir / f"window_{index:04d}.wav"
            write_wav_chunk(audio_path, chunk_path, window["start"], window["end"])
            payload = mlx_whisper.transcribe(str(chunk_path), **transcribe_kwargs)
            if not isinstance(payload, dict):
                raise BackendUnavailableError(
                    "mlx-whisper returned an unexpected non-dict payload."
                )
            chunk_segments = payload.get("segments", [])
            if not isinstance(chunk_segments, list):
                raise BackendUnavailableError(
                    "mlx-whisper returned an unexpected segments payload."
                )
            segment_count = sum(
                1 for item in chunk_segments if isinstance(item, dict)
            )
            for segment in chunk_segments:
                if isinstance(segment, dict):
                    combined_segments.append(offset_mlx_segment(segment, window["start"]))
            chunk_payloads.append(
                {
                    "window_index": index,
                    "start": window["start"],
                    "end": window["end"],
                    "segment_count": segment_count,
                    "language": payload.get("language"),
                }
            )
    return {
        "segments": resequence_segments(combined_segments),
        "language": first_non_empty_language(chunk_payloads),
        "vad_windows": windows,
        "vad_windowed": True,
        "chunk_payloads": chunk_payloads,
    }, warnings


def merge_vad_regions(
    regions: list[VADRegion],
    *,
    window_seconds: float,
    padding_seconds: float,
    audio_duration_seconds: float,
) -> list[dict[str, float]]:
    if window_seconds <= 0:
        raise BackendUnavailableError("MLX VAD window seconds must be greater than 0.")
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
        if region["end"] - current["start"] > window_seconds and current["end"] > current["start"]:
            windows.append(round_window(current))
            current = dict(region)
            continue
        current["end"] = max(current["end"], region["end"])
    if current is not None and current["end"] > current["start"]:
        windows.append(round_window(current))
    return windows


def round_window(window: dict[str, float]) -> dict[str, float]:
    return {"start": round(window["start"], 3), "end": round(window["end"], 3)}


def wav_duration_seconds(audio_path: Path) -> float:
    try:
        with wave.open(str(audio_path), "rb") as source:
            rate = source.getframerate()
            if rate <= 0:
                return 0.0
            return source.getnframes() / rate
    except wave.Error as exc:
        raise BackendUnavailableError(MLX_SAMPLE_RATE_WARNING) from exc


def write_wav_chunk(audio_path: Path, chunk_path: Path, start: float, end: float) -> None:
    try:
        with wave.open(str(audio_path), "rb") as source:
            params = source.getparams()
            rate = source.getframerate()
            start_frame = max(0, int(round(start * rate)))
            end_frame = max(start_frame + 1, int(round(end * rate)))
            end_frame = min(end_frame, source.getnframes())
            source.setpos(start_frame)
            frames = source.readframes(max(0, end_frame - start_frame))
        with wave.open(str(chunk_path), "wb") as target:
            target.setparams(params)
            target.writeframes(frames)
    except wave.Error as exc:
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
