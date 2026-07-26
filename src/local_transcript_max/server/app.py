# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Local-only FastAPI app."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, cast

from local_transcript_max import __version__
from local_transcript_max.config import PresetName, load_config
from local_transcript_max.doctor import run_doctor
from local_transcript_max.models import (
    all_statuses,
    ensure_pull_preconditions,
    pull_model,
    selected_entries,
)
from local_transcript_max.pipeline import transcribe_file
from local_transcript_max.platform_security import (
    UnsafePathError,
    open_regular_nofollow,
    reject_reparse_or_symlink,
)
from local_transcript_max.redaction import privacy_safe_text
from local_transcript_max.speaker_id.identity_io import IdentityResolverError, JobIdentityLock
from local_transcript_max.speaker_id.identity_models import (
    ResolverMode,
    ResolverPolicy,
    SourceMediaReviewMode,
)
from local_transcript_max.speaker_id.identity_resolver import (
    ResolveIdentityOptions,
    resolve_identities,
)

try:
    from fastapi import BackgroundTasks, FastAPI, HTTPException
    from fastapi.responses import PlainTextResponse
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - import-time guard for optional extra
    raise RuntimeError("Install API dependencies with: uv sync --extra api") from exc

from .jobs import (
    JOBS_LOCK,
    Job,
    create_job,
    delete_job,
    get_job,
    list_jobs,
    update_job,
)

app = FastAPI(title="Local Transcript Max", version=__version__)
ALLOWED_EXPORTS = {"txt", "md", "srt", "vtt", "json", "jsonl", "rttm", "csv"}
IDENTITY_ARTIFACTS = {
    "report": "speaker_identity_report.json",
    "candidates": "speaker_identity_candidates.json",
    "evidence": "speaker_identity_evidence.jsonl",
    "attribution-audit": "speaker_identity_attribution_audit.json",
    "attribution-audit-csv": "speaker_identity_attribution_audit.csv",
    "diarization-stability": "speaker_identity_diarization_stability.json",
    "media-review-queue": "speaker_identity_media_review_queue.json",
    "media-review-report": "speaker_identity_media_review_report.json",
    "approved-map": "speaker_identity_approved_map.json",
    "transcript-json": "transcript.identity_mapped.json",
    "transcript-srt": "transcript.identity_mapped.srt",
    "transcript-md": "transcript.identity_mapped.md",
    "transcript-vtt": "transcript.identity_mapped.vtt",
    "transcript-txt": "transcript.identity_mapped.txt",
    "transcript-segments-csv": "transcript.identity_mapped.segments.csv",
}
DEFAULT_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_LISTED_JOBS = 100
MAX_LISTED_JOBS = 1_000
MAX_CONCURRENT_JOBS = 16
MAX_QUEUED_JOBS = 1_000
PATH_FIELD_MAX_LENGTH = 4_096


class TranscribeRequest(BaseModel):
    input_path: str = Field(min_length=1, max_length=PATH_FIELD_MAX_LENGTH)
    out_dir: str = Field(default="outputs/api", min_length=1, max_length=PATH_FIELD_MAX_LENGTH)
    preset: str = Field(default="max", min_length=1, max_length=32)
    debug_report: bool = False


class ModelPullRequest(BaseModel):
    all_models: bool = False
    asr: str | None = None
    asr_mlx: str | None = None
    diarization: str | None = None
    alignment: str | None = None
    speaker_id: str | None = None
    cache_dir: str = Field(default="models", min_length=1, max_length=PATH_FIELD_MAX_LENGTH)
    dry_run: bool = Field(default=True)


class ResolveIdentitiesRequest(BaseModel):
    mode: Literal["review", "apply-safe", "apply-approved"] = "review"
    known_speakers: int | None = Field(default=None, ge=1, le=128)
    use_roster: bool = False
    use_approved_map: bool = False
    use_stability_config: bool = False
    use_media_review_report: bool = False
    write_identity_mapped_exports: bool = False
    write_media_review_queue: bool = False
    audit_csv: bool = True


