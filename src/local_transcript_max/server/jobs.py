# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Local filesystem-backed job store for the API."""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from local_transcript_max.redaction import redact_text
from local_transcript_max.utils import atomic_write_json

JobStatus = Literal["queued", "running", "complete", "failed"]

JOB_STORE_SCHEMA_VERSION = 1
JOB_STORE_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_RETAINED_JOBS = 1_000
MAX_RETAINED_JOBS = 10_000
MAX_ERROR_LENGTH = 2_000
MAX_PATH_LENGTH = 4_096
VALID_JOB_STATUSES: frozenset[str] = frozenset({"queued", "running", "complete", "failed"})


class JobStoreError(RuntimeError):
    """Raised for a job-store configuration or persistence failure."""


class InvalidJobStoreError(ValueError):
    """Raised when persisted job metadata is malformed or unsupported."""


@dataclass
class Job:
    input_path: Path
    out_dir: Path
    status: JobStatus = "queued"
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None

    @property
    def job_dir(self) -> Path:
        return self.out_dir / self.id


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.RLock()
_LOADED_STORE_PATH: Path | None = None


def job_store_path() -> Path:
    root = Path(os.getenv("LTM_API_JOB_STORE", "outputs/api/.job-store")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise JobStoreError("LTM_API_JOB_STORE must identify a directory")
    return root.resolve() / "jobs.json"


def load_jobs(*, force: bool = False) -> None:
    """Load and validate the configured store once for this process.

    Active persisted jobs cannot still be running after process startup, so they
    are deterministically recovered as failed. A malformed store is quarantined
    as an opaque local recovery artifact; no records from it are authorized.
    """
    global _LOADED_STORE_PATH

    with JOBS_LOCK:
        path = job_store_path()
        if not force and _LOADED_STORE_PATH == path:
            return
        JOBS.clear()
        _LOADED_STORE_PATH = path
        if not path.exists():
            return
        try:
            jobs = _read_and_validate_store(path)
        except (InvalidJobStoreError, OSError, UnicodeError, json.JSONDecodeError):
            JOBS.clear()
            _quarantine_corrupt_store(path)
            save_jobs()
            return

        recovered = False
        for job in jobs:
            if job.status in {"queued", "running"}:
                job.status = "failed"
                job.error = "job interrupted by API process restart"
                recovered = True
            JOBS[job.id] = job
        if recovered:
            save_jobs()


def save_jobs() -> None:
    global _LOADED_STORE_PATH

    with JOBS_LOCK:
        path = job_store_path()
        if _LOADED_STORE_PATH != path:
            load_jobs()
        _prune_terminal_jobs()
        atomic_write_json(
            path,
            {
                "schema_version": JOB_STORE_SCHEMA_VERSION,
                "jobs": [
                    {
                        "input_path": str(job.input_path),
                        "out_dir": str(job.out_dir),
                        "status": job.status,
                        "id": job.id,
                        "created_at": job.created_at.isoformat(),
                        "error": _bounded_error(job.error),
                    }
                    for job in sorted(JOBS.values(), key=lambda item: (item.created_at, item.id))
                ],
            },
        )


def create_job(input_path: Path, out_dir: Path) -> Job:
    with JOBS_LOCK:
        load_jobs()
        job = Job(
            input_path=input_path.expanduser().resolve(),
            out_dir=out_dir.expanduser().resolve(),
        )
        JOBS[job.id] = job
        try:
            save_jobs()
        except Exception:
            JOBS.pop(job.id, None)
            raise
        return job


def update_job(
    job_id: str,
    *,
    status: JobStatus,
    error: str | None = None,
) -> Job | None:
    with JOBS_LOCK:
        load_jobs()
        job = JOBS.get(job_id)
        if job is None:
            return None
        job.status = status
        job.error = _bounded_error(error)
        save_jobs()
        return job


def get_job(job_id: str) -> Job | None:
    with JOBS_LOCK:
        load_jobs()
        return JOBS.get(job_id)


def list_jobs(*, limit: int | None = None) -> list[Job]:
    with JOBS_LOCK:
        load_jobs()
        jobs = sorted(JOBS.values(), key=lambda job: (job.created_at, job.id))
        if limit is not None:
            if limit < 1:
                raise ValueError("job listing limit must be positive")
            jobs = jobs[-limit:]
        return jobs


def delete_job(job_id: str, *, delete_files: bool = False) -> bool:
    """Delete files first, then revoke the persisted job record.

    A filesystem failure leaves the record intact and retriable. Symlinked job
    roots are never recursively deleted.
    """
    with JOBS_LOCK:
        load_jobs()
        job = JOBS.get(job_id)
        if job is None:
            return False
        if job.status == "running":
            raise ValueError("running jobs cannot be deleted without cancellation support")
        if delete_files:
            _delete_job_files(job)
        JOBS.pop(job_id)
        try:
            save_jobs()
        except Exception:
            JOBS[job_id] = job
            raise
        return True


def _read_and_validate_store(path: Path) -> list[Job]:
    if path.is_symlink():
        raise InvalidJobStoreError("job-store file must not be a symlink")
    if path.stat().st_size > JOB_STORE_MAX_BYTES:
        raise InvalidJobStoreError("job-store file exceeds the size limit")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise InvalidJobStoreError("job-store root must be an object")
    unknown_root_fields = set(payload) - {"schema_version", "jobs"}
    if unknown_root_fields:
        raise InvalidJobStoreError("job-store contains unknown fields")
    version = payload.get("schema_version", 0)
    if type(version) is not int or version not in {0, JOB_STORE_SCHEMA_VERSION}:
        raise InvalidJobStoreError("unsupported job-store schema version")
    rows = payload.get("jobs")
    if not isinstance(rows, list):
        raise InvalidJobStoreError("job-store jobs must be an array")
    if len(rows) > MAX_RETAINED_JOBS:
        raise InvalidJobStoreError("job-store exceeds the retained metadata limit")

    jobs: list[Job] = []
    seen_ids: set[str] = set()
    for row in rows:
        job = _parse_job_row(row)
        if job.id in seen_ids:
            raise InvalidJobStoreError("job-store contains duplicate job ids")
        seen_ids.add(job.id)
        jobs.append(job)
    return jobs


def _parse_job_row(value: object) -> Job:
    if not isinstance(value, dict):
        raise InvalidJobStoreError("job record must be an object")
    row = cast(dict[object, object], value)
    required = {"input_path", "out_dir", "status", "id", "created_at"}
    allowed = required | {"error"}
    if not required.issubset(row) or set(row) - allowed:
        raise InvalidJobStoreError("job record has invalid fields")

    job_id = row["id"]
    if not isinstance(job_id, str) or len(job_id) != 32:
        raise InvalidJobStoreError("job id is invalid")
    try:
        int(job_id, 16)
    except ValueError as exc:
        raise InvalidJobStoreError("job id is invalid") from exc
    if job_id.lower() != job_id:
        raise InvalidJobStoreError("job id is invalid")

    status = row["status"]
    if not isinstance(status, str) or status not in VALID_JOB_STATUSES:
        raise InvalidJobStoreError("job status is invalid")
    input_path = _parse_stored_path(row["input_path"])
    out_dir = _parse_stored_path(row["out_dir"])

    created_at_value = row["created_at"]
    if not isinstance(created_at_value, str) or len(created_at_value) > 64:
        raise InvalidJobStoreError("job creation timestamp is invalid")
    try:
        created_at = datetime.fromisoformat(created_at_value)
    except ValueError as exc:
        raise InvalidJobStoreError("job creation timestamp is invalid") from exc
    if created_at.tzinfo is None:
        raise InvalidJobStoreError("job creation timestamp must be timezone-aware")
    created_at = created_at.astimezone(UTC)

    error = row.get("error")
    if error is not None and (not isinstance(error, str) or len(error) > MAX_ERROR_LENGTH):
        raise InvalidJobStoreError("job error metadata is invalid")
    return Job(
        input_path=input_path,
        out_dir=out_dir,
        status=cast(JobStatus, status),
        id=job_id,
        created_at=created_at,
        error=error,
    )


def _parse_stored_path(value: object) -> Path:
    if not isinstance(value, str) or not value or len(value) > MAX_PATH_LENGTH or "\x00" in value:
        raise InvalidJobStoreError("job path is invalid")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise InvalidJobStoreError("job path must be normalized and absolute")
    return path


def _quarantine_corrupt_store(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    suffix = f".corrupt-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:8]}"
    quarantine = path.with_name(path.name + suffix)
    try:
        path.replace(quarantine)
    except OSError as exc:
        raise JobStoreError("invalid API job store could not be quarantined") from exc


def _prune_terminal_jobs() -> None:
    limit = retained_job_limit()
    if len(JOBS) <= limit:
        return
    terminal = sorted(
        (job for job in JOBS.values() if job.status in {"complete", "failed"}),
        key=lambda job: (job.created_at, job.id),
    )
    for job in terminal:
        if len(JOBS) <= limit:
            break
        JOBS.pop(job.id, None)


def retained_job_limit() -> int:
    raw = os.getenv("LTM_API_MAX_RETAINED_JOBS")
    if raw is None:
        return DEFAULT_MAX_RETAINED_JOBS
    try:
        value = int(raw)
    except ValueError as exc:
        raise JobStoreError("LTM_API_MAX_RETAINED_JOBS must be an integer") from exc
    if not 1 <= value <= MAX_RETAINED_JOBS:
        raise JobStoreError(f"LTM_API_MAX_RETAINED_JOBS must be between 1 and {MAX_RETAINED_JOBS}")
    return value


def _bounded_error(error: str | None) -> str | None:
    if not error:
        return None
    return redact_text(error)[:MAX_ERROR_LENGTH]


def _delete_job_files(job: Job) -> None:
    path = job.job_dir
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        raise ValueError("refusing to delete a symlinked job directory")
    expected = job.out_dir.resolve() / job.id
    if path.resolve() != expected or not path.is_dir():
        raise ValueError("refusing to delete an invalid job directory")
    shutil.rmtree(path)
