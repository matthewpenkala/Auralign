# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Transparent lexicographic candidate selection.

The order is fixed and auditable:

1. reject candidates violating a hard invariant;
2. reject candidates that regress a protected quality dimension;
3. require improvement in at least one approved dimension;
4. require the versioned improvement margin;
5. preserve the first pass whenever evidence ties, conflicts, or is missing.

Incompatible signals are never folded into a weighted sum.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import Field, field_validator

from local_transcript_max.schemas import LTMBaseModel

from . import policy
from .evidence import CandidateEvidence

Selection = Literal["source", "wide"]
Subject = Literal["span", "control", "wide"]
Calibration = Literal["uncalibrated", "deterministic"]

DECISION_REASONS: Final[tuple[str, ...]] = (
    "abstained_candidate_failed",
    "abstained_crosses_speaker_boundary",
    "abstained_hard_gate_failed",
    "abstained_insufficient_independent_support",
    "abstained_missing_alignment_evidence",
    "abstained_no_wide_beam_disagreement",
    "abstained_protected_material_ambiguity",
    "abstained_protected_regression",
    "abstained_score_margin_below_threshold",
    "promoted_wide_beam",
)


class GateOutcome(LTMBaseModel):
    name: str
    subject: Subject
    passed: bool
    detail: str | None = None


class ScoreMargin(LTMBaseModel):
    name: str
    control_value: float | None = None
    wide_value: float | None = None
    margin: float | None = None
    direction: Literal["higher_is_better", "lower_is_better"]
    calibration: Calibration
    improved: bool = False
    regressed: bool = False


class SpanEvaluationInput(LTMBaseModel):
    """Everything the evaluator is allowed to look at for one span."""

    source_text: str
    control_text: str
    wide_text: str
    expected_language: str | None = None
    speaker_count: int = Field(ge=0)
    minimum_alignment_coverage: float = Field(ge=0.0, le=1.0)
    minimum_logprob_margin: float = Field(ge=0.0)
    protected_margin_multiplier: float = Field(ge=1.0)


class SpanDecision(LTMBaseModel):
    selected: Selection
    decision_reason: str
    policy_version: Literal["adaptive_redecode_policy.v1"] = policy.ADAPTIVE_POLICY_VERSION
    hard_gates: list[GateOutcome] = Field(default_factory=list)
    margins: list[ScoreMargin] = Field(default_factory=list)
    protected_classes: list[str] = Field(default_factory=list)
    protected_support_required: bool = False
    independent_support_count: int = Field(default=0, ge=0)
    required_independent_support: int = Field(default=0, ge=0)
    failure_reasons: list[str] = Field(default_factory=list)

    @field_validator("decision_reason")
    @classmethod
    def validate_decision_reason(cls, value: str) -> str:
        """Reject a reason code outside the versioned vocabulary.

        A replayed plan whose decision reason is unknown to this policy version
        cannot be audited, so it fails closed instead of loading opaquely.
        """

        if value not in DECISION_REASONS:
            raise ValueError(
                f"unknown adaptive decision reason '{value}' for {policy.ADAPTIVE_POLICY_VERSION}"
            )
        return value