@app.get("/health")
def health() -> dict[str, object]:
    config = load_config()
    report = run_doctor(config, include_docker=False)
    return {"ok": report.ok, "offline": report.offline, "queue": queue_state()}


@app.get("/models")
def models() -> list[dict[str, object]]:
    config = load_config()
    return [
        {
            "role": status.entry.role,
            "key": status.entry.key,
            "model_id": status.entry.model_id,
            "present": status.present,
            "path": status.entry.directory_name,
            "artifact_id": f"model-cache/{status.entry.key}",
        }
        for status in all_statuses(config.model_cache)
    ]


@app.post("/models/pull")
def models_pull(request: ModelPullRequest) -> dict[str, object]:
    entries = selected_entries(
        all_models=request.all_models,
        asr=request.asr,
        asr_mlx=request.asr_mlx,
        diarization=request.diarization,
        alignment=request.alignment,
        speaker_embedding=request.speaker_id,
    )
    cache_dir = authorize_request_path(Path(request.cache_dir), kind="output")
    try:
        results = []
        for entry in entries:
            artifact_id = f"model-cache/{entry.key}"
            if request.dry_run:
                results.append(
                    {
                        "key": entry.key,
                        "status": "dry_run",
                        "model_id": entry.model_id,
                        "artifact_id": artifact_id,
                    }
                )
        if request.dry_run:
            return {"ok": True, "results": results}
        if active_transcription_count() > 0:
            raise HTTPException(
                status_code=409,
                detail="model pulls are blocked while transcription jobs are queued or running",
            )
        ensure_pull_preconditions(entries, cache_dir)
        for entry in entries:
            pull_model(entry, cache_dir)
            results.append(
                {
                    "key": entry.key,
                    "status": "pulled",
                    "path": entry.directory_name,
                    "artifact_id": f"model-cache/{entry.key}",
                }
            )
        return {"ok": True, "results": results}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=public_operation_error("model pull", exc),
        ) from exc


@app.post("/transcribe")
def transcribe(request: TranscribeRequest, background_tasks: BackgroundTasks) -> dict[str, object]:
    input_path = authorize_request_path(Path(request.input_path), kind="input")
    if not input_path.exists() or not input_path.is_file():
        raise HTTPException(status_code=400, detail="input_path does not exist or is not a file")
    if request.preset not in {"max", "balanced", "fast", "cpu"}:
        raise HTTPException(
            status_code=400,
            detail="preset must be one of: max, balanced, fast, cpu",
        )
    out_dir = authorize_request_path(Path(request.out_dir), kind="output")
    with JOBS_LOCK:
        enforce_job_capacity()
        job = create_job(input_path, out_dir)
    background_tasks.add_task(run_transcription_job, job.id, request.preset, request.debug_report)
    return {"id": job.id, "status": job.status, "error": job.error}


def run_transcription_job(job_id: str, preset: str, debug_report: bool) -> None:
    job = get_job(job_id)
    if job is None:
        return
    try:
        claimed_job = update_job(job_id, status="running")
        if claimed_job is None:
            return
        job = claimed_job
        config = load_config(preset=cast(PresetName, preset))
        transcribe_file(
            job.input_path,
            job.job_dir,
            config,
            debug_report_path=job.job_dir / "debug_report.json" if debug_report else None,
        )
        update_job(job_id, status="complete")
    except Exception as exc:  # pragma: no cover - exercised by API smoke tests
        update_job(job_id, status="failed", error=public_operation_error("transcription", exc))


def enforce_job_capacity() -> None:
    state = queue_state()
    running = int(state["running"])
    queued = int(state["queued"])
    max_running = int(state["max_running"])
    max_queued = int(state["max_queued"])
    effective_queued_limit = int(state["effective_queued_limit"])
    if running >= max_running or queued >= effective_queued_limit:
        raise HTTPException(
            status_code=429,
            detail=(
                "local transcription queue is full "
                f"(running={running}/{max_running}, "
                f"queued={queued}/{effective_queued_limit}, "
                f"configured_max_queued={max_queued})"
            ),
        )


