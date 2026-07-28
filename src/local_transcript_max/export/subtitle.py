# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Shared subtitle cue formatting for SRT and WebVTT exports."""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass

from local_transcript_max.config import SubtitleExportConfig
from local_transcript_max.schemas import AlignedWord, SpeakerTurn, Transcript

_STRONG_PUNCTUATION = (".", "?", "!", "。", "？", "！")
_MEDIUM_PUNCTUATION = (",", ";", ":", "，", "、", "；", "：", "…")
_TRAILING_CLOSERS = "\"'”’)]}）】」』》"
_HARD_PAUSE_SECONDS = 2.5
_BAD_TRAILING_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "if",
        "in",
        "into",
        "is",
        "must",
        "no",
        "nor",
        "not",
        "of",
        "on",
        "or",
        "should",
        "so",
        "than",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
        "would",
        "yet",
    }
)
_PREFERRED_LEADING_WORDS = frozenset(
    {
        "although",
        "and",
        "because",
        "but",
        "however",
        "if",
        "or",
        "since",
        "so",
        "though",
        "unless",
        "until",
        "when",
        "whereas",
        "while",
        "yet",
    }
)
_BAD_BOUNDARY_PAIRS = frozenset(
    {
        ("able", "to"),
        ("going", "to"),
        ("got", "to"),
        ("had", "to"),
        ("has", "to"),
        ("have", "to"),
        ("need", "to"),
        ("needs", "to"),
        ("supposed", "to"),
        ("try", "to"),
        ("trying", "to"),
        ("used", "to"),
        ("want", "to"),
        ("wants", "to"),
    }
)


@dataclass(frozen=True)
class SubtitleCue:
    speaker: str
    start: float
    end: float
    text: str
    words: list[AlignedWord]
    speaker_on_own_line: bool = False

    def with_text(self, text: str) -> SubtitleCue:
        return SubtitleCue(
            speaker=self.speaker,
            start=self.start,
            end=self.end,
            text=text,
            words=self.words,
            speaker_on_own_line=self.speaker_on_own_line,
        )


@dataclass(frozen=True)
class _SpeakerLayout:
    first_line_reserved_width: int
    speaker_on_own_line: bool
    body_line_count: int | None


@dataclass(frozen=True)
class _WordPartition:
    """One deterministic globally scored partition of a word-timed turn."""

    cue_count: int
    orphan_severity_counts: tuple[int, int, int, int]
    total_orphan_penalty: int
    boundary_severity_counts: tuple[int, ...]
    total_boundary_penalty: int
    total_balance_penalty: int
    next_index: int

    @property
    def score(
        self,
    ) -> tuple[
        int,
        tuple[int, int, int, int],
        int,
        tuple[int, ...],
        int,
        int,
        int,
    ]:
        return (
            self.cue_count,
            self.orphan_severity_counts,
            self.total_orphan_penalty,
            self.boundary_severity_counts,
            self.total_boundary_penalty,
            self.total_balance_penalty,
            self.next_index,
        )


@dataclass(frozen=True)
class _TextPartition:
    """One balanced set of visual line ranges for a subtitle cue."""

    line_count: int
    orphan_penalty: int
    boundary_penalty: int
    balance_penalty: int
    ranges: tuple[tuple[int, int], ...]

    @property
    def score(self) -> tuple[int, int, int, int, tuple[tuple[int, int], ...]]:
        return (
            self.line_count,
            self.orphan_penalty,
            self.boundary_penalty,
            self.balance_penalty,
            self.ranges,
        )


def iter_subtitle_cues(
    transcript: Transcript,
    options: SubtitleExportConfig | None = None,
) -> list[SubtitleCue]:
    options = options or SubtitleExportConfig()
    cues = normalize_cues(
        transcript.speaker_turns
        or [
            SpeakerTurn(
                speaker="TRANSCRIPT",
                start=segment.start,
                end=segment.end,
                text=segment.text,
            )
            for segment in transcript.segments
        ]
    )
    rendered: list[SubtitleCue] = []
    for speaker_turn in cues:
        base = SubtitleCue(
            speaker=speaker_display_name(speaker_turn, options),
            start=speaker_turn.start,
            end=speaker_turn.end,
            text=sanitize_subtitle_text(speaker_turn.text),
            words=speaker_turn.words,
        )
        if options.split_long_cues:
            rendered.extend(split_cue_by_words(base, options, transcript.language))
        else:
            rendered.append(base)
    highlighted: list[SubtitleCue] = []
    for subtitle_cue in rendered:
        if options.highlight_words:
            highlighted.extend(
                highlight_cue_words(subtitle_cue, transcript.language, options)
            )
        else:
            highlighted.append(subtitle_cue)
    return [subtitle_cue for subtitle_cue in highlighted if subtitle_cue.text.strip()]


