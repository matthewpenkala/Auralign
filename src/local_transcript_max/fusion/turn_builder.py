# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Build readable speaker turns from assigned words."""

from __future__ import annotations

from collections import defaultdict

from local_transcript_max.schemas import AlignedWord, SpeakerAuditRow, SpeakerReport, SpeakerTurn

UNKNOWN_SPEAKER = "UNKNOWN"


def build_speaker_turns(
    words: list[AlignedWord],
    *,
    max_pause: float = 1.2,
    max_duration: float = 45.0,
) -> list[SpeakerTurn]:
    turns: list[SpeakerTurn] = []
    current_words: list[AlignedWord] = []
    current_speaker: str | None = None

    def flush() -> None:
        nonlocal current_words, current_speaker
        if not current_words:
            return
        start = min((word.start for word in current_words if word.start is not None), default=0.0)
        end = max((word.end for word in current_words if word.end is not None), default=start)
        text = " ".join(word.text for word in current_words).strip()
        turns.append(
            SpeakerTurn(
                speaker=current_speaker or UNKNOWN_SPEAKER,
                start=start,
                end=end,
                text=text,
                words=list(current_words),
            )
        )
        current_words = []
        current_speaker = None

    for word in words:
        speaker = word.speaker or UNKNOWN_SPEAKER
        should_flush = False
        if current_words:
            last = current_words[-1]
            pause = 0.0
            if last.end is not None and word.start is not None:
                pause = max(0.0, word.start - last.end)
            duration = 0.0
            if current_words[0].start is not None and word.end is not None:
                duration = word.end - current_words[0].start
            should_flush = (
                speaker != current_speaker
                or pause > max_pause
                or duration > max_duration
                or last.text.endswith((".", "?", "!")) and len(current_words) >= 18
            )
        if should_flush:
            flush()
        current_speaker = speaker
        current_words.append(word)
    flush()
    return turns


def repair_unknown_fragment_labels(
    turns: list[SpeakerTurn],
    *,
    max_words: int = 12,
    max_duration: float = 4.0,
) -> list[SpeakerTurn]:
    repaired: list[SpeakerTurn] = []
    index = 0
    while index < len(turns):
        turn = turns[index]
        if turn.speaker != UNKNOWN_SPEAKER:
            repaired.append(turn)
            index += 1
            continue

        group_start = index
        while index < len(turns) and turns[index].speaker == UNKNOWN_SPEAKER:
            index += 1
        group = turns[group_start:index]
        previous_speaker = repaired[-1].speaker if repaired else None
        next_speaker = turns[index].speaker if index < len(turns) else None

        if (
            _is_concrete_speaker(previous_speaker)
            and previous_speaker == next_speaker
            and _group_word_count(group) <= max_words
            and _group_duration(group) <= max_duration
        ):
            repaired.extend(
                fragment.model_copy(update={"speaker": previous_speaker}) for fragment in group
            )
        else:
            repaired.extend(group)
    return repaired


def _is_concrete_speaker(speaker: str | None) -> bool:
    return bool(speaker and speaker != UNKNOWN_SPEAKER)


def _group_word_count(turns: list[SpeakerTurn]) -> int:
    count = 0
    for turn in turns:
        count += len(turn.words) if turn.words else len(turn.text.split())
    return count


def _group_duration(turns: list[SpeakerTurn]) -> float:
    if not turns:
        return 0.0
    start = min(turn.start for turn in turns)
    end = max(turn.end for turn in turns)
    return max(0.0, end - start)


def build_speaker_report(turns: list[SpeakerTurn]) -> SpeakerReport:
    durations: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    warnings: dict[str, list[str]] = defaultdict(list)
    for turn in turns:
        durations[turn.speaker] += max(0.0, turn.end - turn.start)
        counts[turn.speaker] += 1
        if turn.speaker == UNKNOWN_SPEAKER:
            warnings[turn.speaker].append("Some words could not be assigned to a diarized speaker.")
    rows = [
        SpeakerAuditRow(
            speaker=speaker,
            total_speaking_time=round(duration, 3),
            turn_count=counts[speaker],
            warnings=sorted(set(warnings[speaker])),
        )
        for speaker, duration in sorted(durations.items())
    ]
    return SpeakerReport(speakers=rows)
