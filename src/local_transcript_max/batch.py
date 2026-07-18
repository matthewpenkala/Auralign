# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Batch transcription discovery, execution, and aggregate reporting."""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import math
import time
from pathlib import Path
from typing import Any

from .alignment.base import AlignmentBackend
from .alignment.forced_alignment import WhisperXForcedAlignment
from .asr.base import ASRBackend
from .config import PipelineConfig
from .diarization.base import DiarizationBackend
from .pipeline import select_asr_backend, select_diarization_backend, transcribe_file
from .redaction import privacy_safe_text
from .schemas import CompletionTimingReport
from .utils import (
    atomic_text_write,
    atomic_text_writer,
    read_json,
    sha256_file,
    write_json,
)
from .validation.speakers import validate_speaker_artifacts

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".mov", ".mp4"}


def discover_audio(
    input_dir: Path,
    *,
    recursive: bool = False,
    include: str = "*",
    exclude: str | None = None,
) -> list[Path]:
    iterator = input_dir.rglob("*") if recursive else input_dir.glob("*")
    files = []
    for path in iterator:
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        rel = str(path.relative_to(input_dir))
        if not fnmatch.fnmatch(path.name, include) and not fnmatch.fnmatch(rel, include):
            continue
        if exclude and (fnmatch.fnmatch(path.name, exclude) or fnmatch.fnmatch(rel, exclude)):
            continue
        files.append(path)
    return sorted(files)


