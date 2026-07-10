# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Strict, versioned contracts for the offline speaker-identity resolver."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from local_transcript_max.schemas import LTMBaseModel, Transcript

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
ANONYMOUS_LABEL_RE = re.compile(r"^SPEAKER_\d+$", re.IGNORECASE)
MAX_DISPLAY_LABEL_LENGTH = 80


class ResolverMode(StrEnum):
    REVIEW = "review"
    APPLY_SAFE = "apply-safe"
    APPLY_APPROVED = "apply-approved"


class ResolutionStatus(StrEnum):
    MAPPED = "mapped"
    REVIEW = "review"
    UNRESOLVED = "unresolved"
    UNATTRIBUTED = "unattributed"
    CONFLICT = "conflict"
    REJECTED = "rejected"


class AssignmentScope(StrEnum):
    CLUSTER = "cluster"
    TURN = "turn"
    FRAGMENT = "fragment"
    BACKEND = "backend"


class BackendState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "backend_unavailable"
    FAILED = "failed"
    NOT_REQUESTED = "not_requested"


class EvidenceTier(StrEnum):
    APPROVED_MAP = "explicit_approved_map"
    LEGACY_RENAME = "legacy_explicit_rename"
    LOCAL_PROFILE = "local_profile_match"
    DIRECT_ANCHOR = "direct_anchor"
    MEDIA_REVIEW = "source_media_review"
    STABILITY = "diarization_stability"
    CANONICAL = "canonical_artifact_timing"
    ROSTER = "roster_metadata"
    SIBLING = "sibling_job"
    ADJACENCY = "adjacency_continuation"
    ROLE = "role_inference"
    FORCED_PROXIMITY = "forced_proximity"


class ReasonCode(StrEnum):
    EXPLICIT_APPROVED_MAP = "explicit_approved_map"
    LEGACY_EXPLICIT_RENAME = "legacy_explicit_rename"
    LOCAL_PROFILE_MATCH = "local_profile_match"
    LOCAL_PROFILE_AMBIGUOUS = "local_profile_ambiguous"
    LOCAL_PROFILE_BACKEND_UNAVAILABLE = "local_profile_backend_unavailable"
    DIRECT_ADDRESS_ANCHOR = "direct_address_anchor"
    SELF_IDENTIFICATION_ANCHOR = "self_identification_anchor"
    ROLE_INFERENCE_ONLY = "role_inference_only"
    ADJACENT_TURN_CONTINUATION = "adjacent_turn_continuation"
    SAME_SPEAKER_BOUNDED_FRAGMENT = "same_speaker_bounded_fragment"
    NO_DIARIZATION_COVERAGE = "no_diarization_coverage"
    POST_VAD_TAIL = "post_vad_tail"
    ASR_TAIL_ARTIFACT_RISK = "asr_tail_artifact_risk"
    DERIVED_LABEL_DRIFT = "derived_label_drift"
    CANONICAL_UNKNOWN_FOLDED_INTO_CLUSTER = "canonical_unknown_folded_into_cluster"
    REQUIRES_TURN_LEVEL_OVERRIDE = "requires_turn_level_override"
    TURN_LEVEL_OVERRIDE = "turn_level_override"
    PHRASE_CONTINUATION_OVERRIDE = "phrase_continuation_override"
    QUESTION_ANSWER_BOUNDARY_OVERRIDE = "question_answer_boundary_override"
    MIXED_TURN_BOUNDARY = "mixed_turn_boundary"
    PARTIAL_LINE_SPEAKER_CHANGE = "partial_line_speaker_change"
    LABEL_ONLY_FALLBACK = "label_only_fallback"
    SPLIT_RECOMMENDED = "split_recommended"
    NON_LABEL_TEXT_PRESERVED = "non_label_text_preserved"
    LABEL_JOIN_REGRESSION_CHECK = "label_join_regression_check"
    SOURCE_MEDIA_REVIEW_REQUIRED = "source_media_review_required"
    SOURCE_MEDIA_SPEECH_CONFIRMED = "source_media_speech_confirmed"
    SOURCE_MEDIA_TAIL_ARTIFACT = "source_media_tail_artifact"
    VIDEO_SPEAKER_INDICATOR_SUPPORT = "video_speaker_indicator_support"
    CHANNEL_SEPARATION_SUPPORT = "channel_separation_support"
    DIARIZATION_STABLE = "diarization_stable"
    DIARIZATION_UNSTABLE_BOUNDARY = "diarization_unstable_boundary"
    ENSEMBLE_DISAGREEMENT = "ensemble_disagreement"
    COMPETING_CANDIDATE_REJECTED = "competing_candidate_rejected"
    UNATTRIBUTED_MORE_HONEST = "unattributed_more_honest"
    MISSING_CANONICAL_ARTIFACT = "missing_canonical_artifact"
    SIBLING_EVIDENCE_STALE = "sibling_evidence_stale"
    SIBLING_EVIDENCE_CONFLICT = "sibling_evidence_conflict"
    ROSTER_COUNT_CONFLICT = "roster_count_conflict"
    ROSTER_MISSING_EXPECTED_NAMES = "roster_missing_expected_names"
    COMPLETE_ROSTER_PROVIDED = "complete_roster_provided"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FORCED_PROXIMITY_ONLY = "forced_proximity_only"