def evaluate_span(
    inputs: SpanEvaluationInput,
    control: CandidateEvidence,
    wide: CandidateEvidence,
) -> SpanDecision:
    """Decide whether the wide-beam candidate may replace the first pass."""

    gates: list[GateOutcome] = []
    failures: list[str] = []

    span_ok = _span_gates(inputs, gates, failures)
    # Hard invariants gate the candidate that could be promoted. The control is
    # only required to be comparable: a degenerate beam-5 window result is
    # exactly the evidence that makes a wider search worth trusting, so its own
    # gate failures are recorded rather than treated as blocking.
    control_failures: list[str] = []
    _candidate_gates(control, inputs, gates, control_failures, blocking=False)
    wide_ok = _candidate_gates(wide, inputs, gates, failures, blocking=True)
    comparable = _control_is_comparable(control, gates, failures)

    if not span_ok:
        return _abstain("abstained_crosses_speaker_boundary", gates, failures)
    if control.status == "failed" or wide.status == "failed":
        return _abstain("abstained_candidate_failed", gates, failures)
    if not comparable:
        return _abstain("abstained_missing_alignment_evidence", gates, failures)
    if control_failures:
        failures.extend(f"control_observed:{item}" for item in control_failures)
    if not wide_ok:
        reason = (
            "abstained_missing_alignment_evidence"
            if "missing_alignment_evidence" in failures
            else "abstained_hard_gate_failed"
        )
        return _abstain(reason, gates, failures)

    if control.normalized_text_sha256 == wide.normalized_text_sha256:
        # Both beams read the identical bytes the same way. Any difference from
        # the first pass is a windowing artifact, not wider-search evidence.
        return _abstain("abstained_no_wide_beam_disagreement", gates, failures)

    margins = _margins(control, wide)
    protected = policy.protected_classes(inputs.source_text, inputs.wide_text)
    protected += tuple(
        item for item in policy.protected_classes(inputs.control_text, inputs.wide_text)
        if item not in protected
    )
    protected_sorted: list[str] = sorted({str(item) for item in protected})
    multiplier = inputs.protected_margin_multiplier if protected_sorted else 1.0
    required_support = (
        policy.REQUIRED_PROTECTED_INDEPENDENT_SUPPORT
        if protected_sorted
        else policy.REQUIRED_INDEPENDENT_SUPPORT
    )

    regressed = [margin.name for margin in margins if margin.regressed]
    independent = [
        margin.name
        for margin in margins
        if margin.improved and margin.calibration == "deterministic"
    ]
    support_count = len(independent)

    base = SpanDecision(
        selected="source",
        decision_reason="abstained_protected_regression",
        hard_gates=gates,
        margins=margins,
        protected_classes=protected_sorted,
        protected_support_required=bool(protected_sorted),
        independent_support_count=support_count,
        required_independent_support=required_support,
        failure_reasons=sorted({*failures, *(f"regression:{name}" for name in regressed)}),
    )
    if regressed:
        return base

    logprob_margin = next(
        (margin.margin for margin in margins if margin.name == "avg_logprob"),
        None,
    )
    required_margin = inputs.minimum_logprob_margin * multiplier
    if logprob_margin is None or logprob_margin < required_margin:
        return base.model_copy(
            update={
                "decision_reason": "abstained_score_margin_below_threshold",
                "failure_reasons": sorted(
                    {*failures, f"score_margin_below:{required_margin:.6f}"}
                ),
            }
        )
    if support_count < required_support:
        reason = (
            "abstained_protected_material_ambiguity"
            if protected_sorted
            else "abstained_insufficient_independent_support"
        )
        return base.model_copy(
            update={
                "decision_reason": reason,
                "failure_reasons": sorted(
                    {*failures, f"independent_support:{support_count}<{required_support}"}
                ),
            }
        )
    return base.model_copy(
        update={
            "selected": "wide",
            "decision_reason": "promoted_wide_beam",
            "failure_reasons": sorted(failures),
        }
    )


def _abstain(reason: str, gates: list[GateOutcome], failures: list[str]) -> SpanDecision:
    return SpanDecision(
        selected="source",
        decision_reason=reason,
        hard_gates=gates,
        failure_reasons=sorted(set(failures)),
    )


def _span_gates(
    inputs: SpanEvaluationInput,
    gates: list[GateOutcome],
    failures: list[str],
) -> bool:
    single_speaker = inputs.speaker_count <= 1
    gates.append(
        GateOutcome(
            name="single_speaker_span",
            subject="span",
            passed=single_speaker,
            detail=f"speaker_count={inputs.speaker_count}",
        )
    )
    if not single_speaker:
        failures.append("crosses_speaker_boundary")
    return single_speaker


def _control_is_comparable(
    control: CandidateEvidence,
    gates: list[GateOutcome],
    failures: list[str],
) -> bool:
    """The control must at least yield the metrics every margin is computed from."""

    comparable = (
        control.status == "generated"
        and control.alignment_coverage is not None
        and control.avg_logprob is not None
    )
    gates.append(
        GateOutcome(
            name="control_comparable",
            subject="control",
            passed=comparable,
            detail=control.alignment_failure_reason,
        )
    )
    if not comparable:
        failures.append("missing_alignment_evidence")
    return comparable


