# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Private local corpus benchmark helpers.

The corpus harness keeps private media, generated excerpts, rough SRT
references, logs, and benchmark artifacts in ignored local output directories.
It deliberately reports metadata and counts, never transcript or SRT text.
"""

from __future__ import annotations

import csv
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from local_transcript_max.asr.faster_whisper_backend import supported_compute_types
from local_transcript_max.utils import read_json, run_command, which, write_json

MEDIA_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".wav",
    ".webm",
}

PRESET_MATRIX: tuple[str, ...] = ("max", "balanced", "fast", "cpu")
ASR_COMPUTE_MATRIX: tuple[str, ...] = ("int8", "int8_float32", "float32")
ASR_BACKEND_MATRIX: tuple[str, ...] = ("faster-whisper", "mlx-whisper")
BenchmarkMode = Literal[
    "inventory-only",
    "excerpt-only",
    "run-single",
    "preset-matrix",
    "backend-matrix",
    "compute-matrix",
    "full-corpus",
]


@dataclass(frozen=True)
class MediaProbe:
    duration_seconds: float | None
    container: str | None
    audio_codec: str | None
    video_codec: str | None
    size_bytes: int


@dataclass(frozen=True)
class CorpusMedia:
    path: Path
    slug: str
    display_name: str
    inferred_speakers: int
    paired_srt: Path | None
    duration_seconds: float | None
    size_bytes: int
    container: str | None
    audio_codec: str | None
    video_codec: str | None
    benchmark_status: str = "planned"
    skipped_reason: str | None = None


@dataclass(frozen=True)
class SRTReference:
    source_srt: Path
    reference_text: Path
    kind: str
    captions_kept: int
    malformed_caption_blocks_skipped: int
    lines: int
    words: int
    window_start_seconds: float | None
    window_end_seconds: float | None


@dataclass(frozen=True)
class CommandRunResult:
    command: list[str]
    exit_code: int
    status: str
    wall_seconds: float
    stdout_log: Path
    stderr_log: Path
    resource_samples_csv: Path | None
    resource_sampling_supported: bool
    macos_time_supported: bool
    macos_time: dict[str, float | int]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "status": self.status,
            "wall_seconds": self.wall_seconds,
            "stdout_log": str(self.stdout_log),
            "stderr_log": str(self.stderr_log),
            "resource_samples_csv": str(self.resource_samples_csv)
            if self.resource_samples_csv
            else None,
            "resource_sampling_supported": self.resource_sampling_supported,
            "macos_time_supported": self.macos_time_supported,
            "macos_time": self.macos_time,
            "warnings": self.warnings,
        }


def discover_media(
    root: Path,
    *,
    recursive: bool = True,
    speaker_overrides: dict[str, int] | None = None,
) -> list[CorpusMedia]:
    root = root.expanduser().resolve()
    candidates = root.rglob("*") if recursive else root.glob("*")
    media_paths = sorted(
        path for path in candidates if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    )
    return [corpus_media(path, speaker_overrides=speaker_overrides) for path in media_paths]


def corpus_media(path: Path, *, speaker_overrides: dict[str, int] | None = None) -> CorpusMedia:
    path = path.expanduser().resolve()
    probe = probe_media(path)
    slug = slug_for_media(path)
    return CorpusMedia(
        path=path,
        slug=slug,
        display_name=path.name,
        inferred_speakers=infer_speaker_count(path, slug=slug, overrides=speaker_overrides),
        paired_srt=paired_youtube_srt(path),
        duration_seconds=probe.duration_seconds,
        size_bytes=probe.size_bytes,
        container=probe.container,
        audio_codec=probe.audio_codec,
        video_codec=probe.video_codec,
    )


def probe_media(path: Path) -> MediaProbe:
    args = [
        "ffprobe",
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    completed = run_command(args)
    payload = json.loads(completed.stdout) if completed.stdout else {}
    if not isinstance(payload, dict):
        payload = {}
    return media_probe_from_ffprobe(path, payload)


def media_probe_from_ffprobe(path: Path, payload: dict[str, Any]) -> MediaProbe:
    streams = payload.get("streams", [])
    stream_rows = streams if isinstance(streams, list) else []
    audio_stream = next(
        (
            stream
            for stream in stream_rows
            if isinstance(stream, dict) and stream.get("codec_type") == "audio"
        ),
        {},
    )
    video_stream = next(
        (
            stream
            for stream in stream_rows
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        {},
    )
    fmt = payload.get("format", {})
    fmt_dict = fmt if isinstance(fmt, dict) else {}
    return MediaProbe(
        duration_seconds=_float_or_none(
            audio_stream.get("duration") if isinstance(audio_stream, dict) else None
        )
        or _float_or_none(fmt_dict.get("duration")),
        container=_str_or_none(fmt_dict.get("format_name")),
        audio_codec=_str_or_none(audio_stream.get("codec_name"))
        if isinstance(audio_stream, dict)
        else None,
        video_codec=_str_or_none(video_stream.get("codec_name"))
        if isinstance(video_stream, dict)
        else None,
        size_bytes=(
            path.stat().st_size if path.exists() else _int_or_none(fmt_dict.get("size")) or 0
        ),
    )


def infer_speaker_count(
    path: Path,
    *,
    slug: str | None = None,
    overrides: dict[str, int] | None = None,
) -> int:
    """Infer speaker count from filename, defaulting to 2 when not explicit."""
    resolved_slug = slug or slug_for_media(path)
    if overrides:
        override = (
            overrides.get(resolved_slug) or overrides.get(path.name) or overrides.get(path.stem)
        )
        if override is not None:
            return override
    match = re.search(r"(?<!\d)([1-9]\d*)[-_\s]?way(?![a-z])", path.name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return 2


def paired_youtube_srt(path: Path) -> Path | None:
    prefix = timestamp_prefix(path.name)
    if not prefix:
        return None
    matches = sorted(path.parent.glob(f"{prefix}*youtube-transcript-rip.srt"))
    return matches[0] if matches else None


def timestamp_prefix(name: str) -> str | None:
    match = re.match(r"(\d{4}-\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2})", name)
    return match.group(1) if match else None


def slug_for_media(path: Path) -> str:
    parts = [safe_slug(part) for part in (*path.parent.parts[-1:], path.stem)]
    return "-".join(part for part in parts if part)


def safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned or "media"


def parse_speaker_overrides(values: list[str] | None) -> dict[str, int]:
    overrides: dict[str, int] = {}
    for value in values or []:
        chunks = [chunk.strip() for chunk in value.split(",") if chunk.strip()]
        for chunk in chunks:
            key, sep, raw_count = chunk.partition("=")
            if not sep:
                raise ValueError("speaker overrides must use slug=count")
            try:
                count = int(raw_count)
            except ValueError as exc:
                raise ValueError(f"speaker override count must be an integer: {chunk}") from exc
            if count < 1:
                raise ValueError(f"speaker override count must be positive: {chunk}")
            overrides[key.strip()] = count
    return overrides


def write_srt_reference_text(
    source_srt: Path,
    output_path: Path,
    *,
    window_start_seconds: float | None = None,
    window_end_seconds: float | None = None,
) -> SRTReference:
    lines: list[str] = []
    captions_kept = 0
    malformed = 0
    for block in srt_blocks(source_srt.read_text(encoding="utf-8", errors="replace")):
        if block is None:
            malformed += 1
            continue
        start, end, text_lines = block
        if window_start_seconds is not None and end <= window_start_seconds:
            continue
        if window_end_seconds is not None and start >= window_end_seconds:
            continue
        captions_kept += 1
        for raw in text_lines:
            text = re.sub(r"<[^>]+>", "", raw)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                lines.append(text)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return SRTReference(
        source_srt=source_srt,
        reference_text=output_path,
        kind="rough_reference",
        captions_kept=captions_kept,
        malformed_caption_blocks_skipped=malformed,
        lines=len(lines),
        words=sum(len(line.split()) for line in lines),
        window_start_seconds=window_start_seconds,
        window_end_seconds=window_end_seconds,
    )


def srt_blocks(text: str) -> list[tuple[float, float, list[str]] | None]:
    blocks: list[tuple[float, float, list[str]] | None] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        rows = [line.strip() for line in block.splitlines() if line.strip()]
        time_line = next((line for line in rows if "-->" in line), None)
        if time_line is None:
            continue
        try:
            left, right = [part.strip().split()[0] for part in time_line.split("-->", 1)]
        except (IndexError, ValueError):
            blocks.append(None)
            continue
        start = srt_timestamp_seconds(left)
        end = srt_timestamp_seconds(right)
        if start is None or end is None:
            blocks.append(None)
            continue
        text_lines = [line for line in rows if line != time_line and not line.isdigit()]
        blocks.append((start, end, text_lines))
    return blocks


def srt_timestamp_seconds(value: str) -> float | None:
    try:
        hms, millis = value.split(",", maxsplit=1)
        hours, minutes, seconds = hms.split(":", maxsplit=2)
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
    except (TypeError, ValueError):
        return None


def build_excerpt_command(
    input_path: Path,
    output_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
    stream_copy: bool = True,
) -> list[str]:
    codecs = ["-c", "copy"] if stream_copy else ["-c:v", "libx264", "-c:a", "aac"]
    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-ss",
        str(start_seconds),
        "-t",
        str(duration_seconds),
        "-i",
        str(input_path),
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0",
        *codecs,
        "-avoid_negative_ts",
        "make_zero",
        "-reset_timestamps",
        "1",
        str(output_path),
    ]


def create_media_excerpt(
    input_path: Path,
    output_path: Path,
    *,
    start_seconds: float,
    duration_seconds: float,
) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_excerpt_command(
        input_path,
        output_path,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        stream_copy=True,
    )
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode == 0:
        return command
    fallback = build_excerpt_command(
        input_path,
        output_path,
        start_seconds=start_seconds,
        duration_seconds=duration_seconds,
        stream_copy=False,
    )
    subprocess.run(fallback, text=True, capture_output=True, check=True)
    return fallback


def write_corpus_inventory(path: Path, rows: list[dict[str, Any]]) -> None:
    write_json(path, rows)


def inventory_rows(
    media: list[CorpusMedia],
    *,
    out: Path,
    full: bool,
    excerpt_seconds: float | None,
    start_seconds: float,
    presets: list[str],
    reference_text_override: Path | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    excerpts_dir = out / "excerpts"
    references_dir = out / "references"
    for item in media:
        window_start = None if full else start_seconds
        window_end = None if full or excerpt_seconds is None else start_seconds + excerpt_seconds
        input_path = item.path
        excerpt_path: Path | None = None
        if not full and excerpt_seconds is not None:
            excerpt_path = excerpts_dir / (
                f"{item.slug}-excerpt-{format_seconds(start_seconds)}s-"
                f"{format_seconds(window_end or 0)}s{item.path.suffix}"
            )
            input_path = excerpt_path
        reference_path: Path | None = reference_text_override
        reference_kind: str | None = "rough_reference" if reference_path else None
        if reference_path is None and item.paired_srt is not None:
            reference_path = references_dir / f"{item.slug}.txt"
            if not full and excerpt_seconds is not None:
                reference_path = references_dir / (
                    f"{item.slug}-excerpt-{format_seconds(start_seconds)}s-"
                    f"{format_seconds(window_end or 0)}s.txt"
                )
            reference_kind = "rough_reference"
        rows.append(
            {
                "source": str(item.path),
                "input": str(input_path),
                "slug": item.slug,
                "display_name": item.display_name,
                "size_bytes": item.size_bytes,
                "duration_seconds": item.duration_seconds,
                "container": item.container,
                "audio_codec": item.audio_codec,
                "video_codec": item.video_codec,
                "inferred_speakers": item.inferred_speakers,
                "paired_srt": str(item.paired_srt) if item.paired_srt else None,
                "paired_srt_status": "paired" if item.paired_srt else "missing",
                "reference": str(reference_path) if reference_path else None,
                "reference_kind": reference_kind,
                "reference_summary": None,
                "excerpt": str(excerpt_path) if excerpt_path else None,
                "excerpt_start_seconds": window_start,
                "excerpt_duration_seconds": excerpt_seconds if not full else None,
                "benchmark_status": item.benchmark_status,
                "skipped_reason": item.skipped_reason,
                "presets": presets,
            }
        )
    return rows


def prepare_local_inputs(
    rows: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    for row in rows:
        excerpt = Path(str(row["excerpt"])) if row.get("excerpt") else None
        if excerpt is not None:
            command = create_media_excerpt(
                Path(str(row["source"])),
                excerpt,
                start_seconds=float(row["excerpt_start_seconds"] or 0.0),
                duration_seconds=float(row["excerpt_duration_seconds"] or 0.0),
            )
            row["ffmpeg_command"] = command
        else:
            row["ffmpeg_command"] = []
        reference_path = Path(str(row["reference"])) if row.get("reference") else None
        source_srt = Path(str(row["paired_srt"])) if row.get("paired_srt") else None
        if reference_path is not None and source_srt is not None:
            reference = write_srt_reference_text(
                source_srt,
                reference_path,
                window_start_seconds=cast(float | None, row["excerpt_start_seconds"]),
                window_end_seconds=(
                    float(row["excerpt_start_seconds"] or 0.0)
                    + float(row["excerpt_duration_seconds"])
                    if row.get("excerpt_duration_seconds") is not None
                    else None
                ),
            )
            row["reference_summary"] = srt_reference_summary(reference)
        elif reference_path is not None:
            row["reference_summary"] = {
                "reference_text": str(reference_path),
                "kind": row.get("reference_kind") or "rough_reference",
                "source": "provided_reference_text",
            }


def srt_reference_summary(reference: SRTReference) -> dict[str, Any]:
    return {
        "source_srt": str(reference.source_srt),
        "reference_text": str(reference.reference_text),
        "kind": reference.kind,
        "captions_kept": reference.captions_kept,
        "malformed_caption_blocks_skipped": reference.malformed_caption_blocks_skipped,
        "lines": reference.lines,
        "words": reference.words,
        "window_start_seconds": reference.window_start_seconds,
        "window_end_seconds": reference.window_end_seconds,
    }


def compute_type_support_metadata(compute_type: str, *, device: str) -> dict[str, Any]:
    supported = supported_compute_types(device)
    return {
        "device": device,
        "compute_type": compute_type,
        "supported_compute_types": sorted(supported) if supported is not None else None,
        "supported": compute_type in supported if supported is not None else None,
    }


def expand_job_matrix(
    rows: list[dict[str, Any]],
    *,
    out: Path,
    mode: BenchmarkMode,
    presets: list[str],
    compute_types: list[str],
    asr_backends: list[str],
    asr_mlx_model: str | None,
    asr_mlx_model_path: Path | None,
    diarization_devices: list[str],
    alignment_devices: list[str],
    infer_speakers: bool,
    max_depth: bool,
    verbose: bool,
    asr_cpu_threads: int | None = None,
    asr_num_workers: int | None = None,
    asr_batched: bool = False,
    asr_batch_size: int | None = None,
    asr_cpu_thread_values: list[int | None] | None = None,
    asr_num_worker_values: list[int | None] | None = None,
) -> list[dict[str, Any]]:
    if mode in {"inventory-only", "excerpt-only"}:
        return []
    selected_rows = rows[:1] if mode in {"run-single", "backend-matrix"} else rows
    jobs_dir = out / "jobs"
    reports_dir = out / "benchmark_reports"
    matrix_configs_dir = out / "matrix_configs"
    jobs: list[dict[str, Any]] = []
    for row in selected_rows:
        if mode == "compute-matrix":
            preset_values = ["max"]
        elif mode in {"run-single", "backend-matrix"}:
            preset_values = presets[:1]
        else:
            preset_values = presets
        compute_values: list[str | None] = (
            [value for value in compute_types] if mode == "compute-matrix" else [None]
        )
        diarization_values: list[str | None] = (
            [value for value in diarization_devices] if diarization_devices else [None]
        )
        alignment_values: list[str | None] = (
            [value for value in alignment_devices] if alignment_devices else [None]
        )
        cpu_thread_values = asr_cpu_thread_values or [asr_cpu_threads]
        worker_values = asr_num_worker_values or [asr_num_workers]
        for preset_name in preset_values:
            for asr_backend in asr_backends:
                for compute_type in compute_values:
                    for diarization_device in diarization_values:
                        for alignment_device in alignment_values:
                            for cpu_thread_value in cpu_thread_values:
                                for worker_value in worker_values:
                                    matrix_bits = [preset_name, f"asr-{safe_slug(asr_backend)}"]
                                    config_path: Path | None = None
                                    asr_device: str | None = None
                                    support_metadata: dict[str, Any] | None = None
                                    skipped_reason: str | None = None
                                    if compute_type:
                                        matrix_bits.append(compute_type)
                                        config_path = (
                                            matrix_configs_dir / f"asr_compute_{compute_type}.yaml"
                                        )
                                        if mode == "compute-matrix":
                                            if asr_backend != "faster-whisper":
                                                skipped_reason = (
                                                    "unsupported_backend_for_compute_matrix"
                                                )
                                            else:
                                                asr_device = "cpu"
                                                support_metadata = compute_type_support_metadata(
                                                    compute_type,
                                                    device=asr_device,
                                                )
                                                if support_metadata.get("supported") is False:
                                                    skipped_reason = "unsupported_ct2_compute_type"
                                    if asr_backend != "faster-whisper" and config_path is None:
                                        config_path = matrix_configs_dir / (
                                            f"asr_backend_{safe_slug(asr_backend)}.yaml"
                                        )
                                    if diarization_device:
                                        matrix_bits.append(f"diarization-{diarization_device}")
                                        if config_path is None:
                                            config_path = matrix_configs_dir / (
                                                f"diarization_device_{diarization_device}.yaml"
                                            )
                                    if alignment_device:
                                        matrix_bits.append(f"alignment-{alignment_device}")
                                        if config_path is None:
                                            config_path = matrix_configs_dir / (
                                                f"alignment_device_{alignment_device}.yaml"
                                            )
                                    if len(cpu_thread_values) > 1 or cpu_thread_value is not None:
                                        matrix_bits.append(
                                            f"threads-{cpu_thread_value}"
                                            if cpu_thread_value is not None
                                            else "threads-unset"
                                        )
                                    if len(worker_values) > 1 or worker_value is not None:
                                        matrix_bits.append(
                                            f"workers-{worker_value}"
                                            if worker_value is not None
                                            else "workers-unset"
                                        )
                                    job_slug = f"{row['slug']}-{'-'.join(matrix_bits)}"
                                    job_dir = jobs_dir / job_slug
                                    report_dir = reports_dir / job_slug
                                    speaker_count = (
                                        int(row["inferred_speakers"]) if infer_speakers else None
                                    )
                                    transcribe_command = build_transcribe_command(
                                        Path(str(row["input"])),
                                        job_dir,
                                        preset=preset_name,
                                        speaker_count=speaker_count,
                                        config_file=config_path,
                                        max_depth=max_depth,
                                        verbose=verbose,
                                        asr_backend=asr_backend,
                                        asr_mlx_model=asr_mlx_model,
                                        asr_mlx_model_path=asr_mlx_model_path,
                                        diarization_device=diarization_device,
                                        alignment_device=alignment_device,
                                        asr_cpu_threads=None,
                                        asr_num_workers=None,
                                        asr_batched=asr_batched,
                                        asr_batch_size=asr_batch_size,
                                    )
                                    benchmark_command = build_benchmark_command(
                                        job_dir,
                                        report_dir,
                                        Path(str(row["reference"]))
                                        if row.get("reference")
                                        else None,
                                    )
                                    validate_command = (
                                        build_validate_speakers_command(job_dir, speaker_count)
                                        if speaker_count is not None
                                        else None
                                    )
                                    env_overrides: dict[str, str] = {}
                                    if cpu_thread_value is not None:
                                        env_overrides["LTM_ASR_CPU_THREADS"] = str(
                                            cpu_thread_value
                                        )
                                    if worker_value is not None:
                                        env_overrides["LTM_ASR_NUM_WORKERS"] = str(worker_value)
                                    jobs.append(
                                        {
                                            "job_slug": job_slug,
                                            "source": row["source"],
                                            "input": row["input"],
                                            "job_dir": str(job_dir),
                                            "report_dir": str(report_dir),
                                            "preset": preset_name,
                                            "asr_backend": asr_backend,
                                            "asr_compute_type": compute_type,
                                            "asr_device": asr_device,
                                            "asr_mlx_model": asr_mlx_model,
                                            "asr_mlx_model_path": str(asr_mlx_model_path)
                                            if asr_mlx_model_path
                                            else None,
                                            "compute_type_support": support_metadata,
                                            "diarization_device": diarization_device,
                                            "alignment_device": alignment_device,
                                            "asr_cpu_threads": cpu_thread_value,
                                            "asr_num_workers": worker_value,
                                            "asr_batched": asr_batched,
                                            "asr_batch_size": asr_batch_size,
                                            "env_overrides": env_overrides,
                                            "speaker_count": speaker_count,
                                            "reference": row.get("reference"),
                                            "reference_kind": row.get("reference_kind"),
                                            "config_file": str(config_path)
                                            if config_path
                                            else None,
                                            "float32_memory_warning": (
                                                "float32 compute can be memory-heavy"
                                                if compute_type == "float32"
                                                else None
                                            ),
                                            "benchmark_status": (
                                                "skipped" if skipped_reason else "planned"
                                            ),
                                            "skipped_reason": skipped_reason,
                                            "transcribe_command": transcribe_command,
                                            "benchmark_command": benchmark_command,
                                            "validate_command": validate_command,
                                        }
                                    )
    return jobs


def build_transcribe_command(
    input_path: Path,
    job_dir: Path,
    *,
    preset: str,
    speaker_count: int | None,
    config_file: Path | None,
    max_depth: bool,
    verbose: bool,
    asr_backend: str = "faster-whisper",
    asr_mlx_model: str | None = None,
    asr_mlx_model_path: Path | None = None,
    diarization_device: str | None = None,
    alignment_device: str | None = None,
    asr_cpu_threads: int | None,
    asr_num_workers: int | None,
    asr_batched: bool = False,
    asr_batch_size: int | None = None,
) -> list[str]:
    command = [
        "uv",
        "run",
        "ltm",
        "transcribe",
        str(input_path),
        "--preset",
        preset,
        "--out",
        str(job_dir),
        "--diarization-backend",
        "pyannote",
        "--forced-alignment",
        "auto",
        "--word-timestamps",
        "--cleanup",
        "off",
        "--debug-report",
        "--asr-backend",
        asr_backend,
    ]
    if asr_mlx_model is not None:
        command.extend(["--asr-mlx-model", asr_mlx_model])
    if asr_mlx_model_path is not None:
        command.extend(["--asr-mlx-model-path", str(asr_mlx_model_path)])
    if diarization_device is not None:
        command.extend(["--diarization-device", diarization_device])
    if alignment_device is not None:
        command.extend(["--alignment-device", alignment_device])
    if config_file is not None:
        command.extend(["--config", str(config_file)])
    if speaker_count is not None:
        command.extend(
            ["--num-speakers", str(speaker_count), "--known-speakers", str(speaker_count)]
        )
    if max_depth:
        command.extend(
            [
                "--agentic-diarization",
                "--targeted-redecode",
                "all-suspect",
                "--redecode-max-spans",
                "10",
                "--write-redecode-report",
            ]
        )
    else:
        command.extend(["--targeted-redecode", "off"])
    if asr_cpu_threads is not None:
        command.extend(["--asr-cpu-threads", str(asr_cpu_threads)])
    if asr_num_workers is not None:
        command.extend(["--asr-num-workers", str(asr_num_workers)])
    if asr_batched:
        command.append("--asr-batched")
    if asr_batch_size is not None:
        command.extend(["--asr-batch-size", str(asr_batch_size)])
    if verbose:
        command.append("--verbose")
    return command


def build_benchmark_command(
    job_dir: Path,
    report_dir: Path,
    reference_text: Path | None,
) -> list[str]:
    command = ["uv", "run", "ltm", "benchmark", str(job_dir), "--out", str(report_dir)]
    if reference_text is not None:
        command.extend(["--reference-text", str(reference_text)])
    return command


def build_validate_speakers_command(job_dir: Path, expected_speakers: int) -> list[str]:
    return [
        "uv",
        "run",
        "ltm",
        "validate-speakers",
        str(job_dir),
        "--expected-speakers",
        str(expected_speakers),
    ]


def write_matrix_config(
    path: Path,
    *,
    asr_backend: str | None,
    asr_mlx_model: str | None,
    asr_mlx_model_path: Path | None,
    compute_type: str | None,
    diarization_device: str | None,
    alignment_device: str | None,
    asr_device: str | None = None,
) -> None:
    payload: dict[str, Any] = {}
    if asr_backend is not None:
        payload.setdefault("asr", {})["backend"] = asr_backend
    if asr_mlx_model is not None:
        payload.setdefault("asr", {})["mlx_model"] = asr_mlx_model
    if asr_mlx_model_path is not None:
        payload.setdefault("asr", {})["mlx_model_path"] = str(asr_mlx_model_path)
    if compute_type is not None:
        payload.setdefault("asr", {})["compute_type"] = compute_type
    if asr_device is not None:
        payload.setdefault("asr", {})["device"] = asr_device
    if diarization_device is not None:
        payload.setdefault("diarization", {})["device"] = diarization_device
    if alignment_device is not None:
        payload.setdefault("alignment", {})["device"] = alignment_device
    if not payload:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")


def execute_jobs(
    jobs: list[dict[str, Any]],
    *,
    out: Path,
    dry_run: bool,
    sample_interval_seconds: float = 2.0,
    emit: Callable[[str], None] | None = None,
) -> None:
    if dry_run:
        return
    logs_dir = out / "logs"
    for job in jobs:
        if job.get("benchmark_status") == "skipped":
            if emit:
                emit(f"skipping job={job['job_slug']} reason={job.get('skipped_reason')}")
            continue
        config_path = Path(str(job["config_file"])) if job.get("config_file") else None
        if config_path is not None:
            write_matrix_config(
                config_path,
                asr_backend=cast(str | None, job.get("asr_backend")),
                asr_mlx_model=cast(str | None, job.get("asr_mlx_model")),
                asr_mlx_model_path=(
                    Path(str(job["asr_mlx_model_path"]))
                    if job.get("asr_mlx_model_path")
                    else None
                ),
                compute_type=cast(str | None, job.get("asr_compute_type")),
                diarization_device=cast(str | None, job.get("diarization_device")),
                alignment_device=cast(str | None, job.get("alignment_device")),
                asr_device=cast(str | None, job.get("asr_device")),
            )
        if emit:
            emit(f"starting job={job['job_slug']} preset={job['preset']}")
        transcribe_result = run_logged_command(
            cast(list[str], job["transcribe_command"]),
            logs_dir=logs_dir,
            log_prefix=str(job["job_slug"]),
            job_dir=Path(str(job["job_dir"])),
            env_overrides={**offline_benchmark_env(), **cast(dict[str, str], job["env_overrides"])},
            sample_interval_seconds=sample_interval_seconds,
            use_macos_time=True,
        )
        job["transcribe_result"] = transcribe_result.to_dict()
        if transcribe_result.exit_code != 0:
            job["benchmark_status"] = "failed"
            continue
        benchmark_command = cast(list[str], job["benchmark_command"])
        reference_arg_index = (
            benchmark_command.index("--reference-text") + 1
            if "--reference-text" in benchmark_command
            else None
        )
        if (
            reference_arg_index is not None
            and not Path(benchmark_command[reference_arg_index]).exists()
        ):
            benchmark_command = [
                part
                for index, part in enumerate(benchmark_command)
                if index not in {reference_arg_index - 1, reference_arg_index}
            ]
            job["benchmark_command"] = benchmark_command
        benchmark_result = run_logged_command(
            benchmark_command,
            logs_dir=logs_dir,
            log_prefix=f"{job['job_slug']}.benchmark",
            env_overrides=offline_benchmark_env(),
            sample_interval_seconds=sample_interval_seconds,
            use_macos_time=False,
        )
        job["benchmark_result"] = benchmark_result.to_dict()
        if benchmark_result.exit_code != 0:
            job["benchmark_status"] = "failed"
            continue
        validate_command = cast(list[str] | None, job.get("validate_command"))
        if validate_command is not None:
            validate_result = run_logged_command(
                validate_command,
                logs_dir=logs_dir,
                log_prefix=f"{job['job_slug']}.validate-speakers",
                env_overrides=offline_benchmark_env(),
                sample_interval_seconds=sample_interval_seconds,
                use_macos_time=False,
            )
            job["speaker_validation_result"] = validate_result.to_dict()
            if validate_result.exit_code != 0:
                merge_job_metrics(job)
                job["benchmark_status"] = "failed"
                continue
        merge_job_metrics(job)
        job["benchmark_status"] = "complete"
        if emit:
            emit(f"finished job={job['job_slug']} report={job['report_dir']}")


def merge_job_metrics(job: dict[str, Any]) -> None:
    report_path = Path(str(job["report_dir"])) / "benchmark.json"
    if report_path.exists():
        report = read_json(report_path)
        files = report.get("files", []) if isinstance(report, dict) else []
        metric = files[0] if files and isinstance(files[0], dict) else {}
        timings = metric.get("timings", {}) if isinstance(metric, dict) else {}
        resource_usage = metric.get("resource_usage", {}) if isinstance(metric, dict) else {}
        model_device_compute = (
            metric.get("model_device_compute", {}) if isinstance(metric, dict) else {}
        )
        asr_device_compute = (
            model_device_compute.get("asr_device_compute")
            if isinstance(model_device_compute, dict)
            else {}
        )
        asr_device_compute = asr_device_compute if isinstance(asr_device_compute, dict) else {}
        job["metrics"] = {
            "asr_backend": model_device_compute.get("asr_backend")
            if isinstance(model_device_compute, dict)
            else None,
            "real_time_factor": metric.get("real_time_factor"),
            "wer": metric.get("wer"),
            "cer": metric.get("cer"),
            "der": metric.get("der"),
            "asr_stage_seconds": timings.get("asr") if isinstance(timings, dict) else None,
            "diarization_seconds": timings.get("diarization")
            if isinstance(timings, dict)
            else None,
            "alignment_seconds": timings.get("alignment") if isinstance(timings, dict) else None,
            "export_seconds": timings.get("export") if isinstance(timings, dict) else None,
            "total_seconds": timings.get("total") if isinstance(timings, dict) else None,
            "rss_peak_sampled_bytes": resource_usage.get("rss_peak_sampled_bytes")
            if isinstance(resource_usage, dict)
            else None,
            "rss_peak_process_lifetime_bytes": resource_usage.get("rss_peak_process_lifetime_bytes")
            if isinstance(resource_usage, dict)
            else None,
            "asr_device": model_device_compute.get("device")
            if isinstance(model_device_compute, dict)
            else None,
            "asr_compute_type": model_device_compute.get("compute_type")
            if isinstance(model_device_compute, dict)
            else None,
            "asr_cpu_threads": model_device_compute.get("asr_cpu_threads")
            if isinstance(model_device_compute, dict)
            else None,
            "asr_num_workers": model_device_compute.get("asr_num_workers")
            if isinstance(model_device_compute, dict)
            else None,
            "asr_batched": model_device_compute.get("asr_batched")
            if isinstance(model_device_compute, dict)
            else None,
            "asr_batch_size": model_device_compute.get("asr_batch_size")
            if isinstance(model_device_compute, dict)
            else None,
            "requested_cpu_threads": asr_device_compute.get("requested_cpu_threads"),
            "requested_num_workers": asr_device_compute.get("requested_num_workers"),
            "diarization_device": infer_diarization_device(model_device_compute),
            "diarization_requested_device": model_device_compute.get(
                "diarization_requested_device"
            )
            if isinstance(model_device_compute, dict)
            else None,
            "diarization_resolved_device": model_device_compute.get("diarization_resolved_device")
            if isinstance(model_device_compute, dict)
            else None,
            "diarization_device_fallback": model_device_compute.get(
                "diarization_device_fallback"
            )
            if isinstance(model_device_compute, dict)
            else None,
            "alignment_device": model_device_compute.get("alignment_device")
            if isinstance(model_device_compute, dict)
            else None,
        }


def infer_diarization_device(model_device_compute: dict[str, Any]) -> str | None:
    resolved = model_device_compute.get("diarization_resolved_device")
    if resolved:
        return str(resolved)
    requested = model_device_compute.get("diarization_requested_device")
    if requested:
        return str(requested)
    runtime = model_device_compute.get("runtime_environment")
    if not isinstance(runtime, dict):
        return None
    value = runtime.get("ltm_diarization_device")
    return str(value) if value else None


def run_logged_command(
    command: list[str],
    *,
    logs_dir: Path,
    log_prefix: str,
    job_dir: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    sample_interval_seconds: float = 2.0,
    use_macos_time: bool = True,
) -> CommandRunResult:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = logs_dir / f"{log_prefix}.stdout.txt"
    stderr_log = logs_dir / f"{log_prefix}.stderr_time.txt"
    samples_csv = logs_dir / f"{log_prefix}.resource_samples.csv"
    env = os.environ.copy()
    env.update(env_overrides or {})
    wrapped, macos_time_supported = maybe_wrap_macos_time(command, use_macos_time=use_macos_time)
    sampling_supported = resource_sampling_supported()
    samples: list[dict[str, Any]] = []
    started = time.perf_counter()
    with (
        stdout_log.open("w", encoding="utf-8") as stdout_handle,
        stderr_log.open("w", encoding="utf-8") as stderr_handle,
    ):
        process = subprocess.Popen(
            wrapped,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            env=env,
        )
        while process.poll() is None:
            if sampling_supported:
                sample = sample_process_tree(process.pid)
                if sample is not None:
                    sample["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                    sample["stage_hint"] = stage_hint(job_dir) if job_dir else None
                    samples.append(sample)
            time.sleep(max(0.1, sample_interval_seconds))
        exit_code = process.wait()
    wall_seconds = round(time.perf_counter() - started, 6)
    if samples:
        write_resource_samples(samples_csv, samples)
    macos_time = parse_macos_time_l(stderr_log.read_text(encoding="utf-8", errors="replace"))
    return CommandRunResult(
        command=command,
        exit_code=exit_code,
        status=command_status(exit_code),
        wall_seconds=wall_seconds,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        resource_samples_csv=samples_csv if samples else None,
        resource_sampling_supported=sampling_supported,
        macos_time_supported=macos_time_supported,
        macos_time=macos_time,
        warnings=[] if sampling_supported else ["resource sampling unsupported on this host"],
    )


def maybe_wrap_macos_time(command: list[str], *, use_macos_time: bool) -> tuple[list[str], bool]:
    supported = bool(use_macos_time and sys.platform == "darwin" and Path("/usr/bin/time").exists())
    return (["/usr/bin/time", "-l", *command], True) if supported else (command, False)


def parse_macos_time_l(text: str) -> dict[str, float | int]:
    values: dict[str, float | int] = {}
    real = re.search(r"^\s*([0-9.]+)\s+real\b", text, flags=re.MULTILINE)
    user = re.search(r"([0-9.]+)\s+user\b", text)
    system_match = re.search(r"([0-9.]+)\s+sys\b", text)
    rss = re.search(
        r"^\s*([0-9]+)\s+maximum resident set size\b",
        text,
        flags=re.MULTILINE,
    )
    if real:
        values["wall_seconds"] = float(real.group(1))
    if user:
        values["user_cpu_seconds"] = float(user.group(1))
    if system_match:
        values["system_cpu_seconds"] = float(system_match.group(1))
    if rss:
        values["max_rss_bytes"] = int(rss.group(1))
    return values


def resource_sampling_supported() -> bool:
    return which("ps") is not None


def sample_process_tree(pid: int) -> dict[str, Any] | None:
    pids = process_tree_pids(pid)
    if not pids:
        return None
    completed = subprocess.run(
        ["ps", "-o", "pid=,stat=,%cpu=,rss=", "-p", ",".join(str(item) for item in pids)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    total_cpu = 0.0
    total_rss_kb = 0
    statuses: list[str] = []
    seen_pids: list[int] = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            seen_pids.append(int(parts[0]))
            statuses.append(parts[1])
            total_cpu += float(parts[2])
            total_rss_kb += int(parts[3])
        except ValueError:
            continue
    if not seen_pids:
        return None
    return {
        "pids": " ".join(str(item) for item in seen_pids),
        "process_count": len(seen_pids),
        "status": ",".join(sorted(set(statuses))),
        "cpu_percent": round(total_cpu, 3),
        "rss_bytes": total_rss_kb * 1024,
    }


def process_tree_pids(pid: int) -> list[int]:
    pids = [pid]
    index = 0
    while index < len(pids):
        parent = pids[index]
        index += 1
        try:
            completed = subprocess.run(
                ["pgrep", "-P", str(parent)],
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError:
            continue
        if completed.returncode != 0:
            continue
        for line in completed.stdout.splitlines():
            try:
                child = int(line.strip())
            except ValueError:
                continue
            if child not in pids:
                pids.append(child)
    return pids


def write_resource_samples(path: Path, samples: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "elapsed_seconds",
        "pids",
        "process_count",
        "status",
        "cpu_percent",
        "rss_bytes",
        "stage_hint",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for sample in samples:
            writer.writerow({field: sample.get(field) for field in fieldnames})


def stage_hint(job_dir: Path | None) -> str | None:
    if job_dir is None:
        return None
    if (job_dir / "transcript.json").exists():
        return "complete"
    if (job_dir / "speaker_report.json").exists():
        return "export"
    if (job_dir / "diarization.json").exists():
        return "fusion"
    if (job_dir / "transcript.raw.json").exists():
        return "alignment_or_diarization"
    if (job_dir / "vad.json").exists():
        return "asr"
    if (job_dir / "canonical.wav").exists() or (job_dir / "audio_metadata.json").exists():
        return "prep"
    return "starting"


def command_status(exit_code: int) -> str:
    if exit_code == 0:
        return "complete"
    if exit_code < 0 or exit_code in {130, 143}:
        return "terminated"
    return "failed"


def offline_benchmark_env() -> dict[str, str]:
    return {
        "LTM_OFFLINE": "1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYANNOTE_METRICS_ENABLED": "false",
    }


def run_corpus_harness(
    corpus_dir: Path,
    *,
    out: Path,
    mode: BenchmarkMode,
    presets: list[str],
    compute_types: list[str],
    asr_backends: list[str],
    asr_mlx_model: str | None,
    asr_mlx_model_path: Path | None,
    diarization_devices: list[str],
    alignment_devices: list[str],
    recursive: bool,
    excerpt_seconds: float | None,
    start_seconds: float,
    full: bool,
    max_files: int | None,
    max_depth: bool,
    infer_speakers: bool,
    dry_run: bool,
    verbose: bool,
    speaker_overrides: dict[str, int] | None,
    asr_cpu_threads: int | None,
    asr_num_workers: int | None,
    asr_batched: bool = False,
    asr_batch_size: int | None = None,
    asr_cpu_thread_values: list[int | None] | None = None,
    asr_num_worker_values: list[int | None] | None = None,
    reference_text: Path | None = None,
    sample_interval_seconds: float,
    emit: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if mode == "full-corpus" and not full:
        raise ValueError("full-corpus mode requires --full")
    out = out.expanduser()
    media = discover_media(corpus_dir, recursive=recursive, speaker_overrides=speaker_overrides)
    if max_files is not None:
        media = media[:max_files]
    if not media:
        raise ValueError("No local media files found.")
    if reference_text is not None and len(media) != 1:
        raise ValueError(
            "--reference-text is a single private rough reference and may only be used "
            "when exactly one media file is selected; use --max-files 1 or a single-file corpus."
        )
    rows = inventory_rows(
        media,
        out=out,
        full=full,
        excerpt_seconds=excerpt_seconds,
        start_seconds=start_seconds,
        presets=presets,
        reference_text_override=reference_text,
    )
    prepare_local_inputs(rows, dry_run=dry_run or mode == "inventory-only")
    jobs = expand_job_matrix(
        rows,
        out=out,
        mode=mode,
        presets=presets,
        compute_types=compute_types,
        asr_backends=asr_backends,
        asr_mlx_model=asr_mlx_model,
        asr_mlx_model_path=asr_mlx_model_path,
        diarization_devices=diarization_devices,
        alignment_devices=alignment_devices,
        infer_speakers=infer_speakers,
        max_depth=max_depth,
        verbose=verbose,
        asr_cpu_threads=asr_cpu_threads,
        asr_num_workers=asr_num_workers,
        asr_batched=asr_batched,
        asr_batch_size=asr_batch_size,
        asr_cpu_thread_values=asr_cpu_thread_values,
        asr_num_worker_values=asr_num_worker_values,
    )
    write_corpus_inventory(out / "corpus_inventory.json", rows)
    write_corpus_inventory(out / "planned_jobs.json", jobs)
    execute_jobs(
        jobs,
        out=out,
        dry_run=dry_run or mode in {"inventory-only", "excerpt-only"},
        sample_interval_seconds=sample_interval_seconds,
        emit=emit,
    )
    report = build_corpus_report(
        corpus_dir=corpus_dir,
        out=out,
        mode=mode,
        dry_run=dry_run,
        full=full,
        presets=presets,
        compute_types=compute_types,
        asr_backends=asr_backends,
        asr_mlx_model=asr_mlx_model,
        asr_mlx_model_path=asr_mlx_model_path,
        diarization_devices=diarization_devices,
        alignment_devices=alignment_devices,
        asr_batched=asr_batched,
        asr_batch_size=asr_batch_size,
        asr_cpu_thread_values=asr_cpu_thread_values,
        asr_num_worker_values=asr_num_worker_values,
        inventory=rows,
        jobs=jobs,
    )
    write_corpus_reports(out, report)
    return report


def build_corpus_report(
    *,
    corpus_dir: Path,
    out: Path,
    mode: BenchmarkMode,
    dry_run: bool,
    full: bool,
    presets: list[str],
    compute_types: list[str],
    asr_backends: list[str],
    asr_mlx_model: str | None,
    asr_mlx_model_path: Path | None,
    diarization_devices: list[str],
    alignment_devices: list[str],
    asr_batched: bool,
    asr_batch_size: int | None,
    asr_cpu_thread_values: list[int | None] | None,
    asr_num_worker_values: list[int | None] | None,
    inventory: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "privacy_note": (
            "Private local benchmark report. Generated excerpts, references, logs, "
            "transcripts, speaker artifacts, model caches, secrets, and raw media must "
            "remain local and must not be committed or uploaded. Rough SRT references "
            "are smoke/relative metrics only, not ground truth."
        ),
        "measured_vs_inferred": {
            "measured": [
                "ffprobe media metadata",
                "command exit codes",
                "wall time from harness subprocesses",
                "run_metadata real_time_factor and stage timings when jobs complete",
                "macOS /usr/bin/time -l peak RSS when available",
                "resource sampler CPU/RSS when available",
            ],
            "inferred": [
                "speaker count from N-way filename pattern or explicit override",
                "stage hint from artifact presence during live sampling",
            ],
        },
        "repo_state": collect_repo_state(),
        "environment": collect_environment_summary(),
        "settings": {
            "corpus_dir": str(corpus_dir),
            "out": str(out),
            "mode": mode,
            "dry_run": dry_run,
            "full": full,
            "presets": presets,
            "compute_types": compute_types,
            "asr_backends": asr_backends,
            "asr_mlx_model": asr_mlx_model,
            "asr_mlx_model_path": str(asr_mlx_model_path) if asr_mlx_model_path else None,
            "diarization_devices": diarization_devices,
            "alignment_devices": alignment_devices,
            "asr_batched": asr_batched,
            "asr_batch_size": asr_batch_size,
            "asr_cpu_thread_values": asr_cpu_thread_values,
            "asr_num_worker_values": asr_num_worker_values,
            "offline_env": offline_benchmark_env(),
        },
        "inventory": inventory,
        "jobs": jobs,
        "skipped": [
            {"source": row["source"], "reason": row["skipped_reason"]}
            for row in inventory
            if row.get("skipped_reason")
        ],
    }


def collect_repo_state() -> dict[str, Any]:
    return {
        "cwd": str(Path.cwd()),
        "branch": command_stdout(["git", "branch", "--show-current"]),
        "commit": command_stdout(["git", "rev-parse", "--verify", "HEAD"]),
        "status_short": command_stdout(["git", "status", "--short"]),
        "diff_stat": command_stdout(["git", "diff", "--stat"]),
    }


def collect_environment_summary() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "ffmpeg": command_stdout(["ffmpeg", "-version"]).splitlines()[0:1],
        "ffprobe": command_stdout(["ffprobe", "-version"]).splitlines()[0:1],
        "packages": {
            "faster-whisper": package_version("faster-whisper"),
            "ctranslate2": package_version("ctranslate2"),
            "mlx-whisper": package_version("mlx-whisper"),
            "mlx": package_version("mlx"),
            "torch": package_version("torch"),
            "pyannote.audio": package_version("pyannote.audio"),
            "whisperx": package_version("whisperx"),
        },
        "macos_time_l_supported": sys.platform == "darwin" and Path("/usr/bin/time").exists(),
        "resource_sampling_supported": resource_sampling_supported(),
    }


def package_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def command_stdout(command: list[str]) -> str:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError:
        return ""
    return completed.stdout.strip()


def write_corpus_reports(out: Path, report: dict[str, Any]) -> None:
    write_json(out / "corpus_benchmark_report.json", report)
    (out / "corpus_benchmark_report.md").write_text(
        render_corpus_markdown(report),
        encoding="utf-8",
    )
    write_corpus_summary_csv(out / "corpus_benchmark_summary.csv", report)


def render_corpus_markdown(report: dict[str, Any]) -> str:
    settings = report["settings"]
    lines = [
        "# Private Local Corpus Benchmark Report",
        "",
        str(report["privacy_note"]),
        "",
        "## Settings",
        "",
        f"- Mode: `{settings['mode']}`",
        f"- Dry run: `{settings['dry_run']}`",
        f"- Full media: `{settings['full']}`",
        f"- Presets: `{','.join(settings['presets'])}`",
        f"- ASR backends: `{','.join(settings.get('asr_backends', ['faster-whisper']))}`",
        f"- ASR compute matrix: `{','.join(settings['compute_types'])}`",
        f"- ASR batched: `{settings['asr_batched']}`",
        f"- ASR batch size: `{settings['asr_batch_size']}`",
        "",
        "## Media Inventory",
        "",
        "| slug | duration | size bytes | container | audio | video | speakers | SRT | status |",
        "| --- | ---: | ---: | --- | --- | --- | ---: | --- | --- |",
    ]
    for row in report["inventory"]:
        lines.append(
            "| {slug} | {duration} | {size} | {container} | {audio} | {video} | "
            "{speakers} | {srt} | {status} |".format(
                slug=row["slug"],
                duration=_fmt_any(row["duration_seconds"]),
                size=_fmt_any(row["size_bytes"]),
                container=row.get("container") or "",
                audio=row.get("audio_codec") or "",
                video=row.get("video_codec") or "",
                speakers=row["inferred_speakers"],
                srt=row["paired_srt_status"],
                status=row["benchmark_status"],
            )
        )
    lines.extend(
        [
            "",
            "## Jobs",
            "",
            "| job | preset | backend | compute | diar dev | align dev | cpu threads | workers | "
            "speaker validation | RTF | "
            "batched | batch size | ASR | diarization | RSS peak | status |",
            (
                "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | ---: | --- | ---: | "
                "---: | ---: | ---: | --- |"
            ),
        ]
    )
    for job in report["jobs"]:
        metrics = job.get("metrics", {}) if isinstance(job.get("metrics"), dict) else {}
        validation = job.get("speaker_validation_result")
        validation_status = (
            validation.get("status")
            if isinstance(validation, dict)
            else ("planned" if job.get("validate_command") else "")
        )
        rss = metrics.get("rss_peak_sampled_bytes") or _macos_time_rss(job)
        cpu_threads = metrics.get("asr_cpu_threads", job.get("asr_cpu_threads"))
        num_workers = metrics.get("asr_num_workers", job.get("asr_num_workers"))
        asr_batched = metrics.get("asr_batched", job.get("asr_batched"))
        asr_batch_size = metrics.get("asr_batch_size", job.get("asr_batch_size"))
        asr_backend = metrics.get("asr_backend") or job.get("asr_backend") or "faster-whisper"
        diarization_device = metrics.get("diarization_device", job.get("diarization_device"))
        alignment_device = metrics.get("alignment_device", job.get("alignment_device"))
        lines.append(
            "| {job} | {preset} | {backend} | {compute} | {diar_device} | {align_device} | "
            "{threads} | {workers} | {validation} | "
            "{rtf} | {batched} | {batch_size} | {asr} | {diar} | {rss} | {status} |".format(
                job=job["job_slug"],
                preset=job["preset"],
                backend=asr_backend,
                compute=job.get("asr_compute_type") or "",
                diar_device=_fmt_any(diarization_device),
                align_device=_fmt_any(alignment_device),
                threads=_fmt_any(cpu_threads),
                workers=_fmt_any(num_workers),
                validation=validation_status,
                rtf=_fmt_any(metrics.get("real_time_factor")),
                batched=_fmt_any(asr_batched),
                batch_size=_fmt_any(asr_batch_size),
                asr=_fmt_any(metrics.get("asr_stage_seconds")),
                diar=_fmt_any(metrics.get("diarization_seconds")),
                rss=_fmt_bytes(rss),
                status=job.get("benchmark_status", "planned"),
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Rough SRT references are labeled `rough_reference`; WER/CER are "
            "smoke/relative metrics only.",
            "- DER is only meaningful when an RTTM reference exists.",
            "- transcript/SRT text, raw media, excerpts, speaker embeddings, model "
            "weights, secrets, and `.env` content are intentionally omitted.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_corpus_summary_csv(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "job_slug",
        "source",
        "input",
        "preset",
        "asr_backend",
        "asr_compute_type",
        "asr_cpu_threads",
        "asr_num_workers",
        "asr_batched",
        "asr_batch_size",
        "diarization_device",
        "diarization_requested_device",
        "diarization_resolved_device",
        "diarization_device_fallback",
        "alignment_device",
        "speaker_count",
        "status",
        "wall_seconds",
        "real_time_factor",
        "asr_stage_seconds",
        "diarization_seconds",
        "alignment_seconds",
        "export_seconds",
        "rss_peak_bytes",
        "wer",
        "cer",
        "der",
        "speaker_validation_status",
        "report_dir",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for job in report["jobs"]:
            metrics = job.get("metrics", {}) if isinstance(job.get("metrics"), dict) else {}
            transcribe = (
                job.get("transcribe_result")
                if isinstance(job.get("transcribe_result"), dict)
                else {}
            )
            validation = (
                job.get("speaker_validation_result")
                if isinstance(job.get("speaker_validation_result"), dict)
                else {}
            )
            writer.writerow(
                {
                    "job_slug": job["job_slug"],
                    "source": job["source"],
                    "input": job["input"],
                    "preset": job["preset"],
                    "asr_backend": metrics.get("asr_backend", job.get("asr_backend")),
                    "asr_compute_type": job.get("asr_compute_type"),
                    "asr_cpu_threads": metrics.get("asr_cpu_threads", job.get("asr_cpu_threads")),
                    "asr_num_workers": metrics.get("asr_num_workers", job.get("asr_num_workers")),
                    "asr_batched": metrics.get("asr_batched", job.get("asr_batched")),
                    "asr_batch_size": metrics.get("asr_batch_size", job.get("asr_batch_size")),
                    "diarization_device": metrics.get(
                        "diarization_device",
                        job.get("diarization_device"),
                    ),
                    "diarization_requested_device": metrics.get(
                        "diarization_requested_device"
                    ),
                    "diarization_resolved_device": metrics.get("diarization_resolved_device"),
                    "diarization_device_fallback": metrics.get(
                        "diarization_device_fallback"
                    ),
                    "alignment_device": metrics.get(
                        "alignment_device",
                        job.get("alignment_device"),
                    ),
                    "speaker_count": job.get("speaker_count"),
                    "status": job.get("benchmark_status", "planned"),
                    "wall_seconds": transcribe.get("wall_seconds"),
                    "real_time_factor": metrics.get("real_time_factor"),
                    "asr_stage_seconds": metrics.get("asr_stage_seconds"),
                    "diarization_seconds": metrics.get("diarization_seconds"),
                    "alignment_seconds": metrics.get("alignment_seconds"),
                    "export_seconds": metrics.get("export_seconds"),
                    "rss_peak_bytes": metrics.get("rss_peak_sampled_bytes") or _macos_time_rss(job),
                    "wer": metrics.get("wer"),
                    "cer": metrics.get("cer"),
                    "der": metrics.get("der"),
                    "speaker_validation_status": validation.get("status"),
                    "report_dir": job["report_dir"],
                }
            )


def _macos_time_rss(job: dict[str, Any]) -> int | None:
    transcribe = job.get("transcribe_result")
    if not isinstance(transcribe, dict):
        return None
    macos_time = transcribe.get("macos_time")
    if not isinstance(macos_time, dict):
        return None
    value = macos_time.get("max_rss_bytes")
    return int(value) if isinstance(value, int | float) else None


def _fmt_any(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    if value is None:
        return ""
    return str(value)


def _fmt_bytes(value: object) -> str:
    if not isinstance(value, int | float):
        return ""
    return f"{float(value) / (1024**3):.2f} GiB"


def format_seconds(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value).replace(".", "p")


def parse_csv_values(value: str, *, valid: set[str], label: str) -> list[str]:
    values = [part.strip() for part in value.split(",") if part.strip()]
    invalid = [part for part in values if part not in valid]
    if invalid:
        raise ValueError(f"{label} must use values from: {', '.join(sorted(valid))}")
    return values


def parse_optional_positive_int_csv(value: str | None, *, label: str) -> list[int | None] | None:
    if value is None or value.strip() == "":
        return None
    parsed: list[int | None] = []
    for part in [chunk.strip() for chunk in value.split(",") if chunk.strip()]:
        if part.lower() in {"unset", "default", "none"}:
            parsed.append(None)
            continue
        try:
            number = int(part)
        except ValueError as exc:
            raise ValueError(f"{label} must contain positive integers or unset.") from exc
        if number <= 0:
            raise ValueError(f"{label} must contain positive integers or unset.")
        parsed.append(number)
    return parsed or None


def _float_or_none(value: object) -> float | None:
    if not isinstance(value, str | bytes | bytearray | int | float):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if not isinstance(value, str | bytes | bytearray | int | float):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None
