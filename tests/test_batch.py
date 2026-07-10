# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

from local_transcript_max.batch import discover_audio, run_batch, safe_job_name
from local_transcript_max.config import load_config
from local_transcript_max.utils import read_json, write_json


def test_discovery_include_exclude_recursive(tmp_path: Path) -> None:
    root = tmp_path / "audio"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "keep.wav").write_bytes(b"x")
    (nested / "drop_bad.wav").write_bytes(b"x")
    (nested / "keep.mp3").write_bytes(b"x")
    files = discover_audio(root, recursive=True, include="*", exclude="*bad*")
    assert [path.name for path in files] == ["keep.wav", "keep.mp3"]


def test_run_batch_success_failure_and_aggregates(tmp_path: Path, monkeypatch) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    good = audio_dir / "good.wav"
    bad = audio_dir / "bad.wav"
    good.write_bytes(b"x")
    bad.write_bytes(b"x")

    def fake_transcribe(
        audio: Path,
        job_dir: Path,
        config,
        *,
        asr_backend=None,
        diarization_backend=None,
        debug_report_path=None,
    ):
        if audio.name == "bad.wav":
            raise RuntimeError("boom")
        job_dir.mkdir(parents=True, exist_ok=True)
        write_json(job_dir / "transcript.json", {"input_path": str(audio)})
        write_json(
            job_dir / "qa_report.json",
            {"summary": {"flag_count": 0, "overall_quality_summary": "ok"}},
        )

    monkeypatch.setattr("local_transcript_max.batch.transcribe_file", fake_transcribe)
    summary = run_batch(audio_dir, tmp_path / "out", load_config(cwd=tmp_path))
    assert summary["complete"] == 1
    assert summary["failed"] == 1
    assert (tmp_path / "out" / "batch_summary.json").exists()
    assert (tmp_path / "out" / "aggregate_qa.csv").exists()
    assert (tmp_path / "out" / "aggregate_speaker_validation.csv").exists()


def test_run_batch_redacts_failure_details(tmp_path: Path, monkeypatch) -> None:
    audio_dir = tmp_path / "private-audio"
    audio_dir.mkdir()
    audio = audio_dir / "bad.wav"
    audio.write_bytes(b"x")
    secret = "hf_" + "private_token_value_for_batch"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"failed at {audio} with {secret}")

    monkeypatch.setattr("local_transcript_max.batch.transcribe_file", fail)
    summary = run_batch(audio_dir, tmp_path / "out", load_config(cwd=tmp_path))

    error = summary["rows"][0]["error"]
    assert str(audio) not in error
    assert secret not in error
    assert "<local-path>" in error
    assert "<redacted>" in error


def test_run_batch_skip_completed(tmp_path: Path, monkeypatch) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = audio_dir / "done.wav"
    audio.write_bytes(b"x")

    def fake_name(path: Path) -> str:
        return "done"

    monkeypatch.setattr("local_transcript_max.batch.safe_job_name", fake_name)
    out = tmp_path / "out"
    (out / "done").mkdir(parents=True)
    write_json(out / "done" / "transcript.json", {"input_path": str(audio)})
    summary = run_batch(audio_dir, out, load_config(cwd=tmp_path), skip_completed=True)
    assert summary["rows"][0]["status"] == "skipped_complete"
    assert read_json(out / "batch_summary.json")["total"] == 1


def test_safe_job_name_is_stable(tmp_path: Path) -> None:
    audio = tmp_path / "audio" / "sample.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"x")

    assert safe_job_name(audio) == safe_job_name(audio)
    assert safe_job_name(audio).startswith("sample-")
