# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Benchmark command implementation for transcript artifacts and job directories."""

from __future__ import annotations

import csv
import importlib.metadata
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_transcript_max.export.json_export import write_json_artifact
from local_transcript_max.schemas import (
    BenchmarkFileMetric,
    BenchmarkReport,
    CompletionTimingReport,
    Transcript,
)
from local_transcript_max.utils import (
    atomic_text_write,
    atomic_text_writer,
    read_json,
    sha256_file,
)

from .der import score_rttm
from .report import render_benchmark_markdown
from .wer import cer, wer


def run_benchmark_target(
    target: Path,
    *,
    reference_text: Path | None = None,
    reference_json: Path | None = None,
    reference_rttm: Path | None = None,
    out: Path,
) -> BenchmarkReport:
    out.mkdir(parents=True, exist_ok=True)
    job_dirs = discover_job_dirs(target)
    if not job_dirs:
        raise ValueError("Benchmark target must be a job directory or directory containing jobs.")
    files = [
        benchmark_job(job_dir, reference_text, reference_json, reference_rttm)
        for job_dir in job_dirs
    ]
    report = BenchmarkReport(
        files=files,
        aggregate=aggregate_metrics(files),
        metadata=benchmark_metadata(
            mode="target",
            target=target,
            out=out,
            references={
                "reference_text": reference_text,
                "reference_json": reference_json,
                "reference_rttm": reference_rttm,
            },
        ),
    )
    write_benchmark_outputs(out, report)
    return report


def run_benchmark(
    input_dir: Path,
    reference_transcripts: Path | None,
    reference_rttm: Path | None,
    out: Path,
) -> BenchmarkReport:
    return run_benchmark_batch(input_dir, references=reference_transcripts, out=out)


def run_benchmark_batch(
    out_dir: Path,
    *,
    references: Path | None,
    out: Path,
) -> BenchmarkReport:
    out.mkdir(parents=True, exist_ok=True)
    files: list[BenchmarkFileMetric] = []
    for job_dir in discover_job_dirs(out_dir):
        stem = job_dir.name
        reference_text = references / f"{stem}.txt" if references else None
        reference_json = references / f"{stem}.json" if references else None
        reference_rttm = references / f"{stem}.rttm" if references else None
        files.append(
            benchmark_job(
                job_dir,
                reference_text if reference_text and reference_text.exists() else None,
                reference_json if reference_json and reference_json.exists() else None,
                reference_rttm if reference_rttm and reference_rttm.exists() else None,
            )
        )
    report = BenchmarkReport(
        files=files,
        aggregate=aggregate_metrics(files),
        metadata=benchmark_metadata(
            mode="batch",
            target=out_dir,
            out=out,
            references={"references": references},
        ),
    )
    write_benchmark_outputs(out, report)
    return report


def discover_job_dirs(target: Path) -> list[Path]:
    if (target / "transcript.json").exists() or (target / "transcript.txt").exists():
        return [target]
    if target.is_dir():
        return sorted(
            path
            for path in target.iterdir()
            if path.is_dir()
            and ((path / "transcript.json").exists() or (path / "transcript.txt").exists())
        )
    return []


