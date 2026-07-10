# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from local_transcript_max.server import app as server_app
from local_transcript_max.server.jobs import JOBS, create_job, get_job, update_job
from local_transcript_max.speaker_id.identity_io import IdentityResolverError
from local_transcript_max.speaker_id.identity_models import ResolverMode


def _complete_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LTM_API_JOB_STORE", str(tmp_path / "store"))
    JOBS.clear()
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")
    job.job_dir.mkdir(parents=True)
    update_job(job.id, status="complete")
    return job


def test_identity_resolution_is_explicit_job_scoped_and_privacy_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _complete_job(tmp_path, monkeypatch)

    def fake_resolve(job_dir: Path, options):
        assert job_dir == job.job_dir
        assert options.policy.mode == ResolverMode.REVIEW
        return SimpleNamespace(
            report=SimpleNamespace(
                mode=ResolverMode.REVIEW,
                run_id="run:0123456789abcdef",
                cluster_decisions=[object()],
                turn_decisions=[object(), object()],
            ),
            written_artifacts=("speaker_identity_report.json",),
        )

    monkeypatch.setattr(server_app, "resolve_identities", fake_resolve)
    response = server_app.resolve_job_identities(job.id, server_app.ResolveIdentitiesRequest())

    assert response["mode"] == "review"
    assert response["cluster_decision_count"] == 1
    assert response["turn_decision_count"] == 2
    assert str(tmp_path) not in repr(response)


def test_identity_resolution_requires_complete_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LTM_API_JOB_STORE", str(tmp_path / "store"))
    JOBS.clear()
    audio = tmp_path / "synthetic.wav"
    audio.write_bytes(b"x")
    job = create_job(audio, tmp_path / "out")

    with pytest.raises(HTTPException) as error:
        server_app.resolve_job_identities(job.id, server_app.ResolveIdentitiesRequest())
    assert error.value.status_code == 409


def test_identity_resolver_error_is_typed_and_does_not_leak_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _complete_job(tmp_path, monkeypatch)

    def fail_resolve(job_dir: Path, options):
        raise IdentityResolverError(
            "resolver_busy",
            f"operation busy at {tmp_path}/private-job",
            f"retry after {tmp_path}/private-lock",
        )

    monkeypatch.setattr(server_app, "resolve_identities", fail_resolve)
    with pytest.raises(HTTPException) as error:
        server_app.resolve_job_identities(job.id, server_app.ResolveIdentitiesRequest())

    assert error.value.status_code == 409
    assert error.value.detail["schema_version"] == "speaker_identity_error.v1"
    assert error.value.detail["code"] == "resolver_busy"
    assert str(tmp_path) not in repr(error.value.detail)


def test_identity_artifact_retrieval_is_allowlisted_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _complete_job(tmp_path, monkeypatch)
    report = job.job_dir / "speaker_identity_report.json"
    report.write_text('{"schema_version":"speaker_identity_report.v1"}\n', encoding="utf-8")

    response = server_app.get_identity_artifact(job.id, "report")
    assert b"speaker_identity_report.v1" in response.body

    with pytest.raises(HTTPException) as invalid:
        server_app.get_identity_artifact(job.id, "../../transcript")
    assert invalid.value.status_code == 400

    monkeypatch.setenv("LTM_API_MAX_ARTIFACT_BYTES", "4")
    with pytest.raises(HTTPException) as oversized:
        server_app.get_identity_artifact(job.id, "report")
    assert oversized.value.status_code == 413


def test_identity_inputs_are_fixed_job_local_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _complete_job(tmp_path, monkeypatch)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (job.job_dir / "speaker_identity_roster.json").symlink_to(outside)

    with pytest.raises(HTTPException) as error:
        server_app.resolve_job_identities(
            job.id,
            server_app.ResolveIdentitiesRequest(use_roster=True),
        )
    assert error.value.status_code == 400


def test_delete_conflicts_with_active_identity_resolution_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _complete_job(tmp_path, monkeypatch)
    entered = Event()
    release = Event()
    failures: list[BaseException] = []

    def blocking_resolve(job_dir: Path, options):
        assert options.job_lock_already_held is True
        entered.set()
        assert release.wait(timeout=5)
        return SimpleNamespace(
            report=SimpleNamespace(
                mode=options.policy.mode,
                run_id="run:0123456789abcdef",
                cluster_decisions=[],
                turn_decisions=[],
            ),
            written_artifacts=("speaker_identity_report.json",),
        )

    monkeypatch.setattr(server_app, "resolve_identities", blocking_resolve)

    def run_resolution() -> None:
        try:
            server_app.resolve_job_identities(job.id, server_app.ResolveIdentitiesRequest())
        except BaseException as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    thread = Thread(target=run_resolution)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(HTTPException) as conflict:
            server_app.delete_job_endpoint(job.id, confirm=True)
        assert conflict.value.status_code == 409
        assert conflict.value.detail == "job identity operation is active; retry deletion later"
        assert str(tmp_path) not in str(conflict.value.detail)
        retained = get_job(job.id)
        assert retained is not None
        assert retained.status == "complete"
    finally:
        release.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert failures == []
    assert server_app.delete_job_endpoint(job.id, confirm=True) == {
        "deleted": True,
        "id": job.id,
    }
    assert get_job(job.id) is None