def active_transcription_count() -> int:
    return sum(1 for job in list_jobs() if job.status in {"queued", "running"})


def queue_state() -> dict[str, int]:
    jobs = list_jobs()
    running = sum(1 for job in jobs if job.status == "running")
    queued = sum(1 for job in jobs if job.status == "queued")
    try:
        max_running = env_int(
            "LTM_API_MAX_CONCURRENT_JOBS",
            1,
            minimum=1,
            maximum=MAX_CONCURRENT_JOBS,
        )
        max_queued = env_int(
            "LTM_API_MAX_QUEUED_JOBS",
            0,
            minimum=0,
            maximum=MAX_QUEUED_JOBS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    effective_queued_limit = max_queued + max(0, max_running - running)
    return {
        "running": running,
        "queued": queued,
        "active": running + queued,
        "max_running": max_running,
        "max_queued": max_queued,
        "effective_queued_limit": effective_queued_limit,
        "capacity_remaining": max(0, max_running + max_queued - running - queued),
    }


def env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


@app.get("/jobs")
def jobs() -> list[dict[str, object]]:
    try:
        limit = env_int(
            "LTM_API_MAX_LISTED_JOBS",
            DEFAULT_MAX_LISTED_JOBS,
            minimum=1,
            maximum=MAX_LISTED_JOBS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [job_to_dict(job) for job in list_jobs(limit=limit)]


@app.get("/jobs/{job_id}")
def get_job_endpoint(job_id: str) -> dict[str, object]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job_to_dict(job)


@app.get("/jobs/{job_id}/status")
def get_job_status(job_id: str) -> dict[str, object]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"id": job.id, "status": job.status, "error": public_job_error(job.error)}


@app.get("/jobs/{job_id}/qa")
def get_job_qa(job_id: str) -> dict[str, object]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    authorize_request_path(job.out_dir, kind="output")
    raw = read_job_artifact(job.job_dir, "qa_report.json", missing_detail="qa report not found")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="qa report is malformed") from exc
    response = payload if isinstance(payload, dict) else {"value": payload}
    return cast(dict[str, object], privacy_safe_response(response, job))


@app.get("/jobs/{job_id}/export/{fmt}")
def get_export(job_id: str, fmt: str) -> PlainTextResponse:
    if fmt not in ALLOWED_EXPORTS:
        raise HTTPException(status_code=400, detail="invalid export format")
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    authorize_request_path(job.out_dir, kind="output")
    filename = "transcript.rttm" if fmt == "rttm" else f"transcript.{fmt}"
    raw = read_job_artifact(job.job_dir, filename, missing_detail="export not found")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="export is not valid UTF-8") from exc
    return PlainTextResponse(text)


