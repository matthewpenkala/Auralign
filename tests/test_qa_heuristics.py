# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

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
