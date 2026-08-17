# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fail-closed cleanup and identity composition for completed local jobs."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from .cleanup import (
    APPLE_FOUNDATION_MODEL_ID,
    SOURCE_GRAMMAR_SCORER_VERSION,
    WINDOW_AGGREGATE_SCORER_VERSION,
    _build_presentation_decoder_words,
    _decoded_surface_quality,
    _presentation_context_transcript,
    _source_and_grammar_scorer,
    cleanup_is_safe,
    cleanup_source_text,
    run_cleanup,
    scorer_output_digest,
    source_grammar_configuration_digest,
    window_aggregate_configuration_digest,
)
from .cleanup_policy import CleanupRejectedError
from .code_provenance import current_code_revision
from .config import PipelineConfig
from .configuration_evidence import configuration_evidence
from .errors import LTMError
from .export.csv_export import formula_safe_cell
from .export.srt_export import render_srt
from .export.subtitle import iter_subtitle_cues, overlay_cue_word_surfaces
from .export.vtt_export import render_vtt
from .presentation import (
    DECODER_VERSION,
    HARD96_ESCALATION_POLICY,
    MAX_PRESENTATION_PLAN_BYTES,
    DecoderWord,
    PresentationPlan,
    TrailingPunctuation,
    apply_presentation_plan,
    deserialize_presentation_plan,
)
from .presentation_sat import (
    SaTMode,
    SaTScoreStatus,
    sat_score_input_digests,
    validate_sat_score_receipt_payload,
)
from .presentation_sat import (
    _scorer_output_digest as sat_scorer_output_digest,
)
from .schemas import AlignedWord, SpeakerTurn, Transcript
from .semantic_cleanup import (
    CanonicalWord,
    LexicalCleanupPolicy,
    SemanticCleanupPlan,
    deserialize_semantic_cleanup_plan,
    deserialize_semantic_reconciliation_result,
    lexical_cleanup_policy_sha256,
    validate_semantic_cleanup_plan,
)
from .speaker_id.identity_exports import (
    build_identity_transcript,
    render_identity_srt,
    render_identity_vtt,
)
from .speaker_id.identity_io import deterministic_json_bytes, strict_json_bytes
from .speaker_id.identity_models import IdentityDecision, IdentityReport, ResolutionStatus
from .utils import atomic_text_write, sha256_file

MAX_POLISH_JSON_BYTES = 64 * 1024 * 1024
MAX_POLISH_TEXT_BYTES = 32 * 1024 * 1024
_KNOWN_CLUSTER_RE = re.compile(r"\bSPEAKER_\d+\b", re.IGNORECASE)
_TRAILING_PRESENTATION = ".,?!;:…\"'”’)]}）】」』》"
_LEADING_PRESENTATION = "\"'“‘([{（【「『《"
_SOURCE_ARTIFACTS = (
    "canonical.wav",
    "transcript.json",
    "transcript.srt",
    "transcript.vtt",
    "transcript.md",
    "transcript.txt",
    "transcript.segments.csv",
    "run_metadata.json",
    "alignment_report.json",
    "speaker_identity_report.json",
    # This may be a rejected post-transaction derivative. It is bound as
    # evidence only and is never used as a composition source.
    "transcript.identity_mapped.srt",
)
_POLISHED_BASENAME = "transcript.polished.identity_mapped"


class PolishError(LTMError):
    """A privacy-safe refusal to publish an invalid polished family."""


