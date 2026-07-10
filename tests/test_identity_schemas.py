# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import math
from datetime import UTC, datetime
from enum import StrEnum

import pytest
from pydantic import BaseModel, ValidationError

from local_transcript_max.schemas import SpeakerTurn, Transcript
from local_transcript_max.speaker_id import identity_models as models

HASH = "a" * 64
RUN_ID = "run:0123456789abcdef01234567"
JOB_ID = "job:0123456789abcdef01234567"


def validation_summary() -> models.IdentityValidationSummary:
    return models.IdentityValidationSummary(
        valid=True,
        audit_complete=True,
        line_count_preserved=True,
        turn_count_preserved=True,
        non_label_text_preserved=True,
        label_join_regression_checked=True,
        canonical_unknown_folded_into_cluster_count=0,
        canonical_hashes_unchanged=True,
    )


def context() -> models.ResolverContext:
    return models.ResolverContext(
        run_id=RUN_ID,
        job_fingerprint=JOB_ID,
        input_hash=HASH,
        artifacts=[
            models.ArtifactReference(
                artifact_id="transcript_json",
                relative_path="transcript.json",
                sha256=HASH,
                size_bytes=20,
                state=models.ArtifactState.PRESENT,
            )
        ],
        generated_at="2026-07-09T00:00:00Z",
    )


def evidence() -> models.IdentityEvidence:
    return models.IdentityEvidence(
        evidence_id="evidence:0",
        assignment_scope=models.AssignmentScope.TURN,
        scope_key="turn:0",
        speaker="SPEAKER_00",
        turn_index=0,
        candidate_name="Participant Alpha",
        tier=models.EvidenceTier.LOCAL_PROFILE,
        source_artifact_id="profile:alpha",
        source_hash=HASH,
        span=models.EvidenceSpan(start=0.0, end=1.0),
        confidence=0.95,
        action="candidate",
        reason_codes=[models.ReasonCode.LOCAL_PROFILE_MATCH],
    )


def candidate() -> models.IdentityCandidate:
    return models.IdentityCandidate(
        candidate_id="candidate:0",
        assignment_scope=models.AssignmentScope.TURN,
        scope_key="turn:0",
        candidate_name="Participant Alpha",
        status=models.ResolutionStatus.MAPPED,
        confidence=0.95,
        strongest_tier=models.EvidenceTier.LOCAL_PROFILE,
        evidence_ids=["evidence:0"],
        reason_codes=[models.ReasonCode.LOCAL_PROFILE_MATCH],
        requires_approval=False,
    )


def decision() -> models.IdentityDecision:
    return models.IdentityDecision(
        decision_id="decision:turn:0",
        assignment_scope=models.AssignmentScope.TURN,
        scope_key="turn:0",
        speaker="SPEAKER_00",
        turn_index=0,
        original_diarization_label="SPEAKER_00",
        canonical_label="SPEAKER_00",
        target_label="SPEAKER_00",
        status=models.ResolutionStatus.MAPPED,
        display_label="Participant Alpha",
        confidence=0.95,
        evidence_ids=["evidence:0"],
        reason_codes=[models.ReasonCode.LOCAL_PROFILE_MATCH],
    )


def audit_row() -> models.AttributionAuditRow:
    return models.AttributionAuditRow(
        decision_id="decision:turn:0",
        assignment_scope=models.AssignmentScope.TURN,
        source_line=3,
        turn_index=0,
        start=0.0,
        end=1.0,
        duration_seconds=1.0,
        word_count=1,
        original_diarization_label="SPEAKER_00",
        canonical_label="SPEAKER_00",
        target_label="SPEAKER_00",
        source_label_provenance="canonical",
        canonical_label_was_unknown=False,
        drift_classification=models.DriftClassification.NONE,
        assigned_display_label="Participant Alpha",
        resolution_status=models.ResolutionStatus.MAPPED,
        confidence=0.95,
        evidence_type=models.EvidenceTier.LOCAL_PROFILE,
        evidence_reason="validated local profile",
        evidence_ids=["evidence:0"],
        unattributed_is_more_honest=False,
        structural_repair_mode=models.StructuralRepairMode.REPORT_ONLY,
        action="map label",
        requires_media_review=False,
        requires_human_approval=False,
        non_label_text_preserved=True,
        label_join_regression_checked=True,
        reason_codes=[models.ReasonCode.LOCAL_PROFILE_MATCH],
    )


