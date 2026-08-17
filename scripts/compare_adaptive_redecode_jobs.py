# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Compare real completed jobs across the beam-5, global-beam-10, and adaptive arms.

This reads finished job directories produced by ``ltm transcribe`` and measures
what actually changed. It emits two artifacts:

* a privacy-safe summary — hashes, counts, metrics, statuses, reason codes, and
  timings, with no transcript text and no absolute paths;
* an optional sensitive human-review packet containing the materially changed
  spans, which is what a human must adjudicate before adaptive-safe could ever
  become a MAX default.

Without a verbatim reference transcript this measures *divergence* between arms,
not accuracy. Divergence is not WER. Any accuracy claim requires the human
review packet to be adjudicated, or a referenced corpus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from local_transcript_max.qa.adaptive.policy import normalized_tokens, protected_classes

COMPARISON_SCHEMA_VERSION = "adaptive_redecode_job_comparison.v1"
CRITICAL_QA_CODES = (
    "impossible_timestamp",
    "missing_word_timestamps",
    "non_monotonic_word_timestamps",
    "repeated_phrase",
    "speech_in_no_speech",
    "high_compression_ratio",
    "very_long_segment",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="beam-5 job directory")
    parser.add_argument("--adaptive", type=Path, required=True, help="adaptive-safe job directory")
    parser.add_argument("--global-beam", type=Path, default=None, help="global beam-10 job")
    parser.add_argument("--replay", type=Path, default=None, help="repeated adaptive job")
    parser.add_argument("--out", type=Path, required=True, help="privacy-safe summary path")
    parser.add_argument("--review-out", type=Path, default=None, help="sensitive review packet")
    args = parser.parse_args(argv)

    report = compare(
        baseline=args.baseline,
        adaptive=args.adaptive,
        global_beam=args.global_beam,
        replay=args.replay,
    )
    args.out.write_text(json.dumps(report["summary"], indent=2, sort_keys=True) + "\n", "utf-8")
    if args.review_out is not None:
        args.review_out.write_text(
            json.dumps(report["review"], indent=2, sort_keys=True) + "\n", "utf-8"
        )
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


def compare(
    *,
    baseline: Path,
    adaptive: Path,
    global_beam: Path | None,
    replay: Path | None,
) -> dict[str, Any]:
    base_transcript = _read(baseline / "transcript.json")
    adaptive_first_pass = _read(adaptive / "transcript.json")
    lane = adaptive / "adaptive"
    sealed = _read(lane / "redecode_plan.json")
    plan = sealed["plan"]
    selected = _read_optional(lane / "transcript.selected.json")

    arms: dict[str, dict[str, Any]] = {
        "beam_5": _arm_metrics(baseline, base_transcript),
        "adaptive_first_pass": _arm_metrics(adaptive, adaptive_first_pass),
    }
    if selected is not None:
        arms["adaptive_selected"] = _arm_metrics(
            adaptive, selected, qa_name="adaptive/qa_report.selected.json"
        )
    if global_beam is not None:
        arms["global_beam_10"] = _arm_metrics(global_beam, _read(global_beam / "transcript.json"))

    base_text = _text(base_transcript)
    divergence = {
        name: _divergence(base_text, _text(_transcript_for(name, arms, baseline, adaptive, selected,
                                                           global_beam)))
        for name in arms
        if name != "beam_5"
    }

    review_spans, protected_span_count = _review_packet(plan, lane, base_transcript)
    determinism = _determinism(sealed, replay)

    summary = {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "evidence_class": "real_audio_development_run",
        "held_out_quality_evidence": False,
        "reference_transcript_available": False,
        "accuracy_claim": (
            "none: no verbatim reference exists for this recording, so cross-arm "
            "differences are divergence, not measured WER or CER"
        ),
        "policy_version": plan["policy_version"],
        "plan_schema_version": plan["schema_version"],
        "beam_ladder": plan["beam_ladder"],
        "canonical_audio_sha256": plan["canonical_audio_sha256"],
        "first_pass_asr_sha256": plan["first_pass_asr_sha256"],
        "selected_asr_sha256": plan["selected_asr_sha256"],
        "audio_duration_seconds": _duration(adaptive),
        "arms": arms,
        "divergence_from_beam_5": divergence,
        "adaptive_plan_summary": plan["summary"],
        "adaptive_limits": plan["limits"],
        "stage_dispositions": plan["stage_dispositions"],
        "first_pass_preserved_byte_identical": _first_pass_preserved(baseline, adaptive),
        "materially_changed_span_count": len(review_spans),
        "protected_material_span_count": protected_span_count,
        "determinism": determinism,
        "artifact_sizes_bytes": _artifact_sizes(lane),
        "critical_qa_regressions": _qa_regressions(arms),
    }
    review = {
        "schema_version": "adaptive_redecode_human_review.v1",
        "sensitive": True,
        "handling": "local review only; contains transcript text",
        "plan_sha256": sealed["plan_sha256"],
        "instructions": (
            "For each span, listen to the audio between window_start and window_end "
            "and record whether the selected text is more faithful than the first pass."
        ),
        "spans": review_spans,
    }
    return {"summary": summary, "review": review}