@app.post("/jobs/{job_id}/resolve-identities")
def resolve_job_identities(job_id: str, request: ResolveIdentitiesRequest) -> dict[str, object]:
    """Run the bounded offline resolver against one completed API job."""
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        with JobIdentityLock(job.job_dir):
            current = get_job(job_id)
            if current is None:
                raise HTTPException(status_code=404, detail="job not found")
            if current.status != "complete":
                raise HTTPException(
                    status_code=409,
                    detail="identity resolution requires a completed transcription job",
                )
            authorize_request_path(current.out_dir, kind="output")
            mode = ResolverMode(request.mode)
            media_mode = (
                SourceMediaReviewMode.REVIEWED_REPORT
                if request.use_media_review_report
                else SourceMediaReviewMode.QUEUE
            )
            options = ResolveIdentityOptions(
                policy=ResolverPolicy(
                    mode=mode,
                    audit_csv=request.audit_csv,
                    source_media_review=media_mode,
                ),
                known_speakers=request.known_speakers,
                roster_path=_identity_input(current, "speaker_identity_roster.json")
                if request.use_roster
                else None,
                approved_map_path=_identity_input(current, "speaker_identity_approved_map.json")
                if request.use_approved_map
                else None,
                stability_config_path=_identity_input(
                    current, "speaker_identity_stability_config.json"
                )
                if request.use_stability_config
                else None,
                media_review_report_path=_identity_input(
                    current, "speaker_identity_media_review_report.json"
                )
                if request.use_media_review_report
                else None,
                write_identity_mapped_exports=request.write_identity_mapped_exports,
                write_media_review_queue=request.write_media_review_queue,
                job_lock_already_held=True,
            )
            result = resolve_identities(current.job_dir, options)
            job = current
    except IdentityResolverError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "schema_version": "speaker_identity_error.v1",
                "code": exc.code,
                "message": privacy_safe_text(str(exc)),
                "remediation": privacy_safe_text(exc.remediation) if exc.remediation else None,
            },
        ) from exc
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        if get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found") from exc
        raise HTTPException(status_code=409, detail="job directory is unavailable") from exc
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=public_operation_error("identity resolution", exc),
        ) from exc
    return {
        "id": job.id,
        "status": "complete",
        "mode": result.report.mode.value,
        "run_id": result.report.run_id,
        "cluster_decision_count": len(result.report.cluster_decisions),
        "turn_decision_count": len(result.report.turn_decisions),
        "artifacts": list(result.written_artifacts),
    }


@app.get("/jobs/{job_id}/identity/{artifact_id}")
def get_identity_artifact(job_id: str, artifact_id: str) -> PlainTextResponse:
    filename = IDENTITY_ARTIFACTS.get(artifact_id)
    if filename is None:
        raise HTTPException(status_code=400, detail="invalid identity artifact")
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    authorize_request_path(job.out_dir, kind="output")
    raw = read_job_artifact(job.job_dir, filename, missing_detail="identity artifact not found")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="identity artifact is not valid UTF-8") from exc
    return PlainTextResponse(text)


def _identity_input(job: Job, filename: str) -> Path:
    path = safe_job_artifact(job.job_dir, filename)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=400, detail=f"required {filename} input not found")
    return path


@app.delete("/jobs/{job_id}")
def delete_job_endpoint(
    job_id: str,
    confirm: bool = False,
    delete_files: bool = False,
) -> dict[str, object]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not confirm:
        raise HTTPException(status_code=400, detail="delete requires confirm=true")
    try:
        if delete_files:
            authorize_request_path(job.out_dir, kind="output")
        if job.job_dir.is_dir() and not job.job_dir.is_symlink():
            with JobIdentityLock(job.job_dir):
                removed = delete_job(job_id, delete_files=delete_files)
        else:
            removed = delete_job(job_id, delete_files=delete_files)
    except IdentityResolverError as exc:
        if exc.code == "resolver_busy":
            raise HTTPException(
                status_code=409,
                detail="job identity operation is active; retry deletion later",
            ) from exc
        raise HTTPException(
            status_code=409,
            detail="job deletion is temporarily unavailable",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="job file deletion failed") from exc
    return {"deleted": removed, "id": job_id}


def job_record(job_id: str) -> dict[str, object]:
    job = get_job(job_id)
    if job is None:
        return {"id": job_id, "status": "missing"}
    return job_to_dict(job)


def job_to_dict(job: Job) -> dict[str, object]:
    return {
        "id": job.id,
        "status": job.status,
        "error": public_job_error(job.error),
        "created_at": job.created_at.isoformat(),
        "artifact_id": f"job/{job.id}",
    }


def safe_job_artifact(job_dir: Path, filename: str) -> Path:
    if (
        not filename
        or len(filename) > 255
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
        or ".." in Path(filename).parts
    ):
        raise HTTPException(status_code=400, detail="invalid artifact path")
    if job_dir.is_symlink():
        raise HTTPException(status_code=400, detail="invalid artifact path")
    path = job_dir / filename
    if path.is_symlink():
        raise HTTPException(status_code=400, detail="invalid artifact path")
    root = job_dir.resolve()
    resolved = path.resolve()
    if root not in resolved.parents:
        raise HTTPException(status_code=400, detail="invalid artifact path")
    return path


