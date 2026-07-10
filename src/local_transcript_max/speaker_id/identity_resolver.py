# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic, offline speaker-identity resolution for existing jobs."""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from local_transcript_max.benchmark.der import _maximum_weight_assignment
from local_transcript_max.export.csv_export import formula_safe_cell
from local_transcript_max.schemas import DiarizationResult, DiarizationTurn

from .identity_exports import (
    build_identity_transcript,
    render_identity_markdown,
    render_identity_segments_csv,
    render_identity_srt,
    render_identity_text,
    render_identity_vtt,
    validate_srt_label_only,
)
from .identity_io import (
    IdentityArtifactTransaction,
    IdentityResolverError,
    JobIdentityLock,
    LoadedIdentityJob,
    ReadArtifact,
    canonical_hash_snapshot,
    deterministic_json_bytes,
    load_identity_job,
    load_optional_model,
    read_optional_bytes,
    resolve_job_dir,
    serialize_jsonl,
    stable_digest,
    strict_json_bytes,
    verify_canonical_hashes,
)
from .identity_models import (
    MAX_DISPLAY_LABEL_LENGTH,
    ApprovedIdentityMap,
    AssignmentScope,
    AttributionAudit,
    AttributionAuditRow,
    BackendState,
    BackendStatus,
    CandidateRejection,
    CandidateSet,
    DeliverableFormat,
    DiarizationStabilityReport,
    DriftClassification,
    EvidenceTier,
    IdentityCandidate,
    IdentityDecision,
    IdentityEvidence,
    IdentityMappedTranscript,
    IdentityQAFlag,
    IdentityReport,
    IdentityValidationSummary,
    MediaReviewQueue,
    MediaReviewRegion,
    MediaReviewReport,
    ReasonCode,
    ResolutionStatus,
    ResolverMode,
    ResolverPolicy,
    SourceMediaReviewMode,
    SpeakerRoster,
    StabilityConfig,
    StabilityVariantResult,
    StructuralRepairMode,
    TurnStability,
    normalize_display_label,
)

ALLOWED_SIBLING_ARTIFACTS = {
    "speaker_identity_report.json",
    "speaker_identity_approved_map.json",
    "speaker_map.json",
    "speaker_validation_report.json",
    "speaker_report.json",
    "run_metadata.json",
}
MAX_SIBLING_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_SIBLING_PATH_LENGTH = 4_096
MAX_SIBLING_ROOT_ENTRIES = 1_000
MAX_DIRECT_ANCHOR_TEXT = 20_000


@dataclass(frozen=True)
class ResolveIdentityOptions:
    policy: ResolverPolicy = field(default_factory=ResolverPolicy)
    known_speakers: int | None = None
    roster_path: Path | None = None
    external_transcript: Path | None = None
    approved_map_path: Path | None = None
    sibling_jobs: tuple[Path, ...] = ()
    sibling_root: Path | None = None
    sibling_manifest: Path | None = None
    stability_config_path: Path | None = None
    media_review_report_path: Path | None = None
    write_identity_mapped_exports: bool = False
    write_media_review_queue: bool = False
    job_lock_already_held: bool = False


@dataclass(frozen=True)
class ResolveIdentityResult:
    report: IdentityReport
    written_artifacts: tuple[str, ...]


@dataclass
class _CandidateSource:
    name: str
    confidence: float
    tier: EvidenceTier
    reason_codes: list[ReasonCode]
    evidence_id: str
    safe_apply: bool = False
    approved: bool = False


