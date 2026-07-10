# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic diarization candidate generation, scoring, and reporting."""

from __future__ import annotations

import copy
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any

from local_transcript_max.config import PipelineConfig
from local_transcript_max.diarization.base import DiarizationBackend, diarize_backend
from local_transcript_max.export.json_export import write_json_artifact
from local_transcript_max.qa.heuristics import speaker_flapping_score
from local_transcript_max.schemas import AlignedWord, DiarizationResult, DiarizationTurn, VADRegion
from local_transcript_max.utils import atomic_text_write


def run_agentic_diarization(
    audio_path: Path,
    config: PipelineConfig,
    backend: DiarizationBackend,
    *,
    words: list[AlignedWord],
    vad_regions: list[VADRegion],
    out_dir: Path,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[DiarizationResult, dict[str, Any]]:
    """Run deterministic diarization candidates and select the best one."""
    candidate_configs = diarization_candidate_configs(config)
    candidate_dir = out_dir / "diarization_candidates"
    candidate_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    candidate_dir.chmod(0o700)
    candidates: list[dict[str, Any]] = []

    for index, candidate_config in enumerate(candidate_configs, start=1):
        candidate_id = f"candidate_{index:03d}"
        run_config = copy.deepcopy(config)
        run_config.diarization.num_speakers = optional_int(candidate_config.get("num_speakers"))
        run_config.diarization.min_speakers = optional_int(candidate_config.get("min_speakers"))
        run_config.diarization.max_speakers = optional_int(candidate_config.get("max_speakers"))
        result = diarize_backend(backend, audio_path, run_config, progress=progress)
        score = score_candidate(
            result,
            words=words,
            vad_regions=vad_regions,
            expected_speakers=config.agentic_diarization.known_speakers
            or config.diarization.num_speakers,
        )
        candidate_record = {
            "candidate_id": candidate_id,
            "config": candidate_config,
            "score": score,
            "result": result.model_dump(mode="json"),
        }
        candidates.append(candidate_record)
        write_json_artifact(candidate_dir / f"{candidate_id}.json", candidate_record)
        atomic_text_write(
            candidate_dir / f"{candidate_id}.rttm",
            render_turns_rttm(result.exclusive_turns or result.turns),
        )

    selected = select_candidate(candidates)
    return DiarizationResult.model_validate(selected["result"]), build_decision_report(
        candidates,
        selected,
    )


def diarization_candidate_configs(config: PipelineConfig) -> list[dict[str, int | None | str]]:
    exact = config.agentic_diarization.known_speakers or config.diarization.num_speakers
    if exact is not None:
        return [
            {
                "candidate_type": "known_speakers",
                "num_speakers": exact,
                "min_speakers": None,
                "max_speakers": None,
            }
        ]
    counts = parse_candidate_range(config.agentic_diarization.candidate_range)
    if config.agentic_diarization.candidates is not None:
        counts = counts[: config.agentic_diarization.candidates]
    return [
        {
            "candidate_type": "estimated_count",
            "num_speakers": count,
            "min_speakers": None,
            "max_speakers": None,
        }
        for count in counts
    ]


def parse_candidate_range(value: str) -> list[int]:
    try:
        left, right = value.split(":", maxsplit=1)
        start = max(1, int(left))
        end = max(start, int(right))
    except ValueError:
        start, end = 2, 8
    return list(range(start, end + 1))


def optional_int(value: int | str | None) -> int | None:
    return value if isinstance(value, int) else None


def score_candidate(
    result: DiarizationResult,
    *,
    words: list[AlignedWord],
    vad_regions: list[VADRegion],
    expected_speakers: int | None,
) -> dict[str, Any]:
    turns = sorted(result.exclusive_turns or result.turns, key=lambda turn: (turn.start, turn.end))
    speakers = sorted({turn.speaker for turn in turns})
    durations = [max(0.0, turn.end - turn.start) for turn in turns]
    median_turn_duration = statistics.median(durations) if durations else 0.0
    short_turn_ratio = (
        sum(1 for duration in durations if duration < 1.0) / len(durations)
        if durations
        else 1.0
    )
    flapping = speaker_flapping_score(turns)
    word_coverage = word_coverage_ratio(words, turns)
    vad_coverage = vad_coverage_ratio(vad_regions, turns)
    unlabeled_seconds = long_unlabeled_seconds(vad_regions, turns)
    contradictions = timestamp_contradictions(words, turns)
    overlap_ratio = sum(1 for turn in turns if turn.overlap) / len(turns) if turns else 0.0
    count_penalty = 0.0
    if expected_speakers is not None:
        count_penalty = abs(len(speakers) - expected_speakers) / max(1, expected_speakers)
    score = (
        1.0
        - count_penalty * 0.25
        + word_coverage * 0.25
        + vad_coverage * 0.15
        - short_turn_ratio * 0.15
        - flapping * 0.15
        - min(1.0, unlabeled_seconds / 30.0) * 0.15
        - min(1.0, contradictions / max(1, len(words))) * 0.10
        + min(0.1, overlap_ratio * 0.05)
    )
    warnings: list[str] = []
    if flapping > 0.35:
        warnings.append("speaker_flapping")
    if unlabeled_seconds > 2:
        warnings.append("long_unlabeled_regions")
    if contradictions:
        warnings.append("timestamp_contradictions")
    return {
        "total": round(score, 6),
        "speaker_count": len(speakers),
        "expected_speaker_count": expected_speakers,
        "speaker_count_fit": round(1.0 - count_penalty, 6),
        "vad_coverage": round(vad_coverage, 6),
        "word_coverage": round(word_coverage, 6),
        "median_turn_duration": round(median_turn_duration, 6),
        "very_short_turn_ratio": round(short_turn_ratio, 6),
        "speaker_flapping_penalty": round(flapping, 6),
        "overlap_turn_ratio": round(overlap_ratio, 6),
        "long_unlabeled_region_seconds": round(unlabeled_seconds, 6),
        "timestamp_contradictions": contradictions,
        "cluster_stability": 1.0,
        "warnings": warnings,
    }


def select_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        raise ValueError("No diarization candidates were generated.")
    return sorted(
        candidates,
        key=lambda candidate: (
            -float(candidate["score"]["total"]),
            int(candidate["score"]["speaker_count"]),
            str(candidate["candidate_id"]),
        ),
    )[0]


def build_decision_report(
    candidates: list[dict[str, Any]],
    selected: dict[str, Any],
) -> dict[str, Any]:
    selected_id = selected["candidate_id"]
    return {
        "candidate_count": len(candidates),
        "candidate_configs": [
            {"candidate_id": candidate["candidate_id"], "config": candidate["config"]}
            for candidate in candidates
        ],
        "candidate_scores": [
            {"candidate_id": candidate["candidate_id"], "score": candidate["score"]}
            for candidate in candidates
        ],
        "selected_candidate": selected_id,
        "selected_candidate_score": selected["score"],
        "rejection_reasons": {
            candidate["candidate_id"]: rejection_reason(candidate, selected)
            for candidate in candidates
            if candidate["candidate_id"] != selected_id
        },
        "final_speaker_count_decision": selected["score"]["speaker_count"],
        "warnings": sorted(
            {
                warning
                for candidate in candidates
                for warning in candidate["score"].get("warnings", [])
            }
        ),
        "deterministic_tie_break": (
            "Candidates are ordered by descending score, then lower speaker count, "
            "then candidate_id."
        ),
    }


def rejection_reason(candidate: dict[str, Any], selected: dict[str, Any]) -> str:
    if candidate["score"]["total"] < selected["score"]["total"]:
        return "lower_total_score"
    if candidate["score"]["speaker_count"] > selected["score"]["speaker_count"]:
        return "tie_break_higher_speaker_count"
    return "tie_break_candidate_id"


def decision_report(result: DiarizationResult) -> dict[str, object]:
    turns = result.exclusive_turns or result.turns
    candidate = {
        "candidate_id": "primary",
        "config": {"candidate_type": "primary"},
        "score": {
            "total": 1.0 if turns else 0.0,
            "speaker_count": len({turn.speaker for turn in turns}),
            "warnings": list(result.warnings),
        },
        "result": result.model_dump(mode="json"),
    }
    report = build_decision_report([candidate], candidate)
    report["selected_backend"] = result.model.model_id if result.model else "none"
    report["turn_count"] = len(turns)
    return report


def word_coverage_ratio(words: list[AlignedWord], turns: list[DiarizationTurn]) -> float:
    sorted_turns = sorted(turns, key=lambda turn: (turn.start, turn.end))
    timed_words = [word for word in words if word.start is not None and word.end is not None]
    if not timed_words:
        return 0.0
    covered = 0
    for word in timed_words:
        start = word.start
        end = word.end
        if start is None or end is None:
            continue
        if has_overlap(start, end, sorted_turns):
            covered += 1
    return covered / len(timed_words)


def vad_coverage_ratio(vad_regions: list[VADRegion], turns: list[DiarizationTurn]) -> float:
    sorted_turns = sorted(turns, key=lambda turn: (turn.start, turn.end))
    total_vad = sum(max(0.0, region.end - region.start) for region in vad_regions)
    if total_vad == 0:
        return 1.0 if turns else 0.0
    covered = sum(max_overlap(region.start, region.end, sorted_turns) for region in vad_regions)
    return min(1.0, covered / total_vad)


def long_unlabeled_seconds(vad_regions: list[VADRegion], turns: list[DiarizationTurn]) -> float:
    sorted_turns = sorted(turns, key=lambda turn: (turn.start, turn.end))
    total = 0.0
    for region in vad_regions:
        uncovered = max(0.0, region.end - region.start) - max_overlap(
            region.start,
            region.end,
            sorted_turns,
        )
        if uncovered > 2.0:
            total += uncovered
    return total


def timestamp_contradictions(words: list[AlignedWord], turns: list[DiarizationTurn]) -> int:
    sorted_turns = sorted(turns, key=lambda turn: (turn.start, turn.end))
    contradictions = 0
    for word in words:
        if word.start is None or word.end is None or word.end < word.start:
            contradictions += 1
            continue
        if sorted_turns and not has_overlap(word.start, word.end, sorted_turns):
            contradictions += 1
    return contradictions


def max_overlap(start: float, end: float, turns: list[DiarizationTurn]) -> float:
    intervals: list[tuple[float, float]] = []
    for turn in turns:
        if turn.end <= start:
            continue
        if turn.start >= end:
            break
        clipped_start = max(start, turn.start)
        clipped_end = min(end, turn.end)
        if clipped_end > clipped_start:
            intervals.append((clipped_start, clipped_end))
    if not intervals:
        return 0.0
    total = 0.0
    current_start, current_end = intervals[0]
    for interval_start, interval_end in intervals[1:]:
        if interval_start <= current_end:
            current_end = max(current_end, interval_end)
            continue
        total += current_end - current_start
        current_start, current_end = interval_start, interval_end
    return total + current_end - current_start


def has_overlap(start: float, end: float, turns: list[DiarizationTurn]) -> bool:
    for turn in turns:
        if turn.end <= start:
            continue
        if turn.start >= end:
            break
        if overlap(start, end, turn.start, turn.end) > 0:
            return True
    return False


def overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def render_turns_rttm(turns: list[DiarizationTurn]) -> str:
    lines = []
    for turn in turns:
        duration = max(0.0, turn.end - turn.start)
        lines.append(
            f"SPEAKER file 1 {turn.start:.3f} {duration:.3f} <NA> <NA> "
            f"{turn.speaker} <NA> <NA>"
        )
    return "\n".join(lines) + ("\n" if lines else "")
