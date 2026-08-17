# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""The immutable adaptive reconciliation plan and its integrity contract.

SHA-256 binding here proves artifact integrity only. It says nothing about
semantic correctness and nothing about authorship or authenticity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final, Literal

from pydantic import Field, ValidationError

from local_transcript_max.errors import LTMError
from local_transcript_max.schemas import LTMBaseModel

from .decision import SpanDecision
from .evidence import CandidateEvidence
from .policy import ADAPTIVE_POLICY_VERSION

ADAPTIVE_PLAN_SCHEMA_VERSION: Final[Literal["adaptive_redecode_plan.v1"]] = (
    "adaptive_redecode_plan.v1"
)
ADAPTIVE_PLAN_PRODUCER: Final = "local-transcript-max"
SEALED_PLAN_KEYS: Final = frozenset({"plan", "plan_sha256", "sealed_schema_version"})
SEALED_SCHEMA_VERSION: Final[Literal["adaptive_redecode_sealed_plan.v1"]] = (
    "adaptive_redecode_sealed_plan.v1"
)


class PlanLimits(LTMBaseModel):
    maximum_spans: int = Field(ge=1)
    maximum_span_seconds: float = Field(gt=0.0)
    maximum_total_window_seconds: float = Field(gt=0.0)
    maximum_candidates_per_span: int = Field(ge=1)
    maximum_context_seconds_each_side: float = Field(ge=0.0)
    maximum_plan_bytes: int = Field(ge=1)
    maximum_attempts_per_span: int = Field(ge=1)
    recursive_redecode: Literal[False] = False


class ModelProvenance(LTMBaseModel):
    role: str
    model_id: str
    revision: str | None = None
    revision_verified: bool | None = None
    checkpoint_sha256: str | None = None
    gated: bool = False
    present: bool = False


class SpanPlan(LTMBaseModel):
    index: int = Field(ge=1)
    reason_codes: list[str] = Field(default_factory=list)
    original_start: float = Field(ge=0.0)
    original_end: float = Field(ge=0.0)
    padded_start: float = Field(ge=0.0)
    padded_end: float = Field(ge=0.0)
    window_duration_seconds: float = Field(ge=0.0)
    window_audio_sha256: str
    source_text_sha256: str
    source_word_count: int = Field(ge=0)
    speaker_labels: list[str] = Field(default_factory=list)
    candidates: list[CandidateEvidence] = Field(min_length=1, max_length=2)
    decision: SpanDecision
    applied: bool = False
    applied_start: float | None = None
    applied_end: float | None = None
    replaced_segment_ids: list[int] = Field(default_factory=list)


class StageDisposition(LTMBaseModel):
    stage: str
    disposition: Literal["reused", "rerun", "invalidated_not_regenerated"]
    reason: str
    input_sha256: str | None = None


class PlanSummary(LTMBaseModel):
    span_count: int = Field(ge=0)
    promoted_span_count: int = Field(ge=0)
    abstained_span_count: int = Field(ge=0)
    failed_candidate_count: int = Field(ge=0)
    decision_reason_counts: dict[str, int] = Field(default_factory=dict)
    protected_class_counts: dict[str, int] = Field(default_factory=dict)
    total_window_seconds: float = Field(ge=0.0)
    wide_beam_decode_count: int = Field(ge=0)


class AdaptiveRedecodePlan(LTMBaseModel):
    schema_version: Literal["adaptive_redecode_plan.v1"] = ADAPTIVE_PLAN_SCHEMA_VERSION
    producer: Literal["local-transcript-max"] = ADAPTIVE_PLAN_PRODUCER
    producer_version: str
    source_revision: str | None = None
    policy_version: Literal["adaptive_redecode_policy.v1"] = ADAPTIVE_POLICY_VERSION
    selection_policy: Literal["adaptive-safe"] = "adaptive-safe"
    beam_ladder: list[int] = Field(min_length=2, max_length=2)
    backend: str
    runtime_device: str | None = None
    inference_mode: str | None = None
    model: ModelProvenance | None = None
    canonical_audio_sha256: str
    first_pass_asr_sha256: str
    first_pass_language: str | None = None
    configuration_sha256: str
    asr_configuration: dict[str, Any] = Field(default_factory=dict)
    limits: PlanLimits
    spans: list[SpanPlan] = Field(default_factory=list)
    summary: PlanSummary
    stage_dispositions: list[StageDisposition] = Field(default_factory=list)
    selected_asr_sha256: str | None = None