@dataclass
class _ResolutionState:
    evidence: list[IdentityEvidence] = field(default_factory=list)
    evidence_ids: set[str] = field(default_factory=set)
    candidates: list[IdentityCandidate] = field(default_factory=list)
    candidate_sources: dict[str, list[_CandidateSource]] = field(
        default_factory=lambda: defaultdict(list)
    )
    backend_statuses: list[BackendStatus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    qa_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _AcquiredResolverInputs:
    """Validated optional inputs acquired while the protected job lock is held."""

    roster: SpeakerRoster | None
    approved_map: ApprovedIdentityMap | None
    media_report: MediaReviewReport | None
    stability_config: StabilityConfig | None
    sibling_reports: tuple[tuple[Path, ReadArtifact | None], ...]
    rename_records: tuple[Any, ...] | None
    optional_hashes: Mapping[str, str]


@dataclass(frozen=True)
class _ValidatedProfileMatch:
    speaker: str
    label: str
    confidence: float
    active_ambiguous: bool


def resolve_identities(
    job_dir: Path,
    options: ResolveIdentityOptions | None = None,
) -> ResolveIdentityResult:
    options = options or ResolveIdentityOptions()
    root = resolve_job_dir(job_dir)
    lock = nullcontext() if options.job_lock_already_held else JobIdentityLock(root)
    with lock:
        inputs = _acquire_resolver_inputs(root, options)
        prior_report = (
            _load_prior_identity_report(root)
            if options.policy.mode == ResolverMode.APPLY_APPROVED
            else None
        )
        job = load_identity_job(
            root,
            policy=options.policy,
            known_speakers=options.known_speakers,
            external_transcript=options.external_transcript,
            optional_input_hashes=inputs.optional_hashes,
        )
        canonical_hashes = canonical_hash_snapshot(job)
        roster = inputs.roster
        approved_map = inputs.approved_map
        media_report = inputs.media_report
        _validate_mode_inputs(job, options, roster, approved_map)
        state = _ResolutionState()
        _validate_roster(job, options.policy.mode, roster, state)
        approved_map = _validate_approved_map(job, approved_map, canonical_hashes, prior_report)

        stability_report, unstable_turns = _build_stability_report(job, inputs.stability_config)
        clusters = _stable_clusters(job)
        _collect_profile_evidence(job, clusters, roster, options.policy, state)
        _collect_rename_evidence(job, clusters, roster, inputs.rename_records, state)
        if approved_map is not None:
            _collect_approved_evidence(job, approved_map, clusters, roster, state)
        _collect_anchor_and_role_evidence(job, clusters, roster, state)
        _collect_sibling_evidence(job, inputs.sibling_reports, state)
        cluster_decisions = _resolve_clusters(job, clusters, options.policy, state)
        _enforce_unique_cluster_names(cluster_decisions, state)
        reviewed_media: Mapping[str, set[str]] = {}
        if media_report is not None:
            baseline_turns = _resolve_turns(
                job,
                cluster_decisions,
                approved_map,
                {},
                unstable_turns,
                options.policy,
                _ResolutionState(),
            )
            expected_queue = _build_media_queue(
                job,
                baseline_turns,
                run_id=media_report.run_id,
            )
            reviewed_media = _reviewed_media_by_decision(
                media_report,
                job,
                expected_queue,
                _media_source_run_ids(job, options, inputs.optional_hashes),
            )
        turn_decisions = _resolve_turns(
            job,
            cluster_decisions,
            approved_map,
            reviewed_media,
            unstable_turns,
            options.policy,
            state,
        )
        cluster_decisions = _link_decision_graph(cluster_decisions, turn_decisions)
        audit = _build_audit(job, cluster_decisions, turn_decisions, state)
        validation = _validate_decisions_and_audit(
            job,
            cluster_decisions,
            turn_decisions,
            audit,
            canonical_hashes,
        )
        media_queue = _build_media_queue(job, turn_decisions)
        state.qa_flags = _derive_identity_qa_flags(
            job,
            state,
            cluster_decisions,
            turn_decisions,
            validation,
            stability_report,
        )

        output_bytes, artifacts_written, validation = _build_output_family(
            job,
            options,
            state,
            cluster_decisions,
            turn_decisions,
            audit,
            validation,
            stability_report,
            media_queue,
        )
        if not verify_canonical_hashes(job, canonical_hashes):
            raise IdentityResolverError(
                "canonical_artifact_drift",
                "Canonical artifacts changed during identity resolution; nothing was published.",
            )
        validation = validation.model_copy(update={"canonical_hashes_unchanged": True})
        output_bytes = _refresh_validation_payloads(output_bytes, validation)
        report = IdentityReport(
            run_id=job.context.run_id,
            approval_source_run_id=(None if approved_map is None else approved_map.resolver_run_id),
            generated_at=job.context.generated_at,
            mode=options.policy.mode,
            resolver_context=job.context,
            policy=options.policy,
            cluster_decisions=cluster_decisions,
            turn_decisions=turn_decisions,
            backend_statuses=state.backend_statuses,
            stability_summary={
                "ran": stability_report is not None,
                "requested_variants": 0
                if stability_report is None
                else len(stability_report.variants),
                "unstable_turns": len(unstable_turns),
            },
            media_review_summary={
                "queued_regions": len(media_queue.regions),
                "reviewed_regions": 0 if media_report is None else len(media_report.regions),
                "unattributed_after_review": sum(
                    decision.status == ResolutionStatus.UNATTRIBUTED for decision in turn_decisions
                ),
            },
            artifacts_written=artifacts_written,
            validation_summary=validation,
            qa_flags=sorted({IdentityQAFlag(value) for value in state.qa_flags}, key=str),
            warnings=sorted(set(state.warnings)),
        )
        output_bytes["speaker_identity_report.json"] = deterministic_json_bytes(report)
        with IdentityArtifactTransaction(root, job.context.run_id) as transaction:
            for name, data in sorted(output_bytes.items()):
                transaction.stage_bytes(name, data)
            transaction.publish()
        return ResolveIdentityResult(report=report, written_artifacts=tuple(sorted(output_bytes)))


def _acquire_resolver_inputs(
    job_dir: Path, options: ResolveIdentityOptions
) -> _AcquiredResolverInputs:
    """Acquire, hash, and parse every optional control input exactly once."""

    hashes: dict[str, str] = {}
    roster, roster_hash = load_optional_model(options.roster_path, SpeakerRoster)
    approved_map, approved_hash = load_optional_model(
        options.approved_map_path, ApprovedIdentityMap
    )
    media_report, media_hash = load_optional_model(
        options.media_review_report_path, MediaReviewReport
    )
    stability_config, stability_hash = load_optional_model(
        options.stability_config_path, StabilityConfig
    )
    try:
        from .enrollment import load_validated_rename_evidence

        rename_records: tuple[Any, ...] | None = tuple(
            load_validated_rename_evidence(job_dir)
        )
    except Exception:
        rename_records = None
    for name, digest in (
        ("roster", roster_hash),
        ("approved_map", approved_hash),
        ("media_review_report", media_hash),
        ("stability_config", stability_hash),
    ):
        if digest is not None:
            hashes[name] = digest
    if rename_records:
        hashes["rename_records"] = hashlib.sha256(
            deterministic_json_bytes(
                sorted(str(record.record_hash) for record in rename_records)
            )
        ).hexdigest()

    manifest_jobs: list[Path] = []
    manifest = read_optional_bytes(
        options.sibling_manifest,
        max_bytes=MAX_SIBLING_ARTIFACT_BYTES,
    )
    if manifest is not None:
        hashes["sibling_manifest"] = manifest.sha256
        try:
            payload = strict_json_bytes(manifest.data)
            if (
                not isinstance(payload, dict)
                or payload.get("schema_version") != "speaker_identity_sibling_manifest.v1"
                or not isinstance(payload.get("jobs"), list)
            ):
                raise ValueError("invalid sibling manifest")
        except Exception as exc:
            raise IdentityResolverError(
                "malformed_sibling_manifest", "Sibling manifest is invalid."
            ) from exc
        manifest_jobs = _bounded_manifest_jobs(
            payload["jobs"], max_jobs=options.policy.max_sibling_jobs
        )

    sibling_jobs = _discover_sibling_jobs(job_dir, options, manifest_jobs)
    sibling_reports: list[tuple[Path, ReadArtifact | None]] = []
    for index, sibling in enumerate(sibling_jobs):
        report_path = sibling / "speaker_identity_report.json"
        if not report_path.exists() and not report_path.is_symlink():
            continue
        artifact: ReadArtifact | None = None
        try:
            artifact = read_optional_bytes(
                report_path,
                max_bytes=MAX_SIBLING_ARTIFACT_BYTES,
            )
        except IdentityResolverError:
            artifact = None
        sibling_reports.append((sibling, artifact))
        if artifact is not None:
            hashes[f"sibling_{index}"] = artifact.sha256

    return _AcquiredResolverInputs(
        roster=roster,
        approved_map=approved_map,
        media_report=media_report,
        stability_config=stability_config,
        sibling_reports=tuple(sibling_reports),
        rename_records=rename_records,
        optional_hashes=hashes,
    )


def _validate_mode_inputs(
    job: LoadedIdentityJob,
    options: ResolveIdentityOptions,
    roster: SpeakerRoster | None,
    approved_map: ApprovedIdentityMap | None,
) -> None:
    if (
        options.policy.source_media_review == SourceMediaReviewMode.REVIEWED_REPORT
        and options.media_review_report_path is None
    ):
        raise IdentityResolverError(
            "media_review_report_required",
            "Reviewed-report mode requires a media review report.",
        )
    if (
        options.policy.source_media_review != SourceMediaReviewMode.REVIEWED_REPORT
        and options.media_review_report_path is not None
    ):
        raise IdentityResolverError(
            "media_review_report_wrong_mode",
            "Media review reports are consumed only in reviewed-report mode.",
        )
    drift = (
        DriftClassification.NONE
        if job.context.external_transcript is None
        else job.context.external_transcript.drift_classification
    )
    export_requested = (
        options.write_identity_mapped_exports or options.policy.mode != ResolverMode.REVIEW
    )
    if export_requested and drift not in {DriftClassification.NONE, DriftClassification.LABEL_ONLY}:
        raise IdentityResolverError(
            "external_transcript_drift",
            f"Identity export is blocked by {drift.value} external transcript drift.",
        )
    if options.policy.mode == ResolverMode.APPLY_APPROVED and approved_map is None:
        raise IdentityResolverError(
            "approved_map_required",
            "apply-approved requires --approved-map.",
        )
    if options.policy.mode != ResolverMode.APPLY_APPROVED and approved_map is not None:
        raise IdentityResolverError(
            "approved_map_wrong_mode",
            "Approved maps are consumed only in apply-approved mode.",
        )
    if options.known_speakers is not None and options.policy.mode != ResolverMode.REVIEW:
        if roster is None:
            raise IdentityResolverError(
                "roster_required",
                "Identity apply requires a complete roster when known speaker count is supplied.",
            )


def _validate_roster(
    job: LoadedIdentityJob,
    mode: ResolverMode,
    roster: SpeakerRoster | None,
    state: _ResolutionState,
) -> None:
    known = job.context.known_speakers
    if known is None:
        return
    expected = (
        0 if roster is None else sum(participant.expected for participant in roster.participants)
    )
    roster_count = None if roster is None else roster.expected_speaker_count
    complete = roster is not None and expected == known and roster_count in {None, known}
    if complete:
        return
    state.warnings.append(ReasonCode.ROSTER_MISSING_EXPECTED_NAMES.value)
    state.qa_flags.append(IdentityQAFlag.ROSTER_COUNT_CONFLICT.value)
    if mode != ResolverMode.REVIEW:
        raise IdentityResolverError(
            "roster_count_conflict",
            "Known speaker count and expected roster participants must match for identity apply.",
        )


def _validate_approved_map(
    job: LoadedIdentityJob,
    approved: ApprovedIdentityMap | None,
    canonical_hashes: Mapping[str, str],
    prior_report: IdentityReport | None,
) -> ApprovedIdentityMap | None:
    if approved is None:
        return None
    if approved.job_fingerprint != job.context.job_fingerprint:
        raise IdentityResolverError(
            "cross_job_approval", "Approved map does not belong to this canonical job."
        )
    for name, digest in approved.canonical_artifact_hashes.items():
        if canonical_hashes.get(name) != digest:
            raise IdentityResolverError(
                "stale_approval", "Approved map does not match current canonical artifact hashes."
            )
    if not approved.canonical_artifact_hashes:
        raise IdentityResolverError("stale_approval", "Approved map has no canonical hashes.")
    allowed_source_runs = set()
    if prior_report is not None:
        allowed_source_runs.add(prior_report.run_id)
        if prior_report.approval_source_run_id is not None:
            allowed_source_runs.add(prior_report.approval_source_run_id)
    if approved.resolver_run_id not in allowed_source_runs:
        raise IdentityResolverError(
            "stale_approval_run",
            "Approved map does not reference the current review decision run.",
        )
    return approved


def _load_prior_identity_report(job_dir: Path) -> IdentityReport | None:
    path = job_dir / "speaker_identity_report.json"
    if not path.exists() and not path.is_symlink():
        return None
    try:
        artifact = read_optional_bytes(path)
        if artifact is None:
            return None
        return IdentityReport.model_validate(strict_json_bytes(artifact.data))
    except Exception as exc:
        raise IdentityResolverError(
            "malformed_prior_identity_report",
            "The current identity report cannot validate an approved map.",
        ) from exc


def _stable_clusters(job: LoadedIdentityJob) -> list[str]:
    labels = {
        turn.speaker
        for turn in job.transcript.speaker_turns
        if turn.speaker and turn.speaker.upper() not in {"UNKNOWN", "UNATTRIBUTED"}
    }
    labels.update(
        turn.speaker
        for turn in job.transcript.diarization_turns
        if turn.speaker and turn.speaker.upper() not in {"UNKNOWN", "UNATTRIBUTED"}
    )
    return sorted(labels)


def _roster_names(roster: SpeakerRoster | None) -> set[str] | None:
    return (
        None
        if roster is None
        else {participant.name.casefold() for participant in roster.participants}
    )


def _candidate_allowed(name: str, roster: SpeakerRoster | None) -> bool:
    names = _roster_names(roster)
    return names is None or name.casefold() in names


def _collect_profile_evidence(
    job: LoadedIdentityJob,
    clusters: Sequence[str],
    roster: SpeakerRoster | None,
    policy: ResolverPolicy,
    state: _ResolutionState,
) -> None:
    artifact = job.artifact_bytes.get("speaker_map.json")
    if artifact is None:
        if "speaker_map.json" in job.context.malformed_artifacts:
            _profile_map_failed(state, "speaker_map_malformed")
            return
        state.backend_statuses.append(
            BackendStatus(
                backend="speechbrain-ecapa",
                status=BackendState.NOT_REQUESTED,
                safe_detail="speaker_map_absent",
            )
        )
        return
    try:
        payload = strict_json_bytes(artifact.data)
    except Exception:
        _profile_map_failed(state, "speaker_map_malformed")
        return
    try:
        validated = _validate_profile_map(job, payload, clusters, policy)
    except (ImportError, OSError, TypeError, ValueError):
        _profile_map_failed(state, "speaker_map_provenance_invalid")
        return
    for match in validated:
        if not _candidate_allowed(match.label, roster):
            continue
        safe = bool(
            not match.active_ambiguous
            and match.confidence >= max(policy.profile_threshold, policy.min_apply_confidence)
        )
        reason = (
            ReasonCode.LOCAL_PROFILE_MATCH
            if safe
            else ReasonCode.LOCAL_PROFILE_AMBIGUOUS
        )
        _add_candidate_source(
            job,
            state,
            scope=AssignmentScope.CLUSTER,
            scope_key=f"cluster:{match.speaker}",
            speaker=match.speaker,
            turn_index=None,
            candidate_name=match.label,
            tier=EvidenceTier.LOCAL_PROFILE,
            confidence=match.confidence,
            reason_codes=[reason],
            source_artifact_id="speaker_map_json",
            source_hash=artifact.sha256,
            safe_apply=safe,
        )
    state.backend_statuses.append(
        BackendStatus(
            backend="speechbrain-ecapa",
            status=BackendState.AVAILABLE,
            safe_detail="current_hash_bound_recomputed_ecapa",
        )
    )


def _profile_map_failed(state: _ResolutionState, detail: str) -> None:
    state.backend_statuses.append(
        BackendStatus(
            backend="speechbrain-ecapa",
            status=BackendState.FAILED,
            reason_code=ReasonCode.LOCAL_PROFILE_BACKEND_UNAVAILABLE,
            safe_detail=detail,
        )
    )
    state.qa_flags.append("identity_backend_unavailable")


def _validate_profile_map(
    job: LoadedIdentityJob,
    payload: Any,
    clusters: Sequence[str],
    policy: ResolverPolicy,
) -> list[_ValidatedProfileMatch]:
    """Recompute a v2 speaker map from current embeddings and active policy.

    ``speaker_map.json`` is compatibility output, not an approval.  Every field
    it asserts is therefore treated as untrusted provenance and checked against
    the current transcript, cluster-embedding artifact, and contained local
    ECAPA profiles before it can become safe resolver evidence.
    """

    if not isinstance(payload, dict) or payload.get("schema_version") != "speaker_map.v2":
        raise ValueError("speaker map schema is unsupported")
    raw_rows = payload.get("mappings")
    if not isinstance(raw_rows, list):
        raise ValueError("speaker map mappings must be a list")
    if payload.get("transcript_sha256") != job.transcript_artifact.sha256:
        raise ValueError("speaker map transcript provenance is stale")
    cluster_artifact = job.artifact_bytes.get("speaker_cluster_embeddings.json")
    if (
        cluster_artifact is None
        or payload.get("cluster_embeddings_sha256") != cluster_artifact.sha256
    ):
        raise ValueError("speaker map cluster provenance is stale")
    map_threshold = _strict_unit_float(payload.get("threshold"), "speaker map threshold")
    map_margin = _strict_unit_float(
        payload.get("ambiguity_margin"), "speaker map ambiguity margin"
    )
    raw_embeddings = strict_json_bytes(cluster_artifact.data)
    if not isinstance(raw_embeddings, dict):
        raise ValueError("cluster embeddings must be an object")

    from .enrollment import load_profile_embeddings, score_profiles

    profiles = load_profile_embeddings()
    if not profiles or not any(
        profile.get("evidence_eligible") is True for profile in profiles.values()
    ):
        raise ValueError("no current production profile evidence")

    expected_clusters = set(clusters)
    rows_by_speaker: dict[str, dict[str, Any]] = {}
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            raise ValueError("speaker map row must be an object")
        speaker = raw_row.get("speaker")
        if not isinstance(speaker, str):
            raise ValueError("speaker map cluster must be a string")
        if speaker.upper() in {"UNKNOWN", "UNATTRIBUTED"}:
            if (
                raw_row.get("label") != "UNKNOWN"
                or raw_row.get("source_profile_id") is not None
            ):
                raise ValueError("speaker map reserved-cluster row is inconsistent")
            continue
        if speaker not in expected_clusters:
            raise ValueError("speaker map contains an unknown cluster")
        if speaker in rows_by_speaker:
            raise ValueError("speaker map contains duplicate cluster rows")
        rows_by_speaker[speaker] = raw_row
    if set(rows_by_speaker) != expected_clusters:
        raise ValueError("speaker map does not cover every current cluster")

    validated: list[_ValidatedProfileMatch] = []
    for speaker in sorted(expected_clusters):
        raw_vector = raw_embeddings.get(speaker)
        if not isinstance(raw_vector, list):
            raise ValueError("speaker map lacks a current cluster embedding")
        alternatives = score_profiles(raw_vector, profiles)
        if not alternatives:
            raise ValueError("speaker map lacks current production alternatives")
        row = rows_by_speaker[speaker]
        _validate_profile_alternatives(row.get("alternatives"), alternatives[:3])
        best = alternatives[0]
        second = alternatives[1] if len(alternatives) > 1 else None
        produced_ambiguous = bool(
            second is not None and best["score"] - second["score"] < map_margin
        )
        active_ambiguous = bool(
            second is not None
            and best["score"] - second["score"] < policy.profile_ambiguity_margin
        )
        row_ambiguous = row.get("ambiguous")
        if not isinstance(row_ambiguous, bool) or row_ambiguous is not produced_ambiguous:
            raise ValueError("speaker map ambiguity provenance is inconsistent")
        label = row.get("label")
        source_profile_id = row.get("source_profile_id")
        backend = row.get("profile_backend")
        confidence = _strict_optional_confidence(row.get("confidence"))
        expected_concrete = best["score"] >= map_threshold and not produced_ambiguous
        if not expected_concrete:
            if (
                label != "UNKNOWN"
                or source_profile_id is not None
                or confidence != best["score"]
            ):
                raise ValueError("speaker map rejected-row provenance is inconsistent")
            continue
        if (
            label != best["name"]
            or source_profile_id != best["profile_id"]
            or backend != "speechbrain-ecapa"
            or best["backend"] != "speechbrain-ecapa"
            or confidence != best["score"]
            or row.get("reason") != "conservative_embedding_match"
        ):
            raise ValueError("speaker map profile provenance is inconsistent")
        validated.append(
            _ValidatedProfileMatch(
                speaker=speaker,
                label=str(label),
                confidence=confidence,
                active_ambiguous=active_ambiguous,
            )
        )
    return validated


def _strict_unit_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{label} must be finite and bounded")
    return parsed


def _strict_optional_confidence(value: Any) -> float:
    return _strict_unit_float(value, "speaker map confidence")


def _validate_profile_alternatives(
    reported: Any,
    expected: Sequence[Mapping[str, Any]],
) -> None:
    if not isinstance(reported, list) or len(reported) != len(expected):
        raise ValueError("speaker map alternatives are incomplete")
    for row, current in zip(reported, expected, strict=True):
        if not isinstance(row, dict):
            raise ValueError("speaker map alternative must be an object")
        score = _strict_unit_float(row.get("score"), "speaker map alternative score")
        if (
            row.get("name") != current["name"]
            or row.get("profile_id") != current["profile_id"]
            or row.get("backend") != current["backend"]
            or score != current["score"]
        ):
            raise ValueError("speaker map alternatives do not match current profiles")


def _collect_rename_evidence(
    job: LoadedIdentityJob,
    clusters: Sequence[str],
    roster: SpeakerRoster | None,
    records: Sequence[Any] | None,
    state: _ResolutionState,
) -> None:
    if records is None:
        state.warnings.append("legacy_rename_read_failed")
        records = ()
    for record in records:
        speaker = str(getattr(record, "speaker", ""))
        name = str(getattr(record, "name", ""))
        if speaker not in clusters or not name or not _candidate_allowed(name, roster):
            continue
        source_hash = str(getattr(record, "record_hash", "")) or None
        _add_candidate_source(
            job,
            state,
            scope=AssignmentScope.CLUSTER,
            scope_key=f"cluster:{speaker}",
            speaker=speaker,
            turn_index=None,
            candidate_name=name,
            tier=EvidenceTier.LEGACY_RENAME,
            confidence=1.0,
            reason_codes=[ReasonCode.LEGACY_EXPLICIT_RENAME],
            source_artifact_id="legacy_rename_record",
            source_hash=source_hash,
            safe_apply=True,
        )


def _collect_approved_evidence(
    job: LoadedIdentityJob,
    approved: ApprovedIdentityMap,
    clusters: Sequence[str],
    roster: SpeakerRoster | None,
    state: _ResolutionState,
) -> None:
    for speaker, name in sorted(approved.cluster_mappings.items()):
        if speaker not in clusters:
            raise IdentityResolverError(
                "approved_unknown_cluster", "Approved map contains an unknown speaker cluster."
            )
        if not _candidate_allowed(name, roster):
            raise IdentityResolverError(
                "approved_name_not_in_roster", "Approved identity is not present in the roster."
            )
        _add_candidate_source(
            job,
            state,
            scope=AssignmentScope.CLUSTER,
            scope_key=f"cluster:{speaker}",
            speaker=speaker,
            turn_index=None,
            candidate_name=name,
            tier=EvidenceTier.APPROVED_MAP,
            confidence=1.0,
            reason_codes=[ReasonCode.EXPLICIT_APPROVED_MAP],
            source_artifact_id="approved_map",
            source_hash=None,
            safe_apply=False,
            approved=True,
        )


def _collect_anchor_and_role_evidence(
    job: LoadedIdentityJob,
    clusters: Sequence[str],
    roster: SpeakerRoster | None,
    state: _ResolutionState,
) -> None:
    if roster is None:
        return
    aliases: dict[str, str] = {}
    for participant in roster.participants:
        for alias in [participant.name, *participant.aliases]:
            aliases[alias.casefold()] = participant.name
    for index, turn in enumerate(job.transcript.speaker_turns):
        if turn.speaker not in clusters or len(turn.text) > MAX_DIRECT_ANCHOR_TEXT:
            continue
        normalized_text = turn.text.casefold()
        self_match = re.search(r"\b(?:my name is|i am|i'm|im)\s+", normalized_text)
        self_name = (
            None
            if self_match is None
            else _match_roster_alias_prefix(normalized_text[self_match.end() :], aliases)
        )
        if self_name is not None:
            _add_candidate_source(
                job,
                state,
                scope=AssignmentScope.CLUSTER,
                scope_key=f"cluster:{turn.speaker}",
                speaker=turn.speaker,
                turn_index=index,
                candidate_name=self_name,
                tier=EvidenceTier.DIRECT_ANCHOR,
                confidence=0.80,
                reason_codes=[ReasonCode.SELF_IDENTIFICATION_ANCHOR],
                source_artifact_id="transcript_json",
                source_hash=job.transcript_artifact.sha256,
            )
        address_match = re.match(r"^\s*([^,:\n]{1,80})\s*[,,:]", normalized_text)
        address_name = (
            None if address_match is None else aliases.get(address_match.group(1).strip())
        )
        if address_name is not None and index + 1 < len(job.transcript.speaker_turns):
            next_turn = job.transcript.speaker_turns[index + 1]
            if next_turn.speaker in clusters and next_turn.speaker != turn.speaker:
                _add_candidate_source(
                    job,
                    state,
                    scope=AssignmentScope.CLUSTER,
                    scope_key=f"cluster:{next_turn.speaker}",
                    speaker=next_turn.speaker,
                    turn_index=index + 1,
                    candidate_name=address_name,
                    tier=EvidenceTier.DIRECT_ANCHOR,
                    confidence=0.70,
                    reason_codes=[ReasonCode.DIRECT_ADDRESS_ANCHOR],
                    source_artifact_id="transcript_json",
                    source_hash=job.transcript_artifact.sha256,
                )
    for cluster in clusters:
        for participant in roster.participants:
            if not participant.role:
                continue
            _add_candidate_source(
                job,
                state,
                scope=AssignmentScope.CLUSTER,
                scope_key=f"cluster:{cluster}",
                speaker=cluster,
                turn_index=None,
                candidate_name=participant.name,
                tier=EvidenceTier.ROLE,
                confidence=0.20,
                reason_codes=[ReasonCode.ROLE_INFERENCE_ONLY],
                source_artifact_id="roster",
                source_hash=None,
            )


def _match_roster_alias_prefix(text: str, aliases: Mapping[str, str]) -> str | None:
    bounded = text[: MAX_DISPLAY_LABEL_LENGTH + 1]
    matches = [
        (alias, name)
        for alias, name in aliases.items()
        if bounded.startswith(alias)
        and (len(bounded) == len(alias) or not bounded[len(alias)].isalnum())
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: (len(item[0]), item[0]))[1]


def _collect_sibling_evidence(
    job: LoadedIdentityJob,
    sibling_reports: Sequence[tuple[Path, ReadArtifact | None]],
    state: _ResolutionState,
) -> None:
    for _sibling, artifact in sibling_reports:
        if artifact is None:
            state.warnings.append(ReasonCode.SIBLING_EVIDENCE_STALE.value)
            continue
        try:
            report = IdentityReport.model_validate(strict_json_bytes(artifact.data))
        except Exception:
            state.warnings.append(ReasonCode.SIBLING_EVIDENCE_STALE.value)
            continue
        current_hash = job.transcript_artifact.sha256
        sibling_hash = next(
            (
                item.sha256
                for item in report.resolver_context.artifacts
                if item.relative_path == "transcript.json"
            ),
            None,
        )
        linked = bool(
            sibling_hash == current_hash
            or (
                job.context.input_hash is not None
                and report.resolver_context.input_hash == job.context.input_hash
            )
        )
        if not linked:
            state.warnings.append(ReasonCode.SIBLING_EVIDENCE_STALE.value)
            continue
        # A sibling report can establish that a related local artifact was
        # inspected, but SPEAKER_## labels are recording-local partitions.  A
        # matching anonymous label -- even for the same input hash -- is not a
        # correspondence proof and must never transfer a person's name.
        scope_key = f"sibling:{artifact.sha256[:16]}"
        evidence_id = stable_digest(
            {
                "transcript_hash": job.transcript_artifact.sha256,
                "scope": AssignmentScope.BACKEND.value,
                "scope_key": scope_key,
                "tier": EvidenceTier.SIBLING.value,
                "source_hash": artifact.sha256,
            },
            prefix="ev",
        )
        if evidence_id not in state.evidence_ids:
            state.evidence.append(
                IdentityEvidence(
                    evidence_id=evidence_id,
                    assignment_scope=AssignmentScope.BACKEND,
                    scope_key=scope_key,
                    tier=EvidenceTier.SIBLING,
                    source_artifact_id=scope_key,
                    source_hash=artifact.sha256,
                    artifact_metadata={
                        "link_basis": (
                            "canonical_transcript_hash"
                            if sibling_hash == current_hash
                            else "input_hash"
                        )
                    },
                    confidence=0.0,
                    action="review_only_no_anonymous_label_transfer",
                    reason_codes=[ReasonCode.INSUFFICIENT_EVIDENCE],
                )
            )
            state.evidence_ids.add(evidence_id)


def _discover_sibling_jobs(
    job_dir: Path,
    options: ResolveIdentityOptions,
    manifest_jobs: Sequence[Path] = (),
) -> list[Path]:
    candidates: list[Path] = []
    candidate_keys: set[str] = set()
    for candidate in (*options.sibling_jobs, *manifest_jobs):
        _append_sibling_candidate(
            candidates,
            candidate_keys,
            candidate,
            max_jobs=options.policy.max_sibling_jobs,
        )
    if options.sibling_root is not None:
        root = options.sibling_root.expanduser()
        if root.is_symlink():
            raise IdentityResolverError("unsafe_sibling_root", "Sibling root cannot be a symlink.")
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise IdentityResolverError(
                "missing_sibling_root", "Sibling root is unavailable."
            ) from exc
        home = Path.home().resolve()
        if resolved == home:
            raise IdentityResolverError(
                "broad_sibling_root", "Home-directory sibling scans are forbidden."
            )
        try:
            inspected = 0
            for path in resolved.iterdir():
                inspected += 1
                if inspected > MAX_SIBLING_ROOT_ENTRIES:
                    raise IdentityResolverError(
                        "sibling_root_too_large",
                        "Sibling root exceeds the bounded entry limit.",
                    )
                if not path.is_dir():
                    continue
                _append_sibling_candidate(
                    candidates,
                    candidate_keys,
                    path,
                    max_jobs=options.policy.max_sibling_jobs,
                )
        except OSError as exc:
            raise IdentityResolverError(
                "unreadable_sibling_root", "Sibling root could not be read safely."
            ) from exc
    resolved_jobs: list[Path] = []
    for candidate in candidates:
        if candidate.is_symlink():
            raise IdentityResolverError(
                "unsafe_sibling_symlink", "Sibling jobs cannot be symlinks."
            )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise IdentityResolverError(
                "missing_sibling_job", "A requested sibling job is unavailable."
            ) from exc
        if not resolved.is_dir() or resolved == job_dir:
            continue
        resolved_jobs.append(resolved)
    unique = sorted(set(resolved_jobs), key=lambda path: stable_digest(path.name, prefix="s"))
    if len(unique) > options.policy.max_sibling_jobs:
        raise IdentityResolverError(
            "too_many_siblings", "Sibling job count exceeds the configured limit."
        )
    return unique


def _bounded_manifest_jobs(values: Sequence[Any], *, max_jobs: int) -> list[Path]:
    jobs: list[Path] = []
    keys: set[str] = set()
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > MAX_SIBLING_PATH_LENGTH
            or "\x00" in value
        ):
            raise IdentityResolverError(
                "malformed_sibling_manifest", "Sibling manifest paths are invalid."
            )
        _append_sibling_candidate(jobs, keys, Path(value), max_jobs=max_jobs)
    return jobs


