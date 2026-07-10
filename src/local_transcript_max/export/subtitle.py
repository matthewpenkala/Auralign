# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Shared subtitle cue formatting for SRT and WebVTT exports."""

from __future__ import annotations

from dataclasses import dataclass

from local_transcript_max.config import SubtitleExportConfig
from local_transcript_max.schemas import AlignedWord, SpeakerTurn, Transcript


@dataclass(frozen=True)
class SubtitleCue:
    speaker: str
    start: float
    end: float
    text: str
    words: list[AlignedWord]

    def with_text(self, text: str) -> SubtitleCue:
        return SubtitleCue(
            speaker=self.speaker,
            start=self.start,
            end=self.end,
            text=text,
            words=self.words,
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
    if not cue.words:
        return [cue.with_text(wrap_text(cue.text, options.max_line_width, options.max_line_count))]
    words = [word for word in cue.words if word.text.strip()]
    if len(words) < 2:
        return [cue.with_text(wrap_text(cue.text, options.max_line_width, options.max_line_count))]

    max_chars = max_chars_per_cue(options)
    if len(cue.text) <= max_chars and cue.end - cue.start <= 8.0:
        return [cue.with_text(wrap_text(cue.text, options.max_line_width, options.max_line_count))]

    chunks: list[list[AlignedWord]] = []
    current: list[AlignedWord] = []
    joiner = "" if language in {"zh", "ja"} else " "
    for word in words:
        projected = current + [word]
        projected_text = joiner.join(item.text.strip() for item in projected).strip()
        pause = word_pause(current[-1], word) if current else 0.0
        should_split_before = bool(
            current
            and (
                len(projected_text) > max_chars
                or pause > 1.0
                or current[-1].text.endswith((".", "?", "!"))
                and len(projected_text) >= max(24, max_chars // 2)
            )
        )
        if should_split_before:
            chunks.append(current)
            current = [word]
        else:
            current = projected
    if current:
        chunks.append(current)

    if len(chunks) <= 1:
        return [cue.with_text(wrap_text(cue.text, options.max_line_width, options.max_line_count))]
    split: list[SubtitleCue] = []
    for chunk in chunks:
        text = joiner.join(word.text.strip() for word in chunk).strip()
        start = next((word.start for word in chunk if word.start is not None), cue.start)
        end = next((word.end for word in reversed(chunk) if word.end is not None), cue.end)
        split.append(
            SubtitleCue(
                speaker=cue.speaker,
                start=max(cue.start, start if start is not None else cue.start),
                end=max(cue.start + 0.001, end if end is not None else cue.end),
                text=wrap_text(
                    sanitize_subtitle_text(text),
                    options.max_line_width,
                    options.max_line_count,
                ),
                words=list(chunk),
            )
        )
    return split


def max_chars_per_cue(options: SubtitleExportConfig) -> int:
    width = options.max_line_width or 45
    lines = options.max_line_count or 2
    return max(1, width * lines)


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
    if not any(word.start is not None and word.end is not None for word in words):
        return [cue]
    joiner = "" if language in {"zh", "ja"} else " "
    tokens = [sanitize_subtitle_text(word.text) for word in words]
    highlighted: list[SubtitleCue] = []
    for index, word in enumerate(words):
        if word.start is None or word.end is None:
            continue
        text = joiner.join(
            f"<u>{token}</u>" if token_index == index else token
            for token_index, token in enumerate(tokens)
        )
        highlighted.append(
            SubtitleCue(
                speaker=cue.speaker,
                start=max(cue.start, word.start),
                end=max(word.start + 0.001, word.end),
                text=wrap_text(text, options.max_line_width, options.max_line_count),
                words=cue.words,
            )
        )
    return highlighted or [cue]


def wrap_text(text: str, max_line_width: int | None, max_line_count: int | None) -> str:
    clean = sanitize_subtitle_text(" ".join(text.split()))
    if not max_line_width or max_line_width <= 0:
        return clean
    words = clean.split()
    if not words:
        return clean
    lines: list[str] = []
    current = ""
    for word in words:
        projected = word if not current else f"{current} {word}"
        if len(projected) <= max_line_width or not current:
            current = projected
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    if max_line_count and len(lines) > max_line_count:
        kept = lines[: max_line_count - 1]
        kept.append(" ".join(lines[max_line_count - 1 :]))
        lines = kept
    return "\n".join(lines)


def speaker_display_name(turn: SpeakerTurn, options: SubtitleExportConfig) -> str:
    if options.use_mapped_names:
        return turn.mapped_name or turn.speaker_match_label or turn.speaker
    return turn.speaker


def format_cue_text(cue: SubtitleCue, options: SubtitleExportConfig | None = None) -> str:
    options = options or SubtitleExportConfig()
    text = cue.text.strip()
    if not options.speaker_labels or cue.speaker == "TRANSCRIPT":
        return text
    lines = text.splitlines() or [text]
    lines[0] = f"{cue.speaker}: {lines[0]}"
    return "\n".join(lines).strip()


def sanitize_subtitle_text(text: str) -> str:
    return text.replace("-->", "->").strip()
