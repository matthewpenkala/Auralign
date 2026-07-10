# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Compare two local benchmark reports without claiming significance."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_transcript_max.schemas import BenchmarkFileMetric, BenchmarkReport
from local_transcript_max.utils import (
    atomic_text_write,
    atomic_text_writer,
    read_json,
    write_json,
)

METRICS: dict[str, str] = {
    "real_time_factor": "lower_is_better",
    "wall_time_seconds": "lower_is_better",
    "total_seconds": "lower_is_better",
    "asr_seconds": "lower_is_better",
    "diarization_seconds": "lower_is_better",
    "alignment_seconds": "lower_is_better",
    "rss_peak_sampled_bytes": "lower_is_better",
    "rss_peak_process_lifetime_bytes": "lower_is_better",
    "wer": "lower_is_better",
    "cer": "lower_is_better",
    "der": "lower_is_better",
    "words_with_speaker_labels_pct": "higher_is_better",
    "uncertain_speaker_pct": "lower_is_better",
}


def run_benchmark_compare(baseline: Path, candidate: Path, *, out: Path) -> dict[str, Any]:
    baseline_path, baseline_report = load_benchmark_report(baseline)
    candidate_path, candidate_report = load_benchmark_report(candidate)
    matched, warnings = match_files(baseline_report.files, candidate_report.files)
    rows = [
        compare_metric_pair(baseline_metric, candidate_metric)
        for baseline_metric, candidate_metric in matched
    ]
    report: dict[str, Any] = {
        "schema_version": "benchmark_comparison.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "privacy_note": (
            "Comparison reports contain metrics and local paths only. Do not commit or upload "
            "private transcripts, raw media, rough references, model caches, tokens, or .env files."
        ),
        "interpretation_note": (
            "Deltas compare two reports only. They are not statistical significance claims and "
            "must not be described as a speedup without representative same-media evidence."
        ),
        "baseline_report": str(baseline_path),
        "candidate_report": str(candidate_path),
        "warnings": warnings,
        "matched_files": rows,
        "unmatched": unmatched_files(baseline_report.files, candidate_report.files, matched),
    }
    write_comparison_outputs(out, report)
    return report


def load_benchmark_report(path: Path) -> tuple[Path, BenchmarkReport]:
    resolved = resolve_report_path(path.expanduser())
    payload = read_json(resolved)
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark report must be a JSON object: {resolved}")
    return resolved, BenchmarkReport.model_validate(payload)


def resolve_report_path(path: Path) -> Path:
    if path.is_file():
        return path
    if path.is_dir():
        for name in ("benchmark.json", "benchmark_report.json"):
            candidate = path / name
            if candidate.exists():
                return candidate
    raise ValueError(f"could not find benchmark.json or benchmark_report.json at {path}")


def match_files(
    baseline_files: list[BenchmarkFileMetric],
    candidate_files: list[BenchmarkFileMetric],
) -> tuple[list[tuple[BenchmarkFileMetric, BenchmarkFileMetric]], list[str]]:
    warnings: list[str] = []
    matches: list[tuple[BenchmarkFileMetric, BenchmarkFileMetric]] = []
    used_baseline: set[int] = set()
    used_candidate: set[int] = set()
    candidate_by_input = {metric.input_file: index for index, metric in enumerate(candidate_files)}
    for baseline_index, baseline_metric in enumerate(baseline_files):
        candidate_index = candidate_by_input.get(baseline_metric.input_file)
        if candidate_index is not None:
            matches.append((baseline_metric, candidate_files[candidate_index]))
            used_baseline.add(baseline_index)
            used_candidate.add(candidate_index)
    baseline_by_name = unique_basename_map(baseline_files, used_baseline)
    candidate_by_name = unique_basename_map(candidate_files, used_candidate)
    for name, baseline_index in baseline_by_name.items():
        candidate_index = candidate_by_name.get(name)
        if candidate_index is None:
            continue
        matches.append((baseline_files[baseline_index], candidate_files[candidate_index]))
        used_baseline.add(baseline_index)
        used_candidate.add(candidate_index)
        warnings.append(f"matched by unique basename: {name}")
    if not matches and len(baseline_files) == 1 and len(candidate_files) == 1:
        matches.append((baseline_files[0], candidate_files[0]))
        warnings.append("matched single-file reports by fallback; input paths differ")
    return matches, warnings