def run_batch(
    input_dir: Path,
    out_dir: Path,
    config: PipelineConfig,
    *,
    recursive: bool = False,
    include: str = "*",
    exclude: str | None = None,
    resume: bool = False,
    skip_completed: bool = False,
    expected_speakers: int | None = None,
    debug_report: bool = False,
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    reuse_backends = config.runtime.model_lifecycle in {"auto", "persistent"}
    persistent_backends: (
        tuple[ASRBackend, AlignmentBackend | None, DiarizationBackend] | None
    ) = None
    for audio in discover_audio(input_dir, recursive=recursive, include=include, exclude=exclude):
        job_dir = out_dir / safe_job_name(audio)
        completed = job_has_completion_evidence(job_dir)
        if skip_completed and completed:
            rows.append(
                {"input": str(audio), "job_dir": str(job_dir), "status": "skipped_complete"}
            )
            continue
        if resume and completed:
            rows.append(
                {"input": str(audio), "job_dir": str(job_dir), "status": "resumed_complete"}
            )
            continue
        if reuse_backends:
            if persistent_backends is None:
                persistent_backends = _runtime_backends(config)
            asr_backend, alignment_backend, diarization_backend = persistent_backends
        else:
            asr_backend, alignment_backend, diarization_backend = _runtime_backends(
                config
            )
        started = time.perf_counter()
        try:
            transcribe_file(
                audio,
                job_dir,
                config,
                asr_backend=asr_backend,
                alignment_backend=alignment_backend,
                diarization_backend=diarization_backend,
                debug_report_path=job_dir / "debug_report.json" if debug_report else None,
            )
            speaker_validation = None
            if expected_speakers is not None:
                validation = validate_speaker_artifacts(job_dir, expected_speakers)
                speaker_validation = validation.model_dump(mode="json")
            rows.append(
                {
                    "input": str(audio),
                    "job_dir": str(job_dir),
                    "status": "complete",
                    "duration_seconds": round(time.perf_counter() - started, 6),
                    "speaker_validation": speaker_validation,
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "input": str(audio),
                    "job_dir": str(job_dir),
                    "status": "failed",
                    "error_type": exc.__class__.__name__,
                    "error": privacy_safe_text(str(exc)),
                }
            )
    summary = {
        "input_dir": str(input_dir),
        "out_dir": str(out_dir),
        "total": len(rows),
        "complete": sum(1 for row in rows if row["status"] == "complete"),
        "failed": sum(1 for row in rows if row["status"] == "failed"),
        "model_lifecycle": config.runtime.model_lifecycle,
        "rows": rows,
    }
    write_json(out_dir / "batch_summary.json", summary)
    atomic_text_write(out_dir / "batch_summary.md", render_batch_markdown(summary))
    write_aggregate_qa(out_dir / "aggregate_qa.csv", rows)
    write_aggregate_speaker_validation(out_dir / "aggregate_speaker_validation.csv", rows)
    return summary


def job_has_completion_evidence(job_dir: Path) -> bool:
    """Return whether the job reached the pipeline's final durable sentinel."""

    transcript_path = job_dir / "transcript.json"
    metadata_path = job_dir / "run_metadata.json"
    if not transcript_path.is_file() or not metadata_path.is_file():
        return False
    try:
        metadata = read_json(metadata_path)
    except (OSError, TypeError, ValueError):
        return False
    if not isinstance(metadata, dict):
        return False
    if "completion_timing_artifact" not in metadata:
        return _legacy_run_metadata_is_complete(metadata)
    if metadata.get("completion_timing_artifact") != "completion_timing.json":
        return False
    completion_path = job_dir / "completion_timing.json"
    if not completion_path.is_file():
        return False
    try:
        completion = CompletionTimingReport.model_validate(read_json(completion_path))
    except (OSError, TypeError, ValueError):
        return False
    return completion.run_metadata_sha256 == sha256_file(metadata_path)


def _legacy_run_metadata_is_complete(metadata: dict[str, Any]) -> bool:
    """Recognize the pre-completion-artifact batch resume contract."""

    timings = metadata.get("timings")
    if not isinstance(timings, list):
        return False
    for row in timings:
        if not isinstance(row, dict) or row.get("name") != "total":
            continue
        duration = row.get("duration_seconds")
        if (
            isinstance(duration, int | float)
            and not isinstance(duration, bool)
            and math.isfinite(duration)
            and duration >= 0
        ):
            return True
    return False


def _runtime_backends(
    config: PipelineConfig,
) -> tuple[ASRBackend, AlignmentBackend | None, DiarizationBackend]:
    return (
        select_asr_backend(config),
        (
            WhisperXForcedAlignment()
            if config.alignment.enabled
            and config.alignment.backend not in {"off", "whisper", "fallback"}
            else None
        ),
        select_diarization_backend(config),
    )


def safe_job_name(path: Path) -> str:
    digest = hashlib.sha256(str(path.expanduser().resolve()).encode("utf-8")).hexdigest()
    return f"{path.stem}-{digest[:10]}"


def render_batch_markdown(summary: dict[str, Any]) -> str:
    lines = ["# Batch Summary", ""]
    lines.append(f"Total: {summary['total']}")
    lines.append(f"Complete: {summary['complete']}")
    lines.append(f"Failed: {summary['failed']}")
    lines.append("")
    lines.append("| input | status | job |")
    lines.append("| --- | --- | --- |")
    for row in summary["rows"]:
        lines.append(f"| {row['input']} | {row['status']} | {row['job_dir']} |")
    return "\n".join(lines) + "\n"


def write_aggregate_qa(path: Path, rows: list[dict[str, Any]]) -> None:
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["input", "status", "flag_count", "overall"])
        writer.writeheader()
        for row in rows:
            qa_path = Path(row["job_dir"]) / "qa_report.json"
            qa = read_json(qa_path) if qa_path.exists() else {}
            summary = qa.get("summary", {}) if isinstance(qa, dict) else {}
            writer.writerow(
                {
                    "input": row["input"],
                    "status": row["status"],
                    "flag_count": summary.get("flag_count", ""),
                    "overall": summary.get("overall_quality_summary", ""),
                }
            )


def write_aggregate_speaker_validation(path: Path, rows: list[dict[str, Any]]) -> None:
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["input", "status", "unique_speakers", "expected"],
        )
        writer.writeheader()
        for row in rows:
            validation = row.get("speaker_validation") or {}
            writer.writerow(
                {
                    "input": row["input"],
                    "status": row["status"],
                    "unique_speakers": validation.get("unique_speakers", ""),
                    "expected": validation.get("expected_speakers", ""),
                }
            )