def overlay_cue_word_surfaces(
    cues: list[SubtitleCue],
    word_surfaces: list[str],
    options: SubtitleExportConfig,
    language: str | None,
) -> list[SubtitleCue]:
    """Overlay validated word surfaces without recalculating cue ownership or timing."""

    joiner = "" if _uses_compact_script(language) else " "
    cursor = 0
    overlaid: list[SubtitleCue] = []
    for cue in cues:
        cue_words = [word for word in cue.words if word.text.strip()]
        if not cue_words:
            overlaid.append(cue)
            continue
        end = cursor + len(cue_words)
        if end > len(word_surfaces):
            raise ValueError("subtitle surface overlay ended before cue words")
        surfaces = word_surfaces[cursor:end]
        cursor = end
        wrapped_repair = _wrap_surface_cue(
            cue,
            cue_words,
            surfaces,
            options,
            language,
            joiner,
        )
        if (
            not _formatted_cue_fits_limits(wrapped_repair, options)
            and _formatted_cue_fits_limits(cue, options)
        ):
            # Added punctuation consumes visible width. Preserve strong marks
            # and capitalization while removing only the minimum weakest added
            # punctuation needed to keep the frozen cue spatially conforming.
            relaxed_surfaces = list(surfaces)
            for surface_index in _surface_relaxation_order(
                cue_words,
                relaxed_surfaces,
            ):
                relaxed_surfaces[surface_index] = (
                    _remove_added_trailing_punctuation(
                        cue_words[surface_index].text,
                        relaxed_surfaces[surface_index],
                    )
                )
                relaxed_cue = _wrap_surface_cue(
                    cue,
                    cue_words,
                    relaxed_surfaces,
                    options,
                    language,
                    joiner,
                )
                if _formatted_cue_fits_limits(relaxed_cue, options):
                    wrapped_repair = relaxed_cue
                    break
            else:
                # If even removing every model-added mark cannot fit, retain the
                # original presentation rather than retiming or overflowing.
                wrapped_repair = cue
            overlaid.append(wrapped_repair)
        else:
            overlaid.append(wrapped_repair)
    if cursor != len(word_surfaces):
        raise ValueError("subtitle surface overlay has unused words")
    return overlaid


def _wrap_surface_cue(
    cue: SubtitleCue,
    cue_words: list[AlignedWord],
    surfaces: list[str],
    options: SubtitleExportConfig,
    language: str | None,
    joiner: str,
) -> SubtitleCue:
    repaired_words = [
        word.model_copy(update={"text": surface}, deep=True)
        for word, surface in zip(cue_words, surfaces, strict=True)
    ]
    return _wrap_cue(
        SubtitleCue(
            speaker=cue.speaker,
            start=cue.start,
            end=cue.end,
            text=joiner.join(surfaces),
            words=repaired_words,
        ),
        options,
        language,
    )


def _surface_relaxation_order(
    cue_words: list[AlignedWord],
    surfaces: list[str],
) -> list[int]:
    candidates: list[tuple[int, float, int]] = []
    for index, (word, surface) in enumerate(
        zip(cue_words, surfaces, strict=True)
    ):
        original_mark = _surface_trailing_punctuation(word.text)
        added_mark = _surface_trailing_punctuation(surface)
        if original_mark or not added_mark:
            continue
        priority = 0 if added_mark in _MEDIUM_PUNCTUATION else 1
        following_pause = (
            word_pause(word, cue_words[index + 1])
            if index + 1 < len(cue_words)
            else math.inf
        )
        candidates.append((priority, following_pause, index))
    return [index for _, _, index in sorted(candidates)]


def _surface_trailing_punctuation(text: str) -> str:
    body = text.rstrip(_TRAILING_CLOSERS)
    if not body:
        return ""
    mark = body[-1]
    return mark if mark in (*_STRONG_PUNCTUATION, *_MEDIUM_PUNCTUATION) else ""