def _append_sibling_candidate(
    candidates: list[Path],
    keys: set[str],
    candidate: Path,
    *,
    max_jobs: int,
) -> None:
    expanded = candidate.expanduser()
    key = str(expanded)
    if not key or len(key) > MAX_SIBLING_PATH_LENGTH or "\x00" in key:
        raise IdentityResolverError("invalid_sibling_path", "A sibling path is invalid.")
    if key in keys:
        return
    if len(candidates) >= max_jobs:
        raise IdentityResolverError(
            "too_many_siblings", "Sibling job count exceeds the configured limit."
        )
    keys.add(key)
    candidates.append(expanded)


def _add_candidate_source(
    job: LoadedIdentityJob,
    state: _ResolutionState,
    *,
    scope: AssignmentScope,
    scope_key: str,
    speaker: str | None,
    turn_index: int | None,
    candidate_name: str,
    tier: EvidenceTier,
    confidence: float,
    reason_codes: list[ReasonCode],
    source_artifact_id: str,
    source_hash: str | None,
    safe_apply: bool = False,
    approved: bool = False,
) -> None:
    name = normalize_display_label(candidate_name, allow_reserved=approved)
    confidence = min(1.0, max(0.0, confidence if math.isfinite(confidence) else 0.0))
    evidence_payload = {
        "transcript_hash": job.transcript_artifact.sha256,
        "scope": scope.value,
        "scope_key": scope_key,
        "candidate": name,
        "tier": tier.value,
        "reasons": sorted(reason.value for reason in reason_codes),
        "source_hash": source_hash,
    }
    evidence_id = stable_digest(evidence_payload, prefix="ev")
    evidence = IdentityEvidence(
        evidence_id=evidence_id,
        assignment_scope=scope,
        scope_key=scope_key,
        speaker=speaker,
        turn_index=turn_index,
        candidate_name=name,
        tier=tier,
        source_artifact_id=source_artifact_id,
        source_hash=source_hash,
        artifact_metadata={},
        span=None,
        confidence=confidence,
        action="apply_eligible" if safe_apply or approved else "review_candidate",
        reason_codes=reason_codes,
    )
    if evidence_id not in state.evidence_ids:
        state.evidence.append(evidence)
        state.evidence_ids.add(evidence_id)
    state.candidate_sources[scope_key].append(
        _CandidateSource(
            name=name,
            confidence=confidence,
            tier=tier,
            reason_codes=reason_codes,
            evidence_id=evidence_id,
            safe_apply=safe_apply,
            approved=approved,
        )
    )


