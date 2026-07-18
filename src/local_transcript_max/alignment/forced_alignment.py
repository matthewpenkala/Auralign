# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Adapter for actual local WhisperX forced alignment."""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from typing import Any, Literal

from local_transcript_max.config import PipelineConfig, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.models import status_for
from local_transcript_max.runtime_assets import verify_generated_punkt
from local_transcript_max.runtime_policy import resolve_runtime_policy
from local_transcript_max.schemas import AlignedWord, ASRResult


class WhisperXForcedAlignment:
    name = "whisperx-forced"

    def __init__(self) -> None:
        self.last_device: str | None = None
        self.last_model_name: str | None = None
        self.last_model_path: str | None = None
        self.last_model_load_seconds: float | None = None
        self.last_inference_seconds: float | None = None
        self.last_cold_start: bool | None = None
        self.last_expected_segment_count: int = 0
        self.last_aligned_segment_count: int = 0
        self.last_missing_segment_count: int = 0
        self._model_cache: dict[tuple[str, str, str], tuple[Any, dict[str, Any]]] = {}

    def clear_runtime_cache(self) -> None:
        """Release cached alignment models for explicit per-job lifecycle."""

        self._model_cache.clear()

    def align(
        self, audio_path: Path, asr_result: ASRResult, config: PipelineConfig
    ) -> list[AlignedWord]:
        language = config.alignment.language or asr_result.language
        if not language:
            raise BackendUnavailableError(
                "Forced alignment requires an alignment language. Pass --alignment-language LANG."
            )
        model_name = preflight_forced_alignment(config)
        self.last_model_name = model_name
        self.last_model_path = model_name if Path(model_name).expanduser().exists() else None
        if importlib.util.find_spec("whisperx") is None:
            raise BackendUnavailableError(
                "WhisperX forced alignment is unavailable. Run: uv sync --extra alignment"
            )
        import whisperx

        decision = resolve_runtime_policy(config).alignment
        device = decision.resolved_device or _alignment_device(
            config.alignment.device or "auto"
        )
        self.last_device = device
        model_key = (language, device, model_name)
        load_started = time.perf_counter()
        try:
            cached = self._model_cache.get(model_key)
            if cached is None:
                model, metadata = whisperx.load_align_model(
                    language_code=language,
                    device=device,
                    model_name=model_name,
                    model_dir=str(config.model_cache.expanduser()),
                    model_cache_only=True,
                )
                self._model_cache[model_key] = (model, metadata)
                self.last_cold_start = True
            else:
                model, metadata = cached
                self.last_cold_start = False
        except Exception as exc:  # pragma: no cover - dependency/model specific
            raise BackendUnavailableError(
                f"WhisperX alignment model unavailable for language '{language}': {exc}"
            ) from exc
        self.last_model_load_seconds = time.perf_counter() - load_started
        segments = [
            {"start": segment.start, "end": segment.end, "text": segment.text}
            for segment in asr_result.segments
            if segment.text.strip()
        ]
        self.last_expected_segment_count = len(segments)
        inference_started = time.perf_counter()
        try:
            aligned = whisperx.align(
                segments,
                model,
                metadata,
                str(audio_path),
                device,
                return_char_alignments=False,
            )
        except Exception as exc:  # pragma: no cover - dependency/model specific
            raise BackendUnavailableError(f"WhisperX alignment failed: {exc}") from exc
        self.last_inference_seconds = time.perf_counter() - inference_started
        words, coverage = _words_from_whisperx(aligned, expected_segments=segments)
        self.last_aligned_segment_count = coverage["aligned_segment_count"]
        self.last_missing_segment_count = coverage["missing_segment_count"]
        if not words:
            raise BackendUnavailableError("WhisperX alignment returned no word timestamps.")
        return words


def preflight_forced_alignment(config: PipelineConfig) -> str:
    """Fail before ASR when strict forced alignment is not locally runnable."""
    verify_generated_punkt(config.model_cache)
    if importlib.util.find_spec("whisperx") is None:
        raise BackendUnavailableError(
            "WhisperX forced alignment is unavailable. Run: uv sync --extra alignment"
        )
    return _local_alignment_model_name(config)


