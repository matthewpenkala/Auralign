# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Adapter for actual local WhisperX forced alignment."""

from __future__ import annotations

import importlib.util
import math
import time
from pathlib import Path
from typing import Any, Literal

from local_transcript_max.alignment.fallback_word_timestamps import (
    fallback_segment_words,
)
from local_transcript_max.config import PipelineConfig, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.models import status_for
from local_transcript_max.runtime_assets import verify_generated_punkt
from local_transcript_max.runtime_policy import resolve_runtime_policy
from local_transcript_max.schemas import AlignedWord, ASRResult, ASRSegment

_LOCALIZED_FALLBACK_WARNING = "forced_alignment_localized_fallback"


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
        self.last_forced_word_count: int = 0
        self.last_localized_fallback_word_count: int = 0
        self.last_localized_fallback_segments: list[dict[str, int | str]] = []
        self.last_interpolation_method = "ignore"
        self.last_alignment_call_count: int = 0
        self.last_inference_hygiene: dict[str, bool | float] = {}
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
                model, metadata = _load_inference_alignment_model(
                    language=language,
                    device=device,
                    model_name=model_name,
                    model_dir=config.model_cache.expanduser(),
                )
                self._model_cache[model_key] = (model, metadata)
                self.last_cold_start = True
            else:
                model, metadata = cached
                _assert_alignment_model_inference_ready(model)
                self.last_cold_start = False
            self.last_inference_hygiene = _alignment_inference_hygiene(model)
        except Exception as exc:  # pragma: no cover - dependency/model specific
            raise BackendUnavailableError(
                "WhisperX alignment model unavailable for the configured language."
            ) from exc
        self.last_model_load_seconds = time.perf_counter() - load_started
        source_segments = [
            segment
            for segment in asr_result.segments
            if _segment_text(segment)
        ]
        segments = [
            {"start": segment.start, "end": segment.end, "text": _segment_text(segment)}
            for segment in source_segments
        ]
        self.last_expected_segment_count = len(segments)
        self.last_aligned_segment_count = 0
        self.last_missing_segment_count = 0
        self.last_forced_word_count = 0
        self.last_localized_fallback_word_count = 0
        self.last_localized_fallback_segments = []
        self.last_alignment_call_count = 0
        inference_started = time.perf_counter()
        try:
            audio = whisperx.load_audio(str(audio_path))
            aligned_by_source: list[list[dict[str, Any]]] = []
            for segment in segments:
                aligned = whisperx.align(
                    [segment],
                    model,
                    metadata,
                    audio,
                    device,
                    return_char_alignments=True,
                    interpolate_method=self.last_interpolation_method,
                )
                raw_aligned_segments = (
                    aligned.get("segments", []) if isinstance(aligned, dict) else []
                )
                aligned_by_source.append(
                    [
                        item
                        for item in raw_aligned_segments
                        if isinstance(item, dict)
                    ]
                    if isinstance(raw_aligned_segments, list)
                    else []
                )
                self.last_alignment_call_count += 1
        except Exception as exc:  # pragma: no cover - dependency/model specific
            raise BackendUnavailableError(
                "WhisperX alignment execution failed."
            ) from exc
        self.last_inference_seconds = time.perf_counter() - inference_started
        words, coverage = _words_from_whisperx(
            aligned_by_source,
            expected_segments=source_segments,
            allow_localized_fallback=config.alignment.backend != "forced",
        )
        self.last_aligned_segment_count = coverage["aligned_segment_count"]
        self.last_missing_segment_count = coverage["missing_segment_count"]
        self.last_forced_word_count = coverage["forced_word_count"]
        self.last_localized_fallback_word_count = coverage["fallback_word_count"]
        self.last_localized_fallback_segments = coverage["fallback_segments"]
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