def versioned_models() -> list[BaseModel]:
    summary = validation_summary()
    transcript = Transcript(
        input_path="synthetic.wav",
        speaker_turns=[
            SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=1.0, text="Synthetic line.")
        ],
    )
    return [
        context(),
        models.AttributionAudit(run_id=RUN_ID, rows=[audit_row()]),
        models.CandidateSet(run_id=RUN_ID, candidates=[candidate()]),
        models.SpeakerRoster(
            expected_speaker_count=1,
            participants=[models.Participant(name="Participant Alpha")],
        ),
        models.ApprovedIdentityMap(
            job_fingerprint=JOB_ID,
            resolver_run_id=RUN_ID,
            canonical_artifact_hashes={"transcript.json": HASH},
            approved_at=datetime(2026, 7, 9, tzinfo=UTC),
            approved_by="local-reviewer",
            cluster_mappings={"SPEAKER_00": "Participant Alpha"},
        ),
        models.StabilityConfig(
            variants=[
                models.StabilityVariantRequest(
                    variant_id="variant:0",
                    artifact="diarization.variant.json",
                    backend="local-backend",
                    device="cpu",
                )
            ]
        ),
        models.DiarizationStabilityReport(
            run_id=RUN_ID,
            variants=[
                models.StabilityVariantResult(
                    variant_id="variant:0",
                    backend="local",
                    device="cpu",
                    status="success",
                    artifact_id="variant_artifact:0",
                    artifact_hash=HASH,
                )
            ],
        ),
        models.MediaReviewQueue(
            run_id=RUN_ID,
            regions=[
                models.MediaReviewRegion(
                    region_id="region:0",
                    decision_id="decision:turn:0",
                    turn_index=0,
                    start=0.0,
                    end=1.0,
                    original_speaker="UNKNOWN",
                    reason_codes=[models.ReasonCode.NO_DIARIZATION_COVERAGE],
                    questions=["is_real_speech"],
                )
            ],
        ),
        models.MediaReviewReport(
            run_id=RUN_ID,
            job_fingerprint=JOB_ID,
            regions=[
                models.ReviewedMediaRegion(
                    region_id="region:0",
                    conclusions=["unattributed_recommended"],
                    reviewed_at=datetime(2026, 7, 9, tzinfo=UTC),
                )
            ],
        ),
        models.IdentityReport(
            run_id=RUN_ID,
            generated_at="2026-07-09T00:00:00Z",
            mode=models.ResolverMode.REVIEW,
            resolver_context=context(),
            policy=models.ResolverPolicy(),
            cluster_decisions=[],
            turn_decisions=[decision()],
            validation_summary=summary,
        ),
        models.IdentityMappedTranscript(
            resolver_run_id=RUN_ID,
            canonical_transcript_hash=HASH,
            display_labels=["Participant Alpha"],
            turn_decision_ids=["decision:turn:0"],
            validation_summary=summary,
            transcript=transcript,
        ),
        models.MachineReadableError(code="invalid_input", message="Synthetic input failed."),
    ]


