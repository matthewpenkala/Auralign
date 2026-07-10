# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Assign diarization speakers to word-level timestamps."""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from local_transcript_max.schemas import AlignedWord, DiarizationTurn


def assign_speakers_to_words(
    words: list[AlignedWord],
    turns: list[DiarizationTurn],
) -> list[AlignedWord]:
    sorted_turns = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    turn_index = _TurnIntervalIndex(sorted_turns)
    monotonic_words = _words_are_monotonic(words)
    assigned: list[AlignedWord] = []
    first_candidate_index = 0
    for word in words:
        if word.start is None or word.end is None or word.end < word.start:
            assigned.append(
                word.model_copy(
                    update={
                        "speaker": None,
                        "uncertain_speaker": True,
                        "assignment_source": "missing_word_timestamp",
                    }
                )
            )
            continue
        if word.end == word.start:
            midpoint_speaker = turn_index.speaker_at(word.start)
            assigned.append(
                word.model_copy(
                    update={
                        "speaker": midpoint_speaker,
                        "uncertain_speaker": True,
                        "speaker_overlap_seconds": 0.0,
                        "speaker_overlap_ratio": 0.0,
                        "assignment_source": "zero_duration_midpoint",
                    }
                )
            )
            continue
        word_duration = max(0.0, word.end - word.start)
        if monotonic_words:
            while (
                first_candidate_index < len(sorted_turns)
                and sorted_turns[first_candidate_index].end <= word.start
            ):
                first_candidate_index += 1
            scan_start = first_candidate_index
        else:
            scan_start = 0
        overlaps: list[tuple[float, DiarizationTurn]] = []
        for turn in turn_index.overlapping(word.start, word.end, start_index=scan_start):
            score = temporal_overlap(word.start, word.end, turn.start, turn.end)
            if score > 0:
                overlaps.append((score, turn))
        overlaps.sort(key=lambda item: item[0], reverse=True)
        uncertain = False
        speaker: str | None = None
        overlap_seconds: float | None = None
        overlap_ratio: float | None = None
        candidates = [
            {
                "speaker": turn.speaker,
                "overlap_seconds": round(score, 6),
                "overlap_ratio": round(score / word_duration, 6) if word_duration else 0.0,
                "turn_start": turn.start,
                "turn_end": turn.end,
                "turn_overlap": turn.overlap,
            }
            for score, turn in overlaps
        ]
        source = "temporal_overlap"
        if overlaps:
            speaker = overlaps[0][1].speaker
            overlap_seconds = overlaps[0][0]
            overlap_ratio = overlaps[0][0] / word_duration if word_duration else 0.0
            if overlaps[0][0] / word_duration < 0.5:
                uncertain = True
            if any(turn.overlap for _, turn in overlaps):
                uncertain = True
            if len(overlaps) > 1 and overlaps[1][0] / overlaps[0][0] > 0.75:
                uncertain = True
        else:
            midpoint = (word.start + word.end) / 2
            speaker = turn_index.speaker_at(midpoint)
            if speaker:
                uncertain = True
                source = "midpoint_gap"
            else:
                uncertain = True
                source = "no_diarization_coverage"
        assigned.append(
            word.model_copy(
                update={
                    "speaker": speaker,
                    "uncertain_speaker": uncertain,
                    "speaker_overlap_seconds": overlap_seconds,
                    "speaker_overlap_ratio": overlap_ratio,
                    "speaker_candidates": candidates,
                    "assignment_source": source,
                }
            )
        )
    return assigned


def _words_are_monotonic(words: list[AlignedWord]) -> bool:
    previous_start: float | None = None
    for word in words:
        if word.start is None:
            continue
        if previous_start is not None and word.start < previous_start:
            return False
        previous_start = word.start
    return True


def temporal_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def speaker_at_midpoint(midpoint: float, turns: list[DiarizationTurn]) -> str | None:
    return _TurnIntervalIndex(
        sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    ).speaker_at(
        midpoint
    )


class _TurnIntervalIndex:
    def __init__(self, turns: list[DiarizationTurn]) -> None:
        self.turns = turns
        self.starts = [turn.start for turn in turns]

    def overlapping(
        self,
        start: float,
        end: float,
        *,
        start_index: int = 0,
    ) -> list[DiarizationTurn]:
        right_index = bisect_left(self.starts, end)
        return [
            turn
            for turn in self.turns[max(0, start_index) : right_index]
            if turn.start < end and turn.end > start
        ]

    def speaker_at(self, midpoint: float) -> str | None:
        right_index = bisect_right(self.starts, midpoint)
        containing = [
            turn
            for turn in self.turns[:right_index]
            if turn.start <= midpoint <= turn.end
        ]
        if not containing:
            return None
        containing.sort(key=lambda turn: (turn.end - turn.start, turn.speaker), reverse=True)
        return containing[0].speaker