class IdentityQAFlag(StrEnum):
    ROSTER_COUNT_CONFLICT = "identity_roster_count_conflict"
    LOW_CONFIDENCE = "identity_low_confidence"
    CONFLICT = "identity_conflict"
    ROLE_INFERENCE_ONLY = "identity_role_inference_only"
    FORCED_PROXIMITY_ONLY = "identity_forced_proximity_only"
    NO_DIARIZATION_COVERAGE = "identity_no_diarization_coverage"
    POST_VAD_TAIL = "identity_post_vad_tail"
    BACKEND_UNAVAILABLE = "identity_backend_unavailable"
    MISSING_CANONICAL_ARTIFACT = "identity_missing_canonical_artifact"
    DERIVED_LABEL_DRIFT = "identity_derived_label_drift"
    CANONICAL_UNKNOWN_FOLDED = "identity_canonical_unknown_folded_into_cluster"
    TURN_LEVEL_OVERRIDE_REQUIRED = "identity_turn_level_override_required"
    PHRASE_CONTINUATION_OVERRIDE = "identity_phrase_continuation_override"
    QUESTION_ANSWER_BOUNDARY_OVERRIDE = "identity_question_answer_boundary_override"
    MIXED_TURN_BOUNDARY = "identity_mixed_turn_boundary"
    PARTIAL_LINE_SPEAKER_CHANGE = "identity_partial_line_speaker_change"
    LABEL_ONLY_FALLBACK = "identity_label_only_fallback"
    SPLIT_RECOMMENDED = "identity_split_recommended"
    NON_LABEL_TEXT_DRIFT = "identity_non_label_text_drift"
    LINE_COUNT_DRIFT = "identity_line_count_drift"
    LABEL_JOIN_REGRESSION = "identity_label_join_regression"
    SIBLING_EVIDENCE_STALE = "identity_sibling_evidence_stale"
    EXTERNAL_TRANSCRIPT_MISMATCH = "identity_external_transcript_mismatch"
    AUDIT_MISSING_DECISION = "identity_audit_missing_decision"
    DIARIZATION_UNSTABLE = "identity_diarization_unstable"
    STABILITY_VARIANT_FAILED = "identity_stability_variant_failed"
    MEDIA_REVIEW_REQUIRED = "identity_media_review_required"
    MEDIA_REVIEW_CONFIRMED_TAIL = "identity_media_review_confirmed_tail_artifact"
    UNATTRIBUTED_MORE_HONEST = "identity_unattributed_more_honest"
    PRIVATE_EXCERPT_SUPPRESSED = "identity_private_excerpt_suppressed"