def _resolve_clusters(
    job: LoadedIdentityJob,
    clusters: Sequence[str],
    policy: ResolverPolicy,
    state: _ResolutionState,
) -> list[IdentityDecision]:
    decisions: list[IdentityDecision] = []
    for cluster in clusters:
        scope_key = f"cluster:{cluster}"
        sources = state.candidate_sources.get(scope_key, [])
        grouped = _group_sources(sources)
        for name, candidate_sources in grouped.items():
            candidate = _candidate_model(
                scope_key,
                name,
                candidate_sources,
                competing_names=set(grouped) - {name},
            )
            state.candidates.append(candidate)
        approved_sources = [source for source in sources if source.approved]
        safe_sources = [source for source in sources if source.safe_apply]
        apply_sources = (
            approved_sources
            if policy.mode == ResolverMode.APPLY_APPROVED and approved_sources
            else safe_sources
        )
        selected_pool = (
            apply_sources
            if policy.mode != ResolverMode.REVIEW
            else [source for source in sources if source.safe_apply or source.approved]
        )
        selected_names = {source.name for source in selected_pool}
        status: ResolutionStatus
        display_label: str | None
        confidence: float
        reasons: list[ReasonCode]
        evidence_ids: list[str]
        conflicts: list[str] = []
        rejections: list[CandidateRejection] = []
        requires_approval = False
        if len(selected_names) > 1:
            status = ResolutionStatus.CONFLICT
            display_label = None
            confidence = max(source.confidence for source in selected_pool)
            reasons = [ReasonCode.COMPETING_CANDIDATE_REJECTED]
            evidence_ids = sorted({source.evidence_id for source in selected_pool})
            conflicts = sorted(selected_names)
            rejections = _candidate_rejections(grouped, retained_names=set())
            state.qa_flags.append("identity_conflict")
        elif len(selected_names) == 1:
            display_label = next(iter(selected_names))
            chosen = [source for source in selected_pool if source.name == display_label]
            status = ResolutionStatus.MAPPED
            confidence = max(source.confidence for source in chosen)
            reasons = sorted(
                {reason for source in chosen for reason in source.reason_codes}, key=str
            )
            evidence_ids = sorted({source.evidence_id for source in chosen})
            rejections = _candidate_rejections(grouped, retained_names={display_label})
        elif grouped:
            ordered = sorted(
                grouped.items(),
                key=lambda item: (
                    -max(source.confidence for source in item[1]),
                    item[0].casefold(),
                ),
            )
            display_label = None
            confidence = max(source.confidence for source in ordered[0][1])
            evidence_ids = sorted(source.evidence_id for source in ordered[0][1])
            reasons = sorted(
                {reason for source in ordered[0][1] for reason in source.reason_codes}, key=str
            )
            status = ResolutionStatus.REVIEW
            requires_approval = True
            rejections = _candidate_rejections(
                grouped,
                retained_names={ordered[0][0]},
            )
            state.qa_flags.append("identity_low_confidence")
        else:
            status = ResolutionStatus.UNRESOLVED
            display_label = None
            confidence = 0.0
            evidence_ids = []
            reasons = [ReasonCode.INSUFFICIENT_EVIDENCE]
        decision_id = _decision_id(job, AssignmentScope.CLUSTER, scope_key)
        decisions.append(
            IdentityDecision(
                decision_id=decision_id,
                assignment_scope=AssignmentScope.CLUSTER,
                scope_key=scope_key,
                speaker=cluster,
                status=status,
                display_label=display_label,
                confidence=confidence,
                evidence_ids=evidence_ids,
                conflicts=conflicts,
                rejections=rejections,
                reason_codes=reasons,
                requires_approval=requires_approval,
            )
        )
    return decisions


