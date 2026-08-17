# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Atomic publication of the derived adaptive lane.

The selected ASR artifact, its plan, the regenerated dependent outputs, and the
validation report either all validate and publish together, or nothing is
published and the first pass stands unchanged.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_transcript_max.errors import LTMError
from local_transcript_max.export.json_export import write_json_artifact
from local_transcript_max.platform_security import ensure_private_directory
from local_transcript_max.schemas import ASRResult, QAReport, Transcript
from local_transcript_max.utils import sha256_file

from .lane import AdaptiveRedecodeOutcome, asr_result_sha256

ADAPTIVE_DIRECTORY_NAME = "adaptive"
ADAPTIVE_PLAN_FILENAME = "redecode_plan.json"
ADAPTIVE_SELECTED_ASR_FILENAME = "transcript.selected.raw.json"
ADAPTIVE_SELECTED_TRANSCRIPT_FILENAME = "transcript.selected.json"
ADAPTIVE_CANDIDATES_FILENAME = "transcript.adaptive_candidates.json"
ADAPTIVE_QA_FILENAME = "qa_report.selected.json"
ADAPTIVE_VALIDATION_FILENAME = "adaptive_validation_report.json"
ADAPTIVE_SUMMARY_FILENAME = "adaptive_redecode_report.json"

CRITICAL_QA_CODES = frozenset(
    {
        "impossible_timestamp",
        "missing_word_timestamps",
        "non_monotonic_word_timestamps",
        "repeated_phrase",
        "speech_in_no_speech",
        "high_compression_ratio",
    }
)


@dataclass(frozen=True)
class AdaptiveDerivatives:
    """The regenerated dependent outputs for a promoted selection."""

    selected_result: ASRResult
    selected_transcript: Transcript
    qa_report: QAReport
    alignment_report: dict[str, Any]


def publish_adaptive_lane(
    out_dir: Path,
    outcome: AdaptiveRedecodeOutcome,
    *,
    first_pass_qa: QAReport,
    regenerate: Callable[[ASRResult], AdaptiveDerivatives] | None,
    write_exports: Callable[[Path, Transcript], None] | None = None,
) -> dict[str, Any]:
    """Stage, validate, and atomically promote the derived adaptive lane."""

    destination = out_dir / ADAPTIVE_DIRECTORY_NAME
    if destination.exists():
        raise LTMError(
            "The adaptive lane directory already exists; refusing to overwrite retained "
            "evidence. Choose a fresh output directory."
        )
    staging = out_dir / f".{ADAPTIVE_DIRECTORY_NAME}-staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False, mode=0o700)
    ensure_private_directory(staging)
    try:
        report = _stage(
            staging,
            outcome,
            first_pass_qa=first_pass_qa,
            regenerate=regenerate,
            write_exports=write_exports,
        )
        os.replace(staging, destination)
        return report
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _stage(
    staging: Path,
    outcome: AdaptiveRedecodeOutcome,
    *,
    first_pass_qa: QAReport,
    regenerate: Callable[[ASRResult], AdaptiveDerivatives] | None,
    write_exports: Callable[[Path, Transcript], None] | None,
) -> dict[str, Any]:
    write_json_artifact(staging / ADAPTIVE_PLAN_FILENAME, outcome.sealed_plan)
    write_json_artifact(staging / ADAPTIVE_CANDIDATES_FILENAME, outcome.candidates_artifact)
    write_json_artifact(staging / ADAPTIVE_SUMMARY_FILENAME, privacy_safe_summary(outcome))

    validation: dict[str, Any] = {
        "schema_version": "adaptive_redecode_validation.v1",
        "plan_sha256": outcome.sealed_plan["plan_sha256"],
        "promoted": outcome.promoted,
        "checks": [],
        "regenerated_stages": [],
    }
    checks: list[dict[str, Any]] = []

    if outcome.selected_result is None:
        checks.append(_check("selection_present", True, "no span was promoted"))
        validation["checks"] = checks
        write_json_artifact(staging / ADAPTIVE_VALIDATION_FILENAME, validation)
        return validation

    if regenerate is None:
        raise LTMError(
            "Adaptive publication requires a dependent-stage regenerator when a span "
            "was promoted; refusing to publish a partially applied plan."
        )
    selected = outcome.selected_result
    checks.append(
        _check(
            "selected_asr_digest_matches_plan",
            asr_result_sha256(selected) == outcome.plan.selected_asr_sha256,
            "selected ASR artifact must match the digest sealed in the plan",
        )
    )
    derivatives = regenerate(selected)
    validation["regenerated_stages"] = [
        disposition.stage
        for disposition in outcome.plan.stage_dispositions
        if disposition.disposition == "rerun"
    ]
    checks.append(
        _check(
            "qa_invariants_not_regressed",
            not _regressed_codes(first_pass_qa, derivatives.qa_report),
            "critical QA invariants must not regress",
            detail=sorted(_regressed_codes(first_pass_qa, derivatives.qa_report)),
        )
    )
    checks.append(
        _check(
            "monotonic_word_timestamps",
            _monotonic(derivatives.selected_transcript),
            "derived word timestamps must remain complete and monotonic",
        )
    )
    checks.append(
        _check(
            "speaker_labels_preserved",
            _speaker_labels(derivatives.selected_transcript)
            <= _speaker_labels_from_turns(derivatives.selected_transcript),
            "derived words may only carry labels present in the reused diarization",
        )
    )

    write_json_artifact(staging / ADAPTIVE_SELECTED_ASR_FILENAME, derivatives.selected_result)
    write_json_artifact(
        staging / ADAPTIVE_SELECTED_TRANSCRIPT_FILENAME,
        derivatives.selected_transcript,
    )
    write_json_artifact(staging / ADAPTIVE_QA_FILENAME, derivatives.qa_report)
    write_json_artifact(staging / "alignment_report.selected.json", derivatives.alignment_report)
    if write_exports is not None:
        write_exports(staging, derivatives.selected_transcript)

    failed = [check["name"] for check in checks if not check["passed"]]
    validation["checks"] = checks
    validation["artifact_sha256"] = {
        path.name: sha256_file(path) for path in sorted(staging.iterdir()) if path.is_file()
    }
    validation["valid"] = not failed
    write_json_artifact(staging / ADAPTIVE_VALIDATION_FILENAME, validation)
    if failed:
        raise LTMError(
            "Adaptive lane validation failed; the first-pass transcript is preserved "
            f"and nothing was published (failed checks: {failed})."
        )
    return validation