def benchmark_job(
    job_dir: Path,
    reference_text: Path | None,
    reference_json: Path | None,
    reference_rttm: Path | None,
) -> BenchmarkFileMetric:
    hypothesis = hypothesis_text(job_dir)
    reference = reference_text_value(reference_text, reference_json)
    transcript = transcript_value(job_dir)
    qa = read_json(job_dir / "qa_report.json") if (job_dir / "qa_report.json").exists() else {}
    metadata = (
        read_json(job_dir / "run_metadata.json") if (job_dir / "run_metadata.json").exists() else {}
    )
    completion = completion_timing(job_dir, metadata)
    timing_map = timings(metadata)
    if completion is not None:
        timing_map["total"] = completion.observed_end_to_end_seconds
    audio_duration = audio_duration_seconds(metadata)
    hyp_rttm = job_dir / "transcript.rttm"
    der_result = score_rttm(reference_rttm, hyp_rttm)
    return BenchmarkFileMetric(
        input_file=str(job_dir),
        audio_duration_seconds=audio_duration,
        wall_time_seconds=timing_map.get("total"),
        wer=wer(reference, hypothesis) if reference is not None else None,
        cer=cer(reference, hypothesis) if reference is not None else None,
        der=der_result.der,
        der_metadata=dict(der_result.to_metadata()),
        speaker_count_accuracy=speaker_count_accuracy(transcript, reference_rttm),
        words_with_timestamps_pct=words_with_timestamps_pct(transcript),
        words_with_speaker_labels_pct=words_with_speaker_labels_pct(transcript),
        uncertain_speaker_pct=uncertain_speaker_pct(transcript),
        overlap_span_count=len(qa.get("summary", {}).get("overlap_spans", []))
        if isinstance(qa, dict)
        else None,
        real_time_factor=(
            completion.real_time_factor
            if completion is not None
            else metadata.get("real_time_factor")
        ),
        resource_usage=(
            dict(completion.resource_usage)
            if completion is not None
            else resource_usage(metadata)
        ),
        model_device_compute=model_device_compute(metadata),
        timings=timing_map,
        artifact_counts=artifact_counts(job_dir),
        audio_preprocessing=audio_preprocessing(metadata, job_dir),
        quality_checks=quality_checks(transcript, job_dir, audio_duration),
    )


def completion_timing(
    job_dir: Path,
    metadata: dict[str, Any],
) -> CompletionTimingReport | None:
    """Load current completion evidence; absent legacy artifacts remain supported."""

    path = job_dir / "completion_timing.json"
    declares_completion = "completion_timing_artifact" in metadata
    if not path.exists():
        if declares_completion:
            raise ValueError(
                "current run_metadata.json declares a missing completion_timing.json"
            )
        return None
    if metadata.get("completion_timing_artifact") != "completion_timing.json":
        raise ValueError(
            "completion_timing.json requires a matching current run metadata contract"
        )
    report = CompletionTimingReport.model_validate(read_json(path))
    metadata_path = job_dir / "run_metadata.json"
    if not metadata_path.is_file():
        raise ValueError(
            "completion_timing.json requires the bound run_metadata.json"
        )
    if report.run_metadata_sha256 != sha256_file(metadata_path):
        raise ValueError(
            "completion_timing.json does not match the bound run_metadata.json"
        )
    return report


def hypothesis_text(job_dir: Path) -> str:
    text_path = job_dir / "transcript.txt"
    if text_path.exists():
        return text_path.read_text(encoding="utf-8")
    transcript = transcript_value(job_dir)
    return " ".join(segment.text for segment in transcript.segments)


def reference_text_value(reference_text: Path | None, reference_json: Path | None) -> str | None:
    if reference_text and reference_text.exists():
        return reference_text.read_text(encoding="utf-8")
    if reference_json and reference_json.exists():
        payload = read_json(reference_json)
        if isinstance(payload, dict) and "text" in payload:
            return str(payload["text"])
        if isinstance(payload, dict) and "segments" in payload:
            return " ".join(str(segment.get("text", "")) for segment in payload["segments"])
    return None


def transcript_value(job_dir: Path) -> Transcript:
    path = job_dir / "transcript.json"
    if path.exists():
        return Transcript.model_validate(read_json(path))
    return Transcript(input_path=str(job_dir))


def speaker_count_accuracy(transcript: Transcript, reference_rttm: Path | None) -> float | None:
    if reference_rttm is None or not reference_rttm.exists():
        return None
    reference_speakers = rttm_speakers(reference_rttm)
    hypothesis_speakers = {turn.speaker for turn in transcript.diarization_turns}
    if not reference_speakers:
        return None
    return 1.0 if len(reference_speakers) == len(hypothesis_speakers) else 0.0


def rttm_speakers(path: Path) -> set[str]:
    speakers = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 8:
            speakers.add(parts[7])
    return speakers