class DriftClassification(StrEnum):
    NONE = "none"
    LABEL_ONLY = "label_only"
    TEXT = "text"
    SEGMENTATION = "segmentation"
    LINE_COUNT = "line_count"


class ArtifactState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    MALFORMED = "malformed"


class DeliverableFormat(StrEnum):
    SRT = "srt"
    ALL = "all"


class StructuralRepairMode(StrEnum):
    REPORT_ONLY = "report-only"
    LABEL_ONLY = "label-only"


class SourceMediaReviewMode(StrEnum):
    NONE = "none"
    QUEUE = "queue"
    REVIEWED_REPORT = "reviewed-report"


def _safe_id(value: str) -> str:
    if not SAFE_ID_RE.fullmatch(value):
        raise ValueError("must be a bounded safe identifier")
    return value


def _sha256(value: str) -> str:
    if not SHA256_RE.fullmatch(value):
        raise ValueError("must be a lowercase SHA-256 digest")
    return value


def _artifact_name(value: str) -> str:
    if not value or value in {".", ".."} or PurePosixPath(value).name != value:
        raise ValueError("must be a safe artifact basename")
    if PureWindowsPath(value).is_absolute() or "\\" in value or "/" in value:
        raise ValueError("must be a safe artifact basename")
    return value


def _relative_reference(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or value.startswith("~")
        or PurePosixPath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or ".." in value.replace("\\", "/").split("/")
        or "\\" in value
    ):
        raise ValueError("must be a safe relative reference")
    return value


