# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

from local_transcript_max.batch import (
    discover_audio,
    job_has_completion_evidence,
    run_batch,
    safe_job_name,
)
from local_transcript_max.config import load_config
from local_transcript_max.utils import read_json, sha256_file, write_json


def write_completion_evidence(job_dir: Path, input_path: Path) -> None:
    write_json(job_dir / "transcript.json", {"input_path": str(input_path)})
    metadata_path = job_dir / "run_metadata.json"
    write_json(
        metadata_path,
        {
            "status": "core_complete",
            "completion_timing_artifact": "completion_timing.json",
        },
    )
    write_json(
        job_dir / "completion_timing.json",
        {
            "schema_version": "completion_timing.v1",
            "run_metadata_sha256": sha256_file(metadata_path),
            "pipeline_core_seconds": 1.0,
            "observed_end_to_end_seconds": 1.25,
            "real_time_factor": 0.5,
            "stage_durations": {
                "invocation_setup": 0.05,
                "run_metadata_preparation_and_publish": 0.1,
                "speaker_identity": 0.0,
                "debug_report_publish": 0.0,
            },
            "resource_usage": {},
            "measurement_scope": (
                "through_debug_publish_before_completion_artifact.v1"
            ),
            "excluded_operations": [
                "completion_timing_artifact_publish",
                "completion_progress_callback",
            ],
        },
    )


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
        alignment_backend=None,
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
    monkeypatch.setattr(
        "local_transcript_max.batch._runtime_backends",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("completed jobs must not initialize backends")
        ),
    )
    out = tmp_path / "out"
    (out / "done").mkdir(parents=True)
    write_completion_evidence(out / "done", audio)
    summary = run_batch(audio_dir, out, load_config(cwd=tmp_path), skip_completed=True)
    assert summary["rows"][0]["status"] == "skipped_complete"
    assert read_json(out / "batch_summary.json")["total"] == 1


def test_batch_does_not_treat_partial_job_as_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = audio_dir / "partial.wav"
    audio.write_bytes(b"x")
    out = tmp_path / "out"
    job_dir = out / "partial"
    job_dir.mkdir(parents=True)
    write_json(job_dir / "transcript.json", {"input_path": str(audio)})
    monkeypatch.setattr(
        "local_transcript_max.batch.safe_job_name",
        lambda _path: "partial",
    )
    monkeypatch.setattr(
        "local_transcript_max.batch._runtime_backends",
        lambda _config: (object(), None, object()),
    )

    def reject_partial(*_args, **_kwargs):
        raise RuntimeError("partial output requires operator cleanup")

    monkeypatch.setattr("local_transcript_max.batch.transcribe_file", reject_partial)

    summary = run_batch(
        audio_dir,
        out,
        load_config(cwd=tmp_path),
        resume=True,
        skip_completed=True,
    )

    assert job_has_completion_evidence(job_dir) is False
    assert summary["rows"][0]["status"] == "failed"
    assert summary["rows"][0]["error_type"] == "RuntimeError"


def test_batch_resume_accepts_pre_completion_artifact_job(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio = audio_dir / "legacy.wav"
    audio.write_bytes(b"x")
    out = tmp_path / "out"
    job_dir = out / "legacy"
    job_dir.mkdir(parents=True)
    write_json(job_dir / "transcript.json", {"input_path": str(audio)})
    write_json(
        job_dir / "run_metadata.json",
        {
            "timings": [
                {"name": "asr", "duration_seconds": 1.0},
                {"name": "total", "duration_seconds": 2.0},
            ]
        },
    )
    monkeypatch.setattr(
        "local_transcript_max.batch.safe_job_name",
        lambda _path: "legacy",
    )
    monkeypatch.setattr(
        "local_transcript_max.batch._runtime_backends",
        lambda _config: (_ for _ in ()).throw(
            AssertionError("legacy completed jobs must not initialize backends")
        ),
    )

    summary = run_batch(
        audio_dir,
        out,
        load_config(cwd=tmp_path),
        resume=True,
    )

    assert job_has_completion_evidence(job_dir) is True
    assert summary["rows"][0]["status"] == "resumed_complete"


def test_safe_job_name_is_stable(tmp_path: Path) -> None:
    audio = tmp_path / "audio" / "sample.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"x")

    assert safe_job_name(audio) == safe_job_name(audio)
    assert safe_job_name(audio).startswith("sample-")


def test_per_job_model_lifecycle_constructs_fresh_batch_backends(
    tmp_path: Path,
    monkeypatch,
) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "one.wav").write_bytes(b"x")
    (audio_dir / "two.wav").write_bytes(b"x")
    constructed: list[object] = []
    observed_asr_ids: list[int] = []

    def fake_runtime_backends(_config):
        asr = object()
        constructed.append(asr)
        return asr, None, object()

    def fake_transcribe(
        _audio,
        job_dir,
        _config,
        *,
        asr_backend=None,
        alignment_backend=None,
        diarization_backend=None,
        debug_report_path=None,
    ):
        del alignment_backend, diarization_backend, debug_report_path
        observed_asr_ids.append(id(asr_backend))
        job_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("local_transcript_max.batch._runtime_backends", fake_runtime_backends)
    monkeypatch.setattr("local_transcript_max.batch.transcribe_file", fake_transcribe)
    config = load_config(cwd=tmp_path)
    config.runtime.model_lifecycle = "per-job"

    summary = run_batch(audio_dir, tmp_path / "out", config)

    assert summary["complete"] == 2
    assert summary["model_lifecycle"] == "per-job"
    assert len(constructed) == 2
    assert len(set(observed_asr_ids)) == 2