def _alignment_device(requested: str) -> str:
    if requested == "cuda":
        return "cuda"
    if requested == "auto":
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"
    return requested


def _local_alignment_model_name(config: PipelineConfig) -> str:
    requested = config.alignment.model or "auto"
    entry = resolve_model_entry("alignment", requested)
    if entry is not None:
        status = status_for(entry, config.model_cache)
        if status.present:
            return str(status.path)
        raise BackendUnavailableError(
            "Alignment model "
            f"{entry.key} is not ready for local forced alignment. "
            f"{status.problem_summary(config.model_cache)}"
        )

    candidate = Path(requested).expanduser()
    if candidate.exists():
        _validate_local_alignment_path(candidate)
        return str(candidate)
    raise BackendUnavailableError(
        "Forced alignment requires a local alignment model cache entry or path. "
        "Run: ltm models pull --alignment auto --cache-dir "
        f"{config.model_cache.expanduser()}"
    )


def _validate_local_alignment_path(path: Path) -> None:
    missing: list[str] = []
    for required in (
        "config.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "vocab.json",
    ):
        if not (path / required).exists():
            missing.append(required)
    if not any((path / name).exists() for name in ("pytorch_model.bin", "model.safetensors")):
        missing.append("pytorch_model.bin or model.safetensors")
    if missing:
        raise BackendUnavailableError(
            "Local forced-alignment model path is not a WhisperX-compatible CTC "
            f"alignment model: {path}. Missing: {', '.join(missing)}"
        )


def _words_from_whisperx(
    payload: dict[str, Any],
    *,
    expected_segments: list[dict[str, Any]] | None = None,
) -> tuple[list[AlignedWord], dict[str, int]]:
    words: list[AlignedWord] = []
    raw_segments = payload.get("segments", [])
    aligned_segments = raw_segments if isinstance(raw_segments, list) else []
    aligned_segment_count = 0
    missing_segment_count = 0
    for segment_index, segment in enumerate(aligned_segments):
        if not isinstance(segment, dict):
            continue
        segment_words = segment.get("words", [])
        segment_word_count = 0
        for word in segment_words if isinstance(segment_words, list) else []:
            if not isinstance(word, dict):
                continue
            text = str(word.get("word") or "").strip()
            if not text:
                continue
            start = _optional_float(word.get("start"))
            end = _optional_float(word.get("end"))
            warnings: list[str] = []
            source: Literal["forced", "missing"] = "forced"
            if start is None or end is None:
                source = "missing"
                warnings.append("forced_alignment_missing_timestamp")
            words.append(
                AlignedWord(
                    text=text,
                    start=start,
                    end=end,
                    confidence=_optional_float(word.get("score")),
                    alignment_source=source,
                    warnings=warnings,
                )
            )
            segment_word_count += 1
        if segment_word_count:
            aligned_segment_count += 1
        elif _segment_requires_alignment(expected_segments, segment_index):
            missing_segment_count += 1
            assert expected_segments is not None
            words.append(_missing_segment_word(expected_segments, segment_index))
    if expected_segments:
        for segment_index in range(len(aligned_segments), len(expected_segments)):
            if _segment_requires_alignment(expected_segments, segment_index):
                missing_segment_count += 1
                words.append(_missing_segment_word(expected_segments, segment_index))
    return words, {
        "expected_segment_count": len(expected_segments or []),
        "aligned_segment_count": aligned_segment_count,
        "missing_segment_count": missing_segment_count,
    }


def _segment_requires_alignment(
    expected_segments: list[dict[str, Any]] | None,
    segment_index: int,
) -> bool:
    if expected_segments is None or segment_index >= len(expected_segments):
        return False
    text = str(expected_segments[segment_index].get("text") or "")
    return any(character.isalnum() for character in text)


def _missing_segment_word(
    expected_segments: list[dict[str, Any]],
    segment_index: int,
) -> AlignedWord:
    return AlignedWord(
        text=str(expected_segments[segment_index].get("text") or "").strip(),
        alignment_source="missing",
        warnings=["forced_alignment_missing_segment"],
    )


def _optional_float(value: object) -> float | None:
    if value is None or not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