def normalize_display_label(value: str, *, allow_reserved: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or len(normalized) > MAX_DISPLAY_LABEL_LENGTH:
        raise ValueError(f"display label must contain 1-{MAX_DISPLAY_LABEL_LENGTH} characters")
    if "-->" in normalized:
        raise ValueError("display label cannot contain a subtitle timing arrow")
    for character in normalized:
        category = unicodedata.category(character)
        if category.startswith("C") or character in "\r\n":
            raise ValueError("display label cannot contain control or bidi characters")
    if not allow_reserved and (
        ANONYMOUS_LABEL_RE.fullmatch(normalized)
        or normalized.casefold() in {"unknown", "unattributed", "null", "none"}
    ):
        raise ValueError("participant display label uses a reserved identity label")
    return normalized


class ArtifactReference(LTMBaseModel):
    artifact_id: str
    relative_path: str | None = None
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    state: ArtifactState
    error_code: str | None = None

    _artifact_id = field_validator("artifact_id")(_safe_id)
    _artifact_sha = field_validator("sha256")(
        lambda value: value if value is None else _sha256(value)
    )

    @field_validator("relative_path")
    @classmethod
    def relative_paths_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _relative_reference(value)


class ExternalTranscriptContext(LTMBaseModel):
    artifact: ArtifactReference
    drift_classification: DriftClassification
    line_count_matches: bool
    turn_count_matches: bool
    non_label_text_matches: bool
    target_labels: list[str] = Field(default_factory=list, max_length=100_000)


class ResolverContext(LTMBaseModel):
    schema_version: Literal["speaker_identity_context.v1"] = "speaker_identity_context.v1"
    run_id: str
    job_fingerprint: str
    job_ref: str = "."
    input_hash: str | None = None
    artifacts: list[ArtifactReference] = Field(max_length=128)
    external_transcript: ExternalTranscriptContext | None = None
    known_speakers: int | None = Field(default=None, ge=1, le=128)
    artifacts_read: list[str] = Field(default_factory=list, max_length=128)
    missing_artifacts: list[str] = Field(default_factory=list, max_length=128)
    malformed_artifacts: list[str] = Field(default_factory=list, max_length=128)
    generated_at: str

    _run_id = field_validator("run_id", "job_fingerprint")(_safe_id)
    _input_hash = field_validator("input_hash")(
        lambda value: value if value is None else _sha256(value)
    )

    @field_validator("job_ref")
    @classmethod
    def valid_job_ref(cls, value: str) -> str:
        return value if value == "." else _relative_reference(value)


class ResolverPolicy(LTMBaseModel):
    mode: ResolverMode = ResolverMode.REVIEW
    min_apply_confidence: float = Field(default=0.90, ge=0.0, le=1.0)
    profile_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    profile_ambiguity_margin: float = Field(default=0.05, ge=0.0, le=1.0)
    max_fragment_duration: float = Field(default=4.0, gt=0.0, le=60.0)
    max_fragment_words: int = Field(default=12, ge=1, le=500)
    max_sibling_jobs: int = Field(default=20, ge=1, le=100)
    unattributed_label: str = "Unattributed"
    audit_csv: bool = True
    deliverable_format: DeliverableFormat = DeliverableFormat.SRT
    turn_boundary_repair: StructuralRepairMode = StructuralRepairMode.REPORT_ONLY
    source_media_review: SourceMediaReviewMode = SourceMediaReviewMode.QUEUE

    @field_validator("min_apply_confidence", "profile_threshold", "profile_ambiguity_margin")
    @classmethod
    def finite_threshold(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("thresholds must be finite")
        return value

    @field_validator("unattributed_label")
    @classmethod
    def valid_unattributed_label(cls, value: str) -> str:
        return normalize_display_label(value, allow_reserved=True)


class EvidenceSpan(LTMBaseModel):
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)

    @model_validator(mode="after")
    def ordered(self) -> EvidenceSpan:
        if not math.isfinite(self.start) or not math.isfinite(self.end) or self.end < self.start:
            raise ValueError("span must be finite and ordered")
        return self


class IdentityEvidence(LTMBaseModel):
    evidence_id: str
    assignment_scope: AssignmentScope
    scope_key: str
    speaker: str | None = None
    turn_index: int | None = Field(default=None, ge=0)
    word_index_start: int | None = Field(default=None, ge=0)
    word_index_end: int | None = Field(default=None, ge=0)
    candidate_name: str | None = None
    tier: EvidenceTier
    source_artifact_id: str
    source_hash: str | None = None
    artifact_metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    span: EvidenceSpan | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    action: str
    competing_candidate: str | None = None
    competing_candidate_rejection_reason: str | None = None
    reason_codes: list[ReasonCode] = Field(min_length=1, max_length=32)
    safe_excerpt: None = None

    _evidence_id = field_validator("evidence_id", "scope_key", "source_artifact_id")(_safe_id)
    _source_hash = field_validator("source_hash")(
        lambda value: value if value is None else _sha256(value)
    )

    @field_validator("confidence")
    @classmethod
    def finite_confidence(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("confidence must be finite")
        return value

    @field_validator("candidate_name")
    @classmethod
    def valid_candidate_name(cls, value: str | None) -> str | None:
        return value if value is None else normalize_display_label(value)

    @model_validator(mode="after")
    def ordered_word_indexes(self) -> IdentityEvidence:
        if (
            self.word_index_start is not None
            and self.word_index_end is not None
            and self.word_index_end < self.word_index_start
        ):
            raise ValueError("word index range must be ordered")
        return self


class CandidateRejection(LTMBaseModel):
    candidate_name: str
    status: Literal["rejected"] = "rejected"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list, max_length=128)
    reason_codes: list[ReasonCode] = Field(min_length=1, max_length=32)

    @field_validator("candidate_name")
    @classmethod
    def valid_candidate_name(cls, value: str) -> str:
        return normalize_display_label(value)


class IdentityCandidate(LTMBaseModel):
    candidate_id: str
    assignment_scope: AssignmentScope
    scope_key: str
    candidate_name: str
    status: ResolutionStatus
    confidence: float = Field(ge=0.0, le=1.0)
    strongest_tier: EvidenceTier
    evidence_ids: list[str] = Field(min_length=1, max_length=128)
    conflicts: list[str] = Field(default_factory=list, max_length=128)
    reason_codes: list[ReasonCode] = Field(min_length=1, max_length=32)
    requires_approval: bool

    _candidate_ids = field_validator("candidate_id", "scope_key")(_safe_id)

    @field_validator("candidate_name")
    @classmethod
    def valid_candidate_name(cls, value: str) -> str:
        return normalize_display_label(value)


class BackendStatus(LTMBaseModel):
    backend: str
    status: BackendState
    reason_code: ReasonCode | None = None
    safe_detail: str | None = None


class IdentityDecision(LTMBaseModel):
    decision_id: str
    parent_decision_id: str | None = None
    child_decision_ids: list[str] = Field(default_factory=list, max_length=100_000)
    assignment_scope: AssignmentScope
    scope_key: str
    speaker: str | None = None
    turn_index: int | None = Field(default=None, ge=0)
    member_turn_indexes: list[int] = Field(default_factory=list, max_length=100_000)
    original_diarization_label: str | None = None
    canonical_label: str | None = None
    target_label: str | None = None
    source_label_provenance: str = "canonical"
    status: ResolutionStatus
    display_label: str | None = None
    mapped_global_candidate: str | None = None
    local_override_candidate: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list, max_length=128)
    conflicts: list[str] = Field(default_factory=list, max_length=128)
    rejections: list[CandidateRejection] = Field(default_factory=list, max_length=128)
    reason_codes: list[ReasonCode] = Field(min_length=1, max_length=32)
    structural_repair_mode: StructuralRepairMode = StructuralRepairMode.REPORT_ONLY
    requires_media_review: bool = False
    requires_approval: bool = False

    _decision_ids = field_validator("decision_id", "scope_key")(_safe_id)

    @field_validator("parent_decision_id")
    @classmethod
    def valid_parent_id(cls, value: str | None) -> str | None:
        return value if value is None else _safe_id(value)

    @field_validator("child_decision_ids")
    @classmethod
    def valid_child_ids(cls, values: list[str]) -> list[str]:
        return [_safe_id(value) for value in values]

    @field_validator("member_turn_indexes")
    @classmethod
    def valid_member_indexes(cls, values: list[int]) -> list[int]:
        if any(value < 0 for value in values):
            raise ValueError("member turn indexes must be non-negative")
        return values

    @field_validator("display_label", "mapped_global_candidate", "local_override_candidate")
    @classmethod
    def valid_optional_label(cls, value: str | None) -> str | None:
        return value if value is None else normalize_display_label(value, allow_reserved=True)