def _load_inference_alignment_model(
    *,
    language: str,
    device: str,
    model_name: str,
    model_dir: Path,
) -> tuple[Any, dict[str, Any]]:
    """Load the pinned CTC model without constructing training-only SpecAugment state."""

    from transformers import (
        Wav2Vec2Config,
        Wav2Vec2ForCTC,
        Wav2Vec2Processor,
    )

    processor = Wav2Vec2Processor.from_pretrained(  # type: ignore[no-untyped-call]
        model_name,
        cache_dir=str(model_dir),
        local_files_only=True,
    )
    model_config = Wav2Vec2Config.from_pretrained(
        model_name,
        cache_dir=str(model_dir),
        local_files_only=True,
    )
    _configure_wav2vec2_for_inference(model_config)
    model = Wav2Vec2ForCTC.from_pretrained(
        model_name,
        config=model_config,
        cache_dir=str(model_dir),
        local_files_only=True,
    )
    model = model.to(device)
    model.eval()
    _assert_alignment_model_inference_ready(model)
    vocabulary = processor.tokenizer.get_vocab()
    metadata = {
        "language": language,
        "dictionary": {
            character.lower(): code for character, code in vocabulary.items()
        },
        "type": "huggingface",
    }
    return model, metadata


def _configure_wav2vec2_for_inference(model_config: Any) -> None:
    """Disable unused training augmentation only on the in-memory config."""

    model_config.apply_spec_augment = False
    model_config.mask_time_prob = 0.0
    model_config.mask_feature_prob = 0.0


def _assert_alignment_model_inference_ready(model: Any) -> None:
    if bool(getattr(model, "training", True)):
        raise BackendUnavailableError(
            "Forced-alignment model did not enter evaluation mode."
        )
    model_config = getattr(model, "config", None)
    if (
        model_config is None
        or bool(getattr(model_config, "apply_spec_augment", True))
        or float(getattr(model_config, "mask_time_prob", -1.0)) != 0.0
        or float(getattr(model_config, "mask_feature_prob", -1.0)) != 0.0
    ):
        raise BackendUnavailableError(
            "Forced-alignment model retained a training-time augmentation configuration."
        )
    wav2vec2 = getattr(model, "wav2vec2", None)
    if wav2vec2 is not None and hasattr(wav2vec2, "masked_spec_embed"):
        raise BackendUnavailableError(
            "Forced-alignment model retained training-only SpecAugment parameters."
        )
    if any(
        name.endswith("masked_spec_embed")
        for name, _parameter in model.named_parameters()
    ):
        raise BackendUnavailableError(
            "Forced-alignment model retained training-only SpecAugment parameters."
        )