def _remove_added_trailing_punctuation(original: str, surface: str) -> str:
    if _surface_trailing_punctuation(original):
        return surface
    closer_start = len(surface)
    while closer_start > 0 and surface[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    body = surface[:closer_start]
    closers = surface[closer_start:]
    if body and body[-1] in (*_STRONG_PUNCTUATION, *_MEDIUM_PUNCTUATION):
        body = body[:-1]
    return f"{body}{closers}"


def normalize_cues(cues: list[SpeakerTurn]) -> list[SpeakerTurn]:
    normalized: list[SpeakerTurn] = []
    for cue in sorted(cues, key=lambda item: (item.start, item.end, item.speaker)):
        text = cue.text.strip()
        if not text:
            continue
        start = max(0.0, cue.start)
        end = max(start + 0.001, cue.end)
        normalized.append(cue.model_copy(update={"start": start, "end": end, "text": text}))
    return normalized


def split_cue_by_words(
    cue: SubtitleCue,
    options: SubtitleExportConfig,
    language: str | None,
) -> list[SubtitleCue]:
    speaker_prefix_width = _speaker_prefix_width(cue, options)
    if not cue.words:
        return [_wrap_cue(cue, options, language)]
    words = [word for word in cue.words if word.text.strip()]
    if len(words) < 2 or not _has_usable_word_timing(words, cue):
        # Never invent split timestamps. With absent, invalid, overlapping, or
        # non-monotonic alignment, preserve the truthful turn interval and make
        # visual limits best-effort.
        return [_wrap_cue(cue, options, language)]

    contains_oversized_word = (
        options.max_line_width is not None
        and any(
            len(_normalize_text(word.text, language)) > options.max_line_width
            for word in words
        )
    )
    if (
        _full_cue_fits(cue, options, language)
        and not _contains_hard_pause(words)
        and not contains_oversized_word
    ):
        return [_wrap_cue(cue, options, language)]

    joiner = "" if _uses_compact_script(language) else " "
    ranges = _balanced_word_ranges(
        words,
        cue,
        options,
        joiner,
        language,
        speaker_prefix_width=speaker_prefix_width,
    )
    if len(ranges) <= 1:
        return [_wrap_cue(cue, options, language)]

    split: list[SubtitleCue] = []
    for start_index, end_index in ranges:
        chunk = words[start_index:end_index]
        text = joiner.join(word.text.strip() for word in chunk).strip()
        # ``_has_usable_word_timing`` proves these values are complete and
        # monotonic before partitioning.
        start = chunk[0].start
        end = chunk[-1].end
        if start is None or end is None:  # Defensive against future schema changes.
            return [_wrap_cue(cue, options, language)]
        split.append(
            _wrap_cue(
                SubtitleCue(
                speaker=cue.speaker,
                start=max(cue.start, start),
                end=min(cue.end, max(start + 0.001, end)),
                text=sanitize_subtitle_text(text),
                words=list(chunk),
                ),
                options,
                language,
            )
        )
    return split


def _full_cue_fits(
    cue: SubtitleCue,
    options: SubtitleExportConfig,
    language: str | None,
) -> bool:
    layout = _speaker_layout(cue, options, language)
    return (
        cue.end - cue.start <= options.max_cue_duration
        and _text_fits(
            cue.text,
            options.max_line_width,
            layout.body_line_count,
            language,
            first_line_reserved_width=layout.first_line_reserved_width,
        )
    )


def _has_usable_word_timing(words: list[AlignedWord], cue: SubtitleCue) -> bool:
    previous_end = cue.start
    for word in words:
        if word.start is None or word.end is None:
            return False
        if not math.isfinite(word.start) or not math.isfinite(word.end):
            return False
        if word.start < cue.start or word.end > cue.end or word.end <= word.start:
            return False
        if word.start < previous_end:
            return False
        previous_end = word.end
    return True


def _contains_hard_pause(words: list[AlignedWord]) -> bool:
    return any(
        word_pause(left, right) > _HARD_PAUSE_SECONDS
        for left, right in zip(words, words[1:], strict=False)
    )


def _balanced_word_ranges(
    words: list[AlignedWord],
    cue: SubtitleCue,
    options: SubtitleExportConfig,
    joiner: str,
    language: str | None,
    *,
    speaker_prefix_width: int = 0,
) -> tuple[tuple[int, int], ...]:
    """Choose the globally best feasible partition instead of greedily filling cues.

    The score is lexicographic: use the fewest feasible cues, then minimize
    additive counts of catastrophic-to-minor orphan and boundary severities,
    their totals, and normalized imbalance. Severity histograms preserve
    worst-first behavior without a non-composable ``max`` term, so each suffix
    has a true optimal substructure. A stable next-boundary index is the final
    tie-breaker.
    """

    word_count = len(words)
    token_texts = [_normalize_text(word.text, language) for word in words]
    token_units = [_text_units(token, language) for token in token_texts]
    token_lengths = [len(token) for token in token_texts]
    prefix_lengths = [0]
    for token_length in token_lengths:
        prefix_lengths.append(prefix_lengths[-1] + token_length)
    best: list[_WordPartition | None] = [None] * (word_count + 1)
    best[word_count] = _WordPartition(
        0,
        (0, 0, 0, 0),
        0,
        (0,) * 23,
        0,
        0,
        word_count,
    )
    for start_index in range(word_count - 1, -1, -1):
        layout = _speaker_layout_for_first_unit(
            speaker_prefix_width=speaker_prefix_width,
            max_line_width=options.max_line_width,
            max_line_count=options.max_line_count,
            first_unit_width=token_lengths[start_index],
        )
        line_count = 1
        current_line_width = 0
        oversized_token_count = 0
        for end_index in range(start_index + 1, word_count + 1):
            count = end_index - start_index
            if count > 1 and word_pause(words[end_index - 2], words[end_index - 1]) > (
                _HARD_PAUSE_SECONDS
            ):
                # Every longer range crosses the same forbidden pause.
                break
            if options.max_line_width is not None:
                if token_lengths[end_index - 1] > options.max_line_width:
                    oversized_token_count += 1
                for unit in token_units[end_index - 1]:
                    separator_width = len(joiner) if current_line_width else 0
                    projected = current_line_width + separator_width + len(unit)
                    available_width = _line_width(
                        options.max_line_width,
                        line_count - 1,
                        layout.first_line_reserved_width,
                    )
                    if projected <= available_width or current_line_width == 0:
                        current_line_width = projected
                    else:
                        line_count += 1
                        current_line_width = len(unit)
            if count > 1 and oversized_token_count:
                # A single indivisible atom may exceed the width, but it must
                # not absorb neighbors and turn an explicit exception into a
                # generally non-conforming cue.
                break
            duration = _word_range_duration(words, start_index, end_index, cue)
            if count > 1 and duration is not None and duration > options.max_cue_duration:
                # Complete monotonic timestamps make duration non-decreasing.
                break
            if (
                count > 1
                and layout.body_line_count is not None
                and line_count > layout.body_line_count
            ):
                # Greedy ordered wrapping has the minimum possible line count,
                # which cannot decrease as this range grows.
                break
            text_length = (
                prefix_lengths[end_index]
                - prefix_lengths[start_index]
                + len(joiner) * (count - 1)
            )
            remainder = best[end_index]
            if remainder is None:
                continue
            orphan_penalty = _orphan_penalty(
                words,
                start_index,
                end_index,
                duration,
            )
            boundary_penalty = _boundary_penalty(words, end_index)
            balance_penalty = _balance_penalty(
                text_length,
                duration,
                options,
                first_line_reserved_width=layout.first_line_reserved_width,
                max_line_count=layout.body_line_count,
            )
            candidate = _WordPartition(
                cue_count=1 + remainder.cue_count,
                orphan_severity_counts=_add_orphan_counts(
                    _orphan_severity_counts(orphan_penalty),
                    remainder.orphan_severity_counts,
                ),
                total_orphan_penalty=(
                    orphan_penalty + remainder.total_orphan_penalty
                ),
                boundary_severity_counts=_add_boundary_counts(
                    _boundary_severity_counts(boundary_penalty),
                    remainder.boundary_severity_counts,
                ),
                total_boundary_penalty=(
                    boundary_penalty + remainder.total_boundary_penalty
                ),
                total_balance_penalty=(
                    balance_penalty + remainder.total_balance_penalty
                ),
                next_index=end_index,
            )
            current = best[start_index]
            if current is None or candidate.score < current.score:
                best[start_index] = candidate
    if best[0] is None:  # Single words are always accepted, so this is defensive.
        return ((0, word_count),)
    ranges: list[tuple[int, int]] = []
    cursor = 0
    while cursor < word_count:
        partition = best[cursor]
        if partition is None or partition.next_index <= cursor:
            return ((0, word_count),)
        ranges.append((cursor, partition.next_index))
        cursor = partition.next_index
    return tuple(ranges)


def _add_orphan_counts(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    return (
        left[0] + right[0],
        left[1] + right[1],
        left[2] + right[2],
        left[3] + right[3],
    )


def _add_boundary_counts(
    left: tuple[int, ...],
    right: tuple[int, ...],
) -> tuple[int, ...]:
    return tuple(a + b for a, b in zip(left, right, strict=True))


def _orphan_severity_counts(penalty: int) -> tuple[int, int, int, int]:
    """Count one edge in descending severity buckets for additive ordering."""

    return (
        int(penalty >= 1000),
        int(400 <= penalty < 1000),
        int(100 <= penalty < 400),
        int(0 < penalty < 100),
    )


def _boundary_severity_counts(penalty: int) -> tuple[int, ...]:
    """Count one boundary by exact severity, from worst (22) to best (0)."""

    bounded = min(22, max(0, penalty))
    return tuple(int(bounded == severity) for severity in range(22, -1, -1))


def _word_range_duration(
    words: list[AlignedWord],
    start_index: int,
    end_index: int,
    cue: SubtitleCue,
) -> float | None:
    if start_index == 0 and end_index == len(words):
        return max(0.0, cue.end - cue.start)
    start = words[start_index].start
    end = words[end_index - 1].end
    if start is None or end is None:
        return None
    return max(0.0, end - start)


def _orphan_penalty(
    words: list[AlignedWord],
    start_index: int,
    end_index: int,
    duration: float | None,
) -> int:
    total_words = len(words)
    word_count = end_index - start_index
    if total_words <= 4:
        return 0
    entry_is_semantic = start_index == 0 or _boundary_penalty(words, start_index) <= 2
    exit_is_sentence = _ends_with_punctuation(
        words[end_index - 1].text.strip(),
        _STRONG_PUNCTUATION,
    )
    if word_count < 4 and entry_is_semantic and exit_is_sentence:
        # A genuinely complete short sentence is not an orphan merely because
        # it has few words.
        return 0
    word_penalty = max(0, 4 - word_count) ** 2 * 100
    duration_penalty = (
        0 if duration is None or duration >= 1.0 else int(round((1.0 - duration) * 100))
    )
    if (
        start_index > 0
        and _boundary_word(words[start_index - 1].text) in _BAD_TRAILING_WORDS
    ):
        # This directly captures the reported ``... infantile or | childish``
        # failure class and analogous dangling syntactic connectors.
        word_penalty += 1000
    return word_penalty + duration_penalty


def _boundary_penalty(words: list[AlignedWord], end_index: int) -> int:
    if end_index >= len(words):
        return 0
    left = words[end_index - 1]
    right = words[end_index]
    pause = word_pause(left, right)
    left_text = left.text.strip()
    if pause > _HARD_PAUSE_SECONDS or _ends_with_punctuation(
        left_text, _STRONG_PUNCTUATION
    ):
        penalty = 0
    elif pause >= 0.5:
        penalty = 1
    elif _ends_with_punctuation(left_text, _MEDIUM_PUNCTUATION):
        penalty = 2
    elif pause >= 0.25 or left_text.endswith(("-", "–", "—")):
        penalty = 3
    else:
        penalty = 6
    left_word = _boundary_word(left_text)
    right_word = _boundary_word(right.text)
    if left_word in _BAD_TRAILING_WORDS:
        penalty += 8
    if (left_word, right_word) in _BAD_BOUNDARY_PAIRS:
        penalty += 8
    if right_word in _PREFERRED_LEADING_WORDS:
        # Prefer starting a new clause with its conjunction/subordinator.
        penalty = max(0, penalty - 2)
    return penalty


def _ends_with_punctuation(text: str, punctuation: tuple[str, ...]) -> bool:
    return text.rstrip(_TRAILING_CLOSERS).endswith(punctuation)


def _boundary_word(text: str) -> str:
    return text.casefold().strip(
        ".,;:!?。？！…，、；：()[]{}（）【】「」『』《》\"'“”‘’"
    )


def _balance_penalty(
    text_length: int,
    duration: float | None,
    options: SubtitleExportConfig,
    *,
    first_line_reserved_width: int = 0,
    max_line_count: int | None = None,
) -> int:
    capacity = max_chars_per_cue(
        options,
        first_line_reserved_width=first_line_reserved_width,
        max_line_count=max_line_count,
    )
    text_slack = max(0, capacity - text_length)
    text_slack_ratio = int(round(text_slack * 1000 / capacity))
    duration_slack_ratio = (
        0
        if duration is None
        else int(
            round(
                max(0.0, options.max_cue_duration - duration)
                * 1000
                / options.max_cue_duration
            )
        )
    )
    return text_slack_ratio**2 + duration_slack_ratio**2


def _text_fits(
    text: str,
    max_line_width: int | None,
    max_line_count: int | None,
    language: str | None = None,
    *,
    first_line_reserved_width: int = 0,
) -> bool:
    if not max_line_width or max_line_width <= 0 or not max_line_count:
        return True
    # Greedy wrapping yields the minimum possible line count for ordered words,
    # so it is the inexpensive feasibility check. Global balancing is performed
    # only once for each final cue in ``wrap_text``.
    return (
        len(
            _greedy_wrap_lines(
                text,
                max_line_width,
                language,
                first_line_reserved_width=first_line_reserved_width,
            )
        )
        <= max_line_count
    )


def max_chars_per_cue(
    options: SubtitleExportConfig,
    *,
    first_line_reserved_width: int = 0,
    max_line_count: int | None = None,
) -> int:
    width = options.max_line_width or 120
    lines = max_line_count or options.max_line_count or 4
    return max(1, width * lines - first_line_reserved_width)


def timestamp_milliseconds(seconds: float) -> int:
    """Quantize a non-negative timestamp with the exporter's stable rule."""

    return int(round(max(0.0, seconds) * 1000))


def cue_millisecond_interval(start: float, end: float) -> tuple[int, int]:
    """Quantize once and preserve a strictly positive serialized interval."""

    start_ms = timestamp_milliseconds(start)
    end_ms = max(start_ms + 1, timestamp_milliseconds(end))
    return start_ms, end_ms


def word_pause(left: AlignedWord, right: AlignedWord) -> float:
    if left.end is None or right.start is None:
        return 0.0
    return max(0.0, right.start - left.end)


def highlight_cue_words(
    cue: SubtitleCue,
    language: str | None,
    options: SubtitleExportConfig,
) -> list[SubtitleCue]:
    words = [word for word in cue.words if word.text.strip()]
    if not _has_usable_word_timing(words, cue):
        # Highlight timing must meet the same truthfulness contract as cue
        # splitting. Do not omit untimed words, fabricate tiny cues, or pass
        # NaN/Inf through a serializer.
        return [_wrap_cue(cue, options, language)]
    joiner = "" if _uses_compact_script(language) else " "
    tokens = [_normalize_text(word.text, language) for word in words]
    plain_text = joiner.join(tokens)
    token_offsets: list[int] = []
    cursor = 0
    for token in tokens:
        token_offsets.append(cursor)
        cursor += len(token) + len(joiner)
    layout = _speaker_layout(cue, options, language)
    wrapped_plain_text = wrap_text(
        plain_text,
        options.max_line_width,
        layout.body_line_count,
        language=language,
        first_line_reserved_width=layout.first_line_reserved_width,
    )
    highlighted: list[SubtitleCue] = []
    for index, word in enumerate(words):
        if word.start is None or word.end is None:
            continue
        start_offset = token_offsets[index]
        text = _underline_visible_span(
            wrapped_plain_text,
            start_offset,
            start_offset + len(tokens[index]),
            newline_source_width=len(joiner),
        )
        highlighted.append(
            SubtitleCue(
                speaker=cue.speaker,
                start=max(cue.start, word.start),
                end=max(word.start + 0.001, word.end),
                text=text,
                words=cue.words,
                speaker_on_own_line=layout.speaker_on_own_line,
            )
        )
    return highlighted or [cue]


def _underline_visible_span(
    text: str,
    start: int,
    end: int,
    *,
    newline_source_width: int,
) -> str:
    """Inject underline markup without treating tags as visible wrap units."""

    output: list[str] = []
    visible_offset = 0
    opened = False
    for character in text:
        if character == "\n":
            output.append(character)
            visible_offset += newline_source_width
            continue
        if visible_offset == start:
            output.append("<u>")
            opened = True
        output.append(character)
        visible_offset += 1
        if opened and visible_offset == end:
            output.append("</u>")
            opened = False
    if opened:
        output.append("</u>")
    return "".join(output)


def wrap_text(
    text: str,
    max_line_width: int | None,
    max_line_count: int | None,
    *,
    language: str | None = None,
    first_line_reserved_width: int = 0,
) -> str:
    clean = _normalize_text(text, language)
    if not max_line_width or max_line_width <= 0:
        return clean
    balanced = (
        None
        if not max_line_count
        else _balanced_text_ranges(
            clean,
            max_line_width,
            max_line_count,
            language,
            first_line_reserved_width=first_line_reserved_width,
        )
    )
    lines = (
        _greedy_wrap_lines(
            clean,
            max_line_width,
            language,
            first_line_reserved_width=first_line_reserved_width,
        )
        if balanced is None
        else _text_lines(clean, balanced, language)
    )
    if not lines:
        return clean
    # When timing is unavailable, retaining all width-respecting lines is less
    # destructive than folding overflow into one over-wide line. The configured
    # line count is explicitly best-effort for that unsplittable case.
    return "\n".join(lines)


def _balanced_text_ranges(
    text: str,
    max_line_width: int,
    max_line_count: int,
    language: str | None = None,
    *,
    first_line_reserved_width: int = 0,
) -> _TextPartition | None:
    units = _text_units(text, language)
    joiner = "" if _uses_compact_script(language) else " "
    unit_count = len(units)
    if not units:
        return _TextPartition(0, 0, 0, 0, ())
    best: list[list[_TextPartition | None]] = [
        [None] * (max_line_count + 1) for _ in range(unit_count + 1)
    ]
    for line_index in range(max_line_count + 1):
        best[unit_count][line_index] = _TextPartition(0, 0, 0, 0, ())
    for start_index in range(unit_count - 1, -1, -1):
        for line_index in range(max_line_count - 1, -1, -1):
            available_width = _line_width(
                max_line_width,
                line_index,
                first_line_reserved_width,
            )
            current: _TextPartition | None = None
            for end_index in range(start_index + 1, unit_count + 1):
                line_text = joiner.join(units[start_index:end_index])
                if len(line_text) > available_width and end_index - start_index > 1:
                    break
                remainder = best[end_index][line_index + 1]
                if remainder is None:
                    continue
                line_word_count = end_index - start_index
                orphan_penalty = (
                    max(0, 3 - line_word_count) ** 2 * 100 if unit_count > 3 else 0
                )
                candidate = _TextPartition(
                    line_count=1 + remainder.line_count,
                    orphan_penalty=orphan_penalty + remainder.orphan_penalty,
                    boundary_penalty=(
                        _text_boundary_penalty(units, end_index)
                        + remainder.boundary_penalty
                    ),
                    balance_penalty=(
                        max(0, available_width - len(line_text)) ** 2
                        + remainder.balance_penalty
                    ),
                    ranges=((start_index, end_index), *remainder.ranges),
                )
                if current is None or candidate.score < current.score:
                    current = candidate
            best[start_index][line_index] = current
    return best[0][0]


def _text_boundary_penalty(words: list[str], end_index: int) -> int:
    if end_index >= len(words):
        return 0
    left_text = words[end_index - 1].strip()
    right_text = words[end_index].strip()
    if _ends_with_punctuation(left_text, _STRONG_PUNCTUATION):
        penalty = 0
    elif _ends_with_punctuation(left_text, _MEDIUM_PUNCTUATION):
        penalty = 2
    elif left_text.endswith(("-", "–", "—")):
        penalty = 3
    else:
        penalty = 6
    left_word = _boundary_word(left_text)
    right_word = _boundary_word(right_text)
    if left_word in _BAD_TRAILING_WORDS:
        penalty += 8
    if (left_word, right_word) in _BAD_BOUNDARY_PAIRS:
        penalty += 8
    if right_word in _PREFERRED_LEADING_WORDS:
        penalty = max(0, penalty - 2)
    return penalty


def _text_lines(
    text: str,
    partition: _TextPartition,
    language: str | None = None,
) -> list[str]:
    units = _text_units(text, language)
    joiner = "" if _uses_compact_script(language) else " "
    return [joiner.join(units[start:end]) for start, end in partition.ranges]


def _greedy_wrap_lines(
    text: str,
    max_line_width: int,
    language: str | None = None,
    *,
    first_line_reserved_width: int = 0,
) -> list[str]:
    units = _text_units(text, language)
    joiner = "" if _uses_compact_script(language) else " "
    lines: list[str] = []
    current = ""
    for unit in units:
        projected = unit if not current else f"{current}{joiner}{unit}"
        available_width = _line_width(
            max_line_width,
            len(lines),
            first_line_reserved_width,
        )
        if len(projected) <= available_width or not current:
            current = projected
            continue
        lines.append(current)
        current = unit
    if current:
        lines.append(current)
    return lines


def _line_width(
    max_line_width: int,
    line_index: int,
    first_line_reserved_width: int,
) -> int:
    if line_index != 0:
        return max_line_width
    return max(1, max_line_width - max(0, first_line_reserved_width))


def _normalize_text(text: str, language: str | None) -> str:
    sanitized = sanitize_subtitle_text(text)
    if _uses_compact_script(language):
        return "".join(sanitized.split())
    return " ".join(sanitized.split())


def _text_units(text: str, language: str | None) -> list[str]:
    clean = _normalize_text(text, language)
    if not _uses_compact_script(language):
        return clean.split()
    units: list[str] = []
    for character in clean:
        if (
            units
            and (
                unicodedata.combining(character)
                or character in {"\u200d", "\ufe0e", "\ufe0f"}
                or units[-1].endswith("\u200d")
            )
        ):
            units[-1] += character
        else:
            units.append(character)
    return units


def _uses_compact_script(language: str | None) -> bool:
    if language is None:
        return False
    primary = language.casefold().replace("_", "-").split("-", 1)[0]
    return primary in {"ja", "zh"}


def speaker_display_name(turn: SpeakerTurn, options: SubtitleExportConfig) -> str:
    if options.use_mapped_names:
        return turn.mapped_name or turn.speaker_match_label or turn.speaker
    return turn.speaker


def _speaker_prefix_width(cue: SubtitleCue, options: SubtitleExportConfig) -> int:
    if (
        not options.speaker_labels
        or cue.speaker == "TRANSCRIPT"
        or options.max_line_width is None
    ):
        return 0
    prefix_width = len(f"{cue.speaker}: ")
    # An indivisible label that is itself as wide as the configured line cannot
    # share a conforming line with dialogue. Preserve the label and treat width
    # as best-effort for this explicit metadata edge case.
    return prefix_width if prefix_width < options.max_line_width else 0


def _speaker_layout(
    cue: SubtitleCue,
    options: SubtitleExportConfig,
    language: str | None,
) -> _SpeakerLayout:
    units = _text_units(cue.text, language)
    first_unit_width = len(units[0]) if units else 0
    return _speaker_layout_for_first_unit(
        speaker_prefix_width=_speaker_prefix_width(cue, options),
        max_line_width=options.max_line_width,
        max_line_count=options.max_line_count,
        first_unit_width=first_unit_width,
    )


def _speaker_layout_for_first_unit(
    *,
    speaker_prefix_width: int,
    max_line_width: int | None,
    max_line_count: int | None,
    first_unit_width: int,
) -> _SpeakerLayout:
    can_use_label_line = max_line_count is None or max_line_count > 1
    requires_label_line = bool(
        speaker_prefix_width
        and max_line_width is not None
        and first_unit_width > max_line_width - speaker_prefix_width
        and first_unit_width <= max_line_width
        and can_use_label_line
    )
    if requires_label_line:
        body_line_count = None if max_line_count is None else max_line_count - 1
        return _SpeakerLayout(
            first_line_reserved_width=0,
            speaker_on_own_line=True,
            body_line_count=body_line_count,
        )
    return _SpeakerLayout(
        first_line_reserved_width=speaker_prefix_width,
        speaker_on_own_line=False,
        body_line_count=max_line_count,
    )


def _wrap_cue(
    cue: SubtitleCue,
    options: SubtitleExportConfig,
    language: str | None,
) -> SubtitleCue:
    layout = _speaker_layout(cue, options, language)
    return SubtitleCue(
        speaker=cue.speaker,
        start=cue.start,
        end=cue.end,
        text=wrap_text(
            cue.text,
            options.max_line_width,
            layout.body_line_count,
            language=language,
            first_line_reserved_width=layout.first_line_reserved_width,
        ),
        words=cue.words,
        speaker_on_own_line=layout.speaker_on_own_line,
    )


def format_cue_text(cue: SubtitleCue, options: SubtitleExportConfig | None = None) -> str:
    options = options or SubtitleExportConfig()
    text = cue.text.strip()
    if not options.speaker_labels or cue.speaker == "TRANSCRIPT":
        return text
    if cue.speaker_on_own_line:
        return f"{cue.speaker}:\n{text}".strip()
    lines = text.splitlines() or [text]
    lines[0] = f"{cue.speaker}: {lines[0]}"
    return "\n".join(lines).strip()


def _formatted_cue_fits_limits(
    cue: SubtitleCue,
    options: SubtitleExportConfig,
) -> bool:
    lines = format_cue_text(cue, options).splitlines()
    if options.max_line_count is not None and len(lines) > options.max_line_count:
        return False
    if options.max_line_width is not None and any(
        len(line.replace("<u>", "").replace("</u>", "")) > options.max_line_width
        for line in lines
    ):
        return False
    return True


def sanitize_subtitle_text(text: str) -> str:
    return text.replace("-->", "->").strip()
