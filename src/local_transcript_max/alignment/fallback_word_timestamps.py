# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fallback alignment from faster-whisper word timestamps."""

from __future__ import annotations

from pathlib import Path

from local_transcript_max.config import PipelineConfig
from local_transcript_max.schemas import AlignedWord, ASRResult


class FallbackWordTimestampAlignment:
    name = "fallback-word-timestamps"

    def align(
        self, audio_path: Path, asr_result: ASRResult, config: PipelineConfig
    ) -> list[AlignedWord]:
        words: list[AlignedWord] = []
        cursor = 0.0
        for segment in asr_result.segments:
            if segment.words:
                for word in segment.words:
                    repaired = normalize_word_timing(word, cursor, source="whisper")
                    words.append(repaired)
                    if repaired.end is not None:
                        cursor = repaired.end
                continue
            tokens = segment.text.split()
            if not tokens:
                continue
            span = max(0.0, segment.end - segment.start)
            step = span / len(tokens) if tokens else 0.0
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
                words.append(word)
                if word.end is not None:
                    cursor = word.end
        return words


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
