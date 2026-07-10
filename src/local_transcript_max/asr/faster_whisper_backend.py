# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""faster-whisper ASR backend with explicit local model-cache enforcement."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any

from local_transcript_max.config import PipelineConfig, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError, ModelCacheError
from local_transcript_max.models import require_model
from local_transcript_max.schemas import (
    AlignedWord,
    ASRResult,
    ASRSegment,
    ModelMetadata,
    StageTiming,
    VADRegion,
)


class FasterWhisperBackend:
    name = "faster-whisper"

    def __init__(self) -> None:
        self._models: dict[tuple[str, str, str, int | None, int | None], Any] = {}
        self._batched_transcribers: dict[tuple[str, str, str, int | None, int | None], Any] = {}

    def transcribe(
        self,
        audio_path: Path,
        config: PipelineConfig,
        *,
        vad_regions: list[VADRegion] | None = None,
    ) -> ASRResult:
        if importlib.util.find_spec("faster_whisper") is None:
            raise BackendUnavailableError(
                "faster-whisper is not installed. Run: uv sync --extra asr"
            )
        from faster_whisper import WhisperModel

        batched_pipeline_factory: Any | None = None
        if config.asr.batched:
            try:
                from faster_whisper import BatchedInferencePipeline
            except ImportError as exc:
                raise BackendUnavailableError(
                    "faster-whisper BatchedInferencePipeline is not available. "
                    "Upgrade faster-whisper or unset --asr-batched / LTM_ASR_BATCHED."
                ) from exc
            batched_pipeline_factory = BatchedInferencePipeline

        entry = resolve_model_entry("asr", config.asr.model)
        if entry is None:
            raise ModelCacheError(f"Unknown ASR model '{config.asr.model}'.")
        model_path = require_model(
            "asr",
            config.asr.model,
            config.model_cache,
            offline=config.offline,
        )
        device = resolve_device(config.asr.device)
        compute_type = resolve_compute_type(config.asr.compute_type, device)
        validate_compute_type(compute_type, device)
        cpu_threads = resolve_cpu_threads(config.asr.cpu_threads, device)
        num_workers = resolve_num_workers(config.asr.num_workers)
        start = time.perf_counter()
        model = self._model(
            model_path=model_path,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
            model_factory=WhisperModel,
        )
        model_key = self._runtime_key(
            model_path=model_path,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
        )
        transcriber = (
            self._batched_transcriber(
                key=model_key,
                model=model,
                pipeline_factory=batched_pipeline_factory,
            )
            if batched_pipeline_factory is not None
            else model
        )
        initial_prompt = config.asr.initial_prompt
        if config.asr.glossary_file:
            initial_prompt = (
                (initial_prompt or "") + "\n" + config.asr.glossary_file.read_text(encoding="utf-8")
            )
        transcribe_kwargs: dict[str, Any] = {
            "language": config.asr.language,
            "task": config.asr.task,
            "beam_size": config.asr.beam_size,
            "best_of": config.asr.best_of,
            "patience": config.asr.patience if config.asr.patience is not None else 1.0,
            "temperature": config.asr.temperature,
            "compression_ratio_threshold": config.asr.compression_ratio_threshold,
            "log_prob_threshold": config.asr.logprob_threshold,
            "no_speech_threshold": config.asr.no_speech_threshold,
            "condition_on_previous_text": config.asr.condition_on_previous_text,
            "initial_prompt": initial_prompt,
            "prefix": config.asr.prefix,
            "word_timestamps": config.asr.word_timestamps,
            "vad_filter": config.asr.vad_filter,
        }
        if config.asr.batched:
            transcribe_kwargs["batch_size"] = config.asr.batch_size
            transcribe_kwargs["without_timestamps"] = False
        segments_iter, info = transcriber.transcribe(str(audio_path), **transcribe_kwargs)
        segments: list[ASRSegment] = []
        raw_segments: list[dict[str, Any]] = []
        warnings: list[str] = []
        for segment in segments_iter:
            raw_words = [
                {
                    "word": getattr(word, "word", ""),
                    "start": getattr(word, "start", None),
                    "end": getattr(word, "end", None),
                    "probability": getattr(word, "probability", None),
                }
                for word in (getattr(segment, "words", None) or [])
            ]
            words = [
                AlignedWord(
                    text=getattr(word, "word", "").strip(),
                    start=getattr(word, "start", None),
                    end=getattr(word, "end", None),
                    confidence=getattr(word, "probability", None),
                    alignment_source="whisper",
                )
                for word in (getattr(segment, "words", None) or [])
                if getattr(word, "word", "").strip()
            ]
            if config.asr.word_timestamps and not words:
                warnings.append("missing_word_timestamps")
            raw: dict[str, Any] = {
                "id": getattr(segment, "id", len(segments)),
                "start": getattr(segment, "start", 0.0),
                "end": getattr(segment, "end", 0.0),
                "text": getattr(segment, "text", ""),
                "words": raw_words,
                "avg_logprob": getattr(segment, "avg_logprob", None),
                "no_speech_prob": getattr(segment, "no_speech_prob", None),
                "compression_ratio": getattr(segment, "compression_ratio", None),
            }
            raw_segments.append(raw)
            segments.append(
                ASRSegment(
                    id=_int_value(raw["id"]),
                    start=_float_value(raw["start"]),
                    end=_float_value(raw["end"]),
                    text=str(raw["text"]).strip(),
                    words=words,
                    avg_logprob=_optional_float(raw["avg_logprob"]),
                    no_speech_prob=_optional_float(raw["no_speech_prob"]),
                    compression_ratio=_optional_float(raw["compression_ratio"]),
                )
            )
        duration = time.perf_counter() - start
        return ASRResult(
            segments=segments,
            language=getattr(info, "language", None),
            language_probability=getattr(info, "language_probability", None),
            raw={"segments": raw_segments, "info": safe_info(info)},
            model=ModelMetadata(
                role="asr",
                model_id=entry.model_id,
                local_path=str(model_path),
                gated=entry.gated,
                present=True,
            ),
            config={
                **config.asr.model_dump(mode="json"),
                "device": device,
                "compute_type": compute_type,
                "requested_cpu_threads": config.asr.cpu_threads,
                "effective_cpu_threads": cpu_threads,
                "cpu_threads_passed_to_constructor": cpu_threads is not None,
                "requested_num_workers": config.asr.num_workers,
                "effective_num_workers": num_workers,
                "num_workers_passed_to_constructor": num_workers is not None,
                "inference_mode": "batched" if config.asr.batched else "standard",
                "model_path": str(model_path),
            },
            warnings=sorted(set(warnings)),
            timing=StageTiming(name="asr", duration_seconds=duration),
        )

    def _model(
        self,
        *,
        model_path: Path,
        device: str,
        compute_type: str,
        cpu_threads: int | None,
        num_workers: int | None,
        model_factory: Any,
    ) -> Any:
        key = self._runtime_key(
            model_path=model_path,
            device=device,
            compute_type=compute_type,
            cpu_threads=cpu_threads,
            num_workers=num_workers,
        )
        model = self._models.get(key)
        if model is None:
            kwargs: dict[str, Any] = {"device": device, "compute_type": compute_type}
            if cpu_threads is not None:
                kwargs["cpu_threads"] = cpu_threads
            if num_workers is not None:
                kwargs["num_workers"] = num_workers
            try:
                model = model_factory(str(model_path), **kwargs)
            except TypeError as exc:
                if cpu_threads is not None or num_workers is not None:
                    raise BackendUnavailableError(
                        "faster-whisper rejected explicit ASR threading options "
                        f"cpu_threads={cpu_threads!r} num_workers={num_workers!r}. "
                        "Upgrade faster-whisper/CTranslate2 or unset those options."
                    ) from exc
                raise
            self._models[key] = model
        return model

    def _batched_transcriber(
        self,
        *,
        key: tuple[str, str, str, int | None, int | None],
        model: Any,
        pipeline_factory: Any,
    ) -> Any:
        transcriber = self._batched_transcribers.get(key)
        if transcriber is None:
            transcriber = pipeline_factory(model=model)
            self._batched_transcribers[key] = transcriber
        return transcriber

    @staticmethod
    def _runtime_key(
        *,
        model_path: Path,
        device: str,
        compute_type: str,
        cpu_threads: int | None,
        num_workers: int | None,
    ) -> tuple[str, str, str, int | None, int | None]:
        return (str(model_path), device, compute_type, cpu_threads, num_workers)


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def resolve_compute_type(requested: str, device: str) -> str:
    if requested != "auto":
        return requested
    return "float16" if device == "cuda" else "int8"


