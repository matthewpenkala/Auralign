# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max.errors import LTMError
from local_transcript_max.export.json_export import write_json_artifact
from local_transcript_max.schemas import AlignedWord, DiarizationTurn, Transcript
from local_transcript_max.validation import speakers as speaker_validation
from local_transcript_max.validation.speakers import validate_speaker_artifacts


def test_validate_speakers_reports_four_speaker_metrics(tmp_path: Path) -> None:
    turns = [
        DiarizationTurn(start=0, end=2, speaker="SPEAKER_00"),
        DiarizationTurn(start=2, end=4, speaker="SPEAKER_01"),
        DiarizationTurn(start=4, end=6, speaker="SPEAKER_02"),
        DiarizationTurn(start=6, end=8, speaker="SPEAKER_03"),
    ]
    words = [
        AlignedWord(text="a", start=0.1, end=0.2, speaker="SPEAKER_00"),
        AlignedWord(text="b", start=2.1, end=2.2, speaker="SPEAKER_01"),
        AlignedWord(text="c", start=4.1, end=4.2, speaker="SPEAKER_02"),
        AlignedWord(text="d", start=6.1, end=6.2, speaker="SPEAKER_03", uncertain_speaker=True),
    ]
    write_json_artifact(
        tmp_path / "transcript.diarized.json",
        Transcript(input_path="sample.wav", words=words, diarization_turns=turns),
    )
    report = validate_speaker_artifacts(tmp_path, expected_speakers=4)
    assert report.unique_speakers == 4
    assert report.speaker_turn_count == 4
    assert report.percent_words_assigned_to_speakers == 100.0
    assert report.percent_words_marked_uncertain == 25.0
    assert (tmp_path / "speaker_validation_report.json").exists()


def test_validate_speakers_detects_overlap_and_count_mismatch(tmp_path: Path) -> None:
    turns = [
        DiarizationTurn(start=0, end=2, speaker="SPEAKER_00"),
        DiarizationTurn(start=1, end=3, speaker="SPEAKER_01"),
    ]
    write_json_artifact(
        tmp_path / "transcript.diarized.json",
        Transcript(input_path="sample.wav", diarization_turns=turns),
    )
    report = validate_speaker_artifacts(tmp_path, expected_speakers=4)
    assert report.unique_speakers == 2
    assert report.overlap_spans
    assert any("expected 4 speakers" in mode for mode in report.suspected_failure_modes)


def test_validate_speakers_rejects_symlink_without_reading_target(tmp_path: Path) -> None:
    private_marker = "PRIVATE_TRANSCRIPT_MARKER"
    target = tmp_path / "private.json"
    target.write_text(private_marker, encoding="utf-8")
    (tmp_path / "transcript.diarized.json").symlink_to(target)

    with pytest.raises(LTMError, match="safely open") as exc_info:
        validate_speaker_artifacts(tmp_path, expected_speakers=2)

    assert private_marker not in str(exc_info.value)


def test_validate_speakers_rejects_oversize_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(speaker_validation, "MAX_TRANSCRIPT_BYTES", 4)
    (tmp_path / "transcript.diarized.json").write_bytes(b"12345")

    with pytest.raises(LTMError, match="size limit"):
        validate_speaker_artifacts(tmp_path, expected_speakers=2)


def test_validate_speakers_bounds_embedded_transcript_records(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(speaker_validation, "MAX_VALIDATION_RECORDS", 1)
    turns = [
        DiarizationTurn(start=0, end=1, speaker="SPEAKER_00"),
        DiarizationTurn(start=1, end=2, speaker="SPEAKER_01"),
    ]
    write_json_artifact(
        tmp_path / "transcript.diarized.json",
        Transcript(input_path="sample.wav", diarization_turns=turns),
    )

    with pytest.raises(LTMError, match="malformed"):
        validate_speaker_artifacts(tmp_path, expected_speakers=2)


def test_validate_speakers_bounds_rttm_records(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(speaker_validation, "MAX_VALIDATION_RECORDS", 1)
    (tmp_path / "transcript.rttm").write_text(
        "SPEAKER sample 1 0.0 1.0 <NA> <NA> SPEAKER_00 <NA> <NA>\n"
        "SPEAKER sample 1 1.0 1.0 <NA> <NA> SPEAKER_01 <NA> <NA>\n",
        encoding="utf-8",
    )

    with pytest.raises(LTMError, match="RTTM artifact is malformed"):
        validate_speaker_artifacts(tmp_path, expected_speakers=2)


def test_validate_speakers_bounds_jsonl_records(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(speaker_validation, "MAX_VALIDATION_RECORDS", 1)
    word = AlignedWord(text="a", start=0.0, end=0.1).model_dump_json()
    (tmp_path / "transcript.words.jsonl").write_text(f"{word}\n{word}\n", encoding="utf-8")

    with pytest.raises(LTMError, match="word artifact is malformed"):
        validate_speaker_artifacts(tmp_path, expected_speakers=2)


def test_validate_speakers_bounds_overlap_materialization(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(speaker_validation, "MAX_OVERLAP_SPANS", 4)
    turns = [
        DiarizationTurn(start=0, end=10, speaker=f"SPEAKER_{index:02d}")
        for index in range(4)
    ]
    write_json_artifact(
        tmp_path / "transcript.diarized.json",
        Transcript(input_path="sample.wav", diarization_turns=turns),
    )

    with pytest.raises(LTMError, match="overlap span limit"):
        validate_speaker_artifacts(tmp_path, expected_speakers=4)
