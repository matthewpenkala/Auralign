# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Optional WhisperX-style forced alignment adapter."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Literal

from local_transcript_max.config import PipelineConfig, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.models import status_for
from local_transcript_max.runtime_assets import verify_generated_punkt
from local_transcript_max.schemas import AlignedWord, ASRResult


class WhisperXForcedAlignment:
    name = "whisperx-forced"

    def __init__(self) -> None:
        self.last_device: str | None = None
        self.last_model_name: str | None = None
        self.last_model_path: str | None = None

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

        device = _alignment_device(config.alignment.device or config.asr.device)
        self.last_device = device
        try:
            model, metadata = whisperx.load_align_model(
                language_code=language,
                device=device,
                model_name=model_name,
                model_dir=str(config.model_cache.expanduser()),
                model_cache_only=True,
            )
        except Exception as exc:  # pragma: no cover - dependency/model specific
            raise BackendUnavailableError(
                f"WhisperX alignment model unavailable for language '{language}': {exc}"
            ) from exc
        segments = [
            {"start": segment.start, "end": segment.end, "text": segment.text}
            for segment in asr_result.segments
            if segment.text.strip()
        ]
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
        words = _words_from_whisperx(aligned)
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
    if not any(
        (path / name).exists() for name in ("pytorch_model.bin", "model.safetensors")
    ):
        missing.append("pytorch_model.bin or model.safetensors")
    if missing:
        raise BackendUnavailableError(
            "Local forced-alignment model path is not a WhisperX-compatible CTC "
            f"alignment model: {path}. Missing: {', '.join(missing)}"
        )


def _words_from_whisperx(payload: dict[str, Any]) -> list[AlignedWord]:
    words: list[AlignedWord] = []
    for segment in payload.get("segments", []):
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words", []):
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
    return words


def _optional_float(value: object) -> float | None:
    if value is None or not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