def leaf_models() -> list[BaseModel]:
    return [
        models.ArtifactReference(artifact_id="transcript_json", state=models.ArtifactState.MISSING),
        models.ExternalTranscriptContext(
            artifact=models.ArtifactReference(
                artifact_id="external_transcript", state=models.ArtifactState.PRESENT
            ),
            drift_classification=models.DriftClassification.NONE,
            line_count_matches=True,
            turn_count_matches=True,
            non_label_text_matches=True,
        ),
        models.ResolverPolicy(),
        models.EvidenceSpan(start=0.0, end=1.0),
        evidence(),
        models.CandidateRejection(
            candidate_name="Participant Beta",
            confidence=0.4,
            reason_codes=[models.ReasonCode.COMPETING_CANDIDATE_REJECTED],
        ),
        candidate(),
        models.BackendStatus(
            backend="local-profile",
            status=models.BackendState.AVAILABLE,
        ),
        decision(),
        audit_row(),
        models.Participant(name="Participant Alpha", aliases=["Alpha"]),
        models.ApprovedTurnOverride(
            turn_index=0,
            decision_id="decision:turn:0",
            display_label="Participant Alpha",
            reason="Reviewed locally",
        ),
        models.StabilityVariantRequest(
            variant_id="variant:0",
            artifact="variant.json",
            backend="local",
            device="cpu",
        ),
        models.StabilityVariantResult(
            variant_id="variant:0",
            backend="local",
            device="cpu",
            status="unavailable",
            artifact_id="artifact:0",
        ),
        models.TurnStability(
            turn_index=0,
            stable_label=True,
            stable_boundaries=True,
            agreement_score=1.0,
        ),
        models.ReviewedMediaRegion(
            region_id="region:0",
            conclusions=["confirmed_speech"],
            reviewed_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        validation_summary(),
    ]


def test_every_enum_value_round_trips_through_its_type() -> None:
    enum_types = [
        value
        for value in vars(models).values()
        if isinstance(value, type)
        and issubclass(value, StrEnum)
        and value.__module__ == models.__name__
    ]
    assert enum_types
    for enum_type in enum_types:
        assert [enum_type(member.value) for member in enum_type] == list(enum_type)
        assert len({member.value for member in enum_type}) == len(list(enum_type))


def test_stability_variant_result_requires_safe_requested_dimensions() -> None:
    base = {
        "variant_id": "variant:0",
        "backend": "pyannote",
        "device": "cuda:0",
        "status": "success",
        "artifact_id": "variant:0",
        "artifact_hash": HASH,
    }
    result = models.StabilityVariantResult.model_validate(base)
    assert (result.backend, result.device) == ("pyannote", "cuda:0")

    for missing in ["backend", "device"]:
        with pytest.raises(ValidationError):
            models.StabilityVariantResult.model_validate(
                {key: value for key, value in base.items() if key != missing}
            )
    for field in ["backend", "device"]:
        with pytest.raises(ValidationError):
            models.StabilityVariantResult.model_validate(
                {**base, field: "unsafe dimension\nvalue"}
            )


def test_reason_codes_cover_the_adopted_v1_vocabulary() -> None:
    assert {member.value for member in models.ReasonCode} == {
        "explicit_approved_map",
        "legacy_explicit_rename",
        "local_profile_match",
        "local_profile_ambiguous",
        "local_profile_backend_unavailable",
        "direct_address_anchor",
        "self_identification_anchor",
        "role_inference_only",
        "adjacent_turn_continuation",
        "same_speaker_bounded_fragment",
        "no_diarization_coverage",
        "post_vad_tail",
        "asr_tail_artifact_risk",
        "derived_label_drift",
        "canonical_unknown_folded_into_cluster",
        "requires_turn_level_override",
        "turn_level_override",
        "phrase_continuation_override",
        "question_answer_boundary_override",
        "mixed_turn_boundary",
        "partial_line_speaker_change",
        "label_only_fallback",
        "split_recommended",
        "non_label_text_preserved",
        "label_join_regression_check",
        "source_media_review_required",
        "source_media_speech_confirmed",
        "source_media_tail_artifact",
        "video_speaker_indicator_support",
        "channel_separation_support",
        "diarization_stable",
        "diarization_unstable_boundary",
        "ensemble_disagreement",
        "competing_candidate_rejected",
        "unattributed_more_honest",
        "missing_canonical_artifact",
        "sibling_evidence_stale",
        "sibling_evidence_conflict",
        "roster_count_conflict",
        "roster_missing_expected_names",
        "complete_roster_provided",
        "insufficient_evidence",
        "forced_proximity_only",
    }


def test_identity_qa_flags_cover_the_adopted_v1_vocabulary() -> None:
    assert {member.value for member in models.IdentityQAFlag} == {
        "identity_roster_count_conflict",
        "identity_low_confidence",
        "identity_conflict",
        "identity_role_inference_only",
        "identity_forced_proximity_only",
        "identity_no_diarization_coverage",
        "identity_post_vad_tail",
        "identity_backend_unavailable",
        "identity_missing_canonical_artifact",
        "identity_derived_label_drift",
        "identity_canonical_unknown_folded_into_cluster",
        "identity_turn_level_override_required",
        "identity_phrase_continuation_override",
        "identity_question_answer_boundary_override",
        "identity_mixed_turn_boundary",
        "identity_partial_line_speaker_change",
        "identity_label_only_fallback",
        "identity_split_recommended",
        "identity_non_label_text_drift",
        "identity_line_count_drift",
        "identity_label_join_regression",
        "identity_sibling_evidence_stale",
        "identity_external_transcript_mismatch",
        "identity_audit_missing_decision",
        "identity_diarization_unstable",
        "identity_stability_variant_failed",
        "identity_media_review_required",
        "identity_media_review_confirmed_tail_artifact",
        "identity_unattributed_more_honest",
        "identity_private_excerpt_suppressed",
    }


@pytest.mark.parametrize("instance", versioned_models(), ids=lambda value: type(value).__name__)
def test_versioned_schema_round_trip_is_lossless(instance: BaseModel) -> None:
    parsed = type(instance).model_validate_json(instance.model_dump_json())
    assert parsed == instance


@pytest.mark.parametrize(
    "instance", [*versioned_models(), *leaf_models()], ids=lambda value: type(value).__name__
)
def test_all_identity_models_forbid_unknown_fields(instance: BaseModel) -> None:
    payload = instance.model_dump(mode="json")
    payload["unexpected_private_field"] = "must not be accepted"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        type(instance).model_validate(payload)


@pytest.mark.parametrize("instance", versioned_models(), ids=lambda value: type(value).__name__)
def test_unknown_schema_versions_fail_closed(instance: BaseModel) -> None:
    payload = instance.model_dump(mode="json")
    payload["schema_version"] += ".future"
    with pytest.raises(ValidationError, match="literal_error"):
        type(instance).model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_all_confidence_and_span_numbers_reject_non_finite_values(value: float) -> None:
    with pytest.raises(ValidationError):
        models.ResolverPolicy(min_apply_confidence=value)
    with pytest.raises(ValidationError):
        models.EvidenceSpan(start=0.0, end=value)
    with pytest.raises(ValidationError):
        evidence().model_copy(update={"confidence": value}).__class__.model_validate(
            {**evidence().model_dump(), "confidence": value}
        )
    with pytest.raises(ValidationError):
        models.TurnStability(
            turn_index=0,
            stable_label=True,
            stable_boundaries=True,
            agreement_score=value,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_apply_confidence", -0.01),
        ("min_apply_confidence", 1.01),
        ("profile_threshold", -0.01),
        ("profile_ambiguity_margin", 1.01),
        ("max_fragment_duration", 0.0),
        ("max_fragment_duration", 60.01),
        ("max_fragment_words", 0),
        ("max_sibling_jobs", 101),
    ],
)
def test_policy_bounds_are_enforced(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        models.ResolverPolicy(**{field: value})


def test_display_labels_normalize_unicode_and_reject_unsafe_content() -> None:
    participant = models.Participant(name="  Ｐａｒｔｉｃｉｐａｎｔ Ａｌｐｈａ  ")
    assert participant.name == "Participant Alpha"

    for value in [
        "",
        " ",
        "A\nB",
        "A\x00B",
        "A\u202eB",
        "A --> B",
        "x" * (models.MAX_DISPLAY_LABEL_LENGTH + 1),
    ]:
        with pytest.raises(ValidationError):
            models.Participant(name=value)


@pytest.mark.parametrize(
    "value", ["UNKNOWN", "unknown", "Unattributed", "null", "None", "SPEAKER_00"]
)
def test_roster_rejects_reserved_identity_labels(value: str) -> None:
    with pytest.raises(ValidationError, match="reserved identity label"):
        models.Participant(name=value)


def test_unattributed_and_approved_override_may_use_documented_reserved_label() -> None:
    assert models.ResolverPolicy(unattributed_label=" Unattributed ").unattributed_label == (
        "Unattributed"
    )
    override = models.ApprovedTurnOverride(
        turn_index=0,
        decision_id="decision:turn:0",
        display_label="Unattributed",
        reason="No reliable person-level evidence",
    )
    assert override.display_label == "Unattributed"


@pytest.mark.parametrize(
    "participants",
    [
        [
            models.Participant(name="Participant Alpha"),
            models.Participant(name="participant alpha"),
        ],
        [
            models.Participant(name="Participant Alpha", aliases=["P. Alpha"]),
            models.Participant(name="Participant Beta", aliases=["p. alpha"]),
        ],
        [models.Participant(name="A"), models.Participant(name="Ａ")],
    ],
)
def test_roster_rejects_normalized_name_and_alias_collisions(
    participants: list[models.Participant],
) -> None:
    with pytest.raises(ValidationError, match="normalization-unique"):
        models.SpeakerRoster(participants=participants)


def test_roster_count_and_approval_turn_indexes_are_unambiguous() -> None:
    with pytest.raises(ValidationError, match="expected participants"):
        models.SpeakerRoster(
            expected_speaker_count=2,
            participants=[models.Participant(name="Participant Alpha")],
        )

    duplicate = models.ApprovedTurnOverride(
        turn_index=0,
        decision_id="decision:turn:0",
        display_label="Participant Alpha",
        reason="Reviewed locally",
    )
    with pytest.raises(ValidationError, match="must be unique"):
        models.ApprovedIdentityMap(
            job_fingerprint=JOB_ID,
            resolver_run_id=RUN_ID,
            canonical_artifact_hashes={"transcript.json": HASH},
            approved_at=datetime(2026, 7, 9, tzinfo=UTC),
            approved_by="local-reviewer",
            turn_overrides=[duplicate, duplicate],
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"artifact_id": "../private", "state": "missing"},
        {"artifact_id": "artifact", "relative_path": "/private/job.json", "state": "missing"},
        {"artifact_id": "artifact", "relative_path": "../job.json", "state": "missing"},
        {"artifact_id": "artifact", "relative_path": "sub/../../job.json", "state": "missing"},
        {"artifact_id": "artifact", "sha256": "A" * 64, "state": "present"},
        {"artifact_id": "artifact", "sha256": "a" * 63, "state": "present"},
    ],
)
def test_artifact_reference_rejects_unsafe_ids_paths_and_hashes(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        models.ArtifactReference.model_validate(payload)


@pytest.mark.parametrize(
    "relative_path",
    ["", ".", "C:\\private\\artifact.json", "\\\\server\\share\\artifact.json"],
)
def test_artifact_reference_rejects_empty_dot_and_platform_absolute_paths(
    relative_path: str,
) -> None:
    with pytest.raises(ValidationError):
        models.ArtifactReference(
            artifact_id="artifact",
            relative_path=relative_path,
            state=models.ArtifactState.MISSING,
        )


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (lambda: models.AttributionAudit(run_id=RUN_ID, rows=[]), "run_id"),
        (lambda: models.CandidateSet(run_id=RUN_ID, candidates=[]), "run_id"),
        (
            lambda: models.DiarizationStabilityReport(run_id=RUN_ID, variants=[]),
            "run_id",
        ),
        (lambda: models.MediaReviewQueue(run_id=RUN_ID), "run_id"),
        (
            lambda: models.MediaReviewReport(
                run_id=RUN_ID,
                job_fingerprint=JOB_ID,
            ),
            "run_id",
        ),
        (
            lambda: models.MediaReviewReport(
                run_id=RUN_ID,
                job_fingerprint=JOB_ID,
            ),
            "job_fingerprint",
        ),
        (
            lambda: models.IdentityMappedTranscript(
                resolver_run_id=RUN_ID,
                canonical_transcript_hash=HASH,
                display_labels=[],
                turn_decision_ids=[],
                validation_summary=validation_summary(),
                transcript=Transcript(input_path="synthetic.wav"),
            ),
            "resolver_run_id",
        ),
        (
            lambda: models.MachineReadableError(code="invalid_input", message="Synthetic"),
            "code",
        ),
    ],
)
def test_all_artifact_level_identifiers_reject_path_like_values(
    factory: object, field: str
) -> None:
    instance = factory()  # type: ignore[operator]
    payload = instance.model_dump(mode="json")
    payload[field] = "../private/value"
    with pytest.raises(ValidationError):
        type(instance).model_validate(payload)