def _group_sources(sources: Sequence[_CandidateSource]) -> dict[str, list[_CandidateSource]]:
    grouped: dict[str, list[_CandidateSource]] = defaultdict(list)
    for source in sources:
        grouped[source.name].append(source)
    return dict(grouped)


def _candidate_model(
    scope_key: str,
    name: str,
    sources: Sequence[_CandidateSource],
    *,
    competing_names: set[str],
) -> IdentityCandidate:
    safe = any(source.safe_apply or source.approved for source in sources)
    return IdentityCandidate(
        candidate_id=stable_digest({"scope_key": scope_key, "candidate": name}, prefix="cand"),
        assignment_scope=AssignmentScope.CLUSTER,
        scope_key=scope_key,
        candidate_name=name,
        status=ResolutionStatus.MAPPED if safe else ResolutionStatus.REVIEW,
        confidence=max(source.confidence for source in sources),
        strongest_tier=min(
            (source.tier for source in sources), key=lambda tier: list(EvidenceTier).index(tier)
        ),
        evidence_ids=sorted({source.evidence_id for source in sources}),
        conflicts=sorted(competing_names),
        reason_codes=sorted(
            {reason for source in sources for reason in source.reason_codes}, key=str
        ),
        requires_approval=not safe,
    )


def _candidate_rejections(
    grouped: Mapping[str, Sequence[_CandidateSource]],
    *,
    retained_names: set[str],
) -> list[CandidateRejection]:
    rejections: list[CandidateRejection] = []
    for name in sorted(set(grouped) - retained_names, key=str.casefold):
        sources = grouped[name]
        rejections.append(
            CandidateRejection(
                candidate_name=name,
                confidence=max(source.confidence for source in sources),
                evidence_ids=sorted({source.evidence_id for source in sources}),
                reason_codes=sorted(
                    {
                        ReasonCode.COMPETING_CANDIDATE_REJECTED,
                        *(reason for source in sources for reason in source.reason_codes),
                    },
                    key=str,
                ),
            )
        )
    return rejections


def _enforce_unique_cluster_names(
    decisions: list[IdentityDecision], state: _ResolutionState
) -> None:
    by_name: dict[str, list[int]] = defaultdict(list)
    for index, decision in enumerate(decisions):
        if decision.status == ResolutionStatus.MAPPED and decision.display_label:
            by_name[decision.display_label.casefold()].append(index)
    for indexes in by_name.values():
        if len(indexes) <= 1:
            continue
        labels = [decisions[index].speaker or "cluster" for index in indexes]
        for index in indexes:
            decision = decisions[index]
            rejected = CandidateRejection(
                candidate_name=decision.display_label or "Unattributed",
                confidence=decision.confidence,
                evidence_ids=decision.evidence_ids,
                reason_codes=sorted(
                    {*decision.reason_codes, ReasonCode.COMPETING_CANDIDATE_REJECTED},
                    key=str,
                ),
            )
            decisions[index] = decision.model_copy(
                update={
                    "status": ResolutionStatus.CONFLICT,
                    "display_label": None,
                    "conflicts": labels,
                    "rejections": [*decision.rejections, rejected],
                    "reason_codes": [ReasonCode.COMPETING_CANDIDATE_REJECTED],
                    "requires_approval": True,
                }
            )
        state.qa_flags.append("identity_conflict")


def _resolve_turns(
    job: LoadedIdentityJob,
    cluster_decisions: Sequence[IdentityDecision],
    approved: ApprovedIdentityMap | None,
    reviewed_media: Mapping[str, set[str]],
    unstable_turns: set[int],
    policy: ResolverPolicy,
    state: _ResolutionState,
) -> list[IdentityDecision]:
    clusters = {decision.speaker: decision for decision in cluster_decisions}
    approved_turns = (
        {}
        if approved is None
        else {override.turn_index: override for override in approved.turn_overrides}
    )
    if any(index >= len(job.transcript.speaker_turns) for index in approved_turns):
        raise IdentityResolverError(
            "approved_unknown_turn", "Approved map contains an out-of-range turn override."
        )
    decisions: list[IdentityDecision] = []
    last_vad_end = _last_vad_end(job)
    for index, (turn, provenance) in enumerate(
        zip(job.transcript.speaker_turns, job.turn_provenance, strict=True)
    ):
        is_fragment = bool(
            provenance.folded_unknown
            or provenance.uncertain
            or provenance.canonical_label.upper() == "UNKNOWN"
        )
        scope = AssignmentScope.FRAGMENT if is_fragment else AssignmentScope.TURN
        scope_key = f"{scope.value}:{index}:{turn.start:.3f}:{turn.end:.3f}"
        decision_id = _decision_id(job, scope, scope_key)
        parent = clusters.get(turn.speaker)
        target_label = _target_label(job, index, turn.speaker)
        reasons: list[ReasonCode] = []
        status = ResolutionStatus.UNRESOLVED
        display_label: str | None = None
        confidence = 0.0
        evidence_ids: list[str] = []
        local_override: str | None = None
        requires_media = False
        requires_approval = False
        structural = policy.turn_boundary_repair
        if target_label != turn.speaker:
            reasons.append(ReasonCode.DERIVED_LABEL_DRIFT)
        if provenance.folded_unknown:
            reasons.append(ReasonCode.CANONICAL_UNKNOWN_FOLDED_INTO_CLUSTER)

        override = approved_turns.get(index)
        if override is not None:
            if override.decision_id != decision_id:
                raise IdentityResolverError(
                    "stale_turn_approval", "Approved turn decision ID is stale."
                )
            display_label = override.display_label
            local_override = override.display_label
            status = (
                ResolutionStatus.UNATTRIBUTED
                if override.display_label.casefold() == policy.unattributed_label.casefold()
                else ResolutionStatus.MAPPED
            )
            confidence = 1.0
            reasons.extend([ReasonCode.EXPLICIT_APPROVED_MAP, ReasonCode.TURN_LEVEL_OVERRIDE])
        elif is_fragment:
            no_coverage = not _turn_has_diarization_coverage(turn.start, turn.end, job)
            post_tail = last_vad_end is not None and turn.start >= last_vad_end
            reviewed_conclusions = reviewed_media.get(decision_id, set())
            if (
                "tail_artifact" in reviewed_conclusions
                or "unattributed_recommended" in reviewed_conclusions
            ):
                status = ResolutionStatus.UNATTRIBUTED
                display_label = policy.unattributed_label
                local_override = policy.unattributed_label
                confidence = 1.0
                reasons.append(
                    ReasonCode.SOURCE_MEDIA_TAIL_ARTIFACT
                    if "tail_artifact" in reviewed_conclusions
                    else ReasonCode.UNATTRIBUTED_MORE_HONEST
                )
            elif no_coverage or post_tail:
                status = ResolutionStatus.UNATTRIBUTED
                display_label = policy.unattributed_label
                local_override = policy.unattributed_label
                confidence = 0.0
                reasons.append(
                    ReasonCode.NO_DIARIZATION_COVERAGE if no_coverage else ReasonCode.POST_VAD_TAIL
                )
                reasons.append(ReasonCode.UNATTRIBUTED_MORE_HONEST)
                requires_media = True
                state.qa_flags.extend(
                    ["identity_no_diarization_coverage", "identity_unattributed_more_honest"]
                )
            elif _same_speaker_bounded_fragment(job, index, policy):
                reasons.extend(
                    [
                        ReasonCode.SAME_SPEAKER_BOUNDED_FRAGMENT,
                        ReasonCode.REQUIRES_TURN_LEVEL_OVERRIDE,
                    ]
                )
                status = ResolutionStatus.REVIEW
                confidence = 0.70
                requires_approval = True
                if _phrase_continuation(job, index):
                    reasons.append(ReasonCode.PHRASE_CONTINUATION_OVERRIDE)
                if _question_answer_boundary(job, index):
                    reasons.append(ReasonCode.QUESTION_ANSWER_BOUNDARY_OVERRIDE)
            else:
                reasons.append(ReasonCode.INSUFFICIENT_EVIDENCE)
                requires_media = True
                state.qa_flags.append("identity_media_review_required")
        elif index in unstable_turns:
            status = ResolutionStatus.REVIEW
            confidence = 0.0
            reasons.extend(
                [ReasonCode.DIARIZATION_UNSTABLE_BOUNDARY, ReasonCode.ENSEMBLE_DISAGREEMENT]
            )
            requires_approval = True
            requires_media = True
            state.qa_flags.append("identity_diarization_unstable")
        elif parent is not None and parent.status == ResolutionStatus.MAPPED:
            status = ResolutionStatus.MAPPED
            display_label = parent.display_label
            confidence = parent.confidence
            evidence_ids = list(parent.evidence_ids)
            reasons.extend(parent.reason_codes)
        else:
            status = (
                ResolutionStatus.REVIEW
                if parent and parent.status == ResolutionStatus.REVIEW
                else ResolutionStatus.UNRESOLVED
            )
            confidence = 0.0 if parent is None else parent.confidence
            evidence_ids = [] if parent is None else list(parent.evidence_ids)
            reasons.append(ReasonCode.INSUFFICIENT_EVIDENCE)
            requires_approval = status == ResolutionStatus.REVIEW

        if _mixed_turn(turn):
            reasons.extend(
                [
                    ReasonCode.MIXED_TURN_BOUNDARY,
                    ReasonCode.PARTIAL_LINE_SPEAKER_CHANGE,
                    ReasonCode.SPLIT_RECOMMENDED,
                ]
            )
            if override is None and status == ResolutionStatus.MAPPED:
                # One canonical turn contains words attributed to multiple
                # acoustic speakers.  A cluster-level person label is therefore
                # unsafe for the whole line; only an explicit turn approval may
                # override the conservative anonymous rendering.
                status = ResolutionStatus.REVIEW
                display_label = None
                reasons.append(ReasonCode.REQUIRES_TURN_LEVEL_OVERRIDE)
                requires_approval = True
                requires_media = True
            if policy.turn_boundary_repair == StructuralRepairMode.LABEL_ONLY:
                reasons.append(ReasonCode.LABEL_ONLY_FALLBACK)
            state.qa_flags.append("identity_split_recommended")
        if not reasons:
            reasons = [ReasonCode.INSUFFICIENT_EVIDENCE]
        decisions.append(
            IdentityDecision(
                decision_id=decision_id,
                parent_decision_id=None if parent is None else parent.decision_id,
                assignment_scope=scope,
                scope_key=scope_key,
                speaker=turn.speaker,
                turn_index=index,
                member_turn_indexes=[index] if is_fragment else [],
                original_diarization_label=provenance.original_diarization_label,
                canonical_label=provenance.canonical_label,
                target_label=target_label,
                source_label_provenance=provenance.source_label_provenance,
                status=status,
                display_label=display_label,
                mapped_global_candidate=None if parent is None else parent.display_label,
                local_override_candidate=local_override,
                confidence=confidence,
                evidence_ids=evidence_ids,
                reason_codes=sorted(set(reasons), key=str),
                structural_repair_mode=structural,
                requires_media_review=requires_media,
                requires_approval=requires_approval,
            )
        )
    return decisions


