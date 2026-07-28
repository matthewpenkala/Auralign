# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fallback alignment from faster-whisper word timestamps."""

from __future__ import annotations

import math
from pathlib import Path

from local_transcript_max.config import PipelineConfig
from local_transcript_max.schemas import AlignedWord, ASRResult, ASRSegment


class FallbackWordTimestampAlignment:
    name = "fallback-word-timestamps"

    def align(
        self, audio_path: Path, asr_result: ASRResult, config: PipelineConfig
    ) -> list[AlignedWord]:
        words: list[AlignedWord] = []
        cursor = 0.0
        for segment in asr_result.segments:
            segment_words, cursor = fallback_segment_words(segment, cursor)
            words.extend(segment_words)
        return words


def fallback_segment_words(
    segment: ASRSegment,
    cursor: float,
    *,
    warning: str | None = None,
    reason: str | None = None,
) -> tuple[list[AlignedWord], float]:
    """Return one complete, monotonic segment from preserved ASR timing evidence."""

    words: list[AlignedWord] = []
    warning_values = [value for value in (warning, reason) if value]
    if segment.words:
        for word in segment.words:
            repaired = normalize_word_timing(word, cursor, source="whisper")
            repaired = _append_warnings(repaired, warning_values)
            words.append(repaired)
            if repaired.end is not None:
                cursor = repaired.end
        return words, cursor

    tokens = segment.text.split()
    if not tokens:
        return words, cursor
    span = max(0.0, segment.end - segment.start)
    step = span / len(tokens)
    for index, token in enumerate(tokens):
        start = segment.start + index * step
        word = normalize_word_timing(
            AlignedWord(
                text=token,
                start=start,
                end=start + step,
                alignment_source="estimated",
                warnings=["estimated_from_segment_timing"],
            ),
            cursor,
            source="estimated",
        )
        word = _append_warnings(word, warning_values)
        words.append(word)
        if word.end is not None:
            cursor = word.end
    return words, cursor


def normalize_word_timing(word: AlignedWord, cursor: float, *, source: str) -> AlignedWord:
    warnings = list(word.warnings)
    start = word.start
    end = word.end
    if start is None or end is None:
        if "missing_word_timestamp" not in warnings:
            warnings.append("missing_word_timestamp")
        return word.model_copy(
            update={"alignment_source": "missing", "warnings": sorted(set(warnings))}
        )
    if not math.isfinite(start) or not math.isfinite(end):
        warnings.append("invalid_word_timestamp")
        return word.model_copy(
            update={
                "start": None,
                "end": None,
                "alignment_source": "missing",
                "warnings": sorted(set(warnings)),
            }
        )
    if end < start:
        end = start
        warnings.append("repaired_negative_duration")
    if start < cursor:
        delta = cursor - start
        start = cursor
        end = max(end + delta, start + 0.001)
        warnings.append("repaired_non_monotonic_start")
    if end <= start:
        end = start + 0.001
        warnings.append("repaired_zero_duration")
    return word.model_copy(
        update={
            "start": start,
            "end": end,
            "alignment_source": source,
            "warnings": sorted(set(warnings)),
        }
    )


def _append_warnings(word: AlignedWord, warnings: list[str]) -> AlignedWord:
    if not warnings:
        return word
    return word.model_copy(
        update={"warnings": sorted(set((*word.warnings, *warnings)))}
    )