class AttributionAuditRow(LTMBaseModel):
    decision_id: str
    parent_decision_id: str | None = None
    child_decision_ids: list[str] = Field(default_factory=list)
    assignment_scope: AssignmentScope
    source_line: int | None = Field(default=None, ge=1)
    turn_index: int | None = Field(default=None, ge=0)
    word_index_start: int | None = Field(default=None, ge=0)
    word_index_end: int | None = Field(default=None, ge=0)
    start: float | None = Field(default=None, ge=0.0)
    end: float | None = Field(default=None, ge=0.0)
    duration_seconds: float | None = Field(default=None, ge=0.0)
    word_count: int | None = Field(default=None, ge=0)
    original_diarization_label: str | None = None
    canonical_label: str | None = None
    target_label: str | None = None
    source_label_provenance: str
    canonical_label_was_unknown: bool
    drift_classification: DriftClassification
    assigned_display_label: str
    mapped_global_candidate: str | None = None
    local_override_candidate: str | None = None
    resolution_status: ResolutionStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_type: str
    evidence_reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    artifact_metadata_used: list[str] = Field(default_factory=list)
    competing_candidate: str | None = None
    competing_candidate_rejection_reason: str | None = None
    unattributed_is_more_honest: bool
    structural_repair_mode: StructuralRepairMode
    action: str
    requires_media_review: bool
    requires_human_approval: bool
    non_label_text_preserved: bool
    label_join_regression_checked: bool
    reason_codes: list[ReasonCode] = Field(min_length=1)

    _audit_id = field_validator("decision_id")(_safe_id)

    @field_validator("assigned_display_label")
    @classmethod
    def valid_assigned_label(cls, value: str) -> str:
        return normalize_display_label(value, allow_reserved=True)

    @model_validator(mode="after")
    def ordered_span(self) -> AttributionAuditRow:
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("audit span must be ordered")
        return self