def words_with_timestamps_pct(transcript: Transcript) -> float:
    if not transcript.words:
        return 0.0
    count = sum(1 for word in transcript.words if word.start is not None and word.end is not None)
    return round(100 * count / len(transcript.words), 3)


def words_with_speaker_labels_pct(transcript: Transcript) -> float:
    if not transcript.words:
        return 0.0
    count = sum(1 for word in transcript.words if word.speaker)
    return round(100 * count / len(transcript.words), 3)


def uncertain_speaker_pct(transcript: Transcript) -> float:
    if not transcript.words:
        return 0.0
    count = sum(1 for word in transcript.words if word.uncertain_speaker)
    return round(100 * count / len(transcript.words), 3)


def model_device_compute(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    runtime = metadata.get("runtime_environment")
    asr_device_compute = runtime.get("asr_device_compute", {}) if isinstance(runtime, dict) else {}
    return {
        "asr_backend": metadata.get("asr_backend"),
        "models": metadata.get("models", []),
        "device": metadata.get("device"),
        "compute_type": metadata.get("compute_type"),
        "asr_cpu_threads": metadata.get("asr_cpu_threads"),
        "asr_num_workers": metadata.get("asr_num_workers"),
        "asr_batched": metadata.get("asr_batched"),
        "asr_batch_size": metadata.get("asr_batch_size"),
        "diarization_backend": metadata.get("diarization_backend"),
        "diarization_requested_device": metadata.get("diarization_requested_device"),
        "diarization_resolved_device": metadata.get("diarization_resolved_device"),
        "diarization_device_fallback": metadata.get("diarization_device_fallback"),
        "diarization_warnings": metadata.get("diarization_warnings", []),
        "alignment_backend": metadata.get("alignment_backend"),
        "alignment_device": metadata.get("alignment_device"),
        "asr_device_compute": asr_device_compute,
        "runtime_environment": runtime if isinstance(runtime, dict) else {},
    }


def timings(metadata: dict[str, Any]) -> dict[str, float]:
    if not isinstance(metadata, dict):
        return {}
    rows = metadata.get("timings", [])
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("name")): float(row.get("duration_seconds", 0.0))
        for row in rows
        if isinstance(row, dict)
    }


