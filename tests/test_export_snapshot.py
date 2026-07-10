# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import csv
import json
from pathlib import Path

from local_transcript_max.export import (
    render_markdown,
    render_rttm,
    render_srt,
    render_txt,
    render_vtt,
    write_segments_csv,
    write_words_jsonl,
)
from local_transcript_max.schemas import AlignedWord, DiarizationTurn, SpeakerTurn, Transcript


def tiny_transcript() -> Transcript:
    words = [
        AlignedWord(text="Hello,", start=0.0, end=0.4, speaker="SPEAKER_00"),
        AlignedWord(text="world", start=0.5, end=1.0, speaker="SPEAKER_00"),
    ]
    return Transcript(
        input_path="sample.wav",
        words=words,
        diarization_turns=[DiarizationTurn(start=0.0, end=1.2, speaker="SPEAKER_00")],
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=1.2,
                text="Hello,\nworld",
                words=words,
            ),
            SpeakerTurn(speaker="SPEAKER_01", start=1.3, end=1.2, text=""),
        ],
    )


def test_all_exports_are_consistent_and_safe(tmp_path: Path) -> None:
    transcript = tiny_transcript()
    srt = render_srt(transcript)
    vtt = render_vtt(transcript)
    assert "SPEAKER_00: Hello," in srt
    assert "SPEAKER_01" not in srt
    assert "00:00:00,000 --> 00:00:01,200" in srt
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.200" in vtt
    assert render_rttm(transcript).startswith("SPEAKER audio 1 0.000 1.200")
    assert "SPEAKER_00: Hello," in render_txt(transcript)
    assert "**SPEAKER_00**" in render_markdown(transcript)

    csv_path = tmp_path / "segments.csv"
    jsonl_path = tmp_path / "words.jsonl"
    write_segments_csv(csv_path, transcript)
    write_words_jsonl(jsonl_path, transcript)

    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))
    assert rows[0]["text"] == "Hello,\nworld"
    word_rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert word_rows[0]["text"] == "Hello,"
