# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException

from local_transcript_max import __version__
from local_transcript_max.server import app as server_app
from local_transcript_max.server import jobs as job_store
from local_transcript_max.server.jobs import (
    JOBS,
    create_job,
    get_job,
    list_jobs,
    load_jobs,
    save_jobs,
    update_job,
)
from local_transcript_max.utils import read_json, write_json


def reset_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LTM_API_JOB_STORE", str(tmp_path / "store"))
    JOBS.clear()


def test_health_and_models_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_store(tmp_path, monkeypatch)
    health = server_app.health()
    assert "ok" in health
    assert health["queue"]["max_running"] == 1
    assert health["queue"]["max_queued"] == 0
    assert isinstance(server_app.models(), list)


def test_models_pull_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_store(tmp_path, monkeypatch)
    response = server_app.models_pull(
        server_app.ModelPullRequest(asr="large-v3", cache_dir=str(tmp_path))
    )
    assert response["results"][0]["status"] == "dry_run"


def test_models_pull_gated_dry_run_does_not_require_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("LTM_HF_TOKEN", raising=False)
    response = server_app.models_pull(
        server_app.ModelPullRequest(diarization="community-1", cache_dir=str(tmp_path))
    )
    assert response["results"][0]["status"] == "dry_run"
    assert response["results"][0]["model_id"] == "pyannote/speaker-diarization-community-1"
    assert not any(tmp_path.iterdir())


def test_transcribe_mocked_job_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")

    def fake_run(job_id: str, preset: str, debug_report: bool) -> None:
        job = JOBS[job_id]
        job.status = "complete"

    monkeypatch.setattr(server_app, "run_transcription_job", fake_run)
    response = server_app.transcribe(
        server_app.TranscribeRequest(input_path=str(audio), out_dir=str(tmp_path / "out")),
        BackgroundTasks(),
    )
    assert response["id"]


