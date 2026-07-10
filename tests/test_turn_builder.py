# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from local_transcript_max.fusion.turn_builder import (
    build_speaker_report,
    build_speaker_turns,
    repair_unknown_fragment_labels,
)
from local_transcript_max.schemas import AlignedWord, SpeakerTurn


def test_turn_builder_splits_on_speaker_change() -> None:
    words = [
        AlignedWord(text="hi", start=0, end=0.2, speaker="SPEAKER_00"),
        AlignedWord(text="there", start=0.3, end=0.6, speaker="SPEAKER_00"),
        AlignedWord(text="yes", start=0.7, end=1.0, speaker="SPEAKER_01"),
    ]
    turns = build_speaker_turns(words)
    assert [turn.speaker for turn in turns] == ["SPEAKER_00", "SPEAKER_01"]
    assert turns[0].text == "hi there"


def test_speaker_report_counts_turns() -> None:
    turns = build_speaker_turns(
        [AlignedWord(text="hello", start=0, end=1, speaker="SPEAKER_00")]
    )
    report = build_speaker_report(turns)
    assert report.speakers[0].speaker == "SPEAKER_00"
    assert report.speakers[0].turn_count == 1


def test_unknown_fragment_between_same_speaker_is_relabelled() -> None:
    unknown_word = AlignedWord(text="there", start=0.5, end=0.7, uncertain_speaker=True)
    turns = [
        SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=0.4, text="hi"),
        SpeakerTurn(
            speaker="UNKNOWN",
            start=0.5,
            end=0.7,
            text="there",
            words=[unknown_word],
        ),
        SpeakerTurn(speaker="SPEAKER_00", start=0.8, end=1.0, text="friend"),
    ]

    repaired = repair_unknown_fragment_labels(turns)

    assert [turn.speaker for turn in repaired] == [
        "SPEAKER_00",
        "SPEAKER_00",
        "SPEAKER_00",
    ]
    assert repaired[1].text == "there"
    assert repaired[1].start == 0.5
    assert repaired[1].end == 0.7
    assert repaired[1].words[0].speaker is None
    assert repaired[1].words[0].uncertain_speaker is True


def test_consecutive_unknown_fragments_between_same_speaker_are_relabelled() -> None:
    turns = [
        SpeakerTurn(speaker="SPEAKER_01", start=0.0, end=0.4, text="start"),
        SpeakerTurn(speaker="UNKNOWN", start=0.5, end=0.7, text="small"),
        SpeakerTurn(speaker="UNKNOWN", start=0.8, end=1.0, text="fragment"),
        SpeakerTurn(speaker="SPEAKER_01", start=1.1, end=1.4, text="done"),
    ]

    repaired = repair_unknown_fragment_labels(turns)

    assert [turn.speaker for turn in repaired] == [
        "SPEAKER_01",
        "SPEAKER_01",
        "SPEAKER_01",
        "SPEAKER_01",
    ]


def test_unknown_fragment_between_different_speakers_stays_unknown() -> None:
    turns = [
        SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=0.4, text="left"),
        SpeakerTurn(speaker="UNKNOWN", start=0.5, end=0.7, text="maybe"),
        SpeakerTurn(speaker="SPEAKER_01", start=0.8, end=1.0, text="right"),
    ]

    repaired = repair_unknown_fragment_labels(turns)

    assert [turn.speaker for turn in repaired] == [
        "SPEAKER_00",
        "UNKNOWN",
        "SPEAKER_01",
    ]


def test_unknown_fragments_at_boundaries_stay_unknown() -> None:
    turns = [
        SpeakerTurn(speaker="UNKNOWN", start=0.0, end=0.2, text="start"),
        SpeakerTurn(speaker="SPEAKER_00", start=0.3, end=0.5, text="middle"),
        SpeakerTurn(speaker="UNKNOWN", start=0.6, end=0.8, text="end"),
    ]

    repaired = repair_unknown_fragment_labels(turns)

    assert [turn.speaker for turn in repaired] == [
        "UNKNOWN",
        "SPEAKER_00",
        "UNKNOWN",
    ]


def test_over_threshold_unknown_fragment_stays_unknown() -> None:
    long_text = " ".join(f"word{index}" for index in range(13))
    turns = [
        SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=0.4, text="left"),
        SpeakerTurn(speaker="UNKNOWN", start=0.5, end=2.0, text=long_text),
        SpeakerTurn(speaker="SPEAKER_00", start=2.1, end=2.5, text="right"),
    ]

    repaired = repair_unknown_fragment_labels(turns)

    assert [turn.speaker for turn in repaired] == [
        "SPEAKER_00",
        "UNKNOWN",
        "SPEAKER_00",
    ]
