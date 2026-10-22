# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import math

import pytest

from local_transcript_max.qa.heuristics import run_qa
from local_transcript_max.schemas import AlignedWord, ASRSegment, DiarizationTurn, Transcript


def test_qa_flags_low_logprob() -> None:
    report = run_qa(
        Transcript(
            input_path="sample.wav",
            segments=[ASRSegment(id=0, start=0, end=1, text="hello", avg_logprob=-2.0)],
        )
    )
    assert any(flag.code == "low_avg_logprob" for flag in report.flags)


def test_qa_flags_compression_missing_timestamps_and_overlap() -> None:
    report = run_qa(
        Transcript(
            input_path="sample.wav",
            segments=[
                ASRSegment(
                    id=0,
                    start=0,
                    end=1,
                    text="hello",
                    compression_ratio=3.0,
                )
            ],
            words=[
                AlignedWord(text="hello", alignment_source="missing"),
                AlignedWord(
                    text="world",
                    start=0.2,
                    end=0.5,
                    speaker="SPEAKER_00",
                    alignment_source="whisper",
                ),
            ],
            diarization_turns=[
                DiarizationTurn(start=0.0, end=1.0, speaker="SPEAKER_00"),
                DiarizationTurn(start=0.5, end=1.5, speaker="SPEAKER_01"),
            ],
        ),
        requested_speaker_count=2,
    )
    codes = {flag.code for flag in report.flags}
    assert "high_compression_ratio" in codes
    assert "missing_word_timestamps" in codes
    assert "overlapping_diarization_turns" in codes
    assert report.summary["whisper_alignment_percentage"] == 50.0
    assert report.summary["missing_timestamp_count"] == 1
    assert report.summary["requested_speaker_count"] == 2
    assert report.summary["detected_speaker_count"] == 1
    assert report.summary["unassigned_word_count"] == 1
    assert report.summary["percent_words_unassigned"] == 50.0
    assert report.summary["overlap_span_count"] == 1
    assert report.summary["speaker_flapping_definition"]["warning_threshold"] == 0.35


@pytest.mark.parametrize("start,end", [(math.nan, 1), (0, math.inf), (-1, 0), (1, 1), (2, 1)])
def test_qa_does_not_count_invalid_word_timing_as_covered(start: float, end: float) -> None:
    report = run_qa(Transcript(input_path="sample.wav", words=[
        AlignedWord(text="private", start=start, end=end, speaker="SPEAKER_00"),
    ]))
    assert report.summary["invalid_word_timestamp_count"] == 1
    assert report.summary["timestamp_coverage_percentage"] == 0.0
    flag = next(flag for flag in report.flags if flag.code == "invalid_word_timestamp")
    assert flag.severity == "error"
    assert "private" not in str(flag.evidence)
    assert flag.start is None or math.isfinite(flag.start)
    assert flag.end is None or math.isfinite(flag.end)


def test_qa_long_forced_word_is_review_cue_without_modifying_transcript() -> None:
    word = AlignedWord(text="private", start=10, end=14.108, alignment_source="forced",
                       speaker="SPEAKER_00")
    transcript = Transcript(input_path="sample.wav", words=[word])
    before = transcript.model_dump()
    report = run_qa(transcript)
    flag = next(flag for flag in report.flags if flag.code == "long_word_duration")
    assert flag.start == 10
    assert flag.end == 14.108
    assert flag.evidence["word_index"] == 0
    assert flag.evidence["alignment_source"] == "forced"
    assert "private" not in str(flag.evidence)
    assert report.summary["review_scope"]["lexical_completeness"] == "not_established"
    assert report.summary["review_scope"]["perceptual_review"] == "not_performed"
    assert transcript.model_dump() == before


def test_qa_valid_ordinary_word_has_no_new_timing_flags() -> None:
    report = run_qa(Transcript(input_path="sample.wav", words=[
        AlignedWord(text="hello", start=0, end=0.4, speaker="SPEAKER_00"),
        AlignedWord(text="world", start=0.5, end=1.0, speaker="SPEAKER_00"),
    ]))
    assert report.summary["invalid_word_timestamp_count"] == 0
    assert report.summary["long_word_duration_count"] == 0
    assert report.summary["timestamp_coverage_percentage"] == 100.0
    assert not report.flags


@pytest.mark.parametrize("start,end", [(math.nan, math.inf), (0, math.inf), (-1, 60)])
def test_invalid_segment_never_emits_nonfinite_flag_boundaries(start: float, end: float) -> None:
    report = run_qa(Transcript(input_path="sample.wav", segments=[
        ASRSegment(id=0, start=start, end=end, text="private", avg_logprob=-2.0),
    ]))
    assert report.summary["error_flag_count"] == 1
    for flag in report.flags:
        assert flag.start is None or math.isfinite(flag.start)
        assert flag.end is None or math.isfinite(flag.end)