def polish_completed_job(
    job_dir: Path,
    out_dir: Path,
    config: PipelineConfig,
    *,
    evidence_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    """Create one fresh, hash-bound polished family without rerunning inference.

    The completed job is read-only. All work occurs in a private sibling staging
    directory, and the entire candidate is atomically renamed into ``out_dir``
    only after cleanup, identity composition, invariant checks, and source
    rehashing succeed.
    """

    source = _resolve_source_job(job_dir)
    destination = out_dir.expanduser()
    _require_fresh_destination(destination)
    if config.cleanup.mode == "off":
        raise PolishError("Polishing requires an explicit local cleanup backend.")
    if config.cleanup.scope != "punctuation":
        raise PolishError("Polishing requires the typed transcript cleanup lane.")
    if not config.offline:
        raise PolishError("Polishing a private completed job requires offline mode.")
    if not bool(getattr(config.cleanup, "required_complete", False)):
        raise PolishError("Polishing requires complete cleanup with zero fallback windows.")

    source_hashes = _source_hashes(source)
    evidence_before = _evidence_hashes(evidence_paths)
    production_source_revision = current_code_revision()
    canonical = _load_transcript(source / "transcript.json")
    identity_report = _load_identity_report(source / "speaker_identity_report.json")
    run_metadata = _load_json_object(source / "run_metadata.json")
    _validate_identity_binding(identity_report, source_hashes)

    parent = destination.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging_path = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging.", dir=parent))
    os.chmod(staging_path, 0o700)
    published = False
    try:
        with tempfile.TemporaryDirectory(prefix=".cleanup-work.", dir=staging_path) as work:
            cleanup_work = Path(work)
            cleanup_report = run_cleanup(canonical, cleanup_work, config)
            _require_complete_cleanup(cleanup_report, canonical, config)
            cleaned = _load_transcript(cleanup_work / "transcript.cleaned.json")
            presentation_plan_path = cleanup_work / "presentation_plan.json"
            presentation_plan = _read_bounded(
                presentation_plan_path,
                limit=MAX_PRESENTATION_PLAN_BYTES,
            )
            presentation_plan_contract = _validate_presentation_plan_binding(
                cleanup_report,
                presentation_plan,
            )
            _write_private_bytes(
                staging_path / "presentation_plan.json",
                presentation_plan,
            )
            semantic_plan_path = cleanup_work / "semantic_cleanup_plan.json"
            semantic_plan = _read_bounded(
                semantic_plan_path,
                limit=MAX_POLISH_JSON_BYTES,
            )
            semantic_plan_contract = _validate_semantic_plan_binding(
                cleanup_report,
                semantic_plan,
            )
            proposal_request = cleanup_report.get("request")
            if not isinstance(proposal_request, Mapping):
                raise PolishError("Cleanup report is missing proposal-source evidence.")
            _validate_proposal_source_evidence(
                proposal_request,
                config,
                decoder_words=_build_presentation_decoder_words(
                    canonical,
                    semantic_plan_contract.clean_surfaces,
                    source_word_indices=semantic_plan_contract.clean_to_source_indices,
                ),
                presentation_plan=presentation_plan_contract,
            )
            _validate_surface_quality_replay(
                proposal_request,
                canonical,
                semantic_plan_contract,
                presentation_plan_contract,
                config,
            )
            _write_private_bytes(
                staging_path / "semantic_cleanup_plan.json",
                semantic_plan,
            )
            semantic_receipt_path = cleanup_work / "semantic_cleanup_receipt.json"
            semantic_receipt = _read_bounded(
                semantic_receipt_path,
                limit=MAX_POLISH_JSON_BYTES,
            )
            _validate_semantic_receipt_binding(
                cleanup_report,
                semantic_receipt,
                semantic_plan,
            )
            _write_private_bytes(
                staging_path / "semantic_cleanup_receipt.json",
                semantic_receipt,
            )
            cleanup_diff_path = cleanup_work / "cleanup_diff.json"
            if cleanup_diff_path.is_file():
                atomic_text_write(
                    staging_path / "cleanup_diff.json",
                    cleanup_diff_path.read_text(encoding="utf-8"),
                )

        invariant_report = _validate_cleaned_transcript(
            canonical,
            cleaned,
            config,
            semantic_plan_contract,
            presentation_plan_contract,
        )
        decisions = [*identity_report.cluster_decisions, *identity_report.turn_decisions]
        outputs = _render_polished_family(
            canonical=canonical,
            cleaned=cleaned,
            decisions=decisions,
            config=config,
        )
        output_validation = _validate_outputs(
            outputs=outputs,
            canonical=canonical,
            cleaned=cleaned,
            decisions=decisions,
            config=config,
        )
        for name, data in outputs.items():
            _write_private_bytes(staging_path / name, data)

        cleanup_receipt = _cleanup_receipt(cleanup_report, config)
        cleanup_receipt["derived_transcript_hashes"] = {
            name: {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
            for name, data in sorted(outputs.items())
        }
        _write_private_bytes(
            staging_path / "cleanup_report.json",
            deterministic_json_bytes(cleanup_receipt),
        )
        validation_report: dict[str, Any] = {
            "schema_version": "auralign_polished_validation.v2",
            "valid": True,
            "cleanup": cleanup_receipt,
            "canonical_invariants": invariant_report,
            "output_invariants": output_validation,
        }
        _write_private_bytes(
            staging_path / "validation_report.json",
            deterministic_json_bytes(validation_report),
        )

        if _source_hashes(source) != source_hashes:
            raise PolishError("A source job artifact changed during polishing.")
        if _evidence_hashes(evidence_paths) != evidence_before:
            raise PolishError("A bound preservation artifact changed during polishing.")
        if current_code_revision() != production_source_revision:
            raise PolishError("Production source changed during polishing.")

        output_hashes = _directory_hashes(staging_path)
        safe_configuration, configuration_sha256 = configuration_evidence(config)
        manifest: dict[str, Any] = {
            "schema_version": "auralign_polished_manifest.v2",
            "commit_state": "complete",
            "source_job": {
                "job_id": source.name,
                "artifact_hashes": source_hashes,
                "recorded_code_revision": run_metadata.get("code_revision"),
                "input_file_hash": run_metadata.get("input_file_hash"),
                "identity_resolver_run_id": identity_report.run_id,
                "identity_report_sha256": source_hashes["speaker_identity_report.json"],
                "identity_mapping_digest": hashlib.sha256(
                    deterministic_json_bytes(
                        [
                            {
                                "display_label": decision.display_label,
                                "status": decision.status.value,
                            }
                            for decision in identity_report.cluster_decisions
                        ]
                    )
                ).hexdigest(),
            },
            "preserved_evidence": evidence_before,
            "production_source_revision": production_source_revision,
            "postprocess_configuration_sha256": configuration_sha256,
            "postprocess_configuration": safe_configuration,
            "cleanup": cleanup_receipt,
            "validation": {
                "valid": True,
                "word_count": invariant_report["word_count"],
                "turn_count": invariant_report["turn_count"],
                "canonical_evidence_preserved": True,
                "semantic_plan_traceability_complete": True,
                "presentation_lexical_sequence_preserved": True,
                "all_nonpresentation_fields_preserved": True,
                "zero_residual_known_cluster_labels": True,
                "canonical_source_rehashed_unchanged": True,
                "bound_evidence_rehashed_unchanged": True,
            },
            "outputs": output_hashes,
            "manifest_self_hash": None,
            "manifest_self_hash_reason": "self-referential hash intentionally excluded",
        }
        _write_private_bytes(
            staging_path / "derived_manifest.json",
            deterministic_json_bytes(manifest),
        )
        _fsync_tree(staging_path)
        _require_fresh_destination(destination)
        os.replace(staging_path, destination)
        published = True
        return manifest
    finally:
        if not published and staging_path.exists():
            shutil.rmtree(staging_path)


def compose_completed_job_derivatives(
    job_dir: Path,
    out_dir: Path,
    config: PipelineConfig,
) -> dict[str, Any]:
    """Compose already-accepted cleanup with identity after a normal pipeline run."""

    source = _resolve_source_job(job_dir)
    destination = out_dir.expanduser()
    _require_fresh_destination(destination)
    canonical = _load_transcript(source / "transcript.json")
    cleaned = _load_transcript(source / "transcript.cleaned.json")
    cleanup_report = _load_json_object(source / "cleanup_report.json")
    identity_report = _load_identity_report(source / "speaker_identity_report.json")
    run_metadata = _load_json_object(source / "run_metadata.json")
    source_hashes = _source_hashes(source)
    source_hashes.update(
        {
            "transcript.cleaned.json": sha256_file(source / "transcript.cleaned.json"),
            "cleanup_report.json": sha256_file(source / "cleanup_report.json"),
            "presentation_plan.json": sha256_file(source / "presentation_plan.json"),
            "semantic_cleanup_plan.json": sha256_file(source / "semantic_cleanup_plan.json"),
            "semantic_cleanup_receipt.json": sha256_file(source / "semantic_cleanup_receipt.json"),
        }
    )
    presentation_plan = _read_bounded(
        source / "presentation_plan.json",
        limit=MAX_PRESENTATION_PLAN_BYTES,
    )
    presentation_plan_contract = _validate_presentation_plan_binding(
        cleanup_report,
        presentation_plan,
    )
    semantic_plan = _read_bounded(
        source / "semantic_cleanup_plan.json",
        limit=MAX_POLISH_JSON_BYTES,
    )
    semantic_plan_contract = _validate_semantic_plan_binding(
        cleanup_report,
        semantic_plan,
    )
    proposal_request = cleanup_report.get("request")
    if not isinstance(proposal_request, Mapping):
        raise PolishError("Cleanup report is missing proposal-source evidence.")
    _validate_proposal_source_evidence(
        proposal_request,
        config,
        decoder_words=_build_presentation_decoder_words(
            canonical,
            semantic_plan_contract.clean_surfaces,
            source_word_indices=semantic_plan_contract.clean_to_source_indices,
        ),
        presentation_plan=presentation_plan_contract,
    )
    _validate_surface_quality_replay(
        proposal_request,
        canonical,
        semantic_plan_contract,
        presentation_plan_contract,
        config,
    )
    semantic_receipt = _read_bounded(
        source / "semantic_cleanup_receipt.json",
        limit=MAX_POLISH_JSON_BYTES,
    )
    _validate_semantic_receipt_binding(
        cleanup_report,
        semantic_receipt,
        semantic_plan,
    )
    _validate_identity_binding(identity_report, source_hashes)
    _require_complete_cleanup(cleanup_report, canonical, config)
    invariant_report = _validate_cleaned_transcript(
        canonical,
        cleaned,
        config,
        semantic_plan_contract,
        presentation_plan_contract,
    )
    decisions = [*identity_report.cluster_decisions, *identity_report.turn_decisions]
    outputs = _render_polished_family(
        canonical=canonical,
        cleaned=cleaned,
        decisions=decisions,
        config=config,
    )
    output_validation = _validate_outputs(
        outputs=outputs,
        canonical=canonical,
        cleaned=cleaned,
        decisions=decisions,
        config=config,
    )

    parent = destination.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging_path = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging.", dir=parent))
    os.chmod(staging_path, 0o700)
    published = False
    try:
        _write_private_bytes(staging_path / "presentation_plan.json", presentation_plan)
        _write_private_bytes(staging_path / "semantic_cleanup_plan.json", semantic_plan)
        _write_private_bytes(
            staging_path / "semantic_cleanup_receipt.json",
            semantic_receipt,
        )
        for name, data in outputs.items():
            _write_private_bytes(staging_path / name, data)
        cleanup_receipt = _cleanup_receipt(cleanup_report, config)
        cleanup_receipt["derived_transcript_hashes"] = {
            name: {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
            for name, data in sorted(outputs.items())
        }
        _write_private_bytes(
            staging_path / "cleanup_report.json",
            deterministic_json_bytes(cleanup_receipt),
        )
        _write_private_bytes(
            staging_path / "validation_report.json",
            deterministic_json_bytes(
                {
                    "schema_version": "auralign_polished_validation.v2",
                    "valid": True,
                    "cleanup": cleanup_receipt,
                    "canonical_invariants": invariant_report,
                    "output_invariants": output_validation,
                }
            ),
        )
        rehashed = _source_hashes(source)
        rehashed.update(
            {
                "transcript.cleaned.json": sha256_file(source / "transcript.cleaned.json"),
                "cleanup_report.json": sha256_file(source / "cleanup_report.json"),
                "presentation_plan.json": sha256_file(source / "presentation_plan.json"),
                "semantic_cleanup_plan.json": sha256_file(source / "semantic_cleanup_plan.json"),
                "semantic_cleanup_receipt.json": sha256_file(
                    source / "semantic_cleanup_receipt.json"
                ),
            }
        )
        if rehashed != source_hashes:
            raise PolishError("A source job artifact changed during composition.")
        safe_configuration, configuration_sha256 = configuration_evidence(config)
        manifest: dict[str, Any] = {
            "schema_version": "auralign_polished_manifest.v2",
            "commit_state": "complete",
            "source_job": {
                "job_id": source.name,
                "artifact_hashes": source_hashes,
                "recorded_code_revision": run_metadata.get("code_revision"),
                "input_file_hash": run_metadata.get("input_file_hash"),
                "identity_resolver_run_id": identity_report.run_id,
            },
            "production_source_revision": current_code_revision(),
            "postprocess_configuration_sha256": configuration_sha256,
            "postprocess_configuration": safe_configuration,
            "cleanup": cleanup_receipt,
            "validation": {
                "valid": True,
                "word_count": invariant_report["word_count"],
                "turn_count": invariant_report["turn_count"],
                "canonical_evidence_preserved": True,
                "semantic_plan_traceability_complete": True,
                "presentation_lexical_sequence_preserved": True,
                "all_nonpresentation_fields_preserved": True,
                "zero_residual_known_cluster_labels": True,
                "canonical_source_rehashed_unchanged": True,
            },
            "outputs": _directory_hashes(staging_path),
            "manifest_self_hash": None,
            "manifest_self_hash_reason": "self-referential hash intentionally excluded",
        }
        _write_private_bytes(
            staging_path / "derived_manifest.json",
            deterministic_json_bytes(manifest),
        )
        _fsync_tree(staging_path)
        _require_fresh_destination(destination)
        os.replace(staging_path, destination)
        published = True
        return manifest
    finally:
        if not published and staging_path.exists():
            shutil.rmtree(staging_path)


def _resolve_source_job(job_dir: Path) -> Path:
    source = job_dir.expanduser().resolve(strict=True)
    if not source.is_dir() or source.is_symlink():
        raise PolishError("The completed job must be a real local directory.")
    return source


def _require_fresh_destination(path: Path) -> None:
    if os.path.lexists(path):
        raise PolishError("The polished output directory must not already exist.")
    if path.name in {"", ".", ".."}:
        raise PolishError("The polished output directory is invalid.")


def _source_hashes(source: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in _SOURCE_ARTIFACTS:
        path = source / name
        if not path.exists():
            if name in {"transcript.identity_mapped.srt", "alignment_report.json"}:
                continue
            raise PolishError(f"Required completed-job artifact is missing: {name}")
        _require_regular_file(path)
        hashes[name] = sha256_file(path)
    return hashes


def _evidence_hashes(paths: Sequence[Path]) -> list[dict[str, str | int]]:
    records: list[dict[str, str | int]] = []
    for index, supplied in enumerate(paths):
        path = supplied.expanduser().resolve(strict=True)
        _require_regular_file(path)
        records.append(
            {
                "evidence_id": f"evidence-{index:03d}",
                "basename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def _require_regular_file(path: Path) -> None:
    if path.is_symlink():
        raise PolishError("Polish inputs cannot be symlinks.")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise PolishError("Polish inputs must be regular files.")


def _read_bounded(path: Path, *, limit: int) -> bytes:
    _require_regular_file(path)
    if path.stat().st_size > limit:
        raise PolishError(f"Artifact exceeds the polish read limit: {path.name}")
    data = path.read_bytes()
    if len(data) > limit:
        raise PolishError(f"Artifact exceeds the polish read limit: {path.name}")
    return data


def _load_transcript(path: Path) -> Transcript:
    try:
        return Transcript.model_validate(
            strict_json_bytes(_read_bounded(path, limit=MAX_POLISH_JSON_BYTES))
        )
    except (OSError, ValueError) as exc:
        raise PolishError(f"Transcript artifact is invalid: {path.name}") from exc


def _load_identity_report(path: Path) -> IdentityReport:
    try:
        return IdentityReport.model_validate(
            strict_json_bytes(_read_bounded(path, limit=MAX_POLISH_JSON_BYTES))
        )
    except (OSError, ValueError) as exc:
        raise PolishError("Speaker identity report is invalid.") from exc


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = strict_json_bytes(_read_bounded(path, limit=MAX_POLISH_JSON_BYTES))
    except (OSError, ValueError) as exc:
        raise PolishError(f"JSON artifact is invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise PolishError(f"JSON artifact must be an object: {path.name}")
    return value


def _validate_identity_binding(report: IdentityReport, source_hashes: Mapping[str, str]) -> None:
    references = {
        reference.relative_path: reference.sha256
        for reference in report.resolver_context.artifacts
        if reference.relative_path is not None and reference.sha256 is not None
    }
    for name in ("transcript.json", "transcript.srt", "transcript.vtt", "transcript.md"):
        if references.get(name) != source_hashes.get(name):
            raise PolishError(f"Identity report is not bound to the current {name}.")
    if len(report.turn_decisions) == 0 or not report.validation_summary.valid:
        raise PolishError("Identity report is not a complete validated resolver result.")


def _require_complete_cleanup(
    report: Mapping[str, Any],
    transcript: Transcript,
    config: PipelineConfig,
) -> None:
    if report.get("accepted") is not True:
        raise PolishError("Cleanup did not produce an accepted derived clean transcript.")
    request = report.get("request")
    if not isinstance(request, Mapping):
        raise PolishError("Cleanup did not report its bounded-window results.")
    window_count = request.get("window_count")
    accepted = request.get("accepted_window_count")
    fallback = request.get("fallback_window_count")
    if cleanup_source_text(transcript).split() and (
        not isinstance(window_count, int)
        or window_count <= 0
        or accepted != window_count
        or fallback != 0
    ):
        raise PolishError("Complete cleanup requires every nonempty window to succeed.")
    if (
        request.get("decoder_version") != DECODER_VERSION
        or request.get("decoder_escalation_policy") != HARD96_ESCALATION_POLICY
        or not isinstance(request.get("presentation_plan_sha256"), str)
        or request.get("presentation_plan_word_count") != request.get("semantic_clean_word_count")
        or not isinstance(request.get("semantic_cleanup_plan_sha256"), str)
        or not isinstance(request.get("semantic_reconciliation_sha256"), str)
        or request.get("semantic_cleanup_version") != "auralign.semantic-cleanup.v1"
        or request.get("hard_constraint_violation_count") != 0
    ):
        raise PolishError("Complete cleanup requires a bound global presentation plan.")
    surface_quality = request.get("surface_quality")
    if (
        not isinstance(surface_quality, Mapping)
        or surface_quality.get("schema_version") != "global_presentation_quality.v7"
        or not isinstance(surface_quality.get("long_sentence_span_receipt_sha256"), str)
        or not isinstance(surface_quality.get("overlong_sentence_span_receipt_sha256"), str)
        or surface_quality.get("lexical_sequence_preserved") is not True
        or surface_quality.get("suspicious_fragment_addition_count") != 0
        or surface_quality.get("contraction_punctuation_violation_count") != 0
        or surface_quality.get("added_short_strong_stop_burst_count") != 0
        or surface_quality.get("added_strong_stop_burst_count") != 0
        or surface_quality.get("unsupported_added_strong_stop_count") != 0
        or surface_quality.get("incomplete_abandoned_fragment_stop_violation_count") != 0
        or surface_quality.get("lowercase_after_strong_stop_count") != 0
        or surface_quality.get("typed_question_scope_without_question_mark_count") != 0
    ):
        raise PolishError("Complete cleanup requires accepted surface-quality validation.")
    _validate_proposal_source_evidence(request, config)


def _validate_proposal_source_evidence(
    request: Mapping[str, Any],
    config: PipelineConfig,
    *,
    decoder_words: Sequence[DecoderWord] | None = None,
    presentation_plan: PresentationPlan | None = None,
) -> None:
    registry = request.get("proposal_source_registry")
    receipts = request.get("proposal_source_receipts")
    if not isinstance(registry, list) or not isinstance(receipts, list):
        raise PolishError("Cleanup is missing its proposal-source registry or receipts.")
    registry_keys = {
        item.get("key")
        for item in registry
        if isinstance(item, Mapping) and isinstance(item.get("key"), str)
    }
    if {
        "source-grammar-features",
        "cleanup-backend-indexed-windows",
        "sat-local",
        "word-token-classifier",
    } - registry_keys:
        raise PolishError("Cleanup proposal-source registry is incomplete.")

    required_receipt_fields = {
        "schema_version",
        "status",
        "abstention_reason",
        "scorer_id",
        "scorer_version",
        "configuration_digest",
        "presentation_input_digest",
        "proposal_count",
        "output_digest",
        "receipt_sha256",
    }
    normalized_receipts: list[dict[str, object]] = []
    receipt_keys: set[tuple[str, str]] = set()
    for item in receipts:
        if not isinstance(item, Mapping):
            raise PolishError("Cleanup proposal-source receipt is malformed.")
        receipt = dict(item)
        if not required_receipt_fields.issubset(receipt):
            raise PolishError("Cleanup proposal-source receipt is incomplete.")
        receipt_sha256 = receipt.pop("receipt_sha256")
        if (
            not isinstance(receipt_sha256, str)
            or len(receipt_sha256) != 64
            or any(character not in "0123456789abcdef" for character in receipt_sha256)
            or hashlib.sha256(deterministic_json_bytes(receipt)).hexdigest() != receipt_sha256
        ):
            raise PolishError("Cleanup proposal-source receipt hash is invalid.")
        scorer_id = receipt.get("scorer_id")
        scorer_version = receipt.get("scorer_version")
        configuration_digest = receipt.get("configuration_digest")
        output_digest = receipt.get("output_digest")
        proposal_count = receipt.get("proposal_count")
        if (
            receipt.get("schema_version") != "auralign.presentation.scorer-receipt.v1"
            or receipt.get("status") not in {"succeeded", "abstained"}
            or not isinstance(scorer_id, str)
            or not isinstance(scorer_version, str)
            or not isinstance(configuration_digest, str)
            or len(configuration_digest) != 64
            or any(character not in "0123456789abcdef" for character in configuration_digest)
            or receipt.get("presentation_input_digest") != request.get("presentation_input_digest")
            or isinstance(proposal_count, bool)
            or not isinstance(proposal_count, int)
            or proposal_count < 0
            or not isinstance(output_digest, str)
            or len(output_digest) != 64
            or any(character not in "0123456789abcdef" for character in output_digest)
        ):
            raise PolishError("Cleanup proposal-source receipt contract is invalid.")
        receipt_key = (scorer_id, scorer_version)
        if receipt_key in receipt_keys:
            raise PolishError("Cleanup proposal-source receipt is duplicated.")
        receipt_keys.add(receipt_key)
        receipt["receipt_sha256"] = receipt_sha256
        normalized_receipts.append(receipt)

    clean_word_count = request.get("semantic_clean_word_count")
    if isinstance(clean_word_count, bool) or not isinstance(clean_word_count, int):
        raise PolishError("Cleanup semantic word coverage is invalid.")
    source_receipts = [
        receipt
        for receipt in normalized_receipts
        if receipt.get("scorer_id") == "auralign.source-grammar-features"
    ]
    aggregate_scorer_id = f"{config.cleanup.mode}.window-aggregate"
    aggregate_receipts = [
        receipt
        for receipt in normalized_receipts
        if receipt.get("scorer_id") == aggregate_scorer_id
    ]
    source_configuration_digest = source_grammar_configuration_digest(
        proper_nouns=config.cleanup.proper_nouns,
        word_count=clean_word_count,
    )
    expected_source_output_digest: str | None = None
    if decoder_words is not None:
        if len(decoder_words) != clean_word_count:
            raise PolishError("Source scorer input coverage does not match canonical evidence.")
        try:
            expected_source_output_digest = scorer_output_digest(
                _source_and_grammar_scorer(
                    decoder_words,
                    tuple(word.lexical_text for word in decoder_words),
                    proper_nouns=config.cleanup.proper_nouns,
                )
            )
        except (TypeError, ValueError) as exc:
            raise PolishError("Source scorer output cannot be reconstructed.") from exc
        if (
            len(source_receipts) != 1
            or source_receipts[0].get("output_digest") != expected_source_output_digest
        ):
            raise PolishError(
                "Source scorer output digest does not match reconstructed decoder evidence."
            )
    aggregate_receipt = aggregate_receipts[0] if len(aggregate_receipts) == 1 else {}
    expected_backend_model = (
        APPLE_FOUNDATION_MODEL_ID
        if config.cleanup.mode == "apple-foundation"
        else config.cleanup.model
    )
    evidence_digest = aggregate_receipt.get("window_evidence_digest")
    window_size = aggregate_receipt.get("window_size_words")
    window_count = aggregate_receipt.get("window_count")
    expected_aggregate_configuration_digest: str | None = None
    if (
        isinstance(evidence_digest, str)
        and isinstance(window_size, int)
        and not isinstance(window_size, bool)
        and isinstance(window_count, int)
        and not isinstance(window_count, bool)
    ):
        try:
            expected_aggregate_configuration_digest = window_aggregate_configuration_digest(
                mode=config.cleanup.mode,
                model=expected_backend_model,
                window_size=window_size,
                window_count=window_count,
                evidence_digest=evidence_digest,
            )
        except ValueError:
            expected_aggregate_configuration_digest = None
    if (
        len(source_receipts) != 1
        or source_receipts[0].get("scorer_version") != SOURCE_GRAMMAR_SCORER_VERSION
        or source_receipts[0].get("status") != "succeeded"
        or source_receipts[0].get("configuration_digest") != source_configuration_digest
        or source_receipts[0].get("proposal_count") != clean_word_count
        or len(aggregate_receipts) != 1
        or aggregate_receipts[0].get("scorer_version") != WINDOW_AGGREGATE_SCORER_VERSION
        or aggregate_receipts[0].get("status") != "succeeded"
        or aggregate_receipts[0].get("backend_mode") != config.cleanup.mode
        or aggregate_receipts[0].get("backend_model") != expected_backend_model
        or window_size != request.get("window_size_words")
        or window_count != request.get("window_count")
        or expected_aggregate_configuration_digest is None
        or aggregate_receipts[0].get("configuration_digest")
        != expected_aggregate_configuration_digest
        or aggregate_receipts[0].get("proposal_count")
        != clean_word_count * len(TrailingPunctuation)
        or aggregate_receipts[0].get("evidenced_word_count") != clean_word_count
        or aggregate_receipts[0].get("neutral_gap_word_count") != 0
        or aggregate_receipts[0].get("evidenced_word_indices_sha256")
        != hashlib.sha256(deterministic_json_bytes(list(range(clean_word_count)))).hexdigest()
    ):
        raise PolishError("Cleanup source or aggregate proposal evidence is incomplete.")

    expected_receipt_keys = {
        ("auralign.source-grammar-features", SOURCE_GRAMMAR_SCORER_VERSION),
        (aggregate_scorer_id, WINDOW_AGGREGATE_SCORER_VERSION),
    }
    sat_contributes_to_decoder = False

    sat_mode = config.cleanup.presentation_scorers.sat.mode
    sat_receipt = request.get("sat_receipt")
    if not isinstance(sat_receipt, Mapping):
        raise PolishError("Cleanup is missing its SaT scorer receipt.")
    if sat_mode == "off":
        if sat_receipt.get("status") != "off":
            raise PolishError("SaT receipt contradicts the configured off mode.")
    else:
        receipt_payload = dict(sat_receipt)
        receipt_sha256 = receipt_payload.pop("receipt_sha256", None)
        if (
            not isinstance(receipt_sha256, str)
            or len(receipt_sha256) != 64
            or any(character not in "0123456789abcdef" for character in receipt_sha256)
            or hashlib.sha256(deterministic_json_bytes(receipt_payload)).hexdigest()
            != receipt_sha256
        ):
            raise PolishError("SaT receipt hash binding is invalid.")
        try:
            validated_sat = validate_sat_score_receipt_payload(receipt_payload)
        except (TypeError, ValueError) as exc:
            raise PolishError("SaT receipt violates its typed v2 contract.") from exc
        expected_mode = SaTMode(sat_mode)
        expected_statuses = (
            {SaTScoreStatus.SUCCEEDED, SaTScoreStatus.ABSTAINED}
            if expected_mode is SaTMode.OPTIONAL
            else {SaTScoreStatus.SUCCEEDED}
        )
        expected_word_count = request.get("semantic_clean_word_count")
        if (
            validated_sat.mode is not expected_mode
            or validated_sat.status not in expected_statuses
            or validated_sat.signal_weight != config.cleanup.presentation_scorers.sat.signal_weight
            or validated_sat.word_count != expected_word_count
            or validated_sat.offline_enforced is not True
            or validated_sat.network_access is not False
            or validated_sat.turn_features_used is not False
            or validated_sat.cue_boundaries_used is not False
        ):
            raise PolishError("SaT receipt does not satisfy its configured execution policy.")
        if decoder_words is not None:
            try:
                expected_input_digest, expected_lexical_digest = sat_score_input_digests(
                    decoder_words
                )
            except (TypeError, ValueError) as exc:
                raise PolishError("SaT receipt input evidence cannot be reconstructed.") from exc
            if (
                validated_sat.input_digest != expected_input_digest
                or validated_sat.lexical_input_digest != expected_lexical_digest
            ):
                raise PolishError("SaT receipt input digests do not match canonical evidence.")

        expected_proposal_count = (
            validated_sat.word_count * len(TrailingPunctuation)
            if validated_sat.status is SaTScoreStatus.SUCCEEDED
            and validated_sat.signal_weight > 0.0
            else 0
        )
        if validated_sat.proposal_count != expected_proposal_count:
            raise PolishError("SaT receipt proposal coverage is incomplete.")

        expected_receipt_keys.add(
            (
                validated_sat.scorer_provenance.scorer_id,
                validated_sat.scorer_provenance.scorer_version,
            )
        )
        sat_contributes_to_decoder = validated_sat.signal_weight > 0.0

        matching_receipts = [
            item
            for item in normalized_receipts
            if isinstance(item, Mapping)
            and item.get("scorer_id") == validated_sat.scorer_provenance.scorer_id
            and item.get("scorer_version") == validated_sat.scorer_provenance.scorer_version
        ]
        if len(matching_receipts) != 1:
            raise PolishError("SaT proposal-source receipt is missing or duplicated.")
        source_receipt = matching_receipts[0]
        expected_generic_status = (
            "abstained"
            if validated_sat.status is SaTScoreStatus.ABSTAINED
            or validated_sat.signal_weight == 0.0
            else "succeeded"
        )
        if (
            source_receipt.get("schema_version") != "auralign.presentation.scorer-receipt.v1"
            or source_receipt.get("status") != expected_generic_status
            or source_receipt.get("configuration_digest")
            != validated_sat.scorer_provenance.configuration_digest
            or source_receipt.get("presentation_input_digest")
            != request.get("presentation_input_digest")
            or source_receipt.get("proposal_count") != validated_sat.proposal_count
            or source_receipt.get("output_digest") != validated_sat.output_digest
            or source_receipt.get("source_receipt_sha256") != receipt_sha256
        ):
            raise PolishError("SaT proposal-source receipt does not match execution evidence.")

    word_model = request.get("word_model_receipt")
    token_config = config.cleanup.presentation_scorers.token_classifier
    if (
        not isinstance(word_model, Mapping)
        or token_config.mode != "off"
        or token_config.backend != "none"
        or word_model.get("status") != "off"
        or word_model.get("backend") != "none"
        or word_model.get("reason_code") != "no_approved_checkpoint"
        or word_model.get("network_access") is not False
    ):
        raise PolishError("Word-model receipt contradicts the approved-provider registry.")
    if receipt_keys != expected_receipt_keys or len(normalized_receipts) != len(
        expected_receipt_keys
    ):
        raise PolishError("Cleanup contains an unexpected proposal-source receipt.")
    expected_scorer_count = 2 + int(sat_contributes_to_decoder)
    if request.get("scorer_count") != expected_scorer_count:
        raise PolishError("Cleanup scorer count does not match configured decoder inputs.")
    if presentation_plan is not None:
        receipt_provenance = {
            (str(receipt["scorer_id"]), str(receipt["scorer_version"])): receipt.get(
                "configuration_digest"
            )
            for receipt in normalized_receipts
        }
        plan_provenance = (
            {
                provenance
                for decision in presentation_plan.decisions
                for provenance in decision.contributing_provenance
            }
            | {
                provenance
                for decision in presentation_plan.decisions
                for provenance in decision.boundary_support.scorer_provenance
            }
            | {abstention.provenance for abstention in presentation_plan.abstentions}
        )
        if any(
            receipt_provenance.get((item.scorer_id, item.scorer_version))
            != item.configuration_digest
            for item in plan_provenance
        ):
            raise PolishError("Cleanup scorer receipts do not bind presentation-plan provenance.")
        receipts_by_provenance = {
            (str(receipt["scorer_id"]), str(receipt["scorer_version"])): receipt
            for receipt in normalized_receipts
        }
        retained_keys = {
            (output.provenance.scorer_id, output.provenance.scorer_version)
            for output in presentation_plan.scorer_outputs
        }
        expected_retained_keys = {
            (str(receipt["scorer_id"]), str(receipt["scorer_version"]))
            for receipt in normalized_receipts
            if not (
                receipt.get("source_receipt_sha256") is not None and not sat_contributes_to_decoder
            )
        }
        if retained_keys != expected_retained_keys:
            raise PolishError("Presentation plan does not retain every decoder scorer output.")
        for output in presentation_plan.scorer_outputs:
            key = (output.provenance.scorer_id, output.provenance.scorer_version)
            matched_receipt = receipts_by_provenance.get(key)
            if matched_receipt is None:
                raise PolishError("Presentation plan retains an unreceipted scorer output.")
            expected_output_digest = (
                sat_scorer_output_digest(output)
                if matched_receipt.get("source_receipt_sha256") is not None
                else scorer_output_digest(output)
            )
            if matched_receipt.get("output_digest") != expected_output_digest:
                raise PolishError(
                    "Presentation scorer output does not match its exact receipt digest."
                )


def _validate_surface_quality_replay(
    request: Mapping[str, Any],
    canonical: Transcript,
    semantic_plan: SemanticCleanupPlan,
    presentation_plan: PresentationPlan,
    config: PipelineConfig,
) -> None:
    """Recompute quality from bound evidence instead of trusting report counters."""

    decoder_words = _build_presentation_decoder_words(
        canonical,
        semantic_plan.clean_surfaces,
        source_word_indices=semantic_plan.clean_to_source_indices,
    )
    try:
        cleaned_surfaces = apply_presentation_plan(decoder_words, presentation_plan)
    except ValueError as exc:
        raise PolishError("Presentation plan fails exact surface-quality replay.") from exc
    quality_transcript = _presentation_context_transcript(
        canonical,
        semantic_plan.clean_to_source_indices,
        semantic_plan.clean_surfaces,
    )
    recomputed = _decoded_surface_quality(
        quality_transcript,
        semantic_plan.clean_surfaces,
        cleaned_surfaces,
        presentation_plan,
        proper_nouns=config.cleanup.proper_nouns,
    )
    reported = request.get("surface_quality")
    if not isinstance(reported, Mapping) or dict(reported) != recomputed:
        raise PolishError("Cleanup surface-quality report does not match exact replay.")


def _validate_presentation_plan_binding(
    report: Mapping[str, Any],
    plan_bytes: bytes,
) -> PresentationPlan:
    try:
        plan = deserialize_presentation_plan(plan_bytes)
    except ValueError as exc:
        raise PolishError("Presentation plan violates its typed canonical schema.") from exc
    request = report.get("request")
    if not isinstance(request, Mapping):
        raise PolishError("Cleanup report is missing presentation-plan evidence.")
    plan_hash = hashlib.sha256(plan_bytes).hexdigest()
    if request.get("presentation_plan_sha256") != plan_hash:
        raise PolishError("Presentation plan hash does not match the cleanup report.")
    if plan.input_digest != request.get("presentation_input_digest"):
        raise PolishError("Presentation plan input binding does not match the cleanup report.")
    if len(plan.decisions) != request.get("presentation_plan_word_count"):
        raise PolishError("Presentation plan word coverage is incomplete.")
    if plan.decoder_version != request.get("decoder_version"):
        raise PolishError("Presentation plan decoder version does not match the report.")
    if plan.tie_break_policy != request.get("decoder_tie_break_policy"):
        raise PolishError("Presentation plan tie-break policy does not match the report.")
    if plan.escalation_policy != request.get("decoder_escalation_policy"):
        raise PolishError("Presentation plan escalation policy does not match the report.")
    if plan.hard_constraint_application_count != request.get("hard_constraint_application_count"):
        raise PolishError("Presentation plan hard-constraint count does not match the report.")
    if plan.hard_constraint_violation_count != request.get("hard_constraint_violation_count"):
        raise PolishError("Presentation plan hard-constraint status does not match the report.")
    return plan


def _validate_semantic_plan_binding(
    report: Mapping[str, Any],
    plan_bytes: bytes,
) -> SemanticCleanupPlan:
    request = report.get("request")
    if not isinstance(request, Mapping):
        raise PolishError("Cleanup report is missing semantic-plan evidence.")
    if not plan_bytes.endswith(b"\n") or plan_bytes.endswith(b"\n\n"):
        raise PolishError("Semantic cleanup plan must have one canonical final newline.")
    try:
        plan = deserialize_semantic_cleanup_plan(plan_bytes[:-1])
    except ValueError as exc:
        raise PolishError("Semantic cleanup plan violates its typed schema.") from exc
    if hashlib.sha256(plan_bytes[:-1]).hexdigest() != request.get("semantic_cleanup_plan_sha256"):
        raise PolishError("Semantic cleanup plan hash does not match the cleanup report.")
    if plan.canonical_digest != request.get("canonical_semantic_evidence_digest"):
        raise PolishError("Semantic cleanup canonical binding does not match the report.")
    if len(plan.clean_surfaces) != request.get("semantic_clean_word_count"):
        raise PolishError("Semantic cleanup clean-word coverage is incomplete.")
    if len(plan.selected_edits) != request.get("semantic_edit_count"):
        raise PolishError("Semantic cleanup edit count does not match the report.")
    return plan


def _validate_semantic_receipt_binding(
    report: Mapping[str, Any],
    receipt_bytes: bytes,
    plan_bytes: bytes,
) -> None:
    raw_request = report.get("request")
    if isinstance(raw_request, Mapping):
        request = raw_request
    elif report.get("schema_version") == "auralign_cleanup_receipt.v2":
        request = report
    else:
        raise PolishError("Cleanup report is missing semantic-reconciliation evidence.")
    if not receipt_bytes.endswith(b"\n") or receipt_bytes.endswith(b"\n\n"):
        raise PolishError("Semantic cleanup receipt must have one canonical final newline.")
    try:
        receipt = deserialize_semantic_reconciliation_result(receipt_bytes[:-1])
        plan = deserialize_semantic_cleanup_plan(plan_bytes[:-1])
    except ValueError as exc:
        raise PolishError("Semantic cleanup receipt violates its typed canonical schema.") from exc
    if receipt.plan != plan:
        raise PolishError("Semantic cleanup receipt does not embed the accepted plan.")
    quality_profile = request.get("quality_profile")
    if quality_profile == "semantically-faithful-clean-v1":
        policy = LexicalCleanupPolicy.safe_default()
    elif quality_profile == "strict-verbatim-v1":
        policy = LexicalCleanupPolicy(())
    else:
        raise PolishError("Semantic cleanup receipt has an unsupported quality profile.")
    if receipt.policy_digest != lexical_cleanup_policy_sha256(policy) or any(
        item.required_threshold != policy.threshold_for(item.edit_class)
        for item in receipt.proposal_receipts
    ):
        raise PolishError("Semantic cleanup receipt does not bind its cleanup policy.")
    if hashlib.sha256(receipt_bytes[:-1]).hexdigest() != request.get(
        "semantic_reconciliation_sha256"
    ):
        raise PolishError("Semantic cleanup receipt hash does not match the cleanup report.")
    if len(receipt.proposal_receipts) != request.get("semantic_proposal_receipt_count"):
        raise PolishError("Semantic proposal receipt coverage is incomplete.")
    if len(receipt.abstention_receipts) != request.get("semantic_abstention_receipt_count"):
        raise PolishError("Semantic abstention receipt coverage is incomplete.")


def _apply_reviewed_question_scopes(
    words: Sequence[DecoderWord],
    scopes: Mapping[int, int],
) -> tuple[DecoderWord, ...]:
    """Bind a reviewed question judgment without changing canonical evidence.

    The supplied mapping is recording-specific, receipt-bound presentation
    evidence.  It may add only question metadata to an otherwise byte-for-byte
    equivalent decoder-word sequence.  Scopes must be non-overlapping and stay
    inside one exact speaker/container continuity span.
    """

    bound_words = tuple(words)
    if not scopes:
        return bound_words
    normalized: list[tuple[int, int]] = []
    prior_endpoint = -1
    for endpoint, scope_start in sorted(scopes.items()):
        if (
            isinstance(endpoint, bool)
            or not isinstance(endpoint, int)
            or isinstance(scope_start, bool)
            or not isinstance(scope_start, int)
            or scope_start < 0
            or endpoint < scope_start
            or endpoint >= len(bound_words)
        ):
            raise PolishError("Reviewed question scope is out of bounds.")
        if scope_start <= prior_endpoint:
            raise PolishError("Reviewed question scopes overlap.")
        if endpoint - scope_start + 1 > 96:
            raise PolishError("Reviewed question scope exceeds the decoder envelope.")
        scope_words = bound_words[scope_start : endpoint + 1]
        owner = (scope_words[0].speaker_id, scope_words[0].container_index)
        if any((word.speaker_id, word.container_index) != owner for word in scope_words):
            raise PolishError("Reviewed question scope crosses a continuity owner.")
        if any(word.ends_turn for word in scope_words[:-1]):
            raise PolishError("Reviewed question scope crosses an internal turn ending.")
        endpoint_word = bound_words[endpoint]
        if endpoint_word.question_evidence > 0.0 and (
            endpoint_word.question_scope_start_index != scope_start
        ):
            raise PolishError("Reviewed question scope conflicts with typed source evidence.")
        normalized.append((endpoint, scope_start))
        prior_endpoint = endpoint

    reviewed = list(bound_words)
    for endpoint, scope_start in normalized:
        reviewed[endpoint] = replace(
            reviewed[endpoint],
            question_evidence=0.9999,
            question_scope_start_index=scope_start,
        )
    return tuple(reviewed)


def _validate_cleaned_transcript(
    canonical: Transcript,
    cleaned: Transcript,
    config: PipelineConfig,
    semantic_plan: SemanticCleanupPlan,
    presentation_plan: PresentationPlan,
    *,
    reviewed_question_scopes: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    source_text = cleanup_source_text(canonical)
    cleaned_text = cleanup_source_text(cleaned)
    quality_profile = config.cleanup.quality_profile
    if quality_profile == "strict-verbatim-v1":
        accepted, reason = cleanup_is_safe(
            source_text,
            cleaned_text,
            max_word_delta_ratio=0.0,
            preserve_lexical_sequence=True,
        )
        if not accepted:
            raise PolishError(f"Strict cleanup lexical conservation failed: {reason}")
    else:
        reason = "enumerated_semantic_cleanup_plan_accepted"
    if _without_presentation(canonical.model_dump(mode="json")) != _without_presentation(
        cleaned.model_dump(mode="json")
    ):
        raise PolishError("Cleanup changed a non-presentation transcript field.")

    canonical_words: list[AlignedWord] = []
    canonical_context_speakers: list[str | None] = []
    canonical_container_indices: list[int] = []
    cleaned_word_slots: list[AlignedWord] = []
    if len(canonical.speaker_turns) != len(cleaned.speaker_turns):
        raise PolishError("Cleanup changed the speaker-turn count.")
    for turn_index, (source_turn, cleaned_turn) in enumerate(
        zip(canonical.speaker_turns, cleaned.speaker_turns, strict=True)
    ):
        if len(source_turn.words) != len(cleaned_turn.words):
            raise PolishError("Cleanup changed canonical timed-word slots.")
        expected_turn_text = " ".join(
            word.text.strip() for word in cleaned_turn.words if word.text.strip()
        )
        if cleaned_turn.text != expected_turn_text:
            raise PolishError("Cleaned turn text does not match its bound timed-word surfaces.")
        for source_word, cleaned_word in zip(
            source_turn.words,
            cleaned_turn.words,
            strict=True,
        ):
            if source_word.text.strip():
                canonical_words.append(source_word)
                canonical_context_speakers.append(source_turn.speaker)
                canonical_container_indices.append(turn_index)
                cleaned_word_slots.append(cleaned_word)
    if len(canonical_words) != len(cleaned_word_slots):
        raise PolishError("Cleanup changed canonical timed-word coverage.")
    for source_word, cleaned_word in zip(
        canonical_words,
        cleaned_word_slots,
        strict=True,
    ):
        if _word_invariants(source_word) != _word_invariants(cleaned_word):
            raise PolishError("Cleanup changed word timing, alignment, or speaker attribution.")
    bound_canonical_words = tuple(
        CanonicalWord(
            word_index=index,
            surface=word.text,
            speaker_id=canonical_context_speakers[index],
            start_seconds=word.start,
            end_seconds=word.end,
            container_index=canonical_container_indices[index],
        )
        for index, word in enumerate(canonical_words)
    )
    semantic_policy = (
        LexicalCleanupPolicy.safe_default()
        if quality_profile == "semantically-faithful-clean-v1"
        else LexicalCleanupPolicy(())
    )
    try:
        validate_semantic_cleanup_plan(
            semantic_plan,
            bound_canonical_words,
            policy=semantic_policy,
        )
    except ValueError as exc:
        raise PolishError("Semantic cleanup plan failed canonical revalidation.") from exc

    clean_surfaces = semantic_plan.clean_surfaces
    clean_to_source = semantic_plan.clean_to_source_indices
    source_to_clean = semantic_plan.source_to_clean
    try:
        decoder_words = _build_presentation_decoder_words(
            canonical,
            clean_surfaces,
            source_word_indices=clean_to_source,
        )
        decoder_words = _apply_reviewed_question_scopes(
            decoder_words,
            reviewed_question_scopes or {},
        )
        planned_surfaces = apply_presentation_plan(
            decoder_words,
            presentation_plan,
        )
    except (CleanupRejectedError, ValueError) as exc:
        raise PolishError("Presentation plan failed independent canonical reapplication.") from exc
    if len(planned_surfaces) != len(clean_surfaces):
        raise PolishError("Presentation plan did not cover every semantic-clean word.")
    if len(source_to_clean) != len(canonical_words):
        raise PolishError("Semantic cleanup plan mapping shape is invalid.")
    expected_source_clean: list[list[int]] = [[] for _ in canonical_words]
    target_by_clean: list[int] = []
    for clean_index, raw_indices in enumerate(clean_to_source):
        if not raw_indices:
            raise PolishError("Semantic cleanup source mapping is invalid.")
        if tuple(sorted(set(raw_indices))) != raw_indices:
            raise PolishError("Semantic cleanup source mapping is ambiguous.")
        for source_index in raw_indices:
            if source_index < 0 or source_index >= len(canonical_words):
                raise PolishError("Semantic cleanup source mapping is out of bounds.")
            expected_source_clean[source_index].append(clean_index)
        target = raw_indices[-1]
        if target < 0 or target >= len(cleaned_word_slots):
            raise PolishError("Semantic cleanup target index is out of bounds.")
        target_by_clean.append(target)
        displayed = cleaned_word_slots[target].text
        expected = planned_surfaces[clean_index]
        if displayed != expected:
            raise PolishError(
                "Cleaned transcript surface does not match the bound presentation plan."
            )
    if target_by_clean != sorted(set(target_by_clean)):
        raise PolishError("Semantic cleanup result order is ambiguous or duplicated.")
    nonempty_positions = {
        index for index, word in enumerate(cleaned_word_slots) if word.text.strip()
    }
    if nonempty_positions != set(target_by_clean):
        raise PolishError("Cleaned transcript contains an unplanned lexical word.")
    for source_index, raw_mapping in enumerate(source_to_clean):
        if (
            raw_mapping.source_index != source_index
            or raw_mapping.source_surface != canonical_words[source_index].text
            or raw_mapping.clean_indices != tuple(expected_source_clean[source_index])
        ):
            raise PolishError("Semantic cleanup reverse mapping does not bind canonical evidence.")

    _validate_parallel_word_representation(
        "top-level",
        canonical.words,
        cleaned.words,
        cleaned_word_slots,
    )
    _validate_parallel_word_representation(
        "segment",
        [word for segment in canonical.segments for word in segment.words],
        [word for segment in cleaned.segments for word in segment.words],
        cleaned_word_slots,
    )
    expected_cleaned_text = "\n".join(turn.text for turn in cleaned.speaker_turns)
    if cleaned.cleaned_text != expected_cleaned_text:
        raise PolishError("Cleaned transcript text does not match its bound speaker-turn surfaces.")

    proper_nouns = list(getattr(config.cleanup, "proper_nouns", []))
    proper_noun_counts = _validate_proper_noun_casing(cleaned_text, proper_nouns)
    lowercase_i_count = sum(
        1
        for token in cleaned_text.split()
        if _presentation_core(token).casefold() == "i" and _presentation_core(token) != "I"
    )
    if lowercase_i_count:
        raise PolishError("Cleanup left a lowercase first-person pronoun.")
    return {
        "lexical_validation_reason": reason,
        "canonical_word_count": len(canonical_words),
        "word_count": len(clean_surfaces),
        "turn_count": len(cleaned.speaker_turns),
        "semantic_edit_count": len(semantic_plan.selected_edits),
        "canonical_word_order_preserved": True,
        "clean_to_source_traceability_complete": True,
        "word_timestamps_preserved": True,
        "alignment_sources_preserved": True,
        "speaker_attribution_preserved": True,
        "reviewed_question_scope_count": len(reviewed_question_scopes or {}),
        "nonpresentation_fields_preserved": True,
        "lowercase_pronoun_i_count": 0,
        "proper_noun_occurrences": proper_noun_counts,
    }


def _same_presented_lexical_surface(displayed: str, planned: str) -> bool:
    return _presentation_core(displayed).casefold() == _presentation_core(planned).casefold()


def _without_presentation(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_presentation(item)
            for key, item in value.items()
            if key not in {"text", "cleaned_text", "mapped_name"}
        }
    if isinstance(value, list):
        return [_without_presentation(item) for item in value]
    return value


def _turn_words(transcript: Transcript) -> list[AlignedWord]:
    return [word for turn in transcript.speaker_turns for word in turn.words if word.text.strip()]


def _all_turn_word_surfaces(transcript: Transcript) -> list[str]:
    return [word.text for turn in transcript.speaker_turns for word in turn.words]


def _word_invariants(word: AlignedWord) -> tuple[Any, ...]:
    return (
        word.start,
        word.end,
        word.speaker,
        word.uncertain_speaker,
        word.alignment_source,
        word.assignment_source,
        word.confidence,
    )


def _validate_parallel_word_representation(
    label: str,
    canonical_words: Sequence[AlignedWord],
    cleaned_words: Sequence[AlignedWord],
    reference_words: Sequence[AlignedWord],
) -> None:
    if not canonical_words and not cleaned_words:
        return
    if len(canonical_words) != len(cleaned_words) or len(cleaned_words) != len(reference_words):
        raise PolishError(f"Cleaned {label} word representation changed slot coverage.")
    for source, cleaned, reference in zip(
        canonical_words,
        cleaned_words,
        reference_words,
        strict=True,
    ):
        if _word_invariants(source) != _word_invariants(cleaned):
            raise PolishError(f"Cleaned {label} word representation changed canonical evidence.")
        if cleaned.text != reference.text:
            raise PolishError(f"Cleaned {label} word representation disagrees with timed words.")


def _validate_proper_noun_casing(text: str, proper_nouns: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    cores = [_presentation_core(token) for token in text.split()]
    for name in proper_nouns:
        matching = [core for core in cores if _proper_noun_matches(core, name)]
        if any(not _proper_noun_cased(core, name) for core in matching):
            raise PolishError("Cleanup did not preserve requested proper-noun casing.")
        counts[name] = len(matching)
    return counts


def _proper_noun_matches(core: str, name: str) -> bool:
    folded_core = core.casefold()
    folded_name = name.casefold()
    return folded_core in {folded_name, f"{folded_name}'s", f"{folded_name}’s"}


def _proper_noun_cased(core: str, name: str) -> bool:
    return core == name or core in {f"{name}'s", f"{name}’s"}


def _presentation_core(token: str) -> str:
    return token.lstrip(_LEADING_PRESENTATION).rstrip(_TRAILING_PRESENTATION)


def _render_polished_family(
    *,
    canonical: Transcript,
    cleaned: Transcript,
    decisions: list[IdentityDecision],
    config: PipelineConfig,
) -> dict[str, bytes]:
    surfaces = _all_turn_word_surfaces(cleaned)
    cleaned_srt = render_srt(canonical, config.subtitle_export, word_surfaces=surfaces)
    cleaned_vtt = render_vtt(canonical, config.subtitle_export, word_surfaces=surfaces)
    polished_srt = render_identity_srt(cleaned_srt, cleaned, decisions)
    polished_vtt = render_identity_vtt(cleaned_vtt, cleaned, decisions)
    identity_transcript = build_identity_transcript(cleaned, decisions)
    display_labels = [
        turn.mapped_name or turn.speaker for turn in identity_transcript.speaker_turns
    ]
    display_payload = _display_json_payload(identity_transcript, decisions)
    return {
        f"{_POLISHED_BASENAME}.srt": polished_srt.encode("utf-8"),
        f"{_POLISHED_BASENAME}.vtt": polished_vtt.encode("utf-8"),
        f"{_POLISHED_BASENAME}.txt": _render_text(identity_transcript, display_labels).encode(
            "utf-8"
        ),
        f"{_POLISHED_BASENAME}.md": _render_markdown(identity_transcript, display_labels).encode(
            "utf-8"
        ),
        f"{_POLISHED_BASENAME}.json": deterministic_json_bytes(display_payload),
        f"{_POLISHED_BASENAME}.segments.csv": _render_segments_csv(
            identity_transcript, display_labels
        ).encode("utf-8"),
        f"{_POLISHED_BASENAME}.words.csv": _render_words_csv(identity_transcript, decisions).encode(
            "utf-8"
        ),
    }


def _display_json_payload(
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> dict[str, Any]:
    cluster_labels = _cluster_display_labels(decisions)
    turns: list[dict[str, Any]] = []
    word_index = 0
    canonical_word_index = 0
    for turn_index, turn in enumerate(transcript.speaker_turns):
        display_label = turn.mapped_name or turn.speaker
        words: list[dict[str, Any]] = []
        for word in turn.words:
            current_canonical_index = canonical_word_index
            canonical_word_index += 1
            if not word.text.strip():
                continue
            words.append(
                {
                    "word_index": word_index,
                    "canonical_word_index": current_canonical_index,
                    "text": word.text,
                    "start": word.start,
                    "end": word.end,
                    "speaker": _effective_word_display_label(
                        turn,
                        word.speaker,
                        cluster_labels,
                    ),
                    "uncertain_speaker": word.uncertain_speaker,
                    "alignment_source": word.alignment_source,
                    "assignment_source": word.assignment_source,
                    "confidence": word.confidence,
                }
            )
            word_index += 1
        turns.append(
            {
                "turn_index": turn_index,
                "speaker": display_label,
                "start": turn.start,
                "end": turn.end,
                "text": turn.text,
                "words": words,
            }
        )
    return {
        "schema_version": "auralign_polished_identity_transcript.v1",
        "language": transcript.language,
        "word_count": word_index,
        "turn_count": len(turns),
        "speaker_turns": turns,
    }


def _cluster_display_labels(decisions: Sequence[IdentityDecision]) -> dict[str, str]:
    result: dict[str, str] = {}
    for decision in decisions:
        if (
            decision.speaker
            and decision.turn_index is None
            and decision.status in {ResolutionStatus.MAPPED, ResolutionStatus.UNATTRIBUTED}
            and decision.display_label
        ):
            result[decision.speaker] = decision.display_label
    return result


def _effective_word_display_label(
    turn: SpeakerTurn,
    value: str | None,
    labels: Mapping[str, str],
) -> str:
    """Render a word within the effective, safeguard-aware turn identity."""

    display_label = turn.mapped_name or turn.speaker
    if display_label.casefold() in {"unknown", "unattributed"}:
        return display_label
    if value is None or value == turn.speaker:
        return display_label
    return labels.get(value, value)


def _render_text(transcript: Transcript, labels: Sequence[str]) -> str:
    return "\n\n".join(
        f"{labels[index]}: {turn.text}" for index, turn in enumerate(transcript.speaker_turns)
    ) + ("\n" if transcript.speaker_turns else "")


def _render_markdown(transcript: Transcript, labels: Sequence[str]) -> str:
    lines = ["# Transcript", ""]
    for index, turn in enumerate(transcript.speaker_turns):
        label = labels[index].replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_")
        lines.extend((f"**{label}**  ", turn.text, ""))
    return "\n".join(lines).rstrip() + "\n"


def _render_segments_csv(transcript: Transcript, labels: Sequence[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=["turn_index", "speaker", "start", "end", "text"],
        lineterminator="\n",
    )
    writer.writeheader()
    for index, turn in enumerate(transcript.speaker_turns):
        writer.writerow(
            {
                "turn_index": index,
                "speaker": formula_safe_cell(labels[index]),
                "start": f"{turn.start:.3f}",
                "end": f"{turn.end:.3f}",
                "text": formula_safe_cell(turn.text),
            }
        )
    return output.getvalue()


def _render_words_csv(transcript: Transcript, decisions: Sequence[IdentityDecision]) -> str:
    labels = _cluster_display_labels(decisions)
    output = io.StringIO(newline="")
    fieldnames = [
        "word_index",
        "canonical_word_index",
        "text",
        "start",
        "end",
        "speaker",
        "uncertain_speaker",
        "alignment_source",
        "assignment_source",
        "confidence",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    word_index = 0
    canonical_word_index = 0
    for turn in transcript.speaker_turns:
        for word in turn.words:
            current_canonical_index = canonical_word_index
            canonical_word_index += 1
            if not word.text.strip():
                continue
            speaker = _effective_word_display_label(turn, word.speaker, labels)
            writer.writerow(
                {
                    "word_index": word_index,
                    "canonical_word_index": current_canonical_index,
                    "text": formula_safe_cell(word.text),
                    "start": "" if word.start is None else f"{word.start:.3f}",
                    "end": "" if word.end is None else f"{word.end:.3f}",
                    "speaker": formula_safe_cell(speaker),
                    "uncertain_speaker": str(word.uncertain_speaker).lower(),
                    "alignment_source": formula_safe_cell(word.alignment_source),
                    "assignment_source": formula_safe_cell(word.assignment_source or ""),
                    "confidence": ("" if word.confidence is None else f"{word.confidence:.6f}"),
                }
            )
            word_index += 1
    return output.getvalue()


def _validate_outputs(
    *,
    outputs: Mapping[str, bytes],
    canonical: Transcript,
    cleaned: Transcript,
    decisions: Sequence[IdentityDecision],
    config: PipelineConfig,
) -> dict[str, Any]:
    decoded: dict[str, str] = {}
    for name, data in outputs.items():
        try:
            decoded[name] = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PolishError(f"Polished output is not UTF-8: {name}") from exc
        if _KNOWN_CLUSTER_RE.search(decoded[name]):
            raise PolishError(f"Polished output retained a known cluster label: {name}")

    canonical_srt = render_srt(canonical, config.subtitle_export)
    canonical_vtt = render_vtt(canonical, config.subtitle_export)
    srt_name = f"{_POLISHED_BASENAME}.srt"
    vtt_name = f"{_POLISHED_BASENAME}.vtt"
    canonical_srt_timings = _subtitle_timings(canonical_srt, separator=",")
    polished_srt_timings = _subtitle_timings(decoded[srt_name], separator=",")
    canonical_vtt_timings = _subtitle_timings(canonical_vtt, separator=".")
    polished_vtt_timings = _subtitle_timings(decoded[vtt_name], separator=".")
    if canonical_srt_timings != canonical_vtt_timings:
        raise PolishError("Canonical subtitle formats disagree on cue timing.")
    if polished_srt_timings != polished_vtt_timings:
        raise PolishError("Polished subtitle formats disagree on cue timing.")
    if not _is_ordered_subsequence(polished_srt_timings, canonical_srt_timings):
        raise PolishError("Polished subtitles invented or changed cue timing.")

    subtitle_surface_validation = _validate_subtitle_surfaces(
        canonical=canonical,
        cleaned=cleaned,
        config=config,
    )

    identity_transcript = build_identity_transcript(cleaned, decisions)
    display_labels = [
        turn.mapped_name or turn.speaker for turn in identity_transcript.speaker_turns
    ]
    cleaned_surfaces = _all_turn_word_surfaces(cleaned)
    expected_cleaned_srt = render_srt(
        canonical,
        config.subtitle_export,
        word_surfaces=cleaned_surfaces,
    )
    expected_cleaned_vtt = render_vtt(
        canonical,
        config.subtitle_export,
        word_surfaces=cleaned_surfaces,
    )
    expected_outputs = {
        srt_name: render_identity_srt(
            expected_cleaned_srt,
            cleaned,
            decisions,
        ).encode("utf-8"),
        vtt_name: render_identity_vtt(
            expected_cleaned_vtt,
            cleaned,
            decisions,
        ).encode("utf-8"),
        f"{_POLISHED_BASENAME}.txt": _render_text(
            identity_transcript,
            display_labels,
        ).encode("utf-8"),
        f"{_POLISHED_BASENAME}.md": _render_markdown(
            identity_transcript,
            display_labels,
        ).encode("utf-8"),
        f"{_POLISHED_BASENAME}.json": deterministic_json_bytes(
            _display_json_payload(identity_transcript, decisions)
        ),
        f"{_POLISHED_BASENAME}.segments.csv": _render_segments_csv(
            identity_transcript,
            display_labels,
        ).encode("utf-8"),
        f"{_POLISHED_BASENAME}.words.csv": _render_words_csv(
            identity_transcript,
            decisions,
        ).encode("utf-8"),
    }
    if set(outputs) != set(expected_outputs):
        raise PolishError("Polished output family is incomplete or contains extras.")
    for name, expected in expected_outputs.items():
        if outputs[name] != expected:
            raise PolishError(f"Polished output does not match the shared display plan: {name}")

    json_payload = strict_json_bytes(outputs[f"{_POLISHED_BASENAME}.json"])
    if not isinstance(json_payload, dict):
        raise PolishError("Polished JSON is invalid.")
    if json_payload.get("word_count") != len(_turn_words(cleaned)):
        raise PolishError("Polished JSON word count is inconsistent.")
    if json_payload.get("turn_count") != len(cleaned.speaker_turns):
        raise PolishError("Polished JSON turn count is inconsistent.")
    return {
        "output_count": len(outputs),
        "cue_count": len(canonical_srt_timings),
        "clean_cue_count": len(polished_srt_timings),
        "omitted_empty_cue_count": len(canonical_srt_timings) - len(polished_srt_timings),
        "word_count": len(_turn_words(cleaned)),
        "turn_count": len(cleaned.speaker_turns),
        "subtitle_timings_trace_to_canonical": True,
        "subtitle_surfaces": subtitle_surface_validation,
        "cross_format_display_plan_exact": True,
        "known_cluster_label_residuals": {name: 0 for name in sorted(outputs)},
        "utf8_valid": True,
        "json_schema_valid": True,
    }


def _validate_subtitle_surfaces(
    *,
    canonical: Transcript,
    cleaned: Transcript,
    config: PipelineConfig,
) -> dict[str, Any]:
    cleaned_surfaces = _all_turn_word_surfaces(cleaned)
    cues = iter_subtitle_cues(canonical, config.subtitle_export)
    overlaid = overlay_cue_word_surfaces(
        cues,
        cleaned_surfaces,
        config.subtitle_export,
        canonical.language,
    )
    subtitle_surfaces = [word.text for cue in overlaid for word in cue.words if word.text.strip()]
    expected_surfaces = [surface for surface in cleaned_surfaces if surface.strip()]
    if len(subtitle_surfaces) != len(expected_surfaces):
        raise PolishError("Subtitle overlay changed the word count.")
    accepted, reason = cleanup_is_safe(
        " ".join(expected_surfaces),
        " ".join(subtitle_surfaces),
        max_word_delta_ratio=0.0,
        preserve_lexical_sequence=True,
    )
    if not accepted:
        raise PolishError(f"Subtitle overlay changed lexical words: {reason}")
    for cleaned_surface, subtitle_surface in zip(
        expected_surfaces,
        subtitle_surfaces,
        strict=True,
    ):
        if cleaned_surface != subtitle_surface:
            raise PolishError("Subtitle overlay changed the accepted presentation plan.")
    proper_nouns = list(getattr(config.cleanup, "proper_nouns", []))
    proper_noun_counts = _validate_proper_noun_casing(
        " ".join(subtitle_surfaces),
        proper_nouns,
    )
    lowercase_i_count = sum(
        1
        for token in subtitle_surfaces
        if _presentation_core(token).casefold() == "i" and _presentation_core(token) != "I"
    )
    if lowercase_i_count:
        raise PolishError("Subtitle overlay restored a lowercase first-person pronoun.")
    return {
        "word_count": len(subtitle_surfaces),
        "lexical_sequence_preserved": True,
        "presentation_core_and_casing_preserved": True,
        "punctuation_relaxation_count": 0,
        "lowercase_pronoun_i_count": 0,
        "proper_noun_occurrences": proper_noun_counts,
    }


def _subtitle_timings(value: str, *, separator: str) -> list[tuple[int, int]]:
    escaped = re.escape(separator)
    pattern = re.compile(
        rf"^(\d{{2}}):(\d{{2}}):(\d{{2}}){escaped}(\d{{3}}) --> "
        rf"(\d{{2}}):(\d{{2}}):(\d{{2}}){escaped}(\d{{3}})$"
    )
    timings: list[tuple[int, int]] = []
    for line in value.splitlines():
        match = pattern.fullmatch(line)
        if match is None:
            continue
        numbers = [int(item) for item in match.groups()]
        start = ((numbers[0] * 60 + numbers[1]) * 60 + numbers[2]) * 1000 + numbers[3]
        end = ((numbers[4] * 60 + numbers[5]) * 60 + numbers[6]) * 1000 + numbers[7]
        if start < 0 or end <= start or (timings and start < timings[-1][0]):
            raise PolishError("Subtitle timing is invalid or non-monotonic.")
        timings.append((start, end))
    if not timings:
        raise PolishError("Subtitle output contains no timing cues.")
    return timings


def _is_ordered_subsequence(
    selected: Sequence[tuple[int, int]],
    canonical: Sequence[tuple[int, int]],
) -> bool:
    cursor = 0
    for timing in canonical:
        if cursor < len(selected) and selected[cursor] == timing:
            cursor += 1
    return cursor == len(selected)


def _cleanup_receipt(report: Mapping[str, Any], config: PipelineConfig) -> dict[str, Any]:
    raw_request = report.get("request")
    request: Mapping[str, Any] = raw_request if isinstance(raw_request, Mapping) else {}
    raw_backend = report.get("backend")
    backend: Mapping[str, Any] = raw_backend if isinstance(raw_backend, Mapping) else {}
    # ``run_cleanup`` records privacy-safe backend identity beside the bounded
    # window counters in ``request``.  Also accept the older top-level shape so
    # composition remains compatible with already completed jobs.
    effective_backend = backend or {
        key: request[key]
        for key in (
            "backend",
            "locality",
            "model",
            "sdk",
            "sdk_version",
            "os",
            "proper_noun_count",
            "response_contract",
        )
        if key in request
    }
    return {
        "schema_version": "auralign_cleanup_receipt.v2",
        "accepted": report.get("accepted") is True,
        "mode": config.cleanup.mode,
        "scope": config.cleanup.scope,
        "required_complete": bool(getattr(config.cleanup, "required_complete", False)),
        "model": effective_backend.get("model") or report.get("model") or config.cleanup.model,
        "locality": effective_backend.get("locality") or report.get("locality") or "local",
        "endpoint_kind": "none_system_framework"
        if config.cleanup.mode == "apple-foundation"
        else "configured_local_endpoint",
        "window_count": request.get("window_count"),
        "window_size_words": request.get("window_size_words"),
        "accepted_window_count": request.get("accepted_window_count"),
        "fallback_window_count": request.get("fallback_window_count"),
        "fallback_reasons": request.get("fallback_reasons", {}),
        "decoder_version": request.get("decoder_version"),
        "decoder_tie_break_policy": request.get("decoder_tie_break_policy"),
        "decoder_escalation_policy": request.get("decoder_escalation_policy"),
        "presentation_plan_sha256": request.get("presentation_plan_sha256"),
        "presentation_plan_word_count": request.get("presentation_plan_word_count"),
        "presentation_input_digest": request.get("presentation_input_digest"),
        "hard_constraint_application_count": request.get("hard_constraint_application_count", 0),
        "hard_constraint_violation_count": request.get("hard_constraint_violation_count", 0),
        "scorer_count": request.get("scorer_count"),
        "scorer_abstention_count": request.get("scorer_abstention_count", 0),
        "overlap_disagreement_count": request.get("overlap_disagreement_count", 0),
        "selected_negative_local_margin_count": request.get(
            "selected_negative_local_margin_count", 0
        ),
        "proposal_source_registry": [
            dict(item)
            for item in request.get("proposal_source_registry", [])
            if isinstance(item, Mapping)
        ],
        "proposal_source_receipts": [
            dict(item)
            for item in request.get("proposal_source_receipts", [])
            if isinstance(item, Mapping)
        ],
        "sat_receipt": (
            dict(request["sat_receipt"])
            if isinstance(request.get("sat_receipt"), Mapping)
            else None
        ),
        "word_model_receipt": (
            dict(request["word_model_receipt"])
            if isinstance(request.get("word_model_receipt"), Mapping)
            else None
        ),
        "quality_profile": request.get("quality_profile") or config.cleanup.quality_profile,
        "semantic_cleanup_version": request.get("semantic_cleanup_version"),
        "semantic_cleanup_plan_sha256": request.get("semantic_cleanup_plan_sha256"),
        "semantic_reconciliation_sha256": request.get("semantic_reconciliation_sha256"),
        "semantic_proposal_receipt_count": request.get("semantic_proposal_receipt_count"),
        "semantic_abstention_receipt_count": request.get("semantic_abstention_receipt_count"),
        "canonical_semantic_evidence_digest": request.get("canonical_semantic_evidence_digest"),
        "semantic_edit_count": request.get("semantic_edit_count", 0),
        "semantic_rejected_proposal_count": request.get("semantic_rejected_proposal_count", 0),
        "semantic_clean_word_count": request.get("semantic_clean_word_count"),
        "structured_edit_safety_recovery_count": request.get(
            "structured_edit_safety_recovery_count", 0
        ),
        "structured_edit_lexical_recovery_count": request.get(
            "structured_edit_lexical_recovery_count", 0
        ),
        "surface_quality": (
            dict(request["surface_quality"])
            if isinstance(request.get("surface_quality"), Mapping)
            else None
        ),
        "presentation_lexical_conservation": report.get("diff", {}).get("reason")
        if isinstance(report.get("diff"), Mapping)
        else None,
        "backend": dict(effective_backend),
    }


def _write_private_bytes(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory_hashes(directory: Path) -> dict[str, dict[str, str | int]]:
    return {
        path.name: {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
        for path in sorted(directory.iterdir(), key=lambda item: item.name)
        if path.is_file() and not path.is_symlink()
    }


def _fsync_tree(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
