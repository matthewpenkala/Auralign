# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Benchmark report rendering."""

from __future__ import annotations

from local_transcript_max.schemas import BenchmarkReport


def render_benchmark_markdown(report: BenchmarkReport) -> str:
    lines = ["# Benchmark Report", ""]
    metadata = report.metadata
    if metadata:
        lines.extend(
            [
                "## Summary",
                "",
                f"- Schema: `{report.schema_version}`",
                f"- Mode: `{metadata.get('mode', '')}`",
                f"- Target: `{metadata.get('target', '')}`",
                f"- Generated: `{report.generated_at.isoformat()}`",
                "",
            ]
        )
    aggregate = report.aggregate
    if aggregate:
        lines.extend(
            [
                "## Aggregate",
                "",
                f"- Files: `{aggregate.get('file_count', 0)}`",
                f"- Mean RTF: `{_fmt(aggregate.get('mean_real_time_factor'))}`",
                f"- Mean wall seconds: `{_fmt(aggregate.get('mean_wall_time_seconds'))}`",
                f"- Max sampled RSS: `{_fmt_bytes(aggregate.get('max_rss_peak_sampled_bytes'))}`",
                "",
            ]
        )
    lines.extend(["## Files", ""])
    lines.append(
        "| file | audio | wall | WER | CER | DER | RTF | RSS peak | audio prep | "
        "backend | device | compute | diar dev | align dev | cpu threads | workers | "
        "batched | batch size |"
    )
    lines.append(
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | "
        "--- | --- | --- | --- | --- | ---: | ---: | --- | ---: |"
    )
    for metric in report.files:
        backend = metric.model_device_compute.get("asr_backend", "")
        device = metric.model_device_compute.get("device", "")
        compute = metric.model_device_compute.get("compute_type", "")
        diarization_device = metric.model_device_compute.get("diarization_resolved_device") or (
            metric.model_device_compute.get("diarization_requested_device") or ""
        )
        alignment_device = metric.model_device_compute.get("alignment_device", "")
        cpu_threads = _thread_metric(metric.model_device_compute, "asr_cpu_threads")
        num_workers = _thread_metric(metric.model_device_compute, "asr_num_workers")
        asr_batched = _thread_metric(metric.model_device_compute, "asr_batched")
        asr_batch_size = _thread_metric(metric.model_device_compute, "asr_batch_size")
        rss_peak = metric.resource_usage.get("rss_peak_sampled_bytes")
        audio_action = metric.audio_preprocessing.get("action", "")
        row = (
            f"| {metric.input_file} | {_fmt(metric.audio_duration_seconds)} | "
            f"{_fmt(metric.wall_time_seconds)} | {_fmt(metric.wer)} | {_fmt(metric.cer)} |"
        )
        lines.append(
            f"{row} {_fmt(metric.der)} | {_fmt(metric.real_time_factor)} | "
            f"{_fmt_bytes(rss_peak)} | {audio_action} | {backend} | {device} | {compute} | "
            f"{diarization_device} | {alignment_device} | "
            f"{_fmt_thread(cpu_threads)} | {_fmt_thread(num_workers)} | "
            f"{_fmt_thread(asr_batched)} | {_fmt_thread(asr_batch_size)} |"
        )
    if metadata.get("privacy_note"):
        lines.extend(["", "## Privacy", "", str(metadata["privacy_note"])])
    return "\n".join(lines) + "\n"


def _fmt(value: object) -> str:
    return f"{float(value):.4f}" if isinstance(value, int | float) else ""


def _fmt_bytes(value: object) -> str:
    if not isinstance(value, int | float):
        return ""
    gib = float(value) / (1024**3)
    return f"{gib:.2f} GiB"


def _thread_metric(model_device_compute: dict[str, object], key: str) -> object:
    value = model_device_compute.get(key)
    if value is not None:
        return value
    asr = model_device_compute.get("asr_device_compute")
    if not isinstance(asr, dict):
        return None
    fallback_key = {
        "asr_cpu_threads": "effective_cpu_threads",
        "asr_num_workers": "effective_num_workers",
        "asr_batched": "batched",
        "asr_batch_size": "batch_size",
    }[key]
    return asr.get(fallback_key)


def _fmt_thread(value: object) -> str:
    return "" if value is None else str(value)