def read_job_artifact(job_dir: Path, filename: str, *, missing_detail: str) -> bytes:
    """Open a bounded regular file without following its final symlink."""
    path = safe_job_artifact(job_dir, filename)
    limit = artifact_byte_limit()
    artifact_fd: int | None = None
    try:
        reject_reparse_or_symlink(job_dir)
        artifact_fd = open_regular_nofollow(path, os.O_RDONLY)
        file_stat = os.fstat(artifact_fd)
        if file_stat.st_size > limit:
            raise HTTPException(status_code=413, detail="artifact exceeds response size limit")
        with os.fdopen(artifact_fd, "rb", closefd=True) as handle:
            artifact_fd = None
            payload = handle.read(limit + 1)
        if len(payload) > limit:
            raise HTTPException(status_code=413, detail="artifact exceeds response size limit")
        return payload
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=missing_detail) from exc
    except (OSError, UnsafePathError) as exc:
        raise HTTPException(status_code=400, detail="invalid artifact path") from exc
    finally:
        if artifact_fd is not None:
            os.close(artifact_fd)


def artifact_byte_limit() -> int:
    try:
        return env_int(
            "LTM_API_MAX_ARTIFACT_BYTES",
            DEFAULT_MAX_ARTIFACT_BYTES,
            minimum=1,
            maximum=MAX_ARTIFACT_BYTES,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def authorize_request_path(path: Path, *, kind: str) -> Path:
    """Resolve an API path and enforce configured input/output roots."""
    if kind not in {"input", "output"}:
        raise ValueError("path kind must be input or output")
    resolved = path.expanduser().resolve()
    roots = configured_allowed_roots(kind)
    if roots and not any(root == resolved or root in resolved.parents for root in roots):
        raise HTTPException(status_code=403, detail=f"{kind} path is outside configured roots")
    return resolved


def configured_allowed_roots(kind: str) -> tuple[Path, ...]:
    network_bound = network_bound_mode()
    name = f"LTM_API_ALLOWED_{kind.upper()}_ROOTS"
    raw = os.getenv(name)
    if not raw:
        if network_bound:
            raise HTTPException(
                status_code=503,
                detail=(
                    "network-bound API mode requires explicit "
                    "LTM_API_ALLOWED_INPUT_ROOTS and LTM_API_ALLOWED_OUTPUT_ROOTS"
                ),
            )
        return ()
    roots: list[Path] = []
    for item in raw.split(os.pathsep):
        if not item.strip():
            raise HTTPException(status_code=503, detail=f"{name} contains an empty root")
        configured = Path(item.strip()).expanduser()
        if not configured.is_absolute():
            raise HTTPException(status_code=503, detail=f"{name} roots must be absolute")
        resolved = configured.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise HTTPException(
                status_code=503,
                detail=f"{name} roots must be existing directories",
            )
        roots.append(resolved)
    return tuple(roots)


def network_bound_mode() -> bool:
    raw = os.getenv("LTM_API_NETWORK_BOUND")
    if raw is None:
        return False
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=503, detail="LTM_API_NETWORK_BOUND must be a boolean")


def privacy_safe_response(value: Any, job: Job) -> Any:
    if isinstance(value, str):
        return privacy_safe_text(value, private_paths=(job.input_path, job.out_dir, job.job_dir))
    if isinstance(value, dict):
        return {str(key): privacy_safe_response(item, job) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [privacy_safe_response(item, job) for item in value]
    return value


def public_operation_error(operation: str, exc: Exception) -> str:
    detail = privacy_safe_text(str(exc)).strip()
    if not detail:
        return f"{operation} failed"
    return f"{operation} failed: {detail}"[:512]


def public_job_error(error: str | None) -> str | None:
    if error is None:
        return None
    return privacy_safe_text(error)[:512]