class AttributionAudit(LTMBaseModel):
    schema_version: Literal["speaker_identity_attribution_audit.v1"] = (
        "speaker_identity_attribution_audit.v1"
    )
    run_id: str
    rows: list[AttributionAuditRow] = Field(max_length=200_000)

    _run_id = field_validator("run_id")(_safe_id)


class CandidateSet(LTMBaseModel):
    schema_version: Literal["speaker_identity_candidates.v1"] = "speaker_identity_candidates.v1"
    run_id: str
    candidates: list[IdentityCandidate] = Field(max_length=200_000)

    _run_id = field_validator("run_id")(_safe_id)


class Participant(LTMBaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list, max_length=64)
    role: str | None = Field(default=None, max_length=120)
    profile_id: str | None = None
    expected: bool = True

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return normalize_display_label(value)

    @field_validator("aliases")
    @classmethod
    def valid_aliases(cls, values: list[str]) -> list[str]:
        return [normalize_display_label(value) for value in values]

    @field_validator("profile_id")
    @classmethod
    def valid_profile_id(cls, value: str | None) -> str | None:
        return value if value is None else _safe_id(value)


class SpeakerRoster(LTMBaseModel):
    schema_version: Literal["speaker_identity_roster.v1"] = "speaker_identity_roster.v1"
    expected_speaker_count: int | None = Field(default=None, ge=1, le=128)
    participants: list[Participant] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_and_complete(self) -> SpeakerRoster:
        seen: set[str] = set()
        for participant in self.participants:
            for label in [participant.name, *participant.aliases]:
                key = unicodedata.normalize("NFKC", label).casefold()
                if key in seen:
                    raise ValueError("roster names and aliases must be normalization-unique")
                seen.add(key)
        expected = sum(1 for participant in self.participants if participant.expected)
        profile_ids = [
            participant.profile_id
            for participant in self.participants
            if participant.profile_id is not None
        ]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("roster profile IDs must be unique")
        if self.expected_speaker_count is not None and expected != self.expected_speaker_count:
            raise ValueError("expected participants must match expected_speaker_count")
        return self


class ApprovedTurnOverride(LTMBaseModel):
    turn_index: int = Field(ge=0)
    decision_id: str
    display_label: str
    reason: str = Field(min_length=1, max_length=240)

    _decision_id = field_validator("decision_id")(_safe_id)

    @field_validator("display_label")
    @classmethod
    def valid_label(cls, value: str) -> str:
        return normalize_display_label(value, allow_reserved=True)


class ApprovedIdentityMap(LTMBaseModel):
    schema_version: Literal["speaker_identity_approved_map.v1"] = "speaker_identity_approved_map.v1"
    job_fingerprint: str
    resolver_run_id: str
    canonical_artifact_hashes: dict[str, str] = Field(min_length=1, max_length=64)
    approved_at: datetime
    approved_by: str = Field(min_length=1, max_length=120)
    cluster_mappings: dict[str, str] = Field(default_factory=dict, max_length=128)
    turn_overrides: list[ApprovedTurnOverride] = Field(default_factory=list, max_length=100_000)

    _approved_ids = field_validator("job_fingerprint", "resolver_run_id")(_safe_id)

    @field_validator("canonical_artifact_hashes")
    @classmethod
    def valid_hashes(cls, values: dict[str, str]) -> dict[str, str]:
        return {_artifact_name(key): _sha256(value) for key, value in values.items()}

    @field_validator("cluster_mappings")
    @classmethod
    def valid_cluster_labels(cls, values: dict[str, str]) -> dict[str, str]:
        return {
            _safe_id(cluster): normalize_display_label(label, allow_reserved=True)
            for cluster, label in values.items()
        }

    @model_validator(mode="after")
    def unique_overrides(self) -> ApprovedIdentityMap:
        indexes = [override.turn_index for override in self.turn_overrides]
        if len(indexes) != len(set(indexes)):
            raise ValueError("approved turn overrides must be unique")
        return self