def privacy_safe_summary(outcome: AdaptiveRedecodeOutcome) -> dict[str, Any]:
    """Return hashes, counts, metrics, statuses, and reason codes only."""

    plan = outcome.plan
    return {
        "schema_version": "adaptive_redecode_summary.v1",
        "selection_policy": plan.selection_policy,
        "policy_version": plan.policy_version,
        "plan_schema_version": plan.schema_version,
        "plan_sha256": outcome.sealed_plan["plan_sha256"],
        "beam_ladder": list(plan.beam_ladder),
        "canonical_audio_sha256": plan.canonical_audio_sha256,
        "first_pass_asr_sha256": plan.first_pass_asr_sha256,
        "selected_asr_sha256": plan.selected_asr_sha256,
        "configuration_sha256": plan.configuration_sha256,
        "score_margin_calibration": "uncalibrated",
        "summary": plan.summary.model_dump(mode="json"),
        "limits": plan.limits.model_dump(mode="json"),
        "stage_dispositions": [
            disposition.model_dump(mode="json") for disposition in plan.stage_dispositions
        ],
        "spans": [
            {
                "index": span.index,
                "reason_codes": list(span.reason_codes),
                "window_audio_sha256": span.window_audio_sha256,
                "window_duration_seconds": span.window_duration_seconds,
                "decision_reason": span.decision.decision_reason,
                "selected": span.decision.selected,
                "applied": span.applied,
                "protected_classes": list(span.decision.protected_classes),
                "independent_support_count": span.decision.independent_support_count,
                "candidate_status": {
                    candidate.role: candidate.status for candidate in span.candidates
                },
            }
            for span in plan.spans
        ],
    }


def _check(name: str, passed: bool, description: str, *, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "description": description, "detail": detail}


def _regressed_codes(before: QAReport, after: QAReport) -> set[str]:
    previous = _code_counts(before)
    current = _code_counts(after)
    return {
        code
        for code in CRITICAL_QA_CODES
        if current.get(code, 0) > previous.get(code, 0)
    }


def _code_counts(report: QAReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for flag in report.flags:
        counts[flag.code] = counts.get(flag.code, 0) + 1
    return counts


def _monotonic(transcript: Transcript) -> bool:
    previous_end = float("-inf")
    for word in transcript.words:
        if word.start is None or word.end is None:
            return False
        if word.end < word.start or word.start < previous_end - 0.05:
            return False
        previous_end = max(previous_end, word.end)
    return True


def _speaker_labels(transcript: Transcript) -> set[str]:
    return {word.speaker for word in transcript.words if word.speaker}


def _speaker_labels_from_turns(transcript: Transcript) -> set[str]:
    return {turn.speaker for turn in transcript.diarization_turns} | {
        turn.speaker for turn in transcript.exclusive_diarization_turns
    }
