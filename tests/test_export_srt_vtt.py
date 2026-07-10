# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from local_transcript_max.config import SubtitleExportConfig
from local_transcript_max.export.srt_export import render_srt, srt_timestamp
from local_transcript_max.export.vtt_export import render_vtt, vtt_timestamp
from local_transcript_max.schemas import AlignedWord, SpeakerTurn, Transcript


def test_timestamp_formatting() -> None:
    assert srt_timestamp(3661.234) == "01:01:01,234"
    assert vtt_timestamp(3661.234) == "01:01:01.234"


def test_srt_and_vtt_render_speaker_turns() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[SpeakerTurn(speaker="SPEAKER_00", start=0, end=1.5, text="Hello")],
    )
    assert "SPEAKER_00: Hello" in render_srt(transcript)
    assert render_vtt(transcript).startswith("WEBVTT")


def test_long_srt_cue_splits_by_word_timestamps() -> None:
    words = [
        AlignedWord(text=f"word{index}", start=index * 0.4, end=index * 0.4 + 0.2)
        for index in range(12)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=5.0,
                text=" ".join(word.text for word in words),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=18, max_line_count=1),
    )

    assert srt.count("-->") > 1
    assert "00:00:00,000 -->" in srt
    assert "SPEAKER_00:" in srt


def test_highlight_words_renders_timed_word_cues() -> None:
    words = [
        AlignedWord(text="hello", start=0.0, end=0.4),
        AlignedWord(text="world", start=0.4, end=0.9),
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=0.9,
                text="hello world",
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(highlight_words=True, max_line_width=80),
    )

    assert srt.count("-->") == 2
    assert "SPEAKER_00: <u>hello</u> world" in srt
    assert "SPEAKER_00: hello <u>world</u>" in srt


def test_subtitle_text_sanitizes_arrow_token() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=1.0, text="not --> a cue")
        ],
    )

    srt = render_srt(transcript)

    assert "not -> a cue" in srt
    assert "not --> a cue" not in srt


def test_vtt_segment_fallback_still_works_without_speaker_turns() -> None:
    from local_transcript_max.schemas import ASRSegment

    transcript = Transcript(
        input_path="sample.wav",
        segments=[ASRSegment(id=0, start=0.0, end=1.0, text="segment only")],
    )

    assert "segment only" in render_vtt(transcript)