def _reviewed_media_by_decision(
    report: MediaReviewReport,
    job: LoadedIdentityJob,
    expected_queue: MediaReviewQueue,
    allowed_source_runs: set[str],
) -> dict[str, set[str]]:
    if report.job_fingerprint != job.context.job_fingerprint:
        raise IdentityResolverError(
            "cross_job_media_report", "Media review report does not belong to this job."
        )
    if report.run_id not in allowed_source_runs:
        raise IdentityResolverError(
            "stale_media_review_run",
            "Media review report does not reference the current resolver input lineage.",
        )
    queued = {region.region_id: region for region in expected_queue.regions}
    by_decision: dict[str, set[str]] = {}
    seen: set[str] = set()
    for region in report.regions:
        if region.region_id in seen:
            raise IdentityResolverError(
                "duplicate_media_region", "Media review region IDs must be unique."
            )
        seen.add(region.region_id)
        queued_region = queued.get(region.region_id)
        if queued_region is None:
            raise IdentityResolverError(
                "unknown_media_region",
                "Media review region is not a current queued resolver decision.",
            )
        expected_region_id = "media:" + queued_region.decision_id.split(":", 1)[1]
        if region.region_id != expected_region_id:
            raise IdentityResolverError(
                "media_decision_mismatch",
                "Media review region does not match its queued decision.",
            )
        by_decision[queued_region.decision_id] = set(region.conclusions)
    return by_decision


def _link_decision_graph(
    clusters: Sequence[IdentityDecision], turns: Sequence[IdentityDecision]
) -> list[IdentityDecision]:
    child_ids: dict[str, list[str]] = defaultdict(list)
    for decision in turns:
        if decision.parent_decision_id:
            child_ids[decision.parent_decision_id].append(decision.decision_id)
    return [
        decision.model_copy(
            update={"child_decision_ids": sorted(child_ids.get(decision.decision_id, []))}
        )
        for decision in clusters
    ]


def _build_audit(
    job: LoadedIdentityJob,
    clusters: Sequence[IdentityDecision],
    turns: Sequence[IdentityDecision],
    state: _ResolutionState,
) -> AttributionAudit:
    evidence_by_id = {item.evidence_id: item for item in state.evidence}
    rows: list[AttributionAuditRow] = []
    for decision in [*clusters, *turns]:
        turn = (
            None
            if decision.turn_index is None
            else job.transcript.speaker_turns[decision.turn_index]
        )
        provenance = (
            None if decision.turn_index is None else job.turn_provenance[decision.turn_index]
        )
        evidence = [
            evidence_by_id[evidence_id]
            for evidence_id in decision.evidence_ids
            if evidence_id in evidence_by_id
        ]
        display = (
            decision.display_label or decision.canonical_label or decision.speaker or "UNKNOWN"
        )
        row = AttributionAuditRow(
            decision_id=decision.decision_id,
            parent_decision_id=decision.parent_decision_id,
            child_decision_ids=decision.child_decision_ids,
            assignment_scope=decision.assignment_scope,
            source_line=_source_line(job, decision.turn_index),
            turn_index=decision.turn_index,
            word_index_start=None if provenance is None else provenance.word_index_start,
            word_index_end=None if provenance is None else provenance.word_index_end,
            start=None if turn is None else turn.start,
            end=None if turn is None else turn.end,
            duration_seconds=None if turn is None else max(0.0, turn.end - turn.start),
            word_count=None if turn is None else len(turn.words),
            original_diarization_label=decision.original_diarization_label or decision.speaker,
            canonical_label=decision.canonical_label or decision.speaker,
            target_label=decision.target_label or decision.speaker,
            source_label_provenance=decision.source_label_provenance,
            canonical_label_was_unknown=(decision.canonical_label or "").upper() == "UNKNOWN",
            drift_classification=(
                DriftClassification.NONE
                if job.context.external_transcript is None
                else job.context.external_transcript.drift_classification
            ),
            assigned_display_label=display,
            mapped_global_candidate=decision.mapped_global_candidate,
            local_override_candidate=decision.local_override_candidate,
            resolution_status=decision.status,
            confidence=decision.confidence,
            evidence_type=evidence[0].tier.value if evidence else "none",
            evidence_reason=decision.reason_codes[0].value,
            evidence_ids=decision.evidence_ids,
            artifact_metadata_used=sorted({item.source_artifact_id for item in evidence}),
            competing_candidate=(
                decision.rejections[0].candidate_name
                if decision.rejections
                else (decision.conflicts[0] if decision.conflicts else None)
            ),
            competing_candidate_rejection_reason=(
                ReasonCode.COMPETING_CANDIDATE_REJECTED.value
                if decision.rejections or decision.conflicts
                else None
            ),
            unattributed_is_more_honest=decision.status == ResolutionStatus.UNATTRIBUTED,
            structural_repair_mode=decision.structural_repair_mode,
            action=(
                "apply_display_label"
                if decision.status in {ResolutionStatus.MAPPED, ResolutionStatus.UNATTRIBUTED}
                else "retain_anonymous"
            ),
            requires_media_review=decision.requires_media_review,
            requires_human_approval=decision.requires_approval,
            non_label_text_preserved=(
                job.context.external_transcript is None
                or job.context.external_transcript.non_label_text_matches
            ),
            label_join_regression_checked=True,
            reason_codes=decision.reason_codes,
        )
        rows.append(row)
    return AttributionAudit(run_id=job.context.run_id, rows=rows)


def _validate_decisions_and_audit(
    job: LoadedIdentityJob,
    clusters: Sequence[IdentityDecision],
    turns: Sequence[IdentityDecision],
    audit: AttributionAudit,
    canonical_hashes: Mapping[str, str],
) -> IdentityValidationSummary:
    expected = [decision.decision_id for decision in [*clusters, *turns]]
    actual = [row.decision_id for row in audit.rows]
    duplicate = sorted(value for value, count in Counter(actual).items() if count > 1)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    errors: list[str] = []
    if duplicate:
        errors.append("duplicate_audit_decisions")
    if missing:
        errors.append("missing_audit_decisions")
    if extra:
        errors.append("extra_audit_decisions")
    if len(turns) != len(job.transcript.speaker_turns):
        errors.append("turn_cardinality_mismatch")
    cluster_by_id = {decision.decision_id: decision for decision in clusters}
    for decision in turns:
        if decision.parent_decision_id is None:
            continue
        parent = cluster_by_id.get(decision.parent_decision_id)
        if parent is None or decision.decision_id not in parent.child_decision_ids:
            errors.append("nonreciprocal_decision_link")
            break
    external = job.context.external_transcript
    canonical_unchanged = verify_canonical_hashes(job, canonical_hashes)
    valid = not errors and canonical_unchanged
    return IdentityValidationSummary(
        valid=valid,
        audit_complete=not (duplicate or missing or extra),
        expected_decision_ids=expected,
        actual_decision_ids=actual,
        duplicate_decision_ids=duplicate,
        missing_decision_ids=missing,
        external_transcript_drift_classification=(
            DriftClassification.NONE if external is None else external.drift_classification
        ),
        line_count_preserved=external is None or external.line_count_matches,
        turn_count_preserved=len(turns) == len(job.transcript.speaker_turns),
        non_label_text_preserved=external is None or external.non_label_text_matches,
        label_join_regression_checked=True,
        canonical_unknown_folded_into_cluster_count=sum(
            item.folded_unknown for item in job.turn_provenance
        ),
        canonical_hashes_unchanged=canonical_unchanged,
        errors=errors,
    )


def _build_media_queue(
    job: LoadedIdentityJob,
    turns: Sequence[IdentityDecision],
    *,
    run_id: str | None = None,
) -> MediaReviewQueue:
    regions = []
    duration = _job_duration(job)
    for decision in turns:
        if not decision.requires_media_review or decision.turn_index is None:
            continue
        turn = job.transcript.speaker_turns[decision.turn_index]
        regions.append(
            MediaReviewRegion(
                region_id="media:" + decision.decision_id.split(":", 1)[1],
                decision_id=decision.decision_id,
                turn_index=decision.turn_index,
                start=max(0.0, turn.start - 0.5),
                end=min(duration, max(turn.start, turn.end + 0.5)),
                original_speaker=turn.speaker,
                reason_codes=decision.reason_codes,
                questions=[
                    "is_real_speech",
                    "is_visible_speaker_identifiable",
                    "is_unattributed_more_honest",
                ],
            )
        )
    return MediaReviewQueue(run_id=run_id or job.context.run_id, regions=regions)


def _media_source_run_ids(
    job: LoadedIdentityJob,
    options: ResolveIdentityOptions,
    optional_hashes: Mapping[str, str],
) -> set[str]:
    """Return valid queue-producing lineages for an ingested review report."""

    source_hashes = dict(optional_hashes)
    source_hashes.pop("media_review_report", None)
    external_hash = (
        None
        if job.context.external_transcript is None
        else job.context.external_transcript.artifact.sha256
    )
    run_ids: set[str] = set()
    for media_mode in (SourceMediaReviewMode.NONE, SourceMediaReviewMode.QUEUE):
        source_policy = options.policy.model_copy(update={"source_media_review": media_mode})
        run_ids.add(
            stable_digest(
                {
                    "job_fingerprint": job.context.job_fingerprint,
                    "artifact_hashes": {
                        name: artifact.sha256
                        for name, artifact in sorted(job.artifact_bytes.items())
                    },
                    "policy": source_policy.model_dump(mode="json"),
                    "known_speakers": options.known_speakers,
                    "external_hash": external_hash,
                    "optional_input_hashes": dict(sorted(source_hashes.items())),
                },
                prefix="run",
            )
        )
    return run_ids