def test_transcribe_rejects_when_local_queue_is_full(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    monkeypatch.delenv("LTM_API_MAX_CONCURRENT_JOBS", raising=False)
    monkeypatch.delenv("LTM_API_MAX_QUEUED_JOBS", raising=False)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")

    first = server_app.transcribe(
        server_app.TranscribeRequest(input_path=str(audio), out_dir=str(tmp_path / "out")),
        BackgroundTasks(),
    )
    assert first["id"]

    with pytest.raises(HTTPException) as exc_info:
        server_app.transcribe(
            server_app.TranscribeRequest(input_path=str(audio), out_dir=str(tmp_path / "out")),
            BackgroundTasks(),
        )
    assert exc_info.value.status_code == 429
    assert "configured_max_queued=0" in exc_info.value.detail


def test_transcribe_queue_limit_is_configurable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    monkeypatch.setenv("LTM_API_MAX_CONCURRENT_JOBS", "1")
    monkeypatch.setenv("LTM_API_MAX_QUEUED_JOBS", "1")
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")

    first = server_app.transcribe(
        server_app.TranscribeRequest(input_path=str(audio), out_dir=str(tmp_path / "out")),
        BackgroundTasks(),
    )
    second = server_app.transcribe(
        server_app.TranscribeRequest(input_path=str(audio), out_dir=str(tmp_path / "out")),
        BackgroundTasks(),
    )

    assert first["id"]
    assert second["id"]
    assert server_app.queue_state()["queued"] == 2
    with pytest.raises(HTTPException) as exc_info:
        server_app.transcribe(
            server_app.TranscribeRequest(input_path=str(audio), out_dir=str(tmp_path / "out")),
            BackgroundTasks(),
        )
    assert exc_info.value.status_code == 429


def test_models_pull_non_dry_run_is_blocked_while_job_is_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    job.status = "running"
    save_jobs()

    with pytest.raises(HTTPException) as exc_info:
        server_app.models_pull(
            server_app.ModelPullRequest(
                asr="large-v3",
                cache_dir=str(tmp_path / "models"),
                dry_run=False,
            )
        )
    assert exc_info.value.status_code == 409


def test_job_status_qa_export_and_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    job.job_dir.mkdir(parents=True)
    write_json(job.job_dir / "qa_report.json", {"summary": {"flag_count": 0}})
    (job.job_dir / "transcript.txt").write_text("hello", encoding="utf-8")
    assert server_app.get_job_endpoint(job.id)["id"] == job.id
    assert server_app.get_job_status(job.id)["status"] == "queued"
    assert server_app.get_job_qa(job.id)["summary"]["flag_count"] == 0
    assert server_app.get_export(job.id, "txt").body == b"hello"
    with pytest.raises(HTTPException):
        server_app.delete_job_endpoint(job.id)
    assert server_app.delete_job_endpoint(job.id, confirm=True)["deleted"] is True


def test_running_job_delete_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    job.status = "running"
    save_jobs()

    with pytest.raises(HTTPException) as exc_info:
        server_app.delete_job_endpoint(job.id, confirm=True)

    assert exc_info.value.status_code == 409
    assert "running jobs cannot be deleted" in exc_info.value.detail


def test_missing_job_invalid_export_and_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    with pytest.raises(HTTPException):
        server_app.get_job_endpoint("missing")
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    with pytest.raises(HTTPException):
        server_app.get_export(job.id, "../../secret")
    with pytest.raises(HTTPException):
        server_app.get_export(job.id, "exe")


def test_server_error_redaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_store(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        server_app.models_pull(
            server_app.ModelPullRequest(
                diarization="community-1",
                cache_dir=str(tmp_path),
                dry_run=False,
            )
        )
    assert "hf_" not in str(exc_info.value.detail)


def test_api_version_and_normal_metadata_do_not_expose_absolute_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "private-name.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    update_job(job.id, status="failed", error=f"decoder failed at {tmp_path}/private-cache")

    record = server_app.get_job_endpoint(job.id)
    status = server_app.get_job_status(job.id)
    assert server_app.app.version == __version__
    assert str(tmp_path) not in repr(record)
    assert str(tmp_path) not in repr(status)
    assert "input_path" not in record
    assert "out_dir" not in record
    assert "job_dir" not in record
    for model in server_app.models():
        assert not Path(str(model["path"])).is_absolute()

    pull = server_app.models_pull(
        server_app.ModelPullRequest(asr="large-v3", cache_dir=str(tmp_path / "models"))
    )
    assert str(tmp_path) not in repr(pull)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LTM_API_MAX_CONCURRENT_JOBS", "0"),
        ("LTM_API_MAX_CONCURRENT_JOBS", "not-an-int"),
        ("LTM_API_MAX_QUEUED_JOBS", "-1"),
        ("LTM_API_MAX_QUEUED_JOBS", "1001"),
    ],
)
def test_invalid_queue_environment_is_rejected(
    name: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(HTTPException) as exc_info:
        server_app.queue_state()
    assert exc_info.value.status_code == 503
    assert name in str(exc_info.value.detail)


def test_network_bound_mode_requires_and_enforces_explicit_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    input_root = tmp_path / "inputs"
    output_root = tmp_path / "outputs"
    input_root.mkdir()
    output_root.mkdir()
    audio = input_root / "sample.wav"
    audio.write_bytes(b"x")
    monkeypatch.setenv("LTM_API_NETWORK_BOUND", "1")

    with pytest.raises(HTTPException) as exc_info:
        server_app.transcribe(
            server_app.TranscribeRequest(
                input_path=str(audio),
                out_dir=str(output_root),
            ),
            BackgroundTasks(),
        )
    assert exc_info.value.status_code == 503

    monkeypatch.setenv("LTM_API_ALLOWED_INPUT_ROOTS", str(input_root))
    monkeypatch.setenv("LTM_API_ALLOWED_OUTPUT_ROOTS", str(output_root))
    response = server_app.transcribe(
        server_app.TranscribeRequest(
            input_path=str(audio),
            out_dir=str(output_root),
        ),
        BackgroundTasks(),
    )
    assert response["id"]

    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"x")
    with pytest.raises(HTTPException) as exc_info:
        server_app.transcribe(
            server_app.TranscribeRequest(
                input_path=str(outside),
                out_dir=str(output_root),
            ),
            BackgroundTasks(),
        )
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        server_app.models_pull(
            server_app.ModelPullRequest(
                asr="large-v3",
                cache_dir=str(tmp_path / "outside-models"),
            )
        )
    assert exc_info.value.status_code == 403