def unique_basename_map(
    metrics: list[BenchmarkFileMetric],
    used_indexes: set[int],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    indexes: dict[str, int] = {}
    for index, metric in enumerate(metrics):
        if index in used_indexes:
            continue
        name = Path(metric.input_file).name
        counts[name] = counts.get(name, 0) + 1
        indexes[name] = index
    return {name: indexes[name] for name, count in counts.items() if count == 1}


def compare_metric_pair(
    baseline: BenchmarkFileMetric,
    candidate: BenchmarkFileMetric,
) -> dict[str, Any]:
    metrics = {
        name: metric_delta(metric_value(baseline, name), metric_value(candidate, name), direction)
        for name, direction in METRICS.items()
    }
    return {
        "baseline_input_file": baseline.input_file,
        "candidate_input_file": candidate.input_file,
        "baseline_audio_preprocessing": baseline.audio_preprocessing,
        "candidate_audio_preprocessing": candidate.audio_preprocessing,
        "baseline_model_device_compute": baseline.model_device_compute,
        "candidate_model_device_compute": candidate.model_device_compute,
        "metrics": metrics,
    }


def metric_value(metric: BenchmarkFileMetric, name: str) -> float | None:
    if name == "total_seconds":
        return metric.timings.get("total")
    if name == "asr_seconds":
        return metric.timings.get("asr")
    if name == "diarization_seconds":
        return metric.timings.get("diarization")
    if name == "alignment_seconds":
        return metric.timings.get("alignment")
    if name == "rss_peak_sampled_bytes":
        return numeric_dict_value(metric.resource_usage, "rss_peak_sampled_bytes")
    if name == "rss_peak_process_lifetime_bytes":
        return numeric_dict_value(metric.resource_usage, "rss_peak_process_lifetime_bytes")
    value = getattr(metric, name)
    return float(value) if isinstance(value, int | float) else None


def numeric_dict_value(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, int | float) else None


def metric_delta(
    baseline_value: float | None,
    candidate_value: float | None,
    direction: str,
) -> dict[str, Any]:
    if baseline_value is None or candidate_value is None:
        return {
            "baseline": baseline_value,
            "candidate": candidate_value,
            "delta": None,
            "delta_percent": None,
            "direction": direction,
            "status": "missing_metric",
        }
    delta = candidate_value - baseline_value
    delta_percent = (delta / baseline_value * 100.0) if baseline_value else None
    return {
        "baseline": baseline_value,
        "candidate": candidate_value,
        "delta": round(delta, 6),
        "delta_percent": round(delta_percent, 3) if delta_percent is not None else None,
        "direction": direction,
        "status": "measured",
    }


def unmatched_files(
    baseline_files: list[BenchmarkFileMetric],
    candidate_files: list[BenchmarkFileMetric],
    matches: list[tuple[BenchmarkFileMetric, BenchmarkFileMetric]],
) -> dict[str, list[str]]:
    matched_baseline = {baseline.input_file for baseline, _candidate in matches}
    matched_candidate = {candidate.input_file for _baseline, candidate in matches}
    return {
        "baseline": [
            metric.input_file
            for metric in baseline_files
            if metric.input_file not in matched_baseline
        ],
        "candidate": [
            metric.input_file
            for metric in candidate_files
            if metric.input_file not in matched_candidate
        ],
    }


def write_comparison_outputs(out: Path, report: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "benchmark_comparison.json", report)
    atomic_text_write(out / "benchmark_comparison.md", render_comparison_markdown(report))
    write_comparison_csv(out / "benchmark_comparison.csv", report)


def render_comparison_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Benchmark Comparison",
        "",
        str(report["interpretation_note"]),
        "",
        f"- Baseline: `{report['baseline_report']}`",
        f"- Candidate: `{report['candidate_report']}`",
        "",
    ]
    warnings = report.get("warnings", [])
    if isinstance(warnings, list) and warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    lines.extend(
        [
            "## Metrics",
            "",
            "| file | metric | baseline | candidate | delta | delta % | status | direction |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in report["matched_files"]:
        file_label = Path(str(row["candidate_input_file"])).name
        metrics = row["metrics"]
        for metric_name, metric in metrics.items():
            lines.append(
                "| {file} | {metric_name} | {baseline} | {candidate} | {delta} | "
                "{delta_percent} | {status} | {direction} |".format(
                    file=file_label,
                    metric_name=metric_name,
                    baseline=_fmt_any(metric.get("baseline")),
                    candidate=_fmt_any(metric.get("candidate")),
                    delta=_fmt_any(metric.get("delta")),
                    delta_percent=_fmt_any(metric.get("delta_percent")),
                    status=metric.get("status", ""),
                    direction=metric.get("direction", ""),
                )
            )
    return "\n".join(lines) + "\n"


def write_comparison_csv(path: Path, report: dict[str, Any]) -> None:
    fieldnames = [
        "baseline_input_file",
        "candidate_input_file",
        "metric",
        "baseline",
        "candidate",
        "delta",
        "delta_percent",
        "status",
        "direction",
    ]
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in report["matched_files"]:
            for metric_name, metric in row["metrics"].items():
                writer.writerow(
                    {
                        "baseline_input_file": row["baseline_input_file"],
                        "candidate_input_file": row["candidate_input_file"],
                        "metric": metric_name,
                        "baseline": metric.get("baseline"),
                        "candidate": metric.get("candidate"),
                        "delta": metric.get("delta"),
                        "delta_percent": metric.get("delta_percent"),
                        "status": metric.get("status"),
                        "direction": metric.get("direction"),
                    }
                )


def _fmt_any(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    if value is None:
        return ""
    return str(value)
