# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max.config import load_config
from local_transcript_max.errors import LTMError
from local_transcript_max.qa.heuristics import run_qa
from local_transcript_max.qa.redecode import (
    merge_spans,
    redecode_candidate_record,
    run_targeted_redecode,
    select_redecode_spans,
)
from local_transcript_max.schemas import (
    AlignedWord,
    ASRResult,
    ASRSegment,
    QAFlag,
    QAReport,
    Transcript,
)
from local_transcript_max.utils import read_json


class MockASR:
    def transcribe(self, audio_path: Path, config):
        return ASRResult(
            segments=[
                ASRSegment(
                    id=0,
                    start=0.0,
                    end=1.0,
                    text="clean candidate",
                    avg_logprob=-0.1,
                    compression_ratio=1.0,
                )
            ],
            raw={"mock": True},
        )


def suspect_transcript() -> Transcript:
    return Transcript(
        input_path="sample.wav",
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=2.0,
                text="hello hello hello hello",
                avg_logprob=-2.0,
                no_speech_prob=0.95,
                compression_ratio=3.0,
            )
        ],
        words=[
            AlignedWord(text="hello", start=0.0, end=0.4, alignment_source="whisper"),
            AlignedWord(text="hello", start=0.3, end=0.2, alignment_source="whisper"),
        ],
    )


def test_low_confidence_span_selection() -> None:
    transcript = suspect_transcript()
    qa = run_qa(transcript)
    spans = select_redecode_spans(
        transcript,
        qa,
        mode="low-confidence",
        max_spans=5,
        pad_seconds=1.0,
    )
    assert spans
    assert "low_avg_logprob" in spans[0]["reason_codes"]


def test_hallucination_and_repeated_loop_span_selection() -> None:
    transcript = suspect_transcript()
    qa = run_qa(transcript)
    spans = select_redecode_spans(
        transcript,
        qa,
        mode="hallucination",
        max_spans=5,
        pad_seconds=0.5,
    )
    assert spans
    assert set(spans[0]["reason_codes"]) & {"speech_in_no_speech", "high_compression_ratio"}


def test_unlocalized_qa_flag_does_not_trigger_full_transcript_redecode() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        segments=[ASRSegment(id=0, start=0.0, end=3600.0, text="sample")],
    )
    report = QAReport(
        flags=[
            QAFlag(
                code="repeated_phrase",
                severity="warning",
                message="aggregate warning without a known occurrence span",
            )
        ]
    )

    spans = select_redecode_spans(
        transcript,
        report,
        mode="all-suspect",
        max_spans=5,
        pad_seconds=1.0,
    )

    assert spans == []


def test_localized_repeated_phrase_flag_remains_targeted() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        segments=[ASRSegment(id=0, start=0.0, end=3600.0, text="sample")],
    )
    report = QAReport(
        flags=[
            QAFlag(
                code="repeated_phrase",
                severity="warning",
                message="localized warning",
                start=10.0,
                end=12.0,
            )
        ]
    )

    spans = select_redecode_spans(
        transcript,
        report,
        mode="all-suspect",
        max_spans=5,
        pad_seconds=1.0,
    )

    assert [(span["padded_start"], span["padded_end"]) for span in spans] == [(9.0, 13.0)]


def test_padded_audio_boundary_clipping() -> None:
    transcript = suspect_transcript()
    qa = run_qa(transcript)
    spans = select_redecode_spans(
        transcript,
        qa,
        mode="all-suspect",
        max_spans=5,
        pad_seconds=5.0,
    )
    assert spans[0]["padded_start"] == 0.0


def test_merge_spans_preserves_reasons() -> None:
    spans = merge_spans(
        [
            {
                "padded_start": 0.0,
                "padded_end": 2.0,
                "original_start": 0.0,
                "original_end": 1.0,
                "reason_codes": ["a"],
                "original_text": "one",
            },
            {
                "padded_start": 1.5,
                "padded_end": 3.0,
                "original_start": 1.5,
                "original_end": 2.0,
                "reason_codes": ["b"],
                "original_text": "two",
            },
        ]
    )
    assert spans[0]["reason_codes"] == ["a", "b"]


