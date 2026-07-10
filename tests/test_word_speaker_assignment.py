# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from local_transcript_max.fusion.word_speaker_assignment import (
    assign_speakers_to_words,
    speaker_at_midpoint,
)
from local_transcript_max.schemas import AlignedWord, DiarizationTurn


def test_assign_speaker_by_max_overlap() -> None:
    words = [AlignedWord(text="hello", start=1.0, end=2.0, alignment_source="whisper")]
    turns = [
        DiarizationTurn(start=0.0, end=1.2, speaker="SPEAKER_00"),
        DiarizationTurn(start=1.2, end=3.0, speaker="SPEAKER_01"),
    ]
    assigned = assign_speakers_to_words(words, turns)
    assert assigned[0].speaker == "SPEAKER_01"
    assert assigned[0].uncertain_speaker is False


def test_word_contained_inside_one_turn_is_confident() -> None:
    assigned = assign_speakers_to_words(
        [AlignedWord(text="inside", start=1.0, end=1.5)],
        [DiarizationTurn(start=0.0, end=2.0, speaker="SPEAKER_00")],
    )
    assert assigned[0].speaker == "SPEAKER_00"
    assert assigned[0].uncertain_speaker is False


def test_word_overlapping_two_turns_is_uncertain_when_close() -> None:
    assigned = assign_speakers_to_words(
        [AlignedWord(text="both", start=0.9, end=1.3)],
        [
            DiarizationTurn(start=0.0, end=1.1, speaker="SPEAKER_00"),
            DiarizationTurn(start=1.1, end=2.0, speaker="SPEAKER_01"),
        ],
    )
    assert assigned[0].speaker in {"SPEAKER_00", "SPEAKER_01"}
    assert assigned[0].uncertain_speaker is True


def test_word_outside_diarization_coverage_is_unknown() -> None:
    assigned = assign_speakers_to_words(
        [AlignedWord(text="outside", start=5.0, end=6.0)],
        [DiarizationTurn(start=0.0, end=1.0, speaker="SPEAKER_00")],
    )
    assert assigned[0].speaker is None
    assert assigned[0].uncertain_speaker is True


def test_overlapping_speaker_turn_marks_assignment_uncertain() -> None:
    assigned = assign_speakers_to_words(
        [AlignedWord(text="overlap", start=1.0, end=1.4)],
        [
            DiarizationTurn(start=0.0, end=2.0, speaker="SPEAKER_00", overlap=True),
            DiarizationTurn(start=0.8, end=1.6, speaker="SPEAKER_01", overlap=True),
        ],
    )
    assert assigned[0].speaker in {"SPEAKER_00", "SPEAKER_01"}
    assert assigned[0].uncertain_speaker is True


def test_zero_duration_word_uses_midpoint_uncertain() -> None:
    assigned = assign_speakers_to_words(
        [AlignedWord(text="zero", start=1.0, end=1.0)],
        [DiarizationTurn(start=0.0, end=2.0, speaker="SPEAKER_00")],
    )
    assert assigned[0].speaker == "SPEAKER_00"
    assert assigned[0].uncertain_speaker is True


def test_mid_word_boundary_assigns_larger_overlap() -> None:
    assigned = assign_speakers_to_words(
        [AlignedWord(text="boundary", start=0.8, end=1.8)],
        [
            DiarizationTurn(start=0.0, end=1.0, speaker="SPEAKER_00"),
            DiarizationTurn(start=1.0, end=3.0, speaker="SPEAKER_01"),
        ],
    )
    assert assigned[0].speaker == "SPEAKER_01"
    assert assigned[0].uncertain_speaker is False


def test_missing_timestamp_is_uncertain() -> None:
    assigned = assign_speakers_to_words([AlignedWord(text="hmm")], [])
    assert assigned[0].speaker is None
    assert assigned[0].uncertain_speaker is True


def test_non_monotonic_words_still_use_interval_lookup() -> None:
    assigned = assign_speakers_to_words(
        [
            AlignedWord(text="late", start=5.0, end=5.5),
            AlignedWord(text="early", start=1.0, end=1.5),
        ],
        [
            DiarizationTurn(start=0.0, end=2.0, speaker="SPEAKER_00"),
            DiarizationTurn(start=4.0, end=6.0, speaker="SPEAKER_01"),
        ],
    )

    assert [word.speaker for word in assigned] == ["SPEAKER_01", "SPEAKER_00"]


def test_midpoint_lookup_includes_turns_starting_at_boundary() -> None:
    speaker = speaker_at_midpoint(
        1.0,
        [
            DiarizationTurn(start=0.0, end=0.5, speaker="SPEAKER_00"),
            DiarizationTurn(start=1.0, end=2.0, speaker="SPEAKER_01"),
        ],
    )

    assert speaker == "SPEAKER_01"