class StabilityVariantRequest(LTMBaseModel):
    variant_id: str
    artifact: str
    backend: str
    device: str

    _variant_metadata = field_validator("variant_id", "backend", "device")(_safe_id)


class StabilityConfig(LTMBaseModel):
    schema_version: Literal["speaker_identity_stability_config.v1"] = (
        "speaker_identity_stability_config.v1"
    )
    variants: list[StabilityVariantRequest] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique_variant_ids(self) -> StabilityConfig:
        values = [variant.variant_id for variant in self.variants]
        if len(values) != len(set(values)):
            raise ValueError("stability variant IDs must be unique")
        return self


class StabilityVariantResult(LTMBaseModel):
    variant_id: str
    backend: str
    device: str
    status: Literal["success", "failed", "unavailable"]
    artifact_id: str
    artifact_hash: str | None = None
    safe_reason: str | None = None

    _variant_metadata = field_validator(
        "variant_id", "backend", "device", "artifact_id"
    )(_safe_id)
    _artifact_hash = field_validator("artifact_hash")(
        lambda value: value if value is None else _sha256(value)
    )


class TurnStability(LTMBaseModel):
    turn_index: int = Field(ge=0)
    stable_label: bool
    stable_boundaries: bool
    agreement_score: float = Field(ge=0.0, le=1.0)
    reason_codes: list[ReasonCode] = Field(default_factory=list)


class DiarizationStabilityReport(LTMBaseModel):
    schema_version: Literal["speaker_identity_diarization_stability.v1"] = (
        "speaker_identity_diarization_stability.v1"
    )
    run_id: str
    variants: list[StabilityVariantResult] = Field(max_length=16)
    turn_stability: list[TurnStability] = Field(default_factory=list, max_length=100_000)
    warnings: list[str] = Field(default_factory=list, max_length=128)

    _run_id = field_validator("run_id")(_safe_id)


class MediaReviewRegion(LTMBaseModel):
    region_id: str
    decision_id: str
    turn_index: int = Field(ge=0)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    original_speaker: str
    reason_codes: list[ReasonCode] = Field(min_length=1)
    questions: list[str] = Field(min_length=1, max_length=16)

    _region_ids = field_validator("region_id", "decision_id")(_safe_id)

    @model_validator(mode="after")
    def ordered(self) -> MediaReviewRegion:
        if not math.isfinite(self.start) or not math.isfinite(self.end) or self.end < self.start:
            raise ValueError("media review span must be finite and ordered")
        return self


class MediaReviewQueue(LTMBaseModel):
    schema_version: Literal["speaker_identity_media_review_queue.v1"] = (
        "speaker_identity_media_review_queue.v1"
    )
    run_id: str
    regions: list[MediaReviewRegion] = Field(default_factory=list, max_length=10_000)

    _run_id = field_validator("run_id")(_safe_id)

    @model_validator(mode="after")
    def unique_region_ids(self) -> MediaReviewQueue:
        values = [region.region_id for region in self.regions]
        if len(values) != len(set(values)):
            raise ValueError("media review region IDs must be unique")
        return self


class ReviewedMediaRegion(LTMBaseModel):
    region_id: str
    conclusions: list[
        Literal[
            "confirmed_speech",
            "tail_artifact",
            "speaker_visible",
            "speaker_not_visible",
            "unattributed_recommended",
            "needs_user_approval",
        ]
    ] = Field(min_length=1, max_length=8)
    measurements: dict[str, float | int | bool | None] = Field(default_factory=dict)
    reviewed_at: datetime

    _region_id = field_validator("region_id")(_safe_id)