def test_candidate_preservation_and_raw_asr_not_overwritten(tmp_path: Path, monkeypatch) -> None:
    transcript = suspect_transcript()
    qa = run_qa(transcript)
    config = load_config(cwd=tmp_path)
    config.redecode.mode = "low-confidence"
    config.redecode.write_report = True
    temporary_dirs: list[Path] = []
    temporary_permission_bits: list[int] = []

    def fake_extract(source: Path, output: Path, *, start: float, end: float) -> None:
        temporary_dirs.append(output.parent)
        temporary_permission_bits.append(output.parent.stat().st_mode & 0o077)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake-wav")

    monkeypatch.setattr("local_transcript_max.qa.redecode.extract_audio_window", fake_extract)
    run_targeted_redecode(tmp_path / "canonical.wav", transcript, qa, config, MockASR(), tmp_path)
    candidates = read_json(tmp_path / "transcript.redecode_candidates.json")
    assert candidates["replacement_policy"] == "preserve_raw_record_candidates_only"
    assert candidates["candidates"][0]["original_text"]
    assert candidates["candidates"][0]["candidate_text"] == "clean candidate"
    assert candidates["candidates"][0]["status"] == "candidate_generated"
    assert candidates["candidates"][0]["candidate_timestamps"]["coordinate_system"] == (
        "window_relative"
    )
    assert not (tmp_path / "transcript.raw.json").exists()
    assert not (tmp_path / "transcript.redecoded.json").exists()
    assert not (tmp_path / "transcript.redecoded.md").exists()
    assert not (tmp_path / "redecode_windows").exists()
    assert temporary_dirs
    assert all(not path.exists() for path in temporary_dirs)
    assert temporary_permission_bits == [0]


def test_candidate_timestamps_include_window_and_canonical_coordinates() -> None:
    result = ASRResult(
        segments=[ASRSegment(id=0, start=0.25, end=1.5, text="candidate")],
        raw={},
    )
    candidate = redecode_candidate_record(
        {
            "original_start": 10.0,
            "original_end": 11.0,
            "padded_start": 9.0,
            "original_text": "original",
            "original_metrics": {},
            "reason_codes": ["low_avg_logprob"],
        },
        result,
        failure_code=None,
    )

    assert candidate["candidate_timestamps"] == {
        "coordinate_system": "window_relative",
        "start": 0.25,
        "end": 1.5,
    }
    assert candidate["candidate_timestamps_canonical"] == {
        "coordinate_system": "canonical",
        "offset_seconds": 9.0,
        "start": 9.25,
        "end": 10.5,
    }


def test_redecode_failure_is_safe_and_temporary_windows_are_removed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    transcript = suspect_transcript()
    config = load_config(cwd=tmp_path)
    config.redecode.mode = "low-confidence"
    temporary_dirs: list[Path] = []

    def failing_extract(source: Path, output: Path, *, start: float, end: float) -> None:
        temporary_dirs.append(output.parent)
        raise RuntimeError(f"private failure at {source}: token=do-not-serialize")

    monkeypatch.setattr("local_transcript_max.qa.redecode.extract_audio_window", failing_extract)
    payload = run_targeted_redecode(
        tmp_path / "private" / "canonical.wav",
        transcript,
        run_qa(transcript),
        config,
        MockASR(),
        tmp_path,
    )

    candidate = payload["candidates"][0]
    assert candidate["status"] == "failed"
    assert candidate["warnings"] == ["redecode_failed"]
    assert "private" not in str(candidate)
    assert "do-not-serialize" not in str(candidate)
    assert temporary_dirs
    assert all(not path.exists() for path in temporary_dirs)


def test_runtime_guard_rejects_programmatically_enabled_repaired_output(
    tmp_path: Path,
) -> None:
    transcript = suspect_transcript()
    config = load_config(cwd=tmp_path)
    config.redecode.mode = "low-confidence"
    config.redecode.write_repaired_transcript = True

    with pytest.raises(LTMError, match="candidate-only"):
        run_targeted_redecode(
            tmp_path / "canonical.wav",
            transcript,
            run_qa(transcript),
            config,
            MockASR(),
            tmp_path,
        )

    assert not (tmp_path / "transcript.redecode_candidates.json").exists()
    assert not (tmp_path / "transcript.redecoded.json").exists()
