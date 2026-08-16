# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from local_transcript_max.export.markdown_export import render_markdown
from local_transcript_max.export.srt_export import render_srt
from local_transcript_max.export.vtt_export import render_vtt
from local_transcript_max.schemas import (
    AlignedWord,
    DiarizationResult,
    DiarizationTurn,
    SpeakerEnrollment,
    SpeakerTurn,
    Transcript,
)
from local_transcript_max.speaker_id import identity_resolver as identity_resolver_module
from local_transcript_max.speaker_id.embeddings import save_embedding
from local_transcript_max.speaker_id.enrollment import (
    match_job_speakers,
    rename_speaker,
    speaker_home,
)
from local_transcript_max.speaker_id.identity_io import (
    CANONICAL_ARTIFACTS,
    IdentityResolverError,
    canonical_hash_snapshot,
    deterministic_json_bytes,
    load_identity_job,
)
from local_transcript_max.speaker_id.identity_models import (
    ApprovedIdentityMap,
    ApprovedTurnOverride,
    AssignmentScope,
    AttributionAudit,
    BackendState,
    CandidateSet,
    DeliverableFormat,
    DiarizationStabilityReport,
    DriftClassification,
    EvidenceTier,
    IdentityMappedTranscript,
    IdentityReport,
    MediaReviewQueue,
    MediaReviewReport,
    Participant,
    ReasonCode,
    ResolutionStatus,
    ResolverMode,
    ResolverPolicy,
    ReviewedMediaRegion,
    SourceMediaReviewMode,
    SpeakerRoster,
    StabilityConfig,
    StabilityVariantRequest,
    StructuralRepairMode,
)
from local_transcript_max.speaker_id.identity_resolver import (
    ResolveIdentityOptions,
    resolve_identities,
)

INPUT_HASH = "a" * 64
ALPHA = "Participant Alpha"
BETA = "Participant Beta"


@pytest.fixture(autouse=True)
def isolated_speaker_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LTM_SPEAKER_PROFILE_DIR", str(tmp_path / "profiles"))


def _word(
    text: str,
    start: float,
    end: float,
    speaker: str | None,
    *,
    uncertain: bool = False,
) -> AlignedWord:
    return AlignedWord(
        text=text,
        start=start,
        end=end,
        speaker=speaker,
        uncertain_speaker=uncertain,
        assignment_source="exclusive_diarization" if speaker != "UNKNOWN" else "unassigned",
        alignment_source="forced",
    )


def synthetic_transcript(
    *, variant: str = "base", fold_unknown_into_cluster: bool = False
) -> Transcript:
    words = [
        _word(f"I am {ALPHA}.", 0.0, 0.9, "SPEAKER_00"),
        _word("Synthetic bridge.", 1.1, 1.3, "UNKNOWN", uncertain=True),
        _word(f"{BETA}, status?", 1.4, 2.1, "SPEAKER_00"),
        _word("Generic response.", 2.3, 3.0, "SPEAKER_01"),
        _word("Mixed", 3.1, 3.4, "SPEAKER_01"),
        _word("boundary.", 3.5, 3.9, "SPEAKER_00"),
        _word("Synthetic tail.", 5.0, 5.4, None, uncertain=True),
    ]
    if variant != "base":
        words[3] = words[3].model_copy(update={"text": f"Generic response {variant}."})
    turns = [
        SpeakerTurn(
            speaker="SPEAKER_00",
            start=0.0,
            end=0.9,
            text=words[0].text,
            words=[words[0]],
        ),
        SpeakerTurn(
            speaker="SPEAKER_00" if fold_unknown_into_cluster else "UNKNOWN",
            start=1.1,
            end=1.3,
            text=words[1].text,
            words=[words[1]],
        ),
        SpeakerTurn(
            speaker="SPEAKER_00",
            start=1.4,
            end=2.1,
            text=words[2].text,
            words=[words[2]],
        ),
        SpeakerTurn(
            speaker="SPEAKER_01",
            start=2.3,
            end=3.0,
            text=words[3].text,
            words=[words[3]],
        ),
        SpeakerTurn(
            speaker="SPEAKER_01",
            start=3.1,
            end=3.9,
            text="Mixed boundary.",
            words=[words[4], words[5]],
        ),
        SpeakerTurn(
            speaker="UNKNOWN",
            start=5.0,
            end=5.4,
            text=words[6].text,
            words=[words[6]],
        ),
    ]
    return Transcript(
        input_path="synthetic.wav",
        words=words,
        diarization_turns=[
            DiarizationTurn(start=0.0, end=2.2, speaker="SPEAKER_00", source="synthetic"),
            DiarizationTurn(start=2.2, end=4.1, speaker="SPEAKER_01", source="synthetic"),
        ],
        exclusive_diarization_turns=[
            DiarizationTurn(start=0.0, end=2.2, speaker="SPEAKER_00", source="synthetic"),
            DiarizationTurn(start=2.2, end=4.1, speaker="SPEAKER_01", source="synthetic"),
        ],
        speaker_turns=turns,
    )


def write_job(
    job_dir: Path,
    *,
    variant: str = "base",
    fold_unknown_into_cluster: bool = False,
) -> Transcript:
    job_dir.mkdir(parents=True)
    transcript = synthetic_transcript(
        variant=variant, fold_unknown_into_cluster=fold_unknown_into_cluster
    )
    (job_dir / "transcript.json").write_bytes(deterministic_json_bytes(transcript))
    (job_dir / "transcript.md").write_text(render_markdown(transcript), encoding="utf-8")
    (job_dir / "transcript.srt").write_text(render_srt(transcript), encoding="utf-8")
    (job_dir / "transcript.vtt").write_text(render_vtt(transcript), encoding="utf-8")
    (job_dir / "transcript.txt").write_text(
        "\n\n".join(f"{turn.speaker}: {turn.text}" for turn in transcript.speaker_turns) + "\n",
        encoding="utf-8",
    )
    (job_dir / "vad.json").write_text(
        json.dumps({"regions": [{"start": 0.0, "end": 4.2}]}), encoding="utf-8"
    )
    (job_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "input_file_hash": INPUT_HASH,
                "processing_timestamp": "2026-07-09T00:00:00Z",
                "canonical_audio_metadata": {"duration_seconds": 5.5},
            }
        ),
        encoding="utf-8",
    )
    return transcript


def write_roster(path: Path, *, expected: int = 2, participants: int = 2) -> Path:
    names = [ALPHA, BETA][:participants]
    roster = SpeakerRoster(
        expected_speaker_count=expected,
        participants=[
            Participant(
                name=name,
                aliases=["Alpha" if name == ALPHA else "Beta"],
                role="synthetic-role",
            )
            for name in names
        ],
    )
    path.write_bytes(deterministic_json_bytes(roster))
    return path


def write_profile_map(
    job_dir: Path,
    *,
    mappings: dict[str, str] | None = None,
    backend: str = "speechbrain-ecapa",
    threshold: float = 0.75,
    ambiguity_margin: float = 0.05,
    profile_vectors: dict[str, list[float]] | None = None,
    cluster_vectors: dict[str, list[float]] | None = None,
) -> Path:
    mappings = mappings or {"SPEAKER_00": ALPHA, "SPEAKER_01": BETA}
    profile_vectors = profile_vectors or {ALPHA: [1.0, 0.0], BETA: [0.0, 1.0]}
    cluster_vectors = cluster_vectors or {
        speaker: profile_vectors[label] for speaker, label in mappings.items()
    }
    root = speaker_home()
    for name, vector in sorted(profile_vectors.items()):
        profile_id = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        profile_dir = root / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        embedding_path = profile_dir / "embedding.json"
        save_embedding(embedding_path, backend, vector)
        enrollment = SpeakerEnrollment(
            name=name,
            embedding_path=str(embedding_path),
            sample_paths=[],
            backend=backend,
        )
        (profile_dir / "profile.json").write_text(
            enrollment.model_dump_json(indent=2), encoding="utf-8"
        )
    (job_dir / "speaker_cluster_embeddings.json").write_text(
        json.dumps(cluster_vectors, sort_keys=True), encoding="utf-8"
    )
    match_job_speakers(
        job_dir,
        threshold=threshold,
        ambiguity_margin=ambiguity_margin,
    )
    return job_dir / "speaker_map.json"