def _alignment_inference_hygiene(model: Any) -> dict[str, bool | float]:
    model_config = model.config
    wav2vec2 = getattr(model, "wav2vec2", None)
    return {
        "evaluation_mode": not bool(model.training),
        "apply_spec_augment": bool(
            getattr(model_config, "apply_spec_augment", False)
        ),
        "mask_time_prob": float(getattr(model_config, "mask_time_prob", 0.0)),
        "mask_feature_prob": float(
            getattr(model_config, "mask_feature_prob", 0.0)
        ),
        "masked_spec_embed_present": (
            wav2vec2 is not None and hasattr(wav2vec2, "masked_spec_embed")
        )
        or any(
            name.endswith("masked_spec_embed")
            for name, _parameter in model.named_parameters()
        ),
    }


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
    aligned_by_source: list[list[dict[str, Any]]],
    *,
    expected_segments: list[ASRSegment],
    allow_localized_fallback: bool,
) -> tuple[list[AlignedWord], dict[str, Any]]:
    words: list[AlignedWord] = []
    aligned_segment_count = 0
    missing_segment_count = 0
    forced_word_count = 0
    fallback_word_count = 0
    fallback_segments: list[dict[str, int | str]] = []
    previous_start = -math.inf
    previous_end = -math.inf
    for segment_index, expected_segment in enumerate(expected_segments):
        source_results = (
            aligned_by_source[segment_index]
            if segment_index < len(aligned_by_source)
            else []
        )
        segment_words, character_projection_reason = (
            _source_segment_forced_words(source_results, expected_segment)
        )
        reason = character_projection_reason or _forced_segment_defect(
            segment_words,
            expected_segment,
            previous_start=previous_start,
            previous_end=previous_end,
        )
        if reason is None:
            words.extend(segment_words)
            aligned_segment_count += 1
            forced_word_count += len(segment_words)
            last_word = segment_words[-1]
            if last_word.start is not None:
                previous_start = last_word.start
            if last_word.end is not None:
                previous_end = last_word.end
            continue

        if not allow_localized_fallback:
            raise BackendUnavailableError(
                "Forced alignment failed for source segment "
                f"{segment_index}: {reason}."
            )
        fallback_reason = f"forced_alignment_fallback_{reason}"
        fallback_words, _cursor = fallback_segment_words(
            expected_segment,
            max(0.0, previous_end),
            warning=_LOCALIZED_FALLBACK_WARNING,
            reason=fallback_reason,
        )
        if fallback_words and not _words_within_source_segment(
            fallback_words,
            expected_segment,
        ):
            raise BackendUnavailableError(
                "Localized alignment fallback could not preserve source bounds."
            )
        if fallback_words:
            words.extend(fallback_words)
            fallback_word_count += len(fallback_words)
            fallback_segments.append(
                {
                    "segment_index": segment_index,
                    "segment_id": expected_segment.id,
                    "reason": reason,
                    "fallback_word_count": len(fallback_words),
                }
            )
            last_word = fallback_words[-1]
            previous_start = last_word.start if last_word.start is not None else previous_start
            previous_end = last_word.end if last_word.end is not None else previous_end
        else:
            missing_segment_count += 1
            words.append(_missing_segment_word(expected_segment, reason=reason))
    return words, {
        "expected_segment_count": len(expected_segments),
        "aligned_segment_count": aligned_segment_count,
        "missing_segment_count": missing_segment_count,
        "forced_word_count": forced_word_count,
        "fallback_word_count": fallback_word_count,
        "fallback_segments": fallback_segments,
    }


def _forced_segment_words(segment: object) -> list[AlignedWord]:
    if not isinstance(segment, dict):
        return []
    raw_words = segment.get("words", [])
    if not isinstance(raw_words, list):
        return []
    words: list[AlignedWord] = []
    for word in raw_words:
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


def _source_segment_forced_words(
    source_results: list[dict[str, Any]],
    expected_segment: ASRSegment,
) -> tuple[list[AlignedWord], str | None]:
    has_character_alignments = any("chars" in segment for segment in source_results)
    if expected_segment.words and has_character_alignments:
        return _project_character_alignments_to_asr_words(
            source_results,
            expected_segment,
        )
    return (
        [
            word
            for aligned_segment in source_results
            for word in _forced_segment_words(aligned_segment)
        ],
        None,
    )