@pytest.mark.parametrize("job_ref", ["/private/job", "../job", "~/.private", "C:\\private\\job"])
def test_resolver_context_rejects_private_or_escaping_job_references(job_ref: str) -> None:
    payload = context().model_dump(mode="json")
    payload["job_ref"] = job_ref
    with pytest.raises(ValidationError):
        models.ResolverContext.model_validate(payload)


def test_approved_map_rejects_unsafe_cluster_ids_hashes_and_labels() -> None:
    base = {
        "job_fingerprint": JOB_ID,
        "resolver_run_id": RUN_ID,
        "canonical_artifact_hashes": {"transcript.json": HASH},
        "approved_at": datetime(2026, 7, 9, tzinfo=UTC),
        "approved_by": "local-reviewer",
    }
    with pytest.raises(ValidationError):
        models.ApprovedIdentityMap(**base, cluster_mappings={"../cluster": "Participant Alpha"})
    with pytest.raises(ValidationError):
        models.ApprovedIdentityMap(
            **{**base, "canonical_artifact_hashes": {"transcript.json": "bad"}}
        )
    with pytest.raises(ValidationError):
        models.ApprovedIdentityMap(**base, cluster_mappings={"SPEAKER_00": "A\nB"})


@pytest.mark.parametrize(
    "artifact_id", ["/private/transcript.json", "../transcript.json", "sub/transcript.json", ""]
)
def test_approved_map_hash_keys_are_safe_artifact_ids(artifact_id: str) -> None:
    with pytest.raises(ValidationError):
        models.ApprovedIdentityMap(
            job_fingerprint=JOB_ID,
            resolver_run_id=RUN_ID,
            canonical_artifact_hashes={artifact_id: HASH},
            approved_at=datetime(2026, 7, 9, tzinfo=UTC),
            approved_by="local-reviewer",
        )