def resolve_cpu_threads(requested: int | None, device: str) -> int | None:
    if requested is None:
        return None
    if requested <= 0:
        raise BackendUnavailableError("asr.cpu_threads must be a positive integer when set.")
    if device != "cpu":
        return None
    return requested


def resolve_num_workers(requested: int | None) -> int | None:
    if requested is None:
        return None
    if requested <= 0:
        raise BackendUnavailableError("asr.num_workers must be a positive integer when set.")
    return requested


def supported_compute_types(device: str) -> set[str] | None:
    try:
        import ctranslate2

        return set(ctranslate2.get_supported_compute_types(device))
    except Exception:
        return None


def validate_compute_type(compute_type: str, device: str) -> None:
    supported = supported_compute_types(device)
    if supported is None:
        raise BackendUnavailableError(
            "Could not determine CTranslate2 compute support for "
            f"device '{device}'. Install a compatible ctranslate2 wheel or choose cpu."
        )
    if compute_type not in supported:
        supported_text = ", ".join(sorted(supported)) or "none"
        raise BackendUnavailableError(
            f"CTranslate2 compute_type '{compute_type}' is not supported on device "
            f"'{device}'. Supported compute types: {supported_text}. "
            "On Apple Silicon faster-whisper uses CTranslate2 CPU paths; use "
            "--preset cpu, set asr.compute_type to int8/int8_float32/float32, "
            "or set LTM_ASR_DEVICE=cpu."
        )


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


def safe_info(info: object) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("language", "language_probability", "duration", "duration_after_vad"):
        if hasattr(info, key):
            result[key] = getattr(info, key)
    return result