def review_options(**overrides: object) -> ResolveIdentityOptions:
    values: dict[str, object] = {"policy": ResolverPolicy(mode=ResolverMode.REVIEW)}
    values.update(overrides)
    return ResolveIdentityOptions(**values)  # type: ignore[arg-type]


def reviewed_media_options(path: Path) -> ResolveIdentityOptions:
    return review_options(
        policy=ResolverPolicy(
            mode=ResolverMode.REVIEW,
            source_media_review=SourceMediaReviewMode.REVIEWED_REPORT,
        ),
        media_review_report_path=path,
    )


def safe_options(roster: Path | None = None, **overrides: object) -> ResolveIdentityOptions:
    values: dict[str, object] = {
        "policy": ResolverPolicy(mode=ResolverMode.APPLY_SAFE),
        "known_speakers": 2,
        "roster_path": roster,
    }
    values.update(overrides)
    return ResolveIdentityOptions(**values)  # type: ignore[arg-type]


def _report(job_dir: Path) -> IdentityReport:
    return IdentityReport.model_validate_json(
        (job_dir / "speaker_identity_report.json").read_text(encoding="utf-8")
    )


def _canonical_snapshot(job_dir: Path) -> dict[str, bytes]:
    return {
        name: (job_dir / name).read_bytes()
        for name in CANONICAL_ARTIFACTS
        if (job_dir / name).is_file()
    }


def _write_approved_map(
    path: Path,
    job_dir: Path,
    review: IdentityReport,
    *,
    cluster_mappings: dict[str, str],
    turn_overrides: list[ApprovedTurnOverride] | None = None,
    job_fingerprint: str | None = None,
    hashes: dict[str, str] | None = None,
    resolver_run_id: str | None = None,
) -> Path:
    loaded = load_identity_job(job_dir, policy=ResolverPolicy())
    approved = ApprovedIdentityMap(
        job_fingerprint=job_fingerprint or loaded.context.job_fingerprint,
        resolver_run_id=resolver_run_id or review.run_id,
        canonical_artifact_hashes=hashes or canonical_hash_snapshot(loaded),
        approved_at=datetime(2026, 7, 9, tzinfo=UTC),
        approved_by="local-reviewer",
        cluster_mappings=cluster_mappings,
        turn_overrides=turn_overrides or [],
    )
    path.write_bytes(deterministic_json_bytes(approved))
    return path