class MediaReviewReport(LTMBaseModel):
    schema_version: Literal["speaker_identity_media_review_report.v1"] = (
        "speaker_identity_media_review_report.v1"
    )
    run_id: str
    job_fingerprint: str
    regions: list[ReviewedMediaRegion] = Field(default_factory=list, max_length=10_000)

    _report_ids = field_validator("run_id", "job_fingerprint")(_safe_id)

    @model_validator(mode="after")
    def unique_region_ids(self) -> MediaReviewReport:
        values = [region.region_id for region in self.regions]
        if len(values) != len(set(values)):
            raise ValueError("reviewed media region IDs must be unique")
        return self


class IdentityValidationSummary(LTMBaseModel):
    valid: bool
    audit_complete: bool
    expected_decision_ids: list[str] = Field(default_factory=list, max_length=200_000)
    actual_decision_ids: list[str] = Field(default_factory=list, max_length=200_000)
    duplicate_decision_ids: list[str] = Field(default_factory=list)
    missing_decision_ids: list[str] = Field(default_factory=list)
    external_transcript_drift_classification: DriftClassification = DriftClassification.NONE
    line_count_preserved: bool
    turn_count_preserved: bool
    cue_count_preserved: bool | None = None
    non_label_text_preserved: bool
    label_join_regression_checked: bool
    canonical_unknown_folded_into_cluster_count: int = Field(ge=0)
    canonical_hashes_unchanged: bool
    warnings: list[str] = Field(default_factory=list, max_length=1_000)
    errors: list[str] = Field(default_factory=list, max_length=1_000)


class IdentityReport(LTMBaseModel):
    schema_version: Literal["speaker_identity_report.v1"] = "speaker_identity_report.v1"
    run_id: str
    approval_source_run_id: str | None = None
    generated_at: str
    mode: ResolverMode
    resolver_context: ResolverContext
    policy: ResolverPolicy
    cluster_decisions: list[IdentityDecision] = Field(max_length=1_000)
    turn_decisions: list[IdentityDecision] = Field(max_length=200_000)
    backend_statuses: list[BackendStatus] = Field(default_factory=list, max_length=64)
    stability_summary: dict[str, int | bool] = Field(default_factory=dict)
    media_review_summary: dict[str, int] = Field(default_factory=dict)
    artifacts_written: dict[str, str | None] = Field(default_factory=dict)
    validation_summary: IdentityValidationSummary
    qa_flags: list[IdentityQAFlag] = Field(default_factory=list, max_length=200_000)
    warnings: list[str] = Field(default_factory=list, max_length=1_000)
    commit_state: Literal["complete"] = "complete"

    _approval_source_id = field_validator("approval_source_run_id")(
        lambda value: value if value is None else _safe_id(value)
    )


class IdentityMappedTranscript(LTMBaseModel):
    schema_version: Literal["transcript_identity_mapped.v1"] = "transcript_identity_mapped.v1"
    resolver_run_id: str
    canonical_transcript_hash: str
    primary_deliverable: Literal["transcript.identity_mapped.srt"] = (
        "transcript.identity_mapped.srt"
    )
    display_labels: list[str] = Field(default_factory=list, max_length=1_000)
    turn_decision_ids: list[str] = Field(default_factory=list, max_length=200_000)
    validation_summary: IdentityValidationSummary
    transcript: Transcript

    _canonical_hash = field_validator("canonical_transcript_hash")(_sha256)
    _run_id = field_validator("resolver_run_id")(_safe_id)


class MachineReadableError(LTMBaseModel):
    schema_version: Literal["speaker_identity_error.v1"] = "speaker_identity_error.v1"
    code: str
    message: str
    remediation: str | None = None
    safe_context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    _error_code = field_validator("code")(_safe_id)


Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