def resource_usage(metadata: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    usage = metadata.get("resource_usage")
    return usage if isinstance(usage, dict) else {}


def audio_duration_seconds(metadata: dict[str, Any]) -> float | None:
    if not isinstance(metadata, dict):
        return None
    for key in ("canonical_audio_metadata", "input_metadata"):
        row = metadata.get(key)
        if isinstance(row, dict):
            value = row.get("duration_seconds")
            if isinstance(value, int | float):
                return float(value)
    return None


def audio_preprocessing(metadata: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    if isinstance(metadata, dict):
        row = metadata.get("audio_preprocessing")
        if isinstance(row, dict):
            return row
    audio_metadata_path = job_dir / "audio_metadata.json"
    if audio_metadata_path.exists():
        payload = read_json(audio_metadata_path)
        preprocessing = payload.get("preprocessing") if isinstance(payload, dict) else None
        if isinstance(preprocessing, dict):
            return dict(preprocessing)
    ffmpeg_command = metadata.get("ffmpeg_command") if isinstance(metadata, dict) else None
    if isinstance(ffmpeg_command, list) and ffmpeg_command:
        reused = ffmpeg_command[0] == "reuse-canonical-wav"
        return {
            "action": "reused" if reused else "normalized",
            "reused": reused,
            "reason": "legacy_ffmpeg_command",
            "ffmpeg_invoked": not reused,
        }
    return {"action": "unknown", "reason": "metadata_unavailable"}


def artifact_counts(job_dir: Path) -> dict[str, Any]:
    expected = [
        "audio_metadata.json",
        "canonical.wav",
        "transcript.raw.json",
        "transcript.aligned.json",
        "transcript.diarized.json",
        "transcript.json",
        "transcript.txt",
        "transcript.md",
        "transcript.srt",
        "transcript.vtt",
        "transcript.rttm",
        "diarization.rttm",
        "diarization.json",
        "qa_report.json",
        "run_metadata.json",
        "completion_timing.json",
    ]
    present = [name for name in expected if (job_dir / name).exists()]
    return {
        "total_files": sum(1 for path in job_dir.iterdir() if path.is_file())
        if job_dir.exists()
        else 0,
        "json_files": len(list(job_dir.glob("*.json"))) if job_dir.exists() else 0,
        "csv_files": len(list(job_dir.glob("*.csv"))) if job_dir.exists() else 0,
        "expected_present": present,
        "expected_missing": [name for name in expected if name not in present],
    }


def quality_checks(
    transcript: Transcript,
    job_dir: Path,
    audio_duration: float | None,
) -> dict[str, Any]:
    export_presence = {
        name: (job_dir / name).exists()
        for name in (
            "transcript.json",
            "transcript.txt",
            "transcript.srt",
            "transcript.vtt",
            "transcript.rttm",
        )
    }
    segments_monotonic = intervals_monotonic(
        [(segment.start, segment.end) for segment in transcript.segments]
    )
    words_monotonic = intervals_monotonic(
        [
            (word.start, word.end)
            for word in transcript.words
            if word.start is not None and word.end is not None
        ]
    )
    timestamp_bounds_ok = timestamps_within_audio(transcript, audio_duration)
    turn_speakers = {turn.speaker for turn in transcript.diarization_turns if turn.speaker}
    word_speakers = {word.speaker for word in transcript.words if word.speaker}
    speaker_labels_consistent = (
        word_speakers.issubset(turn_speakers) if turn_speakers and word_speakers else None
    )
    failures = [
        value is False
        for value in (
            all(export_presence.values()),
            segments_monotonic,
            words_monotonic,
            timestamp_bounds_ok,
            speaker_labels_consistent,
        )
    ]
    return {
        "exports_present": export_presence,
        "segments_monotonic": segments_monotonic,
        "words_monotonic": words_monotonic,
        "timestamps_within_audio": timestamp_bounds_ok,
        "speaker_labels_consistent": speaker_labels_consistent,
        "issue_count": sum(1 for failed in failures if failed),
    }


def intervals_monotonic(intervals: list[tuple[float | None, float | None]]) -> bool:
    previous_start = -1.0
    for start, end in intervals:
        if start is None or end is None:
            continue
        if end < start or start < previous_start:
            return False
        previous_start = start
    return True


def timestamps_within_audio(transcript: Transcript, audio_duration: float | None) -> bool | None:
    if audio_duration is None:
        return None
    tolerance_seconds = 1.0
    max_allowed = audio_duration + tolerance_seconds
    for segment in transcript.segments:
        if segment.end > max_allowed:
            return False
    for word in transcript.words:
        if word.end is not None and word.end > max_allowed:
            return False
    return True


def aggregate_metrics(files: list[BenchmarkFileMetric]) -> dict[str, float | int | None]:
    return {
        "file_count": len(files),
        "mean_audio_duration_seconds": average([file.audio_duration_seconds for file in files]),
        "mean_wall_time_seconds": average([file.wall_time_seconds for file in files]),
        "mean_wer": average([file.wer for file in files]),
        "mean_cer": average([file.cer for file in files]),
        "mean_der": average([file.der for file in files]),
        "mean_real_time_factor": average([file.real_time_factor for file in files]),
        "max_rss_peak_sampled_bytes": maximum(
            [
                int(file.resource_usage["rss_peak_sampled_bytes"])
                for file in files
                if isinstance(file.resource_usage.get("rss_peak_sampled_bytes"), int)
            ]
        ),
        "max_rss_peak_process_lifetime_bytes": maximum(
            [
                int(file.resource_usage["rss_peak_process_lifetime_bytes"])
                for file in files
                if isinstance(file.resource_usage.get("rss_peak_process_lifetime_bytes"), int)
            ]
        ),
    }


def average(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 6)


def maximum(values: list[int]) -> int | None:
    if not values:
        return None
    return max(values)


def write_benchmark_outputs(out: Path, report: BenchmarkReport) -> None:
    write_json_artifact(out / "benchmark.json", report)
    write_json_artifact(out / "benchmark_report.json", report)
    rendered = render_benchmark_markdown(report)
    atomic_text_write(out / "benchmark.md", rendered)
    atomic_text_write(out / "benchmark_report.md", rendered)
    write_metrics_csv(out / "benchmark.csv", report)
    write_metrics_csv(out / "per_file_metrics.csv", report)


def write_metrics_csv(path: Path, report: BenchmarkReport) -> None:
    fieldnames = [
        "input_file",
        "audio_duration_seconds",
        "wall_time_seconds",
        "wer",
        "cer",
        "der",
        "speaker_count_accuracy",
        "words_with_speaker_labels_pct",
        "uncertain_speaker_pct",
        "real_time_factor",
        "rss_peak_sampled_bytes",
        "rss_peak_process_lifetime_bytes",
        "asr_cpu_threads",
        "asr_num_workers",
        "asr_batched",
        "asr_batch_size",
        "diarization_requested_device",
        "diarization_resolved_device",
        "diarization_device_fallback",
        "alignment_device",
        "asr_backend",
        "canonical_audio_action",
        "quality_issue_count",
        "model_device_compute",
    ]
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for metric in report.files:
            row = {field: getattr(metric, field) for field in fieldnames if hasattr(metric, field)}
            row["rss_peak_sampled_bytes"] = metric.resource_usage.get("rss_peak_sampled_bytes")
            row["rss_peak_process_lifetime_bytes"] = metric.resource_usage.get(
                "rss_peak_process_lifetime_bytes"
            )
            row["asr_cpu_threads"] = thread_metric(metric, "asr_cpu_threads")
            row["asr_num_workers"] = thread_metric(metric, "asr_num_workers")
            row["asr_batched"] = thread_metric(metric, "asr_batched")
            row["asr_batch_size"] = thread_metric(metric, "asr_batch_size")
            row["diarization_requested_device"] = metric.model_device_compute.get(
                "diarization_requested_device"
            )
            row["diarization_resolved_device"] = metric.model_device_compute.get(
                "diarization_resolved_device"
            )
            row["diarization_device_fallback"] = metric.model_device_compute.get(
                "diarization_device_fallback"
            )
            row["alignment_device"] = metric.model_device_compute.get("alignment_device")
            row["asr_backend"] = metric.model_device_compute.get("asr_backend")
            row["canonical_audio_action"] = metric.audio_preprocessing.get("action")
            row["quality_issue_count"] = metric.quality_checks.get("issue_count")
            writer.writerow(row)


def benchmark_metadata(
    *,
    mode: str,
    target: Path,
    out: Path,
    references: dict[str, Path | None],
) -> dict[str, Any]:
    return {
        "privacy_note": (
            "Benchmark reports are local artifacts. Do not commit or upload raw "
            "media, transcripts, rough references, speaker embeddings, model caches, "
            "tokens, or .env files."
        ),
        "mode": mode,
        "target": str(target),
        "out": str(out),
        "references": {
            key: {"path": str(value), "present": value.exists()} if value is not None else None
            for key, value in references.items()
        },
        "generated_by": "ltm benchmark",
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_state": collect_repo_state(),
        "environment": collect_environment_summary(),
    }


def collect_repo_state() -> dict[str, str]:
    return {
        "cwd": str(Path.cwd()),
        "branch": command_stdout(["git", "branch", "--show-current"]),
        "commit": command_stdout(["git", "rev-parse", "--short", "HEAD"]),
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
        },
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


def thread_metric(metric: BenchmarkFileMetric, key: str) -> object:
    value = metric.model_device_compute.get(key)
    if value is not None:
        return value
    asr = metric.model_device_compute.get("asr_device_compute")
    if not isinstance(asr, dict):
        return None
    fallback_key = {
        "asr_cpu_threads": "effective_cpu_threads",
        "asr_num_workers": "effective_num_workers",
        "asr_batched": "batched",
        "asr_batch_size": "batch_size",
    }[key]
    return asr.get(fallback_key)