_REASON_QA_FLAGS: dict[ReasonCode, IdentityQAFlag] = {
    ReasonCode.ROLE_INFERENCE_ONLY: IdentityQAFlag.ROLE_INFERENCE_ONLY,
    ReasonCode.FORCED_PROXIMITY_ONLY: IdentityQAFlag.FORCED_PROXIMITY_ONLY,
    ReasonCode.NO_DIARIZATION_COVERAGE: IdentityQAFlag.NO_DIARIZATION_COVERAGE,
    ReasonCode.POST_VAD_TAIL: IdentityQAFlag.POST_VAD_TAIL,
    ReasonCode.DERIVED_LABEL_DRIFT: IdentityQAFlag.DERIVED_LABEL_DRIFT,
    ReasonCode.CANONICAL_UNKNOWN_FOLDED_INTO_CLUSTER: (IdentityQAFlag.CANONICAL_UNKNOWN_FOLDED),
    ReasonCode.REQUIRES_TURN_LEVEL_OVERRIDE: IdentityQAFlag.TURN_LEVEL_OVERRIDE_REQUIRED,
    ReasonCode.PHRASE_CONTINUATION_OVERRIDE: IdentityQAFlag.PHRASE_CONTINUATION_OVERRIDE,
    ReasonCode.QUESTION_ANSWER_BOUNDARY_OVERRIDE: (
        IdentityQAFlag.QUESTION_ANSWER_BOUNDARY_OVERRIDE
    ),
    ReasonCode.MIXED_TURN_BOUNDARY: IdentityQAFlag.MIXED_TURN_BOUNDARY,
    ReasonCode.PARTIAL_LINE_SPEAKER_CHANGE: IdentityQAFlag.PARTIAL_LINE_SPEAKER_CHANGE,
    ReasonCode.LABEL_ONLY_FALLBACK: IdentityQAFlag.LABEL_ONLY_FALLBACK,
    ReasonCode.SPLIT_RECOMMENDED: IdentityQAFlag.SPLIT_RECOMMENDED,
    ReasonCode.SOURCE_MEDIA_REVIEW_REQUIRED: IdentityQAFlag.MEDIA_REVIEW_REQUIRED,
    ReasonCode.SOURCE_MEDIA_TAIL_ARTIFACT: IdentityQAFlag.MEDIA_REVIEW_CONFIRMED_TAIL,
    ReasonCode.UNATTRIBUTED_MORE_HONEST: IdentityQAFlag.UNATTRIBUTED_MORE_HONEST,
}


def _derive_identity_qa_flags(
    job: LoadedIdentityJob,
    state: _ResolutionState,
    clusters: Sequence[IdentityDecision],
    turns: Sequence[IdentityDecision],
    validation: IdentityValidationSummary,
    stability: DiarizationStabilityReport | None,
) -> list[str]:
    flags = {IdentityQAFlag(value).value for value in state.qa_flags}
    reasons = {reason for item in [*clusters, *turns] for reason in item.reason_codes} | {
        reason for item in state.evidence for reason in item.reason_codes
    }
    flags.update(qa_flag.value for reason, qa_flag in _REASON_QA_FLAGS.items() if reason in reasons)
    if any(
        status.status in {BackendState.FAILED, BackendState.UNAVAILABLE}
        for status in state.backend_statuses
    ):
        flags.add(IdentityQAFlag.BACKEND_UNAVAILABLE.value)
    if any(
        name in job.context.missing_artifacts
        for name in {"transcript.md", "transcript.srt", "transcript.vtt"}
    ):
        flags.add(IdentityQAFlag.MISSING_CANONICAL_ARTIFACT.value)
    external = job.context.external_transcript
    if external is not None and external.drift_classification != DriftClassification.NONE:
        flags.add(IdentityQAFlag.EXTERNAL_TRANSCRIPT_MISMATCH.value)
        if external.drift_classification == DriftClassification.LINE_COUNT:
            flags.add(IdentityQAFlag.LINE_COUNT_DRIFT.value)
        if not external.non_label_text_matches:
            flags.add(IdentityQAFlag.NON_LABEL_TEXT_DRIFT.value)
    if not validation.audit_complete:
        flags.add(IdentityQAFlag.AUDIT_MISSING_DECISION.value)
    if not validation.label_join_regression_checked:
        flags.add(IdentityQAFlag.LABEL_JOIN_REGRESSION.value)
    if ReasonCode.SIBLING_EVIDENCE_STALE.value in state.warnings:
        flags.add(IdentityQAFlag.SIBLING_EVIDENCE_STALE.value)
    if stability is not None and any(row.status != "success" for row in stability.variants):
        flags.add(IdentityQAFlag.STABILITY_VARIANT_FAILED.value)
    flags.add(IdentityQAFlag.PRIVATE_EXCERPT_SUPPRESSED.value)
    return sorted(flags)


def _build_output_family(
    job: LoadedIdentityJob,
    options: ResolveIdentityOptions,
    state: _ResolutionState,
    cluster_decisions: Sequence[IdentityDecision],
    turn_decisions: Sequence[IdentityDecision],
    audit: AttributionAudit,
    validation: IdentityValidationSummary,
    stability: DiarizationStabilityReport | None,
    media_queue: MediaReviewQueue,
) -> tuple[dict[str, bytes], dict[str, str | None], IdentityValidationSummary]:
    ordered_evidence: list[IdentityEvidence] = sorted(
        state.evidence, key=lambda item: item.evidence_id
    )
    outputs: dict[str, bytes] = {
        "speaker_identity_candidates.json": deterministic_json_bytes(
            CandidateSet(
                run_id=job.context.run_id,
                candidates=sorted(state.candidates, key=lambda item: item.candidate_id),
            )
        ),
        "speaker_identity_evidence.jsonl": serialize_jsonl(iter(ordered_evidence)),
        "speaker_identity_attribution_audit.json": deterministic_json_bytes(audit),
    }
    if options.policy.audit_csv:
        outputs["speaker_identity_attribution_audit.csv"] = _audit_csv(audit).encode("utf-8")
    if stability is not None:
        outputs["speaker_identity_diarization_stability.json"] = deterministic_json_bytes(stability)
    if options.write_media_review_queue or options.policy.source_media_review.value == "queue":
        outputs["speaker_identity_media_review_queue.json"] = deterministic_json_bytes(media_queue)

    write_exports = (
        options.write_identity_mapped_exports or options.policy.mode != ResolverMode.REVIEW
    )
    if write_exports:
        validation = _add_identity_exports(job, options, turn_decisions, validation, outputs)
    artifacts_written = {
        "report": "speaker_identity_report.json",
        "candidates": "speaker_identity_candidates.json",
        "evidence": "speaker_identity_evidence.jsonl",
        "attribution_audit": "speaker_identity_attribution_audit.json",
        "attribution_audit_csv": (
            "speaker_identity_attribution_audit.csv" if options.policy.audit_csv else None
        ),
        "diarization_stability": (
            "speaker_identity_diarization_stability.json" if stability is not None else None
        ),
        "media_review_queue": (
            "speaker_identity_media_review_queue.json"
            if "speaker_identity_media_review_queue.json" in outputs
            else None
        ),
        "primary_identity_export": (
            "transcript.identity_mapped.srt"
            if "transcript.identity_mapped.srt" in outputs
            else None
        ),
    }
    return outputs, artifacts_written, validation


def _add_identity_exports(
    job: LoadedIdentityJob,
    options: ResolveIdentityOptions,
    decisions: Sequence[IdentityDecision],
    validation: IdentityValidationSummary,
    outputs: dict[str, bytes],
) -> IdentityValidationSummary:
    canonical_srt = job.artifact_bytes.get("transcript.srt")
    if canonical_srt is None:
        raise IdentityResolverError(
            "missing_canonical_srt", "transcript.srt is required for SRT-first identity delivery."
        )
    identity_srt = render_identity_srt(
        canonical_srt.data.decode("utf-8"), job.transcript, list(decisions)
    )
    srt_preserved, cue_count = validate_srt_label_only(
        canonical_srt.data.decode("utf-8"), identity_srt
    )
    if not srt_preserved:
        raise IdentityResolverError(
            "identity_srt_validation_failed", "Identity SRT changed non-label cue content."
        )
    validation = validation.model_copy(update={"cue_count_preserved": True})
    identity_transcript = build_identity_transcript(job.transcript, list(decisions)).model_copy(
        update={
            "input_path": (
                f"input:{job.context.input_hash[:16]}" if job.context.input_hash else "input:local"
            )
        }
    )
    wrapper = IdentityMappedTranscript(
        resolver_run_id=job.context.run_id,
        canonical_transcript_hash=job.transcript_artifact.sha256,
        display_labels=sorted(
            {decision.display_label for decision in decisions if decision.display_label is not None}
        ),
        turn_decision_ids=[decision.decision_id for decision in decisions],
        validation_summary=validation,
        transcript=identity_transcript,
    )
    outputs["transcript.identity_mapped.json"] = deterministic_json_bytes(wrapper)
    outputs["transcript.identity_mapped.srt"] = identity_srt.encode("utf-8")
    if options.policy.deliverable_format == DeliverableFormat.ALL:
        markdown = job.artifact_bytes.get("transcript.md")
        vtt = job.artifact_bytes.get("transcript.vtt")
        if markdown is None or vtt is None:
            raise IdentityResolverError(
                "missing_optional_export_source",
                "Canonical Markdown and VTT are required for all identity sidecars.",
            )
        outputs["transcript.identity_mapped.md"] = render_identity_markdown(
            markdown.data.decode("utf-8"), job.transcript, list(decisions)
        ).encode("utf-8")
        outputs["transcript.identity_mapped.vtt"] = render_identity_vtt(
            vtt.data.decode("utf-8"), job.transcript, list(decisions)
        ).encode("utf-8")
        outputs["transcript.identity_mapped.txt"] = render_identity_text(
            job.transcript, list(decisions)
        ).encode("utf-8")
        outputs["transcript.identity_mapped.segments.csv"] = render_identity_segments_csv(
            job.transcript, list(decisions)
        ).encode("utf-8")
    _ = cue_count
    return validation


def _refresh_validation_payloads(
    outputs: dict[str, bytes], validation: IdentityValidationSummary
) -> dict[str, bytes]:
    mapped = outputs.get("transcript.identity_mapped.json")
    if mapped is None:
        return outputs
    wrapper = IdentityMappedTranscript.model_validate(strict_json_bytes(mapped))
    outputs["transcript.identity_mapped.json"] = deterministic_json_bytes(
        wrapper.model_copy(update={"validation_summary": validation})
    )
    return outputs


def _audit_csv(audit: AttributionAudit) -> str:
    output = io.StringIO(newline="")
    fieldnames = list(AttributionAuditRow.model_fields)
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in audit.rows:
        values = row.model_dump(mode="json")
        serialized: dict[str, Any] = {}
        for key in fieldnames:
            value = values[key]
            if isinstance(value, list):
                value = "|".join(str(item) for item in value)
            elif value is None:
                value = ""
            text = str(value)
            serialized[key] = formula_safe_cell(text)
        writer.writerow(serialized)
    return output.getvalue()