def _transcript_for(
    name: str,
    arms: dict[str, Any],
    baseline: Path,
    adaptive: Path,
    selected: dict[str, Any] | None,
    global_beam: Path | None,
) -> dict[str, Any]:
    if name == "adaptive_first_pass":
        return _read(adaptive / "transcript.json")
    if name == "adaptive_selected" and selected is not None:
        return selected
    if name == "global_beam_10" and global_beam is not None:
        return _read(global_beam / "transcript.json")
    return _read(baseline / "transcript.json")


def _arm_metrics(
    job: Path,
    transcript: dict[str, Any],
    *,
    qa_name: str = "qa_report.json",
) -> dict[str, Any]:
    metadata = _read_optional(job / "run_metadata.json") or {}
    qa = _read_optional(job / qa_name) or {"flags": []}
    segments = transcript.get("segments", [])
    words = transcript.get("words", [])
    tokens = normalized_tokens(_text(transcript))
    forced = sum(1 for word in words if word.get("alignment_source") == "forced")
    timings = {item["name"]: item["duration_seconds"] for item in metadata.get("timings", [])}
    usage = metadata.get("resource_usage") or {}
    return {
        "segment_count": len(segments),
        "word_count": len(words),
        "token_count": len(tokens),
        "forced_alignment_share": round(forced / len(words), 6) if words else None,
        "adjacent_repeat_ratio": _adjacent_repeat_ratio(tokens),
        "max_compression_ratio": _max_of(segments, "compression_ratio"),
        "segments_with_speech_in_no_speech": sum(
            1
            for segment in segments
            if (segment.get("no_speech_prob") or 0.0) > 0.8 and segment.get("text", "").strip()
        ),
        "qa_flag_counts": _counts(flag["code"] for flag in qa.get("flags", [])),
        "asr_seconds": timings.get("asr"),
        "alignment_seconds": timings.get("alignment"),
        "pipeline_core_seconds": timings.get("pipeline_core"),
        "real_time_factor": metadata.get("real_time_factor"),
        "rss_peak_sampled_bytes": usage.get("rss_peak_sampled_bytes"),
    }


def _review_packet(
    plan: dict[str, Any],
    lane: Path,
    base_transcript: dict[str, Any],
) -> tuple[list[dict[str, Any]], int]:
    candidates = _read_optional(lane / "transcript.adaptive_candidates.json") or {"spans": []}
    by_index = {span["index"]: span for span in candidates["spans"]}
    spans: list[dict[str, Any]] = []
    protected = 0
    for span in plan["spans"]:
        if not span.get("applied"):
            continue
        sensitive = by_index.get(span["index"], {})
        texts = {item["role"]: item["text"] for item in sensitive.get("candidates", [])}
        source_text = sensitive.get("source_text", "")
        selected_text = texts.get("wide", "")
        classes = list(protected_classes(source_text, selected_text))
        if classes:
            protected += 1
        empty: dict[str, Any] = {}
        wide: dict[str, Any] = next(
            (item for item in span["candidates"] if item["role"] == "wide"),
            empty,
        )
        control: dict[str, Any] = next(
            (item for item in span["candidates"] if item["role"] == "control"),
            empty,
        )
        spans.append(
            {
                "index": span["index"],
                "window_start": span["padded_start"],
                "window_end": span["padded_end"],
                "applied_start": span["applied_start"],
                "applied_end": span["applied_end"],
                "reason_codes": span["reason_codes"],
                "decision_reason": span["decision"]["decision_reason"],
                "protected_classes": classes,
                "first_pass_text": source_text,
                "window_beam_5_text": texts.get("control", ""),
                "selected_beam_10_text": selected_text,
                "evidence": {
                    "avg_logprob": [control.get("avg_logprob"), wide.get("avg_logprob")],
                    "alignment_coverage": [
                        control.get("alignment_coverage"),
                        wide.get("alignment_coverage"),
                    ],
                    "compression_ratio": [
                        control.get("compression_ratio"),
                        wide.get("compression_ratio"),
                    ],
                    "repetition_ratio": [
                        control.get("repetition_ratio"),
                        wide.get("repetition_ratio"),
                    ],
                    "no_speech_prob": [control.get("no_speech_prob"), wide.get("no_speech_prob")],
                },
                "independent_support_count": span["decision"]["independent_support_count"],
                "human_verdict": "PENDING",
            }
        )
    assert base_transcript is not None
    return spans, protected