def _candidate_gates(
    evidence: CandidateEvidence,
    inputs: SpanEvaluationInput,
    gates: list[GateOutcome],
    failures: list[str],
    *,
    blocking: bool,
) -> bool:
    subject: Subject = evidence.role
    prefix = f"{subject}:" if blocking else ""
    if evidence.status == "failed":
        gates.append(
            GateOutcome(
                name="candidate_generated",
                subject=subject,
                passed=False,
                detail=evidence.failure_reason,
            )
        )
        failures.append(f"{prefix}candidate_failed")
        return False
    gates.append(GateOutcome(name="candidate_generated", subject=subject, passed=True))

    checks: list[tuple[str, bool, str | None]] = [
        ("non_empty_text", evidence.word_count > 0, f"word_count={evidence.word_count}"),
        ("timestamps_present", evidence.timestamps_present, None),
        ("timestamps_valid", evidence.timestamps_valid, None),
        ("timestamps_monotonic", evidence.timestamps_monotonic, None),
        ("timestamps_within_window", evidence.timestamps_within_window, None),
        (
            "language_consistent",
            inputs.expected_language is None
            or evidence.language is None
            or evidence.language == inputs.expected_language,
            f"language={evidence.language} expected={inputs.expected_language}",
        ),
        (
            "no_speech_probability",
            evidence.no_speech_prob is None
            or evidence.no_speech_prob <= policy.MAXIMUM_NO_SPEECH_PROBABILITY,
            f"no_speech_prob={evidence.no_speech_prob}",
        ),
        (
            "compression_ratio",
            evidence.compression_ratio is None
            or evidence.compression_ratio <= policy.MAXIMUM_COMPRESSION_RATIO,
            f"compression_ratio={evidence.compression_ratio}",
        ),
        (
            "repetition_ratio",
            evidence.repetition_ratio is None
            or evidence.repetition_ratio <= policy.MAXIMUM_REPETITION_RATIO,
            f"repetition_ratio={evidence.repetition_ratio}",
        ),
        (
            "words_per_second_plausible",
            evidence.words_per_second is None
            or (
                policy.MINIMUM_WORDS_PER_SECOND
                <= evidence.words_per_second
                <= policy.MAXIMUM_WORDS_PER_SECOND
            ),
            f"words_per_second={evidence.words_per_second}",
        ),
    ]
    passed = True
    for name, ok, detail in checks:
        gates.append(GateOutcome(name=name, subject=subject, passed=ok, detail=detail))
        if not ok:
            failures.append(f"{prefix}{name}")
            passed = False

    coverage = evidence.alignment_coverage
    if coverage is None:
        gates.append(
            GateOutcome(
                name="alignment_evidence_available",
                subject=subject,
                passed=False,
                detail=evidence.alignment_failure_reason,
            )
        )
        if blocking:
            failures.append("missing_alignment_evidence")
        return False
    gates.append(GateOutcome(name="alignment_evidence_available", subject=subject, passed=True))
    coverage_ok = coverage >= inputs.minimum_alignment_coverage
    gates.append(
        GateOutcome(
            name="alignment_coverage",
            subject=subject,
            passed=coverage_ok,
            detail=f"coverage={coverage}",
        )
    )
    if not coverage_ok:
        failures.append(f"{prefix}alignment_coverage")
        passed = False
    return passed


def _margins(control: CandidateEvidence, wide: CandidateEvidence) -> list[ScoreMargin]:
    """Compare the two candidates on each dimension, keeping the signals separate."""

    return [
        _margin(
            "avg_logprob",
            control.avg_logprob,
            wide.avg_logprob,
            direction="higher_is_better",
            calibration="uncalibrated",
            improvement=0.0,
            tolerance=0.0,
        ),
        _margin(
            "alignment_coverage",
            control.alignment_coverage,
            wide.alignment_coverage,
            direction="higher_is_better",
            calibration="deterministic",
            improvement=policy.ALIGNMENT_COVERAGE_IMPROVEMENT,
            tolerance=policy.ALIGNMENT_COVERAGE_TOLERANCE,
        ),
        _margin(
            "compression_ratio",
            control.compression_ratio,
            wide.compression_ratio,
            direction="lower_is_better",
            calibration="deterministic",
            improvement=policy.COMPRESSION_RATIO_IMPROVEMENT,
            tolerance=policy.COMPRESSION_RATIO_TOLERANCE,
        ),
        _margin(
            "repetition_ratio",
            control.repetition_ratio,
            wide.repetition_ratio,
            direction="lower_is_better",
            calibration="deterministic",
            improvement=policy.REPETITION_RATIO_IMPROVEMENT,
            tolerance=policy.REPETITION_RATIO_TOLERANCE,
        ),
        _margin(
            "no_speech_prob",
            control.no_speech_prob,
            wide.no_speech_prob,
            direction="lower_is_better",
            calibration="deterministic",
            improvement=policy.NO_SPEECH_IMPROVEMENT,
            tolerance=policy.NO_SPEECH_TOLERANCE,
        ),
    ]


def _margin(
    name: str,
    control_value: float | None,
    wide_value: float | None,
    *,
    direction: Literal["higher_is_better", "lower_is_better"],
    calibration: Calibration,
    improvement: float,
    tolerance: float,
) -> ScoreMargin:
    if control_value is None or wide_value is None:
        return ScoreMargin(
            name=name,
            control_value=control_value,
            wide_value=wide_value,
            margin=None,
            direction=direction,
            calibration=calibration,
        )
    delta = (
        wide_value - control_value
        if direction == "higher_is_better"
        else control_value - wide_value
    )
    return ScoreMargin(
        name=name,
        control_value=round(control_value, 6),
        wide_value=round(wide_value, 6),
        margin=round(delta, 6),
        direction=direction,
        calibration=calibration,
        improved=delta >= improvement and delta > 0,
        regressed=delta < -tolerance,
    )