@pytest.mark.parametrize(
    ("instance", "field"),
    [
        (evidence(), "candidate_name"),
        (
            models.CandidateRejection(
                candidate_name="Participant Beta",
                confidence=0.4,
                reason_codes=[models.ReasonCode.COMPETING_CANDIDATE_REJECTED],
            ),
            "candidate_name",
        ),
        (candidate(), "candidate_name"),
        (decision(), "display_label"),
        (audit_row(), "assigned_display_label"),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, BaseModel) else str(value),
)
def test_every_identity_label_field_rejects_control_characters(
    instance: BaseModel, field: str
) -> None:
    payload = instance.model_dump(mode="json")
    payload[field] = "Participant\nPrivate"
    with pytest.raises(ValidationError):
        type(instance).model_validate(payload)


def test_decision_relationship_ids_and_member_indexes_are_safe_and_bounded() -> None:
    payload = decision().model_dump(mode="json")
    payload["parent_decision_id"] = "../parent"
    with pytest.raises(ValidationError):
        models.IdentityDecision.model_validate(payload)

    payload = decision().model_dump(mode="json")
    payload["child_decision_ids"] = ["../child"]
    with pytest.raises(ValidationError):
        models.IdentityDecision.model_validate(payload)

    payload = decision().model_dump(mode="json")
    payload["member_turn_indexes"] = [-1]
    with pytest.raises(ValidationError):
        models.IdentityDecision.model_validate(payload)