def _determinism(sealed: dict[str, Any], replay: Path | None) -> dict[str, Any]:
    if replay is None:
        return {"repeated_run_compared": False}
    other = _read_optional(replay / "adaptive" / "redecode_plan.json")
    if other is None:
        return {"repeated_run_compared": False, "reason": "replay job has no adaptive lane"}
    first = sealed["plan"]
    second = other["plan"]
    volatile = {"source_revision", "producer_version"}
    stable_first = {k: v for k, v in first.items() if k not in volatile}
    stable_second = {k: v for k, v in second.items() if k not in volatile}
    return {
        "repeated_run_compared": True,
        "plan_sha256_identical": sealed["plan_sha256"] == other["plan_sha256"],
        "plan_body_identical_excluding_build_identity": stable_first == stable_second,
        "selected_asr_sha256_identical": first["selected_asr_sha256"]
        == second["selected_asr_sha256"],
        "canonical_audio_sha256_identical": first["canonical_audio_sha256"]
        == second["canonical_audio_sha256"],
        "decision_reason_counts_identical": first["summary"]["decision_reason_counts"]
        == second["summary"]["decision_reason_counts"],
    }


def _first_pass_preserved(baseline: Path, adaptive: Path) -> bool:
    left = _read_optional(baseline / "transcript.raw.json")
    right = _read_optional(adaptive / "transcript.raw.json")
    if left is None or right is None:
        return False
    return _text_of_raw(left) == _text_of_raw(right)


def _text_of_raw(payload: dict[str, Any]) -> str:
    return " ".join(
        segment.get("text", "").strip() for segment in payload.get("segments", [])
    ).strip()


def _artifact_sizes(lane: Path) -> dict[str, int]:
    if not lane.is_dir():
        return {}
    sizes = {path.name: path.stat().st_size for path in sorted(lane.iterdir()) if path.is_file()}
    sizes["_lane_total"] = sum(sizes.values())
    return sizes


def _qa_regressions(arms: dict[str, dict[str, Any]]) -> dict[str, dict[str, int]]:
    baseline = arms.get("adaptive_first_pass", {}).get("qa_flag_counts", {})
    result: dict[str, dict[str, int]] = {}
    for name, metrics in arms.items():
        if name == "adaptive_first_pass":
            continue
        counts = metrics.get("qa_flag_counts", {})
        deltas = {
            code: counts.get(code, 0) - baseline.get(code, 0)
            for code in CRITICAL_QA_CODES
            if counts.get(code, 0) != baseline.get(code, 0)
        }
        result[name] = deltas
    return result


def _divergence(reference: str, hypothesis: str) -> dict[str, float]:
    reference_tokens = normalized_tokens(reference)
    hypothesis_tokens = normalized_tokens(hypothesis)
    token_distance = _edit_distance(reference_tokens, hypothesis_tokens)
    character_distance = _edit_distance(list(reference), list(hypothesis))
    protected = protected_classes(reference, hypothesis)
    return {
        "token_divergence_rate": (
            round(token_distance / len(reference_tokens), 6) if reference_tokens else 0.0
        ),
        "character_divergence_rate": (
            round(character_distance / len(reference), 6) if reference else 0.0
        ),
        "changed_token_count": token_distance,
        "protected_class_count": len(protected),
    }


def _text(transcript: dict[str, Any]) -> str:
    return " ".join(
        segment.get("text", "").strip() for segment in transcript.get("segments", [])
    ).strip()


def _adjacent_repeat_ratio(tokens: list[str]) -> float:
    if len(tokens) < 2:
        return 0.0
    repeats = sum(1 for index in range(1, len(tokens)) if tokens[index] == tokens[index - 1])
    return round(repeats / (len(tokens) - 1), 6)


def _max_of(segments: list[dict[str, Any]], key: str) -> float | None:
    values = [segment[key] for segment in segments if segment.get(key) is not None]
    return round(max(values), 6) if values else None


def _counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _duration(job: Path) -> float | None:
    metadata = _read_optional(job / "run_metadata.json")
    if metadata is None:
        return None
    canonical = metadata.get("canonical_audio_metadata") or {}
    value = canonical.get("duration_seconds")
    return float(value) if value is not None else None


def _edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for row, left_item in enumerate(left, start=1):
        current = [row]
        for column, right_item in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"expected an object in {path.name}")
    return payload


def _read_optional(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return _read(path)


if __name__ == "__main__":
    raise SystemExit(main())