def _project_character_alignments_to_asr_words(
    source_results: list[dict[str, Any]],
    expected_segment: ASRSegment,
) -> tuple[list[AlignedWord], str | None]:
    aligned_characters = [
        character
        for segment in source_results
        for character in _aligned_characters(segment)
        if str(character.get("char") or "") and not str(character["char"]).isspace()
    ]
    expected_words = [
        word for word in expected_segment.words if _compact_text(word.text)
    ]
    expected_character_text = "".join(
        _compact_text(word.text) for word in expected_words
    )
    aligned_character_text = "".join(
        str(character["char"]) for character in aligned_characters
    )
    if aligned_character_text != expected_character_text:
        return [], "character_sequence_mismatch"

    projected: list[AlignedWord] = []
    character_cursor = 0
    for expected_word in expected_words:
        token = _compact_text(expected_word.text)
        word_characters = aligned_characters[
            character_cursor : character_cursor + len(token)
        ]
        character_cursor += len(token)
        timed_pairs: list[tuple[float, float, float | None]] = []
        pair_start_cursor = -math.inf
        pair_end_cursor = -math.inf
        for character in word_characters:
            start = _optional_float(character.get("start"))
            end = _optional_float(character.get("end"))
            if start is None or end is None:
                return [], "missing_character_timestamp"
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or end <= start
                or start < pair_start_cursor
                or end < pair_end_cursor
            ):
                return [], "invalid_character_timestamp"
            score = _optional_float(character.get("score"))
            if score is not None and not math.isfinite(score):
                score = None
            timed_pairs.append((start, end, score))
            pair_start_cursor = start
            pair_end_cursor = end
        if not timed_pairs:
            return [], "missing_character_timestamp"
        scores = [score for _start, _end, score in timed_pairs if score is not None]
        projected.append(
            AlignedWord(
                text=expected_word.text.strip(),
                start=timed_pairs[0][0],
                end=timed_pairs[-1][1],
                confidence=sum(scores) / len(scores) if scores else None,
                alignment_source="forced",
            )
        )
    return projected, None


def _aligned_characters(segment: dict[str, Any]) -> list[dict[str, Any]]:
    raw_characters = segment.get("chars", [])
    if not isinstance(raw_characters, list):
        return []
    return [
        character
        for character in raw_characters
        if isinstance(character, dict)
    ]


def _forced_segment_defect(
    words: list[AlignedWord],
    expected_segment: ASRSegment,
    *,
    previous_start: float,
    previous_end: float,
) -> str | None:
    if not words:
        return "missing_segment"
    if _compact_text(" ".join(word.text for word in words)) != _compact_text(
        _segment_text(expected_segment)
    ):
        return "text_mismatch"
    expected_word_tokens = [
        _compact_text(word.text)
        for word in expected_segment.words
        if _compact_text(word.text)
    ]
    aligned_word_tokens = [
        _compact_text(word.text)
        for word in words
        if _compact_text(word.text)
    ]
    if expected_word_tokens and aligned_word_tokens != expected_word_tokens:
        return "word_sequence_mismatch"
    start_cursor = previous_start
    end_cursor = previous_end
    for word in words:
        start = word.start
        end = word.end
        if start is None or end is None or word.alignment_source != "forced":
            return "missing_timestamp"
        if not math.isfinite(start) or not math.isfinite(end):
            return "non_finite_timestamp"
        if start < 0:
            return "negative_timestamp"
        if end <= start:
            return "non_positive_duration"
        if (
            start < expected_segment.start - 0.05
            or end > expected_segment.end + 0.05
        ):
            return "out_of_segment_bounds"
        if start < start_cursor or end < end_cursor:
            return "non_monotonic_timestamp"
        start_cursor = start
        end_cursor = end
    return None


def _compact_text(value: str) -> str:
    return "".join(value.split())


def _segment_text(segment: ASRSegment) -> str:
    text = segment.text.strip()
    if text:
        return text
    return " ".join(word.text.strip() for word in segment.words if word.text.strip())


def _words_within_source_segment(
    words: list[AlignedWord],
    segment: ASRSegment,
) -> bool:
    tolerance_seconds = 0.05
    return all(
        word.start is not None
        and word.end is not None
        and word.start >= segment.start - tolerance_seconds
        and word.end <= segment.end + tolerance_seconds
        for word in words
    )


def _missing_segment_word(
    expected_segment: ASRSegment,
    *,
    reason: str,
) -> AlignedWord:
    return AlignedWord(
        text=_segment_text(expected_segment),
        alignment_source="missing",
        warnings=[
            "forced_alignment_missing_segment",
            f"forced_alignment_fallback_{reason}",
        ],
    )


def _optional_float(value: object) -> float | None:
    if value is None or not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