def test_ordered_index_ranges_reject_reversed_bounds() -> None:
    payload = evidence().model_dump(mode="json")
    payload["word_index_start"] = 4
    payload["word_index_end"] = 3
    with pytest.raises(ValidationError):
        models.IdentityEvidence.model_validate(payload)

    payload = audit_row().model_dump(mode="json")
    payload["start"] = 2.0
    payload["end"] = 1.0
    with pytest.raises(ValidationError):
        models.AttributionAuditRow.model_validate(payload)


def test_variant_profile_and_region_identifiers_are_collision_free() -> None:
    duplicate_variant = models.StabilityVariantRequest(
        variant_id="variant:0",
        artifact="variant.json",
        backend="local",
        device="cpu",
    )
    with pytest.raises(ValidationError):
        models.StabilityConfig(variants=[duplicate_variant, duplicate_variant])

    duplicate_region = models.MediaReviewRegion(
        region_id="region:0",
        decision_id="decision:turn:0",
        turn_index=0,
        start=0.0,
        end=1.0,
        original_speaker="UNKNOWN",
        reason_codes=[models.ReasonCode.NO_DIARIZATION_COVERAGE],
        questions=["is_real_speech"],
    )
    with pytest.raises(ValidationError):
        models.MediaReviewQueue(run_id=RUN_ID, regions=[duplicate_region, duplicate_region])

    duplicate_reviewed_region = models.ReviewedMediaRegion(
        region_id="region:0",
        conclusions=["confirmed_speech"],
        reviewed_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    with pytest.raises(ValidationError):
        models.MediaReviewReport(
            run_id=RUN_ID,
            job_fingerprint=JOB_ID,
            regions=[duplicate_reviewed_region, duplicate_reviewed_region],
        )

    with pytest.raises(ValidationError):
        models.SpeakerRoster(
            participants=[
                models.Participant(name="Participant Alpha", profile_id="shared-profile"),
                models.Participant(name="Participant Beta", profile_id="shared-profile"),
            ]
        )


def test_media_review_regions_are_finite_ordered_and_bounded() -> None:
    base = {
        "region_id": "region:0",
        "decision_id": "decision:turn:0",
        "turn_index": 0,
        "original_speaker": "UNKNOWN",
        "reason_codes": [models.ReasonCode.NO_DIARIZATION_COVERAGE],
        "questions": ["is_real_speech"],
    }
    with pytest.raises(ValidationError):
        models.MediaReviewRegion(**base, start=2.0, end=1.0)
    for value in [math.nan, math.inf, -math.inf]:
        with pytest.raises(ValidationError):
            models.MediaReviewRegion(**base, start=0.0, end=value)
