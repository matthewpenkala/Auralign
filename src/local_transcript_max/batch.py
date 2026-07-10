# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Batch transcription discovery, execution, and aggregate reporting."""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import time
from pathlib import Path
from typing import Any

from .config import PipelineConfig
from .pipeline import select_asr_backend, select_diarization_backend, transcribe_file
from .redaction import privacy_safe_text
from .utils import atomic_text_write, atomic_text_writer, read_json, write_json
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
    asr_backend = select_asr_backend(config)
    diarization_backend = select_diarization_backend(config)
    for audio in discover_audio(input_dir, recursive=recursive, include=include, exclude=exclude):
        job_dir = out_dir / safe_job_name(audio)
        transcript_path = job_dir / "transcript.json"
        if skip_completed and transcript_path.exists():
            rows.append(
                {"input": str(audio), "job_dir": str(job_dir), "status": "skipped_complete"}
            )
            continue
        if resume and (job_dir / "run_metadata.json").exists() and transcript_path.exists():
            rows.append(
                {"input": str(audio), "job_dir": str(job_dir), "status": "resumed_complete"}
            )
            continue
        started = time.perf_counter()
        try:
            transcribe_file(
                audio,
                job_dir,
                config,
                asr_backend=asr_backend,
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
        "rows": rows,
    }
    write_json(out_dir / "batch_summary.json", summary)
    atomic_text_write(out_dir / "batch_summary.md", render_batch_markdown(summary))
    write_aggregate_qa(out_dir / "aggregate_qa.csv", rows)
    write_aggregate_speaker_validation(out_dir / "aggregate_speaker_validation.csv", rows)
    return summary


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