def test_review_writes_exact_report_family_without_mapped_exports_or_map_mutation(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    before = _canonical_snapshot(job_dir)

    result = resolve_identities(job_dir, review_options())

    assert set(result.written_artifacts) == {
        "speaker_identity_report.json",
        "speaker_identity_candidates.json",
        "speaker_identity_evidence.jsonl",
        "speaker_identity_attribution_audit.json",
        "speaker_identity_attribution_audit.csv",
        "speaker_identity_media_review_queue.json",
    }
    assert not list(job_dir.glob("transcript.identity_mapped.*"))
    assert not (job_dir / "speaker_map.json").exists()
    assert _canonical_snapshot(job_dir) == before
    assert result.report.artifacts_written["primary_identity_export"] is None


def test_apply_safe_consumes_only_hash_bound_healthy_profile_matches_and_exact_roster(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir)
    before = _canonical_snapshot(job_dir)

    result = resolve_identities(job_dir, safe_options(roster))
    by_cluster = {decision.speaker: decision for decision in result.report.cluster_decisions}

    assert by_cluster["SPEAKER_00"].status is ResolutionStatus.MAPPED
    assert by_cluster["SPEAKER_00"].display_label == ALPHA
    assert by_cluster["SPEAKER_01"].status is ResolutionStatus.MAPPED
    assert by_cluster["SPEAKER_01"].display_label == BETA
    assert [item.candidate_name for item in by_cluster["SPEAKER_00"].rejections] == [BETA]
    assert all(
        ReasonCode.COMPETING_CANDIDATE_REJECTED in item.reason_codes
        for item in by_cluster["SPEAKER_00"].rejections
    )
    candidates = CandidateSet.model_validate_json(
        (job_dir / "speaker_identity_candidates.json").read_bytes()
    )
    alpha = next(
        candidate
        for candidate in candidates.candidates
        if candidate.scope_key == "cluster:SPEAKER_00" and candidate.candidate_name == ALPHA
    )
    assert alpha.conflicts == [BETA]
    audit = AttributionAudit.model_validate_json(
        (job_dir / "speaker_identity_attribution_audit.json").read_bytes()
    )
    cluster_row = next(
        row for row in audit.rows if row.decision_id == by_cluster["SPEAKER_00"].decision_id
    )
    assert cluster_row.competing_candidate == BETA
    assert (
        cluster_row.competing_candidate_rejection_reason
        == ReasonCode.COMPETING_CANDIDATE_REJECTED.value
    )
    assert (job_dir / "transcript.identity_mapped.srt").exists()
    assert result.report.artifacts_written["primary_identity_export"] == (
        "transcript.identity_mapped.srt"
    )
    assert _canonical_snapshot(job_dir) == before


def test_safe_cluster_alias_composes_across_all_exports_for_uncertain_child_turn(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    transcript = write_job(job_dir)
    uncertain_word = transcript.words[0].model_copy(update={"uncertain_speaker": True})
    words = [uncertain_word, *transcript.words[1:]]
    turns = list(transcript.speaker_turns)
    turns[0] = turns[0].model_copy(update={"words": [uncertain_word]})
    transcript = transcript.model_copy(update={"words": words, "speaker_turns": turns})
    (job_dir / "transcript.json").write_bytes(deterministic_json_bytes(transcript))
    (job_dir / "transcript.md").write_text(render_markdown(transcript), encoding="utf-8")
    (job_dir / "transcript.srt").write_text(render_srt(transcript), encoding="utf-8")
    (job_dir / "transcript.vtt").write_text(render_vtt(transcript), encoding="utf-8")
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir)

    result = resolve_identities(
        job_dir,
        safe_options(
            roster,
            policy=ResolverPolicy(
                mode=ResolverMode.APPLY_SAFE,
                deliverable_format=DeliverableFormat.ALL,
            ),
        ),
    )
    child = result.report.turn_decisions[0]
    assert child.status in {ResolutionStatus.REVIEW, ResolutionStatus.UNRESOLVED}
    assert child.display_label is None
    assert child.mapped_global_candidate == ALPHA

    wrapper = IdentityMappedTranscript.model_validate_json(
        (job_dir / "transcript.identity_mapped.json").read_bytes()
    )
    assert wrapper.transcript.speaker_turns[0].mapped_name == ALPHA
    for name in (
        "transcript.identity_mapped.srt",
        "transcript.identity_mapped.vtt",
        "transcript.identity_mapped.md",
        "transcript.identity_mapped.txt",
    ):
        payload = (job_dir / name).read_text(encoding="utf-8")
        assert f"{ALPHA}:" in payload or f"**{ALPHA}**" in payload
        assert "SPEAKER_00: I am" not in payload
    with (job_dir / "transcript.identity_mapped.segments.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["display_label"] == ALPHA

    audit = AttributionAudit.model_validate_json(
        (job_dir / "speaker_identity_attribution_audit.json").read_bytes()
    )
    child_row = next(row for row in audit.rows if row.decision_id == child.decision_id)
    assert child_row.assigned_display_label == ALPHA
    assert child_row.resolution_status is child.status
    assert child_row.action == "apply_global_display_label_retain_status"
    assert result.report.derived_artifact_hashes
    for name, expected_hash in result.report.derived_artifact_hashes.items():
        assert hashlib.sha256((job_dir / name).read_bytes()).hexdigest() == expected_hash


def test_apply_safe_recomputes_active_profile_ambiguity_margin(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    alpha_vector = [0.98, math.sqrt(1.0 - 0.98**2), 0.0]
    beta_vector = [0.97, 0.0, math.sqrt(1.0 - 0.97**2)]
    write_profile_map(
        job_dir,
        ambiguity_margin=0.0,
        profile_vectors={ALPHA: alpha_vector, BETA: beta_vector},
        cluster_vectors={"SPEAKER_00": [1.0, 0.0, 0.0], "SPEAKER_01": beta_vector},
    )
    source_map = json.loads((job_dir / "speaker_map.json").read_text(encoding="utf-8"))
    first = {row["speaker"]: row for row in source_map["mappings"]}["SPEAKER_00"]
    assert first["confidence"] == 0.98
    assert [alternative["score"] for alternative in first["alternatives"][:2]] == [
        0.98,
        0.97,
    ]
    assert first["ambiguous"] is False

    result = resolve_identities(
        job_dir,
        safe_options(
            roster,
            policy=ResolverPolicy(
                mode=ResolverMode.APPLY_SAFE,
                profile_ambiguity_margin=0.05,
            ),
        ),
    ).report
    by_cluster = {decision.speaker: decision for decision in result.cluster_decisions}

    assert by_cluster["SPEAKER_00"].status is not ResolutionStatus.MAPPED
    assert by_cluster["SPEAKER_00"].display_label is None
    assert ReasonCode.LOCAL_PROFILE_AMBIGUOUS in by_cluster["SPEAKER_00"].reason_codes


@pytest.mark.parametrize(
    "mutation",
    [
        "malformed",
        "unbound",
        "transcript-hash",
        "cluster-hash",
        "source-profile",
        "alternatives",
        "threshold-nonfinite",
        "current-profile",
    ],
)
def test_profile_map_malformed_unbound_or_stale_provenance_is_non_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    map_path = write_profile_map(job_dir)
    payload = json.loads(map_path.read_text(encoding="utf-8"))
    if mutation == "malformed":
        payload["mappings"] = "not-a-list"
    elif mutation == "unbound":
        payload.pop("cluster_embeddings_sha256")
    elif mutation == "transcript-hash":
        payload["transcript_sha256"] = "0" * 64
    elif mutation == "cluster-hash":
        payload["cluster_embeddings_sha256"] = "0" * 64
    elif mutation == "source-profile":
        payload["mappings"][0]["source_profile_id"] = "wrong-profile"
    elif mutation == "alternatives":
        payload["mappings"][0]["alternatives"][0]["score"] = 0.99
    elif mutation == "threshold-nonfinite":
        payload["threshold"] = math.nan
    elif mutation == "current-profile":
        alpha_embedding = speaker_home() / "participant-alpha" / "embedding.json"
        save_embedding(alpha_embedding, "speechbrain-ecapa", [0.0, 1.0])
    else:  # pragma: no cover - parameter exhaustiveness
        raise AssertionError(mutation)
    if mutation != "current-profile":
        map_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = resolve_identities(job_dir, safe_options(roster)).report

    assert all(
        decision.status is not ResolutionStatus.MAPPED for decision in result.cluster_decisions
    )
    assert all(
        ReasonCode.LOCAL_PROFILE_MATCH not in decision.reason_codes
        for decision in result.cluster_decisions
    )
    profile_status = next(
        status for status in result.backend_statuses if status.backend == "speechbrain-ecapa"
    )
    assert profile_status.status is BackendState.FAILED
    assert profile_status.safe_detail in {
        "speaker_map_malformed",
        "speaker_map_provenance_invalid",
    }
    assert "identity_backend_unavailable" in result.qa_flags


def test_safe_global_mapping_does_not_relabel_a_mixed_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LTM_SPEAKER_RENAME_DIR", str(tmp_path / "renames"))
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    rename_speaker("mixed-turn", "SPEAKER_01", BETA, job_dir=job_dir)

    result = resolve_identities(job_dir, safe_options(roster))
    by_cluster = {decision.speaker: decision for decision in result.report.cluster_decisions}
    assert by_cluster["SPEAKER_01"].status is ResolutionStatus.MAPPED
    assert by_cluster["SPEAKER_01"].display_label == BETA

    mixed = result.report.turn_decisions[4]
    assert mixed.status is ResolutionStatus.REVIEW
    assert mixed.display_label is None
    assert mixed.mapped_global_candidate == BETA
    assert mixed.requires_approval is True
    assert mixed.requires_media_review is True
    assert ReasonCode.MIXED_TURN_BOUNDARY in mixed.reason_codes
    assert ReasonCode.PARTIAL_LINE_SPEAKER_CHANGE in mixed.reason_codes
    assert ReasonCode.SPLIT_RECOMMENDED in mixed.reason_codes
    assert ReasonCode.REQUIRES_TURN_LEVEL_OVERRIDE in mixed.reason_codes
    identity_srt = (job_dir / "transcript.identity_mapped.srt").read_text(encoding="utf-8")
    assert "SPEAKER_01: Mixed boundary." in identity_srt
    assert f"{BETA}: Mixed boundary." not in identity_srt


def test_apply_safe_accepts_only_collision_safe_hash_bound_legacy_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rename_dir = tmp_path / "renames"
    monkeypatch.setenv("LTM_SPEAKER_RENAME_DIR", str(rename_dir))
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    rename_speaker("bound-job", "SPEAKER_00", ALPHA, job_dir=job_dir)

    mapped = resolve_identities(job_dir, safe_options(roster)).report
    by_cluster = {decision.speaker: decision for decision in mapped.cluster_decisions}
    assert by_cluster["SPEAKER_00"].status is ResolutionStatus.MAPPED
    assert by_cluster["SPEAKER_00"].display_label == ALPHA
    assert ReasonCode.LEGACY_EXPLICIT_RENAME in by_cluster["SPEAKER_00"].reason_codes

    rename_speaker("conflicting-bound-job", "SPEAKER_00", BETA, job_dir=job_dir)
    conflicted = resolve_identities(job_dir, safe_options(roster)).report
    by_cluster = {decision.speaker: decision for decision in conflicted.cluster_decisions}
    assert by_cluster["SPEAKER_00"].status is not ResolutionStatus.MAPPED
    assert ReasonCode.LEGACY_EXPLICIT_RENAME not in by_cluster["SPEAKER_00"].reason_codes


def test_run_id_changes_when_hash_bound_rename_evidence_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LTM_SPEAKER_RENAME_DIR", str(tmp_path / "renames"))
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    rename_speaker("first", "SPEAKER_00", ALPHA, job_dir=job_dir)
    first = resolve_identities(job_dir, safe_options(roster)).report

    rename_speaker("second", "SPEAKER_01", BETA, job_dir=job_dir)
    second = resolve_identities(job_dir, safe_options(roster)).report

    assert first.run_id != second.run_id
    assert first.cluster_decisions[1].status is not ResolutionStatus.MAPPED
    assert second.cluster_decisions[1].display_label == BETA


def test_run_id_changes_when_current_profile_evidence_changes(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir)
    first = resolve_identities(job_dir, safe_options(roster)).report

    write_profile_map(
        job_dir,
        cluster_vectors={"SPEAKER_00": [0.0, 1.0], "SPEAKER_01": [1.0, 0.0]},
    )
    second = resolve_identities(job_dir, safe_options(roster)).report

    assert first.run_id != second.run_id
    assert [decision.display_label for decision in first.cluster_decisions] == [ALPHA, BETA]
    assert [decision.display_label for decision in second.cluster_decisions] == [BETA, ALPHA]


def test_apply_approved_is_hash_bound_precedes_safe_evidence_and_applies_turn_override(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir, mappings={"SPEAKER_00": ALPHA, "SPEAKER_01": BETA})
    review = resolve_identities(
        job_dir,
        review_options(known_speakers=2, roster_path=roster),
    ).report
    tail = review.turn_decisions[-1]
    approved_path = _write_approved_map(
        tmp_path / "approved.json",
        job_dir,
        review,
        cluster_mappings={"SPEAKER_00": BETA, "SPEAKER_01": ALPHA},
        turn_overrides=[
            ApprovedTurnOverride(
                turn_index=tail.turn_index or 0,
                decision_id=tail.decision_id,
                display_label="Unattributed",
                reason="Bounded local review",
            )
        ],
    )

    result = resolve_identities(
        job_dir,
        ResolveIdentityOptions(
            policy=ResolverPolicy(mode=ResolverMode.APPLY_APPROVED),
            known_speakers=2,
            roster_path=roster,
            approved_map_path=approved_path,
        ),
    )
    by_cluster = {decision.speaker: decision for decision in result.report.cluster_decisions}

    assert by_cluster["SPEAKER_00"].display_label == BETA
    assert by_cluster["SPEAKER_01"].display_label == ALPHA
    assert result.report.turn_decisions[-1].status is ResolutionStatus.UNATTRIBUTED
    assert result.report.turn_decisions[-1].local_override_candidate == "Unattributed"
    assert ReasonCode.TURN_LEVEL_OVERRIDE in result.report.turn_decisions[-1].reason_codes
    assert result.report.approval_source_run_id == review.run_id

    repeated = resolve_identities(
        job_dir,
        ResolveIdentityOptions(
            policy=ResolverPolicy(mode=ResolverMode.APPLY_APPROVED),
            known_speakers=2,
            roster_path=roster,
            approved_map_path=approved_path,
        ),
    )
    assert repeated.report.run_id == result.report.run_id
    assert repeated.report.approval_source_run_id == review.run_id


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("cross-job", "cross_job_approval"),
        ("stale-hash", "stale_approval"),
        ("stale-run", "stale_approval_run"),
    ],
)
def test_apply_approved_rejects_cross_job_and_stale_maps(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    review = resolve_identities(
        job_dir, review_options(known_speakers=2, roster_path=roster)
    ).report
    loaded = load_identity_job(job_dir, policy=ResolverPolicy())
    fingerprint = (
        "job:000000000000000000000000"
        if mutation == "cross-job"
        else loaded.context.job_fingerprint
    )
    hashes = canonical_hash_snapshot(loaded)
    if mutation == "stale-hash":
        hashes["transcript.json"] = "0" * 64
    approved = _write_approved_map(
        tmp_path / "approved.json",
        job_dir,
        review,
        cluster_mappings={"SPEAKER_00": ALPHA},
        job_fingerprint=fingerprint,
        hashes=hashes,
        resolver_run_id=("run:000000000000000000000000" if mutation == "stale-run" else None),
    )

    with pytest.raises(IdentityResolverError) as error:
        resolve_identities(
            job_dir,
            ResolveIdentityOptions(
                policy=ResolverPolicy(mode=ResolverMode.APPLY_APPROVED),
                known_speakers=2,
                roster_path=roster,
                approved_map_path=approved,
            ),
        )
    assert error.value.code == expected_code


def test_known_count_is_structured_warning_in_review_and_block_in_apply(tmp_path: Path) -> None:
    review_job = tmp_path / "review"
    write_job(review_job)
    review = resolve_identities(review_job, review_options(known_speakers=2)).report
    assert ReasonCode.ROSTER_MISSING_EXPECTED_NAMES.value in review.warnings
    assert "identity_roster_count_conflict" in review.qa_flags

    apply_job = tmp_path / "apply"
    write_job(apply_job)
    write_profile_map(apply_job)
    with pytest.raises(IdentityResolverError) as missing:
        resolve_identities(apply_job, safe_options())
    assert missing.value.code == "roster_required"

    mismatched = write_roster(tmp_path / "mismatched.json", expected=1, participants=1)
    with pytest.raises(IdentityResolverError) as count:
        resolve_identities(apply_job, safe_options(mismatched))
    assert count.value.code == "roster_count_conflict"


def test_wrong_mode_approval_inputs_fail_closed(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    review = resolve_identities(job_dir).report
    approved = _write_approved_map(
        tmp_path / "approved.json",
        job_dir,
        review,
        cluster_mappings={"SPEAKER_00": ALPHA},
    )
    with pytest.raises(IdentityResolverError) as missing:
        resolve_identities(
            job_dir,
            ResolveIdentityOptions(policy=ResolverPolicy(mode=ResolverMode.APPLY_APPROVED)),
        )
    assert missing.value.code == "approved_map_required"
    with pytest.raises(IdentityResolverError) as wrong_mode:
        resolve_identities(job_dir, review_options(approved_map_path=approved))
    assert wrong_mode.value.code == "approved_map_wrong_mode"


def test_direct_self_role_and_waveform_fallback_evidence_are_review_only_and_excerpt_free(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    transcript = write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir, backend="waveform-stats")

    result = resolve_identities(job_dir, review_options(known_speakers=2, roster_path=roster))
    assert all(
        decision.status is not ResolutionStatus.MAPPED
        for decision in result.report.cluster_decisions
    )
    evidence_lines = [
        json.loads(line)
        for line in (job_dir / "speaker_identity_evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    tiers = {row["tier"] for row in evidence_lines}
    assert EvidenceTier.DIRECT_ANCHOR.value in tiers
    assert EvidenceTier.ROLE.value in tiers
    assert EvidenceTier.FORCED_PROXIMITY.value not in tiers
    assert all(row["safe_excerpt"] is None for row in evidence_lines)
    assert all(row["action"] == "review_candidate" for row in evidence_lines)
    assert result.report.backend_statuses[0].status is BackendState.FAILED
    artifacts = "\n".join(
        (job_dir / name).read_text(encoding="utf-8") for name in result.written_artifacts
    )
    assert transcript.speaker_turns[0].text not in artifacts
    assert str(tmp_path) not in artifacts


def test_folded_unknown_adjacent_tail_and_mixed_turns_are_independent_decisions(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    result = resolve_identities(
        job_dir,
        review_options(
            policy=ResolverPolicy(
                mode=ResolverMode.REVIEW,
                turn_boundary_repair=StructuralRepairMode.LABEL_ONLY,
            )
        ),
    ).report
    by_index = {decision.turn_index: decision for decision in result.turn_decisions}

    adjacent = by_index[1]
    assert adjacent.assignment_scope is AssignmentScope.FRAGMENT
    assert adjacent.status is ResolutionStatus.REVIEW
    assert ReasonCode.SAME_SPEAKER_BOUNDED_FRAGMENT in adjacent.reason_codes
    assert ReasonCode.REQUIRES_TURN_LEVEL_OVERRIDE in adjacent.reason_codes

    mixed = by_index[4]
    assert ReasonCode.MIXED_TURN_BOUNDARY in mixed.reason_codes
    assert ReasonCode.PARTIAL_LINE_SPEAKER_CHANGE in mixed.reason_codes
    assert ReasonCode.SPLIT_RECOMMENDED in mixed.reason_codes
    assert ReasonCode.LABEL_ONLY_FALLBACK in mixed.reason_codes

    tail = by_index[5]
    assert tail.assignment_scope is AssignmentScope.FRAGMENT
    assert tail.status is ResolutionStatus.UNATTRIBUTED
    assert tail.display_label == "Unattributed"
    assert ReasonCode.NO_DIARIZATION_COVERAGE in tail.reason_codes
    assert ReasonCode.UNATTRIBUTED_MORE_HONEST in tail.reason_codes
    assert "identity_mixed_turn_boundary" in result.qa_flags
    assert "identity_partial_line_speaker_change" in result.qa_flags
    assert "identity_private_excerpt_suppressed" in result.qa_flags


@pytest.mark.parametrize(
    ("previous_text", "fragment_text", "expected"),
    [
        ("Synthetic lead", "continuation", ReasonCode.PHRASE_CONTINUATION_OVERRIDE),
        ("Synthetic question?", "Answer", ReasonCode.QUESTION_ANSWER_BOUNDARY_OVERRIDE),
    ],
)
def test_fragment_boundary_heuristics_are_review_only_and_audited(
    tmp_path: Path,
    previous_text: str,
    fragment_text: str,
    expected: ReasonCode,
) -> None:
    job_dir = tmp_path / expected.value
    transcript = write_job(job_dir)
    turns = list(transcript.speaker_turns)
    turns[0] = turns[0].model_copy(update={"text": previous_text})
    turns[1] = turns[1].model_copy(update={"text": fragment_text})
    transcript = transcript.model_copy(update={"speaker_turns": turns})
    (job_dir / "transcript.json").write_bytes(deterministic_json_bytes(transcript))
    (job_dir / "transcript.md").write_text(render_markdown(transcript), encoding="utf-8")
    (job_dir / "transcript.srt").write_text(render_srt(transcript), encoding="utf-8")
    (job_dir / "transcript.vtt").write_text(render_vtt(transcript), encoding="utf-8")

    result = resolve_identities(job_dir).report
    fragment = next(item for item in result.turn_decisions if item.turn_index == 1)

    assert fragment.status is ResolutionStatus.REVIEW
    assert expected in fragment.reason_codes
    assert f"identity_{expected.value}" in result.qa_flags


def test_label_only_external_drift_preserves_folded_unknown_provenance(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    transcript = write_job(job_dir)
    target = tmp_path / "review.md"
    external = render_markdown(transcript).replace("**UNKNOWN**", "**SPEAKER_00**")
    target.write_text(external, encoding="utf-8")
    target_before = target.read_bytes()

    result = resolve_identities(job_dir, review_options(external_transcript=target)).report
    decision = result.turn_decisions[1]

    assert result.validation_summary.external_transcript_drift_classification is (
        DriftClassification.LABEL_ONLY
    )
    assert decision.canonical_label == "UNKNOWN"
    assert decision.target_label == "SPEAKER_00"
    assert decision.assignment_scope is AssignmentScope.FRAGMENT
    assert ReasonCode.CANONICAL_UNKNOWN_FOLDED_INTO_CLUSTER in decision.reason_codes
    assert result.validation_summary.canonical_unknown_folded_into_cluster_count >= 1
    assert target.read_bytes() == target_before


def test_folded_canonical_unknown_is_not_blindly_relabelled_by_safe_global_map(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir, fold_unknown_into_cluster=True)
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir)

    result = resolve_identities(job_dir, safe_options(roster)).report
    folded = result.turn_decisions[1]
    assert folded.assignment_scope is AssignmentScope.FRAGMENT
    assert folded.canonical_label == "UNKNOWN"
    assert folded.original_diarization_label == "SPEAKER_00"
    assert ReasonCode.CANONICAL_UNKNOWN_FOLDED_INTO_CLUSTER in folded.reason_codes
    assert folded.status is ResolutionStatus.REVIEW
    srt = (job_dir / "transcript.identity_mapped.srt").read_text(encoding="utf-8")
    assert "SPEAKER_00: Synthetic bridge." in srt
    assert f"{ALPHA}: Synthetic bridge." not in srt


@pytest.mark.parametrize("drift", ["text", "segmentation", "line_count"])
def test_unsafe_external_drift_blocks_all_identity_exports(tmp_path: Path, drift: str) -> None:
    job_dir = tmp_path / drift
    transcript = write_job(job_dir)
    target = tmp_path / f"{drift}.md"
    external = render_markdown(transcript)
    if drift == "text":
        external = external.replace("Generic response.", "Changed response.")
    elif drift == "segmentation":
        external = external.replace("**SPEAKER_00**  ", "SPEAKER_00", 1)
    else:
        external += "\n"
    target.write_text(external, encoding="utf-8")

    with pytest.raises(IdentityResolverError) as error:
        resolve_identities(
            job_dir,
            review_options(
                external_transcript=target,
                write_identity_mapped_exports=True,
            ),
        )
    assert error.value.code == "external_transcript_drift"
    assert not list(job_dir.glob("transcript.identity_mapped.*"))


def test_audit_has_exact_set_cardinality_and_reciprocal_parent_child_links(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    result = resolve_identities(job_dir)
    audit = AttributionAudit.model_validate_json(
        (job_dir / "speaker_identity_attribution_audit.json").read_text(encoding="utf-8")
    )
    expected = {
        decision.decision_id
        for decision in [*result.report.cluster_decisions, *result.report.turn_decisions]
    }
    actual = [row.decision_id for row in audit.rows]

    assert len(actual) == len(expected)
    assert set(actual) == expected
    assert len(actual) == len(set(actual))
    assert result.report.validation_summary.audit_complete is True
    assert set(result.report.validation_summary.expected_decision_ids) == expected
    assert set(result.report.validation_summary.actual_decision_ids) == expected
    turn_by_id = {decision.decision_id: decision for decision in result.report.turn_decisions}
    for cluster in result.report.cluster_decisions:
        assert all(
            turn_by_id[child].parent_decision_id == cluster.decision_id
            for child in cluster.child_decision_ids
        )


def test_identical_repeat_is_byte_idempotent_and_canonical_hashes_do_not_change(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    canonical_before = _canonical_snapshot(job_dir)
    first = resolve_identities(job_dir)
    first_bytes = {name: (job_dir / name).read_bytes() for name in first.written_artifacts}

    second = resolve_identities(job_dir)
    second_bytes = {name: (job_dir / name).read_bytes() for name in second.written_artifacts}

    assert first.report.run_id == second.report.run_id
    assert first_bytes == second_bytes
    assert _canonical_snapshot(job_dir) == canonical_before
    assert second.report.validation_summary.canonical_hashes_unchanged is True
    assert list(job_dir.glob(".speaker_identity.*")) == [job_dir / ".speaker_identity.lock"]


def _write_variant(job_dir: Path, name: str, turns: list[DiarizationTurn]) -> None:
    (job_dir / name).write_bytes(deterministic_json_bytes(DiarizationResult(turns=turns)))


def _write_stability_config(path: Path, artifacts: list[str]) -> Path:
    config = StabilityConfig(
        variants=[
            StabilityVariantRequest(
                variant_id=f"variant:{index}",
                artifact=artifact,
                backend="synthetic",
                device="cpu",
            )
            for index, artifact in enumerate(artifacts)
        ]
    )
    path.write_bytes(deterministic_json_bytes(config))
    return path


def test_stability_success_records_every_variant_and_preserves_canonical_diarization(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    transcript = write_job(job_dir)
    _write_variant(job_dir, "variant.json", transcript.diarization_turns)
    config = _write_stability_config(tmp_path / "stability.json", ["variant.json"])
    canonical = _canonical_snapshot(job_dir)

    resolve_identities(job_dir, review_options(stability_config_path=config))
    report = DiarizationStabilityReport.model_validate_json(
        (job_dir / "speaker_identity_diarization_stability.json").read_text(encoding="utf-8")
    )

    assert [variant.status for variant in report.variants] == ["success"]
    assert [(variant.backend, variant.device) for variant in report.variants] == [
        ("synthetic", "cpu")
    ]
    assert all(report.turn_stability[index].stable_label for index in [0, 2, 3, 4])
    assert _canonical_snapshot(job_dir) == canonical


def test_stability_is_invariant_to_global_anonymous_label_permutation(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    transcript = write_job(job_dir)
    _write_variant(job_dir, "agreeing.json", transcript.diarization_turns)
    disagreeing = [
        turn.model_copy(
            update={"speaker": ("SPEAKER_01" if turn.speaker == "SPEAKER_00" else "SPEAKER_00")}
        )
        for turn in transcript.diarization_turns
    ]
    _write_variant(job_dir, "disagreeing.json", disagreeing)
    config = _write_stability_config(
        tmp_path / "stability.json", ["agreeing.json", "disagreeing.json"]
    )

    resolve_identities(job_dir, review_options(stability_config_path=config))
    stability = DiarizationStabilityReport.model_validate_json(
        (job_dir / "speaker_identity_diarization_stability.json").read_text(encoding="utf-8")
    )

    assert [variant.status for variant in stability.variants] == ["success", "success"]
    assert all(stability.turn_stability[index].stable_label for index in [0, 2, 3, 4])


def test_stability_detects_partition_disagreement_after_optimal_label_alignment(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    transcript = write_job(job_dir)
    collapsed = [
        turn.model_copy(update={"speaker": "SPEAKER_00"}) for turn in transcript.diarization_turns
    ]
    _write_variant(job_dir, "collapsed.json", collapsed)
    config = _write_stability_config(tmp_path / "stability.json", ["collapsed.json"])

    resolve_identities(job_dir, review_options(stability_config_path=config))
    stability = DiarizationStabilityReport.model_validate_json(
        (job_dir / "speaker_identity_diarization_stability.json").read_text(encoding="utf-8")
    )

    assert stability.turn_stability[0].stable_label is True
    assert stability.turn_stability[2].stable_label is True
    assert stability.turn_stability[3].stable_label is False
    assert stability.turn_stability[4].stable_label is False
    assert all(
        ReasonCode.ENSEMBLE_DISAGREEMENT in stability.turn_stability[index].reason_codes
        for index in [3, 4]
    )


def test_stability_failure_and_label_disagreement_block_safe_mapping_for_affected_turns(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    transcript = write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir)
    disagreeing = [
        turn.model_copy(
            update={"speaker": "SPEAKER_01" if turn.speaker == "SPEAKER_00" else "SPEAKER_00"}
        )
        for turn in transcript.diarization_turns
    ]
    _write_variant(job_dir, "disagreeing.json", disagreeing)
    config = _write_stability_config(
        tmp_path / "stability.json", ["disagreeing.json", "missing.json"]
    )

    result = resolve_identities(
        job_dir,
        safe_options(roster, stability_config_path=config),
    )
    stability = DiarizationStabilityReport.model_validate_json(
        (job_dir / "speaker_identity_diarization_stability.json").read_text(encoding="utf-8")
    )

    assert [variant.status for variant in stability.variants] == ["success", "failed"]
    assert [(variant.backend, variant.device) for variant in stability.variants] == [
        ("synthetic", "cpu"),
        ("synthetic", "cpu"),
    ]
    assert "identity_stability_variant_failed" in stability.warnings
    assert all(not turn.stable_label for turn in stability.turn_stability)
    assert all(
        decision.status is not ResolutionStatus.MAPPED
        for decision in result.report.turn_decisions
        if decision.assignment_scope is AssignmentScope.TURN
    )
    identity_srt = (job_dir / "transcript.identity_mapped.srt").read_text(encoding="utf-8")
    assert f"{ALPHA}:" not in identity_srt
    assert f"{BETA}:" not in identity_srt


def test_media_queue_is_bounded_and_review_report_can_confirm_unattributed_tail(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    first = resolve_identities(job_dir)
    queue = MediaReviewQueue.model_validate_json(
        (job_dir / "speaker_identity_media_review_queue.json").read_text(encoding="utf-8")
    )
    tail_region = next(region for region in queue.regions if region.turn_index == 5)
    assert 0.0 <= tail_region.start <= tail_region.end <= 5.5
    report_path = tmp_path / "media-review.json"
    report_path.write_bytes(
        deterministic_json_bytes(
            MediaReviewReport(
                run_id=first.report.run_id,
                job_fingerprint=first.report.resolver_context.job_fingerprint,
                regions=[
                    ReviewedMediaRegion(
                        region_id=tail_region.region_id,
                        conclusions=["tail_artifact"],
                        measurements={"speech_probability": 0.0},
                        reviewed_at=datetime(2026, 7, 9, tzinfo=UTC),
                    )
                ],
            )
        )
    )
    report_before = report_path.read_bytes()

    second = resolve_identities(job_dir, reviewed_media_options(report_path)).report
    tail = second.turn_decisions[5]
    assert tail.status is ResolutionStatus.UNATTRIBUTED
    assert tail.confidence == 1.0
    assert ReasonCode.SOURCE_MEDIA_TAIL_ARTIFACT in tail.reason_codes
    assert tail.requires_media_review is False
    assert report_path.read_bytes() == report_before

    second_bytes = {
        path.name: path.read_bytes() for path in job_dir.glob("speaker_identity*") if path.is_file()
    }
    repeated = resolve_identities(job_dir, reviewed_media_options(report_path)).report
    repeated_bytes = {
        path.name: path.read_bytes() for path in job_dir.glob("speaker_identity*") if path.is_file()
    }
    assert repeated.run_id == second.run_id
    assert repeated_bytes == second_bytes


def test_media_review_rejects_stale_run_and_nonqueued_current_decision(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    first = resolve_identities(job_dir).report
    queue = MediaReviewQueue.model_validate_json(
        (job_dir / "speaker_identity_media_review_queue.json").read_text(encoding="utf-8")
    )
    queued = queue.regions[0]

    stale_path = tmp_path / "stale-media-review.json"
    stale_path.write_bytes(
        deterministic_json_bytes(
            MediaReviewReport(
                run_id="run:stale-lineage",
                job_fingerprint=first.resolver_context.job_fingerprint,
                regions=[
                    ReviewedMediaRegion(
                        region_id=queued.region_id,
                        conclusions=["confirmed_speech"],
                        reviewed_at=datetime(2026, 7, 9, tzinfo=UTC),
                    )
                ],
            )
        )
    )
    with pytest.raises(IdentityResolverError) as stale:
        resolve_identities(job_dir, reviewed_media_options(stale_path))
    assert stale.value.code == "stale_media_review_run"

    nonqueued_decision = next(
        decision for decision in first.turn_decisions if not decision.requires_media_review
    )
    nonqueued_path = tmp_path / "nonqueued-media-review.json"
    nonqueued_path.write_bytes(
        deterministic_json_bytes(
            MediaReviewReport(
                run_id=first.run_id,
                job_fingerprint=first.resolver_context.job_fingerprint,
                regions=[
                    ReviewedMediaRegion(
                        region_id="media:" + nonqueued_decision.decision_id.split(":", 1)[1],
                        conclusions=["confirmed_speech"],
                        reviewed_at=datetime(2026, 7, 9, tzinfo=UTC),
                    )
                ],
            )
        )
    )
    with pytest.raises(IdentityResolverError) as nonqueued:
        resolve_identities(job_dir, reviewed_media_options(nonqueued_path))
    assert nonqueued.value.code == "unknown_media_region"


def test_media_review_duplicate_regions_fail_strict_schema_validation(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    first = resolve_identities(job_dir).report
    queue = MediaReviewQueue.model_validate_json(
        (job_dir / "speaker_identity_media_review_queue.json").read_text(encoding="utf-8")
    )
    region = ReviewedMediaRegion(
        region_id=queue.regions[0].region_id,
        conclusions=["confirmed_speech"],
        reviewed_at=datetime(2026, 7, 9, tzinfo=UTC),
    ).model_dump(mode="json")
    report_path = tmp_path / "duplicate-media-review.json"
    report_path.write_bytes(
        deterministic_json_bytes(
            {
                "schema_version": "speaker_identity_media_review_report.v1",
                "run_id": first.run_id,
                "job_fingerprint": first.resolver_context.job_fingerprint,
                "regions": [region, region],
            }
        )
    )

    with pytest.raises(IdentityResolverError) as duplicate:
        resolve_identities(job_dir, reviewed_media_options(report_path))
    assert duplicate.value.code == "malformed_optional_input"


def test_optional_input_snapshot_uses_the_same_hashed_and_parsed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference_job = tmp_path / "reference-job"
    target_job = tmp_path / "target-job"
    write_job(reference_job)
    write_job(target_job)
    write_profile_map(reference_job)
    write_profile_map(target_job)
    roster = write_roster(tmp_path / "roster.json")
    original_roster = roster.read_bytes()
    reference = resolve_identities(reference_job, safe_options(roster)).report
    roster.write_bytes(original_roster)

    real_load = identity_resolver_module.load_identity_job

    def mutate_after_acquisition(*args: object, **kwargs: object):
        write_roster(roster, expected=1, participants=1)
        return real_load(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(identity_resolver_module, "load_identity_job", mutate_after_acquisition)
    result = resolve_identities(target_job, safe_options(roster)).report

    assert roster.read_bytes() != original_roster
    assert result.run_id == reference.run_id
    assert {decision.display_label for decision in result.cluster_decisions} == {ALPHA, BETA}


def test_sibling_evidence_never_transfers_identity_through_anonymous_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LTM_SPEAKER_RENAME_DIR", str(tmp_path / "renames"))
    sibling = tmp_path / "sibling"
    write_job(sibling, variant="sibling")
    sibling_roster = write_roster(tmp_path / "sibling-roster.json")
    rename_speaker("sibling-alpha", "SPEAKER_00", ALPHA, job_dir=sibling)
    rename_speaker("sibling-beta", "SPEAKER_01", BETA, job_dir=sibling)
    resolve_identities(sibling, safe_options(sibling_roster))

    current = tmp_path / "current"
    write_job(current)
    current_roster = write_roster(tmp_path / "current-roster.json")
    linked = resolve_identities(
        current,
        review_options(roster_path=current_roster, sibling_jobs=(sibling,)),
    )
    evidence = [
        json.loads(line)
        for line in (current / "speaker_identity_evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    sibling_evidence = [row for row in evidence if row["tier"] == EvidenceTier.SIBLING.value]
    assert sibling_evidence
    assert all(row["assignment_scope"] == AssignmentScope.BACKEND.value for row in sibling_evidence)
    assert all(row["candidate_name"] is None for row in sibling_evidence)
    assert all(row["confidence"] == 0.0 for row in sibling_evidence)
    assert all(
        decision.status is not ResolutionStatus.MAPPED
        for decision in linked.report.cluster_decisions
    )

    changed = tmp_path / "changed-transcript-same-input"
    write_job(changed, variant="changed")
    changed_roster = write_roster(tmp_path / "changed-roster.json")
    changed_result = resolve_identities(
        changed,
        review_options(roster_path=changed_roster, sibling_jobs=(sibling,)),
    ).report
    changed_evidence = [
        json.loads(line)
        for line in (changed / "speaker_identity_evidence.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(row["tier"] == EvidenceTier.SIBLING.value for row in changed_evidence)
    assert all(
        row["candidate_name"] is None
        for row in changed_evidence
        if row["tier"] == EvidenceTier.SIBLING.value
    )
    assert all(
        decision.status is not ResolutionStatus.MAPPED
        for decision in changed_result.cluster_decisions
    )

    stale = tmp_path / "stale"
    write_job(stale, variant="stale")
    stale_metadata = json.loads((stale / "run_metadata.json").read_text(encoding="utf-8"))
    stale_metadata["input_file_hash"] = "b" * 64
    (stale / "run_metadata.json").write_text(json.dumps(stale_metadata), encoding="utf-8")
    stale_roster = write_roster(tmp_path / "stale-roster.json")
    write_profile_map(stale)
    resolve_identities(stale, safe_options(stale_roster))
    stale_result = resolve_identities(
        current,
        review_options(roster_path=current_roster, sibling_jobs=(stale,)),
    ).report
    assert ReasonCode.SIBLING_EVIDENCE_STALE.value in stale_result.warnings


def test_sibling_count_is_bounded_and_symlink_roots_fail_closed(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    with pytest.raises(IdentityResolverError) as count:
        resolve_identities(
            job_dir,
            review_options(
                policy=ResolverPolicy(mode=ResolverMode.REVIEW, max_sibling_jobs=1),
                sibling_jobs=(first, second),
            ),
        )
    assert count.value.code == "too_many_siblings"

    sibling_root = tmp_path / "siblings"
    sibling_root.mkdir()
    root_link = tmp_path / "sibling-link"
    root_link.symlink_to(sibling_root, target_is_directory=True)
    with pytest.raises(IdentityResolverError) as symlink:
        resolve_identities(job_dir, review_options(sibling_root=root_link))
    assert symlink.value.code == "unsafe_sibling_root"


def test_sibling_root_stops_iterating_as_soon_as_the_cap_is_exceeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    sibling_root = tmp_path / "many-siblings"
    sibling_root.mkdir()
    for index in range(12):
        (sibling_root / f"sibling-{index:02d}").mkdir()

    inspected_children: list[str] = []
    original_is_dir = Path.is_dir

    def counted_is_dir(path: Path) -> bool:
        if path.parent == sibling_root:
            inspected_children.append(path.name)
        return original_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", counted_is_dir)
    with pytest.raises(IdentityResolverError) as error:
        resolve_identities(
            job_dir,
            review_options(
                policy=ResolverPolicy(mode=ResolverMode.REVIEW, max_sibling_jobs=1),
                sibling_root=sibling_root,
            ),
        )

    assert error.value.code == "too_many_siblings"
    assert len(inspected_children) == 2


def test_sibling_root_caps_total_entries_not_only_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    sibling_root = tmp_path / "file-heavy-root"
    sibling_root.mkdir()
    for index in range(3):
        (sibling_root / f"not-a-job-{index}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(identity_resolver_module, "MAX_SIBLING_ROOT_ENTRIES", 2)

    with pytest.raises(IdentityResolverError) as error:
        resolve_identities(job_dir, review_options(sibling_root=sibling_root))

    assert error.value.code == "sibling_root_too_large"


def test_sibling_manifest_caps_unique_paths_before_resolution_and_deduplicates(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    oversized = tmp_path / "oversized-manifest.json"
    oversized.write_bytes(
        deterministic_json_bytes(
            {
                "schema_version": "speaker_identity_sibling_manifest.v1",
                "jobs": [str(tmp_path / f"missing-{index}") for index in range(20)],
            }
        )
    )

    with pytest.raises(IdentityResolverError) as count:
        resolve_identities(
            job_dir,
            review_options(
                policy=ResolverPolicy(mode=ResolverMode.REVIEW, max_sibling_jobs=2),
                sibling_manifest=oversized,
            ),
        )
    assert count.value.code == "too_many_siblings"

    sibling = tmp_path / "one-sibling"
    sibling.mkdir()
    deduplicated = tmp_path / "deduplicated-manifest.json"
    deduplicated.write_bytes(
        deterministic_json_bytes(
            {
                "schema_version": "speaker_identity_sibling_manifest.v1",
                "jobs": [str(sibling)] * 100,
            }
        )
    )
    result = resolve_identities(
        job_dir,
        review_options(
            policy=ResolverPolicy(mode=ResolverMode.REVIEW, max_sibling_jobs=1),
            sibling_manifest=deduplicated,
        ),
    )
    assert result.report.validation_summary.valid is True


def test_missing_explicit_sibling_is_a_redacted_resolver_error(tmp_path: Path) -> None:
    job_dir = tmp_path / "private-job"
    write_job(job_dir)
    missing = tmp_path / "private-missing-sibling"
    with pytest.raises(IdentityResolverError) as error:
        resolve_identities(job_dir, review_options(sibling_jobs=(missing,)))
    assert error.value.code == "missing_sibling_job"
    assert str(tmp_path) not in str(error.value)


def test_safe_vs_approved_precedence_and_collision_are_deterministic(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir)
    review = resolve_identities(
        job_dir, review_options(known_speakers=2, roster_path=roster)
    ).report
    approved = _write_approved_map(
        tmp_path / "approved.json",
        job_dir,
        review,
        cluster_mappings={"SPEAKER_00": ALPHA, "SPEAKER_01": ALPHA},
    )
    result = resolve_identities(
        job_dir,
        ResolveIdentityOptions(
            policy=ResolverPolicy(mode=ResolverMode.APPLY_APPROVED),
            known_speakers=2,
            roster_path=roster,
            approved_map_path=approved,
        ),
    ).report

    assert all(
        decision.status is ResolutionStatus.CONFLICT for decision in result.cluster_decisions
    )
    assert all(decision.display_label is None for decision in result.cluster_decisions)
    assert all(
        ReasonCode.COMPETING_CANDIDATE_REJECTED in decision.reason_codes
        for decision in result.cluster_decisions
    )
    assert all(
        ALPHA in {rejection.candidate_name for rejection in decision.rejections}
        for decision in result.cluster_decisions
    )


def test_all_sidecars_keep_srt_primary_and_csv_carries_provenance_columns(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    roster = write_roster(tmp_path / "roster.json")
    write_profile_map(job_dir)
    result = resolve_identities(
        job_dir,
        safe_options(
            roster,
            policy=ResolverPolicy(
                mode=ResolverMode.APPLY_SAFE,
                deliverable_format=DeliverableFormat.ALL,
            ),
        ),
    )
    expected = {
        "transcript.identity_mapped.json",
        "transcript.identity_mapped.srt",
        "transcript.identity_mapped.md",
        "transcript.identity_mapped.vtt",
        "transcript.identity_mapped.txt",
        "transcript.identity_mapped.segments.csv",
    }
    assert expected <= set(result.written_artifacts)
    wrapper = IdentityMappedTranscript.model_validate_json(
        (job_dir / "transcript.identity_mapped.json").read_text(encoding="utf-8")
    )
    canonical = Transcript.model_validate_json(
        (job_dir / "transcript.json").read_text(encoding="utf-8")
    )
    assert wrapper.primary_deliverable == "transcript.identity_mapped.srt"
    assert wrapper.transcript.words == canonical.words
    assert [
        (turn.speaker, turn.start, turn.end, turn.text, turn.words)
        for turn in wrapper.transcript.speaker_turns
    ] == [
        (turn.speaker, turn.start, turn.end, turn.text, turn.words)
        for turn in canonical.speaker_turns
    ]
    assert not (job_dir / "transcript.identity_mapped.rttm").exists()
    with (job_dir / "transcript.identity_mapped.segments.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == len(synthetic_transcript().speaker_turns)
    assert {
        "original_label",
        "canonical_label",
        "target_label",
        "display_label",
    } <= set(rows[0])


def test_malformed_and_symlinked_inputs_fail_without_absolute_path_or_excerpt_leak(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "private-job-name"
    write_job(job_dir)
    malformed = tmp_path / "private-roster-name.json"
    malformed.write_text('{"schema_version":"speaker_identity_roster.v1"}', encoding="utf-8")
    with pytest.raises(IdentityResolverError) as malformed_error:
        resolve_identities(job_dir, review_options(roster_path=malformed))
    assert malformed_error.value.code == "malformed_optional_input"
    assert str(tmp_path) not in str(malformed_error.value)
    assert "I am" not in str(malformed_error.value)

    target = tmp_path / "regular.json"
    write_roster(target)
    roster_link = tmp_path / "private-roster-link.json"
    roster_link.symlink_to(target)
    with pytest.raises(IdentityResolverError) as symlink_error:
        resolve_identities(job_dir, review_options(roster_path=roster_link))
    assert symlink_error.value.code == "unsafe_symlink"
    assert str(tmp_path) not in str(symlink_error.value)

    job_link = tmp_path / "private-job-link"
    job_link.symlink_to(job_dir, target_is_directory=True)
    with pytest.raises(IdentityResolverError) as job_error:
        resolve_identities(job_link)
    assert job_error.value.code == "unsafe_job_symlink"
    assert str(tmp_path) not in str(job_error.value)


def test_report_candidates_and_audit_models_read_every_written_authoritative_artifact(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    write_job(job_dir)
    result = resolve_identities(job_dir)
    report = _report(job_dir)
    candidates = CandidateSet.model_validate_json(
        (job_dir / "speaker_identity_candidates.json").read_text(encoding="utf-8")
    )
    audit = AttributionAudit.model_validate_json(
        (job_dir / "speaker_identity_attribution_audit.json").read_text(encoding="utf-8")
    )
    queue = MediaReviewQueue.model_validate_json(
        (job_dir / "speaker_identity_media_review_queue.json").read_text(encoding="utf-8")
    )

    assert report == result.report
    assert candidates.run_id == report.run_id
    assert audit.run_id == report.run_id
    assert queue.run_id == report.run_id
    assert report.commit_state == "complete"
    assert report.validation_summary.valid is True