def _build_stability_report(
    job: LoadedIdentityJob, config: StabilityConfig | None
) -> tuple[DiarizationStabilityReport | None, set[int]]:
    if config is None:
        return None, set()
    variants: list[StabilityVariantResult] = []
    per_variant_turns: list[tuple[list[DiarizationTurn], dict[str, str]]] = []
    for request in config.variants:
        if Path(request.artifact).name != request.artifact:
            variants.append(
                StabilityVariantResult(
                    variant_id=request.variant_id,
                    backend=request.backend,
                    device=request.device,
                    status="failed",
                    artifact_id=request.variant_id,
                    safe_reason="invalid_artifact_id",
                )
            )
            continue
        path = job.job_dir / request.artifact
        try:
            artifact = read_optional_bytes(path)
            if artifact is None:
                raise ValueError("missing")
            payload = strict_json_bytes(artifact.data)
            if isinstance(payload, dict) and "turns" in payload:
                result = DiarizationResult.model_validate(payload)
            else:
                raise ValueError("invalid diarization result")
            per_variant_turns.append(
                (
                    result.turns,
                    _align_anonymous_speaker_labels(
                        job.transcript.diarization_turns,
                        result.turns,
                    ),
                )
            )
            variants.append(
                StabilityVariantResult(
                    variant_id=request.variant_id,
                    backend=request.backend,
                    device=request.device,
                    status="success",
                    artifact_id=request.variant_id,
                    artifact_hash=artifact.sha256,
                )
            )
        except Exception:
            variants.append(
                StabilityVariantResult(
                    variant_id=request.variant_id,
                    backend=request.backend,
                    device=request.device,
                    status="failed",
                    artifact_id=request.variant_id,
                    safe_reason="variant_unavailable_or_malformed",
                )
            )
    turn_stability: list[TurnStability] = []
    unstable: set[int] = set()
    for index, turn in enumerate(job.transcript.speaker_turns):
        dominant = []
        for variant, label_mapping in per_variant_turns:
            variant_speaker, coverage = _dominant_speaker_coverage(
                turn.start, turn.end, variant
            )
            dominant.append(
                (
                    label_mapping.get(variant_speaker)
                    if variant_speaker is not None
                    else None,
                    coverage,
                )
            )
        scores = [coverage for _speaker, coverage in dominant]
        agreement = min(scores) if scores else 0.0
        labels_agree = all(speaker == turn.speaker for speaker, _coverage in dominant)
        stable = (
            bool(scores)
            and labels_agree
            and agreement >= 0.80
            and len(scores) == len(config.variants)
        )
        if not stable:
            unstable.add(index)
        turn_stability.append(
            TurnStability(
                turn_index=index,
                stable_label=stable,
                stable_boundaries=stable,
                agreement_score=round(agreement, 6),
                reason_codes=(
                    [ReasonCode.DIARIZATION_STABLE]
                    if stable
                    else [
                        ReasonCode.ENSEMBLE_DISAGREEMENT,
                        ReasonCode.DIARIZATION_UNSTABLE_BOUNDARY,
                    ]
                ),
            )
        )
    report = DiarizationStabilityReport(
        run_id=job.context.run_id,
        variants=variants,
        turn_stability=turn_stability,
        warnings=["identity_stability_variant_failed"]
        if any(variant.status != "success" for variant in variants)
        else [],
    )
    return report, unstable


def _align_anonymous_speaker_labels(
    canonical: Sequence[DiarizationTurn],
    variant: Sequence[DiarizationTurn],
) -> dict[str, str]:
    """Return the optimal global variant-to-canonical label permutation.

    Anonymous diarization labels have no meaning across runs.  Alignment is
    therefore based only on co-active duration, using the same deterministic
    maximum-weight assignment contract as DER scoring.  The event sweep keeps
    the overlap calculation bounded without a canonical-by-variant nested scan.
    """

    canonical_speakers = sorted({turn.speaker for turn in canonical})
    variant_speakers = sorted({turn.speaker for turn in variant})
    if not canonical_speakers or not variant_speakers:
        return {}

    events: dict[float, list[tuple[str, str, int]]] = defaultdict(list)
    for turn in canonical:
        if turn.end > turn.start:
            events[turn.start].append(("canonical", turn.speaker, 1))
            events[turn.end].append(("canonical", turn.speaker, -1))
    for turn in variant:
        if turn.end > turn.start:
            events[turn.start].append(("variant", turn.speaker, 1))
            events[turn.end].append(("variant", turn.speaker, -1))

    active_canonical: Counter[str] = Counter()
    active_variant: Counter[str] = Counter()
    pair_overlap: dict[tuple[str, str], float] = defaultdict(float)
    previous: float | None = None
    for timestamp in sorted(events):
        if previous is not None and timestamp > previous:
            duration = timestamp - previous
            for variant_speaker in active_variant:
                if active_variant[variant_speaker] <= 0:
                    continue
                for canonical_speaker in active_canonical:
                    if active_canonical[canonical_speaker] > 0:
                        pair_overlap[(variant_speaker, canonical_speaker)] += duration
        for source, speaker, delta in events[timestamp]:
            active = active_canonical if source == "canonical" else active_variant
            active[speaker] += delta
            if active[speaker] <= 0:
                del active[speaker]
        previous = timestamp

    size = max(len(canonical_speakers), len(variant_speakers))
    weights = [[0.0 for _ in range(size)] for _ in range(size)]
    for row, variant_speaker in enumerate(variant_speakers):
        for column, canonical_speaker in enumerate(canonical_speakers):
            weights[row][column] = pair_overlap.get(
                (variant_speaker, canonical_speaker), 0.0
            )
    assignment = _maximum_weight_assignment(weights)
    return {
        variant_speakers[row]: canonical_speakers[column]
        for row, column in enumerate(assignment[: len(variant_speakers)])
        if column < len(canonical_speakers) and weights[row][column] > 0.0
    }


def _dominant_coverage(start: float, end: float, turns: Sequence[DiarizationTurn]) -> float:
    return _dominant_speaker_coverage(start, end, turns)[1]


def _dominant_speaker_coverage(
    start: float, end: float, turns: Sequence[DiarizationTurn]
) -> tuple[str | None, float]:
    duration = max(0.001, end - start)
    by_speaker: dict[str, float] = defaultdict(float)
    for turn in turns:
        overlap = max(0.0, min(end, turn.end) - max(start, turn.start))
        by_speaker[turn.speaker] += overlap
    if not by_speaker:
        return None, 0.0
    speaker, overlap = max(by_speaker.items(), key=lambda item: (item[1], item[0]))
    return speaker, min(1.0, overlap / duration)


def _target_label(job: LoadedIdentityJob, index: int, fallback: str) -> str:
    external = job.context.external_transcript
    if external is None or index >= len(external.target_labels):
        return fallback
    return external.target_labels[index]


def _last_vad_end(job: LoadedIdentityJob) -> float | None:
    artifact = job.artifact_bytes.get("vad.json")
    if artifact is None:
        return None
    try:
        payload = strict_json_bytes(artifact.data)
        regions = payload.get("regions", []) if isinstance(payload, dict) else []
        values = [float(region["end"]) for region in regions if isinstance(region, dict)]
        return max(values) if values else None
    except Exception:
        return None


def _job_duration(job: LoadedIdentityJob) -> float:
    ends = [turn.end for turn in job.transcript.speaker_turns]
    ends.extend(turn.end for turn in job.transcript.diarization_turns)
    run_metadata = job.artifact_bytes.get("run_metadata.json")
    if run_metadata is not None:
        try:
            payload = strict_json_bytes(run_metadata.data)
            if isinstance(payload, dict):
                metadata = payload.get("canonical_audio_metadata")
                if isinstance(metadata, dict):
                    raw_duration = metadata.get("duration_seconds")
                    if isinstance(raw_duration, (int, float, str)):
                        duration = float(raw_duration)
                        if math.isfinite(duration) and duration >= 0:
                            ends.append(duration)
        except Exception:
            pass
    return max(ends, default=0.0)


def _turn_has_diarization_coverage(start: float, end: float, job: LoadedIdentityJob) -> bool:
    return any(
        min(end, turn.end) - max(start, turn.start) > 0.01
        for turn in job.transcript.diarization_turns
    )


def _same_speaker_bounded_fragment(
    job: LoadedIdentityJob, index: int, policy: ResolverPolicy
) -> bool:
    if index == 0 or index + 1 >= len(job.transcript.speaker_turns):
        return False
    turn = job.transcript.speaker_turns[index]
    left = job.transcript.speaker_turns[index - 1]
    right = job.transcript.speaker_turns[index + 1]
    return bool(
        left.speaker == right.speaker
        and left.speaker.upper() != "UNKNOWN"
        and turn.end - turn.start <= policy.max_fragment_duration
        and len(turn.words) <= policy.max_fragment_words
    )


def _phrase_continuation(job: LoadedIdentityJob, index: int) -> bool:
    if index == 0:
        return False
    previous = job.transcript.speaker_turns[index - 1]
    current = job.transcript.speaker_turns[index]
    previous_text = previous.text.rstrip()
    current_text = current.text.lstrip()
    first = next((character for character in current_text if character.isalpha()), "")
    return bool(
        previous_text
        and current_text
        and not previous_text.endswith((".", "?", "!"))
        and first.islower()
        and current.start - previous.end <= 1.5
    )


def _question_answer_boundary(job: LoadedIdentityJob, index: int) -> bool:
    if index == 0:
        return False
    previous = job.transcript.speaker_turns[index - 1]
    current = job.transcript.speaker_turns[index]
    return bool(previous.text.rstrip().endswith("?") and current.start - previous.end <= 2.0)


def _mixed_turn(turn: Any) -> bool:
    speakers = {
        word.speaker
        for word in turn.words
        if word.speaker is not None and word.speaker.upper() != "UNKNOWN"
    }
    return len(speakers) > 1


def _decision_id(job: LoadedIdentityJob, scope: AssignmentScope, scope_key: str) -> str:
    return stable_digest(
        {
            "schema": "speaker_identity_decision.v1",
            "transcript_hash": job.transcript_artifact.sha256,
            "scope": scope.value,
            "scope_key": scope_key,
        },
        prefix="dec",
    )


def _source_line(job: LoadedIdentityJob, turn_index: int | None) -> int | None:
    if turn_index is None:
        return None
    markdown = job.artifact_bytes.get("transcript.md")
    if markdown is None:
        return None
    lines = markdown.data.decode("utf-8", errors="replace").splitlines()
    indexes = [
        index
        for index, line in enumerate(lines, start=1)
        if re.fullmatch(r"\*\*(.+?)\*\*\s{0,2}", line)
    ]
    return indexes[turn_index] if turn_index < len(indexes) else None


def _finite_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) and 0.0 <= parsed <= 1.0 else 0.0