def canonical_plan_bytes(plan: AdaptiveRedecodePlan) -> bytes:
    """Serialize the plan deterministically so identical runs are byte-stable."""

    return json.dumps(
        plan.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def plan_sha256(plan: AdaptiveRedecodePlan) -> str:
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()


def seal_plan(plan: AdaptiveRedecodePlan, *, maximum_bytes: int) -> dict[str, Any]:
    """Bind a plan to its integrity digest, refusing oversized artifacts."""

    encoded = canonical_plan_bytes(plan)
    if len(encoded) > maximum_bytes:
        raise LTMError(
            "Adaptive re-decode plan exceeds the configured artifact size limit "
            f"({len(encoded)} > {maximum_bytes} bytes)."
        )
    return {
        "sealed_schema_version": SEALED_SCHEMA_VERSION,
        "plan": json.loads(encoded.decode("utf-8")),
        "plan_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def load_sealed_plan(payload: object, *, maximum_bytes: int) -> AdaptiveRedecodePlan:
    """Deserialize into the exact typed schema, failing closed on anything else.

    Unknown, stale, extended, malformed, duplicate, non-canonical, or opaque
    fields are rejected rather than ignored.
    """

    if not isinstance(payload, dict):
        raise LTMError("Adaptive re-decode plan artifact must be a JSON object.")
    keys = set(payload)
    if keys != SEALED_PLAN_KEYS:
        unexpected = sorted(keys - SEALED_PLAN_KEYS)
        missing = sorted(SEALED_PLAN_KEYS - keys)
        raise LTMError(
            "Adaptive re-decode plan artifact has an unexpected shape "
            f"(unexpected={unexpected}, missing={missing})."
        )
    if payload["sealed_schema_version"] != SEALED_SCHEMA_VERSION:
        raise LTMError(
            "Adaptive re-decode plan artifact uses an unsupported sealed schema version."
        )
    try:
        plan = AdaptiveRedecodePlan.model_validate(payload["plan"])
    except ValidationError as exc:
        raise LTMError(f"Adaptive re-decode plan failed strict schema validation: {exc}") from exc
    encoded = canonical_plan_bytes(plan)
    if len(encoded) > maximum_bytes:
        raise LTMError("Adaptive re-decode plan exceeds the configured artifact size limit.")
    digest = hashlib.sha256(encoded).hexdigest()
    if digest != payload["plan_sha256"]:
        raise LTMError(
            "Adaptive re-decode plan digest does not match its canonical serialization; "
            "the artifact was modified or was not canonically serialized."
        )
    _reject_duplicate_spans(plan)
    return plan


def _reject_duplicate_spans(plan: AdaptiveRedecodePlan) -> None:
    indexes = [span.index for span in plan.spans]
    if len(set(indexes)) != len(indexes):
        raise LTMError("Adaptive re-decode plan contains duplicate span entries.")
    applied = sorted(
        (span.applied_start, span.applied_end)
        for span in plan.spans
        if span.applied and span.applied_start is not None and span.applied_end is not None
    )
    previous_end: float | None = None
    for start, end in applied:
        if end <= start:
            raise LTMError("Adaptive re-decode plan contains an invalid applied span.")
        if previous_end is not None and start < previous_end:
            raise LTMError("Adaptive re-decode plan contains overlapping applied spans.")
        previous_end = end