def test_configured_roots_are_enforced_in_loopback_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"x")
    monkeypatch.setenv("LTM_API_ALLOWED_INPUT_ROOTS", str(allowed))

    with pytest.raises(HTTPException) as exc_info:
        server_app.transcribe(
            server_app.TranscribeRequest(input_path=str(outside)),
            BackgroundTasks(),
        )
    assert exc_info.value.status_code == 403


def test_export_rejects_symlink_escape_and_oversized_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    job.job_dir.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (job.job_dir / "transcript.txt").symlink_to(outside)

    with pytest.raises(HTTPException) as exc_info:
        server_app.get_export(job.id, "txt")
    assert exc_info.value.status_code == 400

    (job.job_dir / "transcript.txt").unlink()
    (job.job_dir / "transcript.txt").write_text("12345", encoding="utf-8")
    monkeypatch.setenv("LTM_API_MAX_ARTIFACT_BYTES", "4")
    with pytest.raises(HTTPException) as exc_info:
        server_app.get_export(job.id, "txt")
    assert exc_info.value.status_code == 413


def test_qa_response_redacts_known_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    job.job_dir.mkdir(parents=True)
    write_json(
        job.job_dir / "qa_report.json",
        {
            "source": str(audio),
            "message": f"cache at {tmp_path}/cache and /mnt/private/cache",
            "windows": "cache at C:\\Users\\private\\cache",
            "url": "https://localhost.example/status",
        },
    )

    response = server_app.get_job_qa(job.id)
    assert str(tmp_path) not in repr(response)
    assert response["source"] == "<local-path>"
    assert "/mnt/private/cache" not in str(response["message"])
    assert "C:\\Users" not in str(response["windows"])
    assert response["url"] == "https://localhost.example/status"


def test_corrupt_store_is_quarantined_without_loading_partial_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    store = tmp_path / "store"
    store.mkdir()
    valid_but_unauthorized = {
        "input_path": "/etc/passwd",
        "out_dir": "/",
        "status": "complete",
        "id": "a" * 32,
        "created_at": "2026-01-01T00:00:00+00:00",
        "error": None,
    }
    write_json(
        store / "jobs.json",
        {
            "schema_version": 1,
            "jobs": [valid_but_unauthorized, {**valid_but_unauthorized, "id": "invalid"}],
        },
    )

    load_jobs(force=True)

    assert JOBS == {}
    assert len(list(store.glob("jobs.json.corrupt-*"))) == 1
    assert read_json(store / "jobs.json") == {"jobs": [], "schema_version": 1}


def test_persisted_active_jobs_fail_closed_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    update_job(job.id, status="running")

    load_jobs(force=True)

    recovered = get_job(job.id)
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error == "job interrupted by API process restart"
    assert read_json(tmp_path / "store" / "jobs.json")["jobs"][0]["status"] == "failed"


def test_retention_and_job_listing_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    monkeypatch.setenv("LTM_API_MAX_RETAINED_JOBS", "2")
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    ids: list[str] = []
    for _ in range(3):
        job = create_job(audio, tmp_path / "out")
        ids.append(job.id)
        update_job(job.id, status="complete")

    assert len(list_jobs()) == 2
    assert ids[0] not in JOBS
    monkeypatch.setenv("LTM_API_MAX_LISTED_JOBS", "1")
    assert len(server_app.jobs()) == 1


def test_delete_files_failure_preserves_job_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    job.job_dir.mkdir(parents=True)
    (job.job_dir / "transcript.txt").write_text("hello", encoding="utf-8")

    def fail_delete(path: Path) -> None:
        raise OSError("simulated delete failure")

    monkeypatch.setattr(job_store.shutil, "rmtree", fail_delete)
    with pytest.raises(HTTPException) as exc_info:
        server_app.delete_job_endpoint(job.id, confirm=True, delete_files=True)
    assert exc_info.value.status_code == 500
    assert get_job(job.id) is not None
    assert job.job_dir.exists()


def test_delete_files_rejects_symlinked_job_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_store(tmp_path, monkeypatch)
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    job.out_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    job.job_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(HTTPException) as exc_info:
        server_app.delete_job_endpoint(job.id, confirm=True, delete_files=True)
    assert exc_info.value.status_code == 409
    assert get_job(job.id) is not None
    assert outside.exists()
