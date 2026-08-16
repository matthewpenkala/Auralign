# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Explicit model acquisition and cache validation."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import stat
import sys
import tempfile
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import uuid4

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError

from .config import (
    MODEL_MANIFEST,
    ModelEntry,
    hf_token,
    model_local_path,
    resolve_model_entry,
    token_present,
)
from .errors import ModelCacheError, OfflineModeError
from .native_runtime import probe_torchcodec
from .platform_security import (
    HeldFileLock,
    LockUnavailableError,
    ensure_private_directory,
    open_regular_nofollow,
    reject_path_indirection,
)
from .redaction import redact_obj
from .utils import write_json

MODEL_PROVENANCE_FILENAME = ".ltm-model.json"
INSTALLED_MODEL_BUNDLE_ROOT = Path(
    "/Library/Application Support/com.auralign.studio/Models/"
    "com.auralign.studio.models.bootstrap.v2"
)
INSTALLED_MODEL_PACKS_ROOT = (
    Path.home() / "Library" / "Application Support" / "com.auralign.studio" / "Models" / "Packs"
)
INSTALLED_PRODUCT_ENV = "LTM_INSTALLED_PRODUCT"
SYSTEM_MODEL_BUNDLE_ROOT_ENV = "LTM_SYSTEM_MODEL_BUNDLE_ROOT"
USER_MODEL_PACKS_ROOT_ENV = "LTM_USER_MODEL_PACKS_ROOT"
MIN_RUNTIME_PREPARATION_FREE_BYTES = 8 * 1024**3
RUNTIME_PREPARATION_IMPORTS = (
    "faster_whisper",
    "pyannote.audio",
    "whisperx",
    "speechbrain",
    "torch",
    "torchaudio",
    "transformers",
    "nltk",
)


@dataclass(frozen=True)
class ModelStatus:
    entry: ModelEntry
    path: Path
    exists: bool
    files_complete: bool
    present: bool
    missing_files: tuple[str, ...]
    zero_size_files: tuple[str, ...]
    mismatched_size_files: tuple[str, ...]
    inaccessible_files: tuple[str, ...]
    unexpected_files: tuple[str, ...]
    token_required: bool
    token_present: bool
    revision_verified: bool
    provenance_problem: str

    @property
    def pull_command(self) -> str:
        return self.entry.pull_command()

    @property
    def partial(self) -> bool:
        return self.exists and not self.present

    @property
    def offline_usable(self) -> bool:
        return self.present

    @property
    def token_dependent_access_required(self) -> bool:
        return self.entry.gated and not self.present

    def problem_summary(self, cache_dir: Path, *, shareable: bool = False) -> str:
        if self.present:
            return "complete"
        details: list[str] = []
        displayed_path = shareable_model_path(self.path, cache_dir) if shareable else str(self.path)
        if not self.exists:
            details.append(f"directory missing at {displayed_path}")
        if self.missing_files:
            details.append(f"missing files: {', '.join(self.missing_files)}")
        if self.zero_size_files:
            details.append(f"empty files: {', '.join(self.zero_size_files)}")
        if self.mismatched_size_files:
            details.append(
                "files with unexpected byte counts: " + ", ".join(self.mismatched_size_files)
            )
        if self.inaccessible_files:
            details.append(f"inaccessible files: {', '.join(self.inaccessible_files)}")
        if self.unexpected_files:
            details.append(
                f"unexpected non-allowlisted files or links: {', '.join(self.unexpected_files)}"
            )
        repository_contract_incomplete = self.provenance_problem.startswith(
            ("repository digest contract incomplete", "repository size contract incomplete")
        )
        if (
            self.entry.pullable
            and not self.revision_verified
            and (self.exists or repository_contract_incomplete)
        ):
            details.append(self.provenance_problem)
        if self.entry.gated and not self.token_present:
            details.append("HF_TOKEN or LTM_HF_TOKEN is required for gated model access")
        if self.entry.pullable and not repository_contract_incomplete:
            command_cache = "<model-cache>" if shareable else str(cache_dir)
            details.append(f"run: {self.entry.pull_command(command_cache)}")
        else:
            details.append(
                "provide an existing local model directory; runtime downloads are disabled"
            )
        return "; ".join(details)

    def to_audit_record(self, cache_dir: Path) -> dict[str, Any]:
        provenance_file, provenance_type, resolved_source = _model_source_descriptor(
            self.entry,
            self.path,
        )
        return {
            "role": self.entry.role,
            "key": self.entry.key,
            "model_id": self.entry.model_id,
            "aliases": list(self.entry.aliases),
            "directory_aliases": list(self.entry.directory_aliases),
            "revision": self.entry.revision,
            "revision_verified": self.revision_verified,
            "provenance_file": provenance_file,
            "provenance_type": provenance_type,
            "resolved_source": resolved_source,
            "provenance_problem": self.provenance_problem,
            "license_expression": self.entry.license_expression,
            "source_url": self.entry.source_url,
            "terms_url": self.entry.terms_url,
            "attribution": self.entry.attribution,
            "acquisition_status": self.entry.acquisition_status,
            "runtime_status": self.entry.runtime_status,
            "redistribution_status": self.entry.redistribution_status,
            "compliance_review_status": self.entry.review_status,
            "compliance_reviewed_at": self.entry.reviewed_at,
            "product_classification": self.entry.product_classification,
            "product_selectable": self.entry.production_selectable,
            "production_selectable": self.entry.production_selectable,
            "product_blocker": self.entry.blocker,
            "backend_compatibility": self.entry.backend_compatibility,
            "modification_status": self.entry.modification_status,
            "package_bundle_id": self.entry.package_bundle_id,
            "package_bundle_version": self.entry.package_bundle_version,
            "pack_id": self.entry.package_bundle_id,
            "pack_version": self.entry.package_bundle_version,
            "package_relative_path": self.entry.package_relative_path,
            "delivery": self.entry.delivery,
            "local_path": str(self.path),
            "expected_directory": self.entry.directory_name,
            "cache_status": "complete"
            if self.present
            else ("missing" if not self.exists else "partial"),
            "exists": self.exists,
            "files_complete": self.files_complete,
            "complete": self.present,
            "required_files": list(self.entry.required_files),
            "any_required_files": list(self.entry.any_required_files),
            "runtime_file_allowlist": list(self.entry.runtime_files),
            "expected_sha256": self.entry.expected_digest_map,
            "expected_size": self.entry.expected_size_map,
            "missing_files": list(self.missing_files),
            "suspicious_files": sorted({*self.zero_size_files, *self.mismatched_size_files}),
            "zero_size_files": list(self.zero_size_files),
            "mismatched_size_files": list(self.mismatched_size_files),
            "inaccessible_files": list(self.inaccessible_files),
            "unexpected_files": list(self.unexpected_files),
            "gated": self.entry.gated,
            "token_required": self.entry.gated,
            "token_present": self.token_present,
            "offline_usable": self.offline_usable,
            "classification": self.entry.product_classification,
            "selection_requirement": "optional" if self.entry.optional else "required",
            "pullable": self.entry.pullable,
            "pull_command": self.entry.pull_command(str(cache_dir)),
            "verify_command": self.entry.verify_command(str(cache_dir)),
            "problem": self.problem_summary(cache_dir),
        }


def status_for(entry: ModelEntry, cache_dir: Path) -> ModelStatus:
    path = best_existing_model_path(entry, cache_dir)
    return status_for_path(entry, path)


def status_for_path(entry: ModelEntry, path: Path) -> ModelStatus:
    path = path.expanduser()
    if installed_product_mode():
        trusted_path = _installed_product_model_path(entry)
        if trusted_path is None or _absolute_path(path) != _absolute_path(trusted_path):
            raise ModelCacheError(
                "The installed Auralign product accepts models only from its exact "
                "verified system component or native model-pack location."
            )
    exists = os.path.lexists(path)
    installed_status = (
        _installed_delivery_status(entry, path)
        if entry.pullable and _repository_digest_contract_problem(entry) is None
        else None
    )
    unexpected = _unexpected_runtime_assets(entry, path)
    if unexpected:
        installed_source = _is_packaged_model_bundle_path(
            entry,
            path,
        ) or _is_installed_model_pack_path(entry, path)
        return ModelStatus(
            entry=entry,
            path=path,
            exists=exists,
            files_complete=False,
            present=False,
            missing_files=(),
            zero_size_files=(),
            mismatched_size_files=(),
            inaccessible_files=(),
            unexpected_files=unexpected,
            token_required=entry.gated,
            token_present=token_present(),
            revision_verified=False,
            provenance_problem=(
                installed_status[1]
                if installed_source and installed_status is not None
                else "model cache contains linked or non-allowlisted content"
            ),
        )
    missing = tuple(
        required for required in entry.required_files if required and not (path / required).exists()
    )
    if entry.any_required_files and not any(
        (path / required).exists() for required in entry.any_required_files
    ):
        missing = (*missing, " or ".join(entry.any_required_files))
    zero_size = tuple(
        required
        for required in entry.required_files
        if required and (path / required).exists() and (path / required).stat().st_size == 0
    )
    zero_size = (
        *zero_size,
        *tuple(
            required
            for required in entry.any_required_files
            if required and (path / required).exists() and (path / required).stat().st_size == 0
        ),
    )
    mismatched_size = _mismatched_size_files(entry, path)
    inaccessible = tuple(
        required
        for required in entry.required_files
        if required and (path / required).exists() and not _can_read(path / required)
    )
    inaccessible = (
        *inaccessible,
        *tuple(
            required
            for required in entry.any_required_files
            if required and (path / required).exists() and not _can_read(path / required)
        ),
    )
    files_complete = exists and (
        not entry.required_files
        or (not missing and not zero_size and not mismatched_size and not inaccessible)
    )
    if entry.any_required_files:
        files_complete = files_complete and _any_required_file_present(entry, path)
    files_complete = files_complete and not unexpected
    revision_verified, provenance_problem = (
        installed_status if installed_status is not None else _provenance_status(entry, path)
    )
    present = files_complete and (revision_verified if entry.pullable else True)
    return ModelStatus(
        entry=entry,
        path=path,
        exists=exists,
        files_complete=files_complete,
        present=present,
        missing_files=missing,
        zero_size_files=zero_size,
        mismatched_size_files=mismatched_size,
        inaccessible_files=inaccessible,
        unexpected_files=unexpected,
        token_required=entry.gated,
        token_present=token_present(),
        revision_verified=revision_verified,
        provenance_problem=provenance_problem,
    )


def all_statuses(cache_dir: Path) -> list[ModelStatus]:
    return [status_for(entry, cache_dir) for entry in MODEL_MANIFEST.values()]


def audit_payload(cache_dir: Path, *, include_optional: bool = True) -> dict[str, Any]:
    statuses = [
        status
        for status in all_statuses(cache_dir)
        if include_optional or not status.entry.optional
    ]
    complete = sum(1 for status in statuses if status.present)
    return cast(
        dict[str, Any],
        redact_obj(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "model_cache": str(cache_dir.expanduser()),
                "summary": {
                    "total": len(statuses),
                    "complete": complete,
                    "missing_or_partial": len(statuses) - complete,
                    "offline_usable": all(status.offline_usable for status in statuses),
                },
                "environment": {
                    "hf_token_present": token_present(),
                    "ltm_hf_token_present": bool(os.getenv("LTM_HF_TOKEN")),
                    "hf_token_env_present": bool(os.getenv("HF_TOKEN")),
                },
                "models": [status.to_audit_record(cache_dir) for status in statuses],
                "last_verification_timestamp": datetime.now(UTC).isoformat(),
            },
        ),
    )


def write_model_audit(
    cache_dir: Path,
    *,
    output_path: Path | None = None,
    include_optional: bool = True,
) -> Path:
    output = output_path or cache_dir.expanduser() / "model_audit.json"
    write_json(output, audit_payload(cache_dir, include_optional=include_optional))
    return output


def verify_models(cache_dir: Path, *, include_optional: bool = True) -> list[ModelStatus]:
    statuses = [
        status
        for status in all_statuses(cache_dir)
        if status.entry.production_selectable and (include_optional or not status.entry.optional)
    ]
    _raise_for_incomplete(statuses, cache_dir)
    return statuses


def verify_entries(entries: list[ModelEntry], cache_dir: Path) -> list[ModelStatus]:
    statuses = [status_for(entry, cache_dir) for entry in entries]
    _raise_for_incomplete(statuses, cache_dir)
    return statuses


def _raise_for_incomplete(statuses: list[ModelStatus], cache_dir: Path) -> None:
    incomplete = [status for status in statuses if not status.present]
    if incomplete:
        details = "\n".join(
            f"{status.entry.role}:{status.entry.key}: {status.problem_summary(cache_dir)}"
            for status in incomplete
        )
        raise ModelCacheError(f"Model verification failed:\n{details}")


def prune_plan(cache_dir: Path) -> dict[str, Any]:
    known_dirs = {
        path.name
        for entry in MODEL_MANIFEST.values()
        for path in candidate_model_paths(entry, cache_dir)
    }
    root = cache_dir.expanduser()
    candidates: list[dict[str, Any]] = []
    if root.exists():
        for child in sorted(root.iterdir()):
            if child.name == "model_audit.json":
                continue
            if child.is_dir() and child.name not in known_dirs:
                candidates.append({"path": str(child), "reason": "not in model manifest"})
    return {"model_cache": str(root), "candidates": candidates}


def require_model(role: str, value: str, cache_dir: Path, *, offline: bool) -> Path:
    entry = resolve_model_entry(role, value)
    if entry is None:
        raise ModelCacheError(
            f"Unknown {role} model '{value}'. Add it to MODEL_MANIFEST before using it."
        )
    if not entry.production_selectable:
        raise ModelCacheError(
            f"{role.upper()} model {entry.key} is unavailable in the production product. "
            f"Classification: {entry.product_classification}. "
            f"Blocker: {_product_model_unavailability_reason(entry)}"
        )
    status = status_for(entry, cache_dir)
    if status.present:
        return status.path
    message = (
        f"{role.upper()} model {entry.key} is not ready.\n"
        f"Model ID: {entry.model_id}\n"
        f"Expected: {status.path}\n"
        f"Problem: {status.problem_summary(cache_dir)}\n"
        f"Run online: {entry.pull_command(str(cache_dir))}"
    )
    if offline:
        raise OfflineModeError("Offline mode is enabled, but " + message)
    raise ModelCacheError(message)


def metadata_available(entry: ModelEntry) -> bool:
    try:
        info = HfApi(token=hf_token()).model_info(entry.model_id, revision=entry.revision)
    except HfHubHTTPError:
        return False
    return entry.revision is not None and info.sha == entry.revision


def audit_selected_models(
    cache_dir: Path,
    *,
    asr: str,
    diarization: str | None,
    alignment: str | None = None,
    speaker_embedding: str | None = None,
) -> list[ModelStatus]:
    requested = [("asr", asr)]
    if diarization:
        requested.append(("diarization", diarization))
    if alignment:
        requested.append(("alignment", alignment))
    if speaker_embedding:
        requested.append(("speaker-embedding", speaker_embedding))
    statuses: list[ModelStatus] = []
    for role, value in requested:
        entry = resolve_model_entry(role, value)
        if entry is None:
            raise ModelCacheError(f"Unknown {role} model '{value}'.")
        statuses.append(status_for(entry, cache_dir))
    return statuses


def pull_model(
    entry: ModelEntry,
    cache_dir: Path,
    *,
    revision: str | None = None,
    repair: bool = False,
) -> Path:
    if not entry.production_selectable:
        raise ModelCacheError(
            f"{entry.role}:{entry.key} ({entry.model_id}) is unavailable in the "
            f"production product: {_product_model_unavailability_reason(entry)}."
        )
    if not entry.pullable:
        raise ModelCacheError(
            f"{entry.role}:{entry.key} ({entry.model_id}) is audit-only and cannot be "
            "downloaded by ltm models pull. Provide an existing local model directory."
        )
    if not entry.revision:
        raise ModelCacheError(
            f"{entry.role}:{entry.key} is not approved for acquisition: "
            "an immutable upstream revision is required."
        )
    digest_problem = _repository_digest_contract_problem(entry)
    if digest_problem:
        raise ModelCacheError(digest_problem)
    if not entry.review_status.startswith("verified"):
        raise ModelCacheError(
            f"{entry.role}:{entry.key} is not approved for acquisition: "
            f"compliance_status={entry.review_status}."
        )
    if revision is not None and revision != entry.revision:
        raise ModelCacheError(
            f"Refusing unapproved revision override for {entry.role}:{entry.key}. "
            f"Approved revision: {entry.revision}."
        )
    root = ensure_private_directory(cache_dir.expanduser())
    local_dir = model_local_path(entry, root)
    with _model_pull_lock(root, entry):
        current_status = status_for(entry, root)
        if current_status.present:
            return current_status.path
        if entry.gated and not token_present():
            raise ModelCacheError(
                f"{entry.model_id} is gated. Accept the Hugging Face terms and export "
                "HF_TOKEN or LTM_HF_TOKEN before running this pull."
            )
        if current_status.exists and not repair:
            raise ModelCacheError(
                f"Refusing to overwrite a partial or corrupted model cache for "
                f"{entry.model_id}.\nProblem: {current_status.problem_summary(root)}\n"
                "Re-run the acquisition with explicit repair enabled to quarantine "
                "and replace it."
            )

        temporary = Path(tempfile.mkdtemp(prefix=f".{entry.directory_name}.pull-", dir=root))
        try:
            try:
                snapshot_download(
                    repo_id=entry.model_id,
                    revision=entry.revision,
                    local_dir=temporary,
                    local_dir_use_symlinks=False,
                    allow_patterns=list(entry.runtime_files),
                    token=hf_token(),
                )
            except (GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError) as exc:
                raise ModelCacheError(friendly_hf_error(entry, exc, root)) from exc
            shutil.rmtree(temporary / ".cache", ignore_errors=True)
            write_model_provenance(entry, temporary)
            downloaded_status = status_for_path(entry, temporary)
            if not downloaded_status.present:
                raise ModelCacheError(
                    f"Downloaded model cache is partial or corrupted for {entry.model_id}.\n"
                    f"Problem: {downloaded_status.problem_summary(root)}"
                )

            quarantine: Path | None = None
            replaced_path = current_status.path if current_status.exists else local_dir
            if replaced_path.exists() or replaced_path.is_symlink():
                quarantine = _quarantine_path(replaced_path)
                os.replace(replaced_path, quarantine)
            try:
                if local_dir.exists() or local_dir.is_symlink():
                    raise ModelCacheError(
                        f"Refusing to replace an unexpected model path at {local_dir}."
                    )
                os.replace(temporary, local_dir)
            except Exception:
                if quarantine is not None and not replaced_path.exists():
                    os.replace(quarantine, replaced_path)
                raise
            return local_dir
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def review_model_digests(entry: ModelEntry, cache_dir: Path) -> dict[str, str]:
    """Acquire one authorized revision ephemerally for manifest digest review only."""

    if not entry.production_selectable:
        raise ModelCacheError(
            f"{entry.role}:{entry.key} ({entry.model_id}) is unavailable in the "
            f"production product: {_product_model_unavailability_reason(entry)}."
        )
    if not entry.pullable or not entry.revision or entry.license_expression == "NOASSERTION":
        raise ModelCacheError(
            f"{entry.role}:{entry.key} is not approved for connected digest review."
        )
    if entry.gated and not token_present():
        raise ModelCacheError(
            f"{entry.model_id} is gated. Accept its terms and export HF_TOKEN or "
            "LTM_HF_TOKEN before explicit digest review."
        )
    root = ensure_private_directory(cache_dir.expanduser())
    with _model_pull_lock(root, entry):
        temporary = Path(tempfile.mkdtemp(prefix=f".{entry.directory_name}.review-", dir=root))
        try:
            try:
                snapshot_download(
                    repo_id=entry.model_id,
                    revision=entry.revision,
                    local_dir=temporary,
                    local_dir_use_symlinks=False,
                    allow_patterns=list(entry.runtime_files),
                    token=hf_token(),
                )
            except (GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError) as exc:
                raise ModelCacheError(friendly_hf_error(entry, exc, root)) from exc
            shutil.rmtree(temporary / ".cache", ignore_errors=True)
            unexpected = _unexpected_runtime_assets(entry, temporary)
            if unexpected:
                raise ModelCacheError(
                    "Digest-review download contained non-allowlisted files or links."
                )
            digests = _runtime_file_digests(entry, temporary)
            missing = set(entry.runtime_files) - set(digests)
            if missing:
                raise ModelCacheError(
                    "Digest-review download is incomplete for: " + ", ".join(sorted(missing))
                )
            return digests
        finally:
            shutil.rmtree(temporary)
            if temporary.exists() or temporary.is_symlink():
                raise ModelCacheError("Digest-review temporary model bytes could not be removed.")


def import_mlx_model(
    source_dir: Path,
    cache_dir: Path,
    *,
    model: str = "mlx-large-v3",
    symlink: bool = False,
    overwrite: bool = False,
) -> ModelStatus:
    entry = resolve_model_entry("asr-mlx", model)
    if entry is None:
        raise ModelCacheError(f"Unknown MLX ASR model '{model}'.")
    if not entry.production_selectable:
        raise ModelCacheError(
            f"MLX ASR model {entry.key} is unavailable in the production product: "
            f"{_product_model_unavailability_reason(entry)}."
        )
    if symlink:
        raise ModelCacheError(
            "MLX model symlink imports are prohibited. Re-run with --copy so the "
            "verified cache contains only regular local files."
        )
    source = source_dir.expanduser()
    source_status = status_for_path(entry, source)
    if not source_status.present:
        raise ModelCacheError(
            f"MLX model source is incomplete: {source}.\n"
            f"Problem: {source_status.problem_summary(cache_dir)}"
        )
    destination = model_local_path(entry, cache_dir)
    if os.path.lexists(destination):
        if not overwrite:
            raise ModelCacheError(
                f"Destination already exists: {destination}. Re-run with --overwrite to replace it."
            )
        try:
            reject_path_indirection(destination)
        except (OSError, RuntimeError) as exc:
            raise ModelCacheError("Refusing to overwrite a linked MLX cache path.") from exc
        _remove_cache_path(destination)
    ensure_private_directory(destination.parent)
    shutil.copytree(source, destination)
    imported_status = status_for_path(entry, destination)
    if not imported_status.present:
        raise ModelCacheError(
            f"Imported MLX model is incomplete at {destination}.\n"
            f"Problem: {imported_status.problem_summary(cache_dir)}"
        )
    return imported_status


def friendly_hf_error(entry: ModelEntry, exc: Exception, cache_dir: Path) -> str:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    base = [f"Could not download {entry.role}:{entry.key} ({entry.model_id})."]
    if entry.gated and not token_present():
        base.append("Missing HF_TOKEN/LTM_HF_TOKEN for gated Hugging Face model access.")
        base.append("Accept the model terms, export a read token, then retry.")
    elif isinstance(exc, GatedRepoError) or status_code in {401, 403}:
        base.append("Access was denied for a gated Hugging Face model.")
        base.append(
            "Confirm that you accepted the model terms and that HF_TOKEN/LTM_HF_TOKEN "
            "has read access."
        )
    elif status_code == 404:
        base.append("The configured model ID was not found. Check the manifest entry.")
    else:
        base.append(str(exc).splitlines()[0])
    base.append(f"Retry: {entry.pull_command(str(cache_dir))}")
    return "\n".join(base)


def ensure_pull_preconditions(entries: list[ModelEntry], cache_dir: Path) -> None:
    blocked: list[str] = []
    for entry in entries:
        if not entry.production_selectable:
            blocked.append(
                f"{entry.role}:{entry.key} ({entry.model_id}) is unavailable in the "
                f"production product: {_product_model_unavailability_reason(entry)}."
            )
            continue
        if not entry.revision or not entry.review_status.startswith("verified"):
            blocked.append(
                f"{entry.role}:{entry.key} ({entry.model_id}) is not compliance-approved "
                f"for acquisition: {entry.review_status}."
            )
            continue
        digest_problem = _repository_digest_contract_problem(entry)
        if digest_problem:
            blocked.append(digest_problem)
            continue
        if not entry.pullable:
            blocked.append(
                f"{entry.role}:{entry.key} ({entry.model_id}) is audit-only and cannot be "
                "downloaded by ltm models pull."
            )
            continue
        status = status_for(entry, cache_dir)
        if status.present:
            continue
        if entry.gated and not token_present():
            blocked.append(
                f"{entry.role}:{entry.key} ({entry.model_id}) requires HF_TOKEN or LTM_HF_TOKEN. "
                "Accept the Hugging Face model terms before pulling."
            )
    if blocked:
        raise ModelCacheError("\n".join(blocked))


def preflight_runtime_preparation(
    entries: list[ModelEntry], cache_dir: Path, *, repair: bool = False
) -> None:
    """Validate release runtime prerequisites before any model network access."""

    if not _release_python_is_311():
        raise ModelCacheError("Runtime preparation for release requires Python 3.11 exactly.")
    missing_binaries = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing_binaries:
        raise ModelCacheError(
            "Runtime preparation is missing required native commands: "
            f"{', '.join(missing_binaries)}."
        )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for module_name in RUNTIME_PREPARATION_IMPORTS:
            try:
                importlib.import_module(module_name)
            except Exception as exc:
                raise ModelCacheError(
                    f"Runtime preparation dependency import failed: {module_name} "
                    f"({exc.__class__.__name__}). Install the locked full dependency profile."
                ) from exc
    try:
        probe_torchcodec()
    except Exception as exc:
        raise ModelCacheError(
            "Runtime preparation native TorchCodec/FFmpeg probe failed. Install the locked "
            "native dependency profile."
        ) from exc

    root = cache_dir.expanduser()
    try:
        ensure_private_directory(root)
        with tempfile.NamedTemporaryFile(
            prefix=".ltm-write-test-", dir=root, delete=False
        ) as handle:
            probe = Path(handle.name)
        probe.unlink()
    except Exception as exc:
        raise ModelCacheError(
            "Runtime preparation model cache must be a private writable local directory."
        ) from exc

    needs_storage = repair or any(not status_for(entry, root).present for entry in entries)
    if needs_storage and shutil.disk_usage(root).free < MIN_RUNTIME_PREPARATION_FREE_BYTES:
        raise ModelCacheError(
            "Runtime preparation requires at least 8 GiB free on the model-cache volume "
            "before acquiring or repairing strict models."
        )


def _release_python_is_311() -> bool:
    """Return whether this interpreter is the supported release-preparation Python."""

    return (sys.version_info.major, sys.version_info.minor) == (3, 11)


def selected_entries(
    *,
    all_models: bool = False,
    asr: str | None = None,
    asr_mlx: str | None = None,
    diarization: str | None = None,
    alignment: str | None = None,
    speaker_embedding: str | None = None,
) -> list[ModelEntry]:
    if all_models:
        return [entry for entry in MODEL_MANIFEST.values() if entry.production_selectable]
    requested: list[tuple[str, str | None]] = [
        ("asr", asr),
        ("asr-mlx", asr_mlx),
        ("diarization", diarization),
        ("alignment", alignment),
        ("speaker-embedding", speaker_embedding),
    ]
    entries: list[ModelEntry] = []
    for role, value in requested:
        if not value:
            continue
        entry = resolve_model_entry(role, value)
        if entry is None:
            raise ModelCacheError(f"Unknown {role} model '{value}'.")
        if not entry.production_selectable:
            raise ModelCacheError(
                f"{entry.role}:{entry.key} ({entry.model_id}) is unavailable in the "
                f"production product: {_product_model_unavailability_reason(entry)}."
            )
        entries.append(entry)
    return entries


def _remove_cache_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _can_read(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError:
        return False
    return True


def candidate_model_paths(entry: ModelEntry, cache_dir: Path) -> list[Path]:
    if installed_product_mode():
        trusted_path = _installed_product_model_path(entry)
        if trusted_path is None:
            raise ModelCacheError(
                f"Installed-product model delivery is not configured for {entry.role}:{entry.key}."
            )
        return [trusted_path]

    root = cache_dir.expanduser()
    paths: list[Path] = []
    installed_pack = _installed_model_pack_locations(entry)
    if installed_pack is not None:
        paths.append(installed_pack[1])
    installed_bundle = _installed_model_bundle_location(entry)
    if installed_bundle is not None:
        paths.append(installed_bundle)
    paths.append(model_local_path(entry, cache_dir))
    for alias in entry.directory_aliases:
        paths.append(root / alias)
    for alias in entry.aliases:
        if "/" in alias:
            paths.append(root / alias.replace("/", "--"))
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def installed_product_mode() -> bool:
    """Return whether the engine is bound to the signed installed-product roots.

    Development and controlled release-machine acquisition intentionally retain
    support for a private cache. The embedded app sets all three variables
    below; treating a partial or redirected declaration as development state
    would re-open the legacy cache path in the installed product.
    """

    marker = os.getenv(INSTALLED_PRODUCT_ENV)
    if marker is None:
        return False
    if marker != "1":
        raise ModelCacheError("The installed-product runtime marker is invalid.")
    expected_roots = {
        SYSTEM_MODEL_BUNDLE_ROOT_ENV: INSTALLED_MODEL_BUNDLE_ROOT,
        USER_MODEL_PACKS_ROOT_ENV: INSTALLED_MODEL_PACKS_ROOT,
    }
    for name, expected in expected_roots.items():
        value = os.getenv(name)
        if value is None or _absolute_path(Path(value)) != _absolute_path(expected):
            raise ModelCacheError(
                f"The installed-product model root contract is missing or invalid: {name}."
            )
    return True


def _installed_product_model_path(entry: ModelEntry) -> Path | None:
    if entry.delivery == "native-download":
        locations = _installed_model_pack_locations(entry)
        return locations[1] if locations is not None else None
    if entry.delivery == "system-component":
        return _installed_model_bundle_location(entry)
    return None


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def best_existing_model_path(entry: ModelEntry, cache_dir: Path) -> Path:
    paths = candidate_model_paths(entry, cache_dir)
    for path in paths:
        if _model_path_complete(entry, path):
            return path
    for path in paths:
        if path.exists():
            return path
    return paths[0]


def _model_path_complete(entry: ModelEntry, path: Path) -> bool:
    if not path.exists() or path.is_symlink() or _unexpected_runtime_assets(entry, path):
        return False
    for required in entry.required_files:
        if not required:
            continue
        required_path = path / required
        try:
            expected_size = entry.expected_size_map.get(required)
            if (
                not required_path.exists()
                or required_path.stat().st_size == 0
                or (expected_size is not None and required_path.stat().st_size != expected_size)
                or not _can_read(required_path)
            ):
                return False
        except OSError:
            return False
    if entry.any_required_files and not _any_required_file_present(entry, path):
        return False
    if entry.pullable and not _provenance_status(entry, path)[0]:
        return False
    return True


def _any_required_file_present(entry: ModelEntry, path: Path) -> bool:
    for required in entry.any_required_files:
        if not required:
            continue
        required_path = path / required
        try:
            expected_size = entry.expected_size_map.get(required)
            if (
                required_path.exists()
                and required_path.stat().st_size > 0
                and (expected_size is None or required_path.stat().st_size == expected_size)
                and _can_read(required_path)
            ):
                return True
        except OSError:
            return False
    return False


def model_manifest_digest(entry: ModelEntry) -> str:
    """Digest the reviewed acquisition/runtime contract for one manifest entry."""

    contract = {
        "key": entry.key,
        "role": entry.role,
        "model_id": entry.model_id,
        "revision": entry.revision,
        "gated": entry.gated,
        "runtime_files": list(entry.runtime_files),
        "expected_sha256": entry.expected_digest_map,
        "expected_size": entry.expected_size_map,
        "license_expression": entry.license_expression,
        "source_url": entry.source_url,
        "terms_url": entry.terms_url,
        "acquisition_status": entry.acquisition_status,
        "runtime_status": entry.runtime_status,
        "redistribution_status": entry.redistribution_status,
        "review_status": entry.review_status,
        "product_classification": entry.product_classification,
        "backend_compatibility": entry.backend_compatibility,
        "modification_status": entry.modification_status,
        "package_bundle_id": entry.package_bundle_id,
        "package_bundle_version": entry.package_bundle_version,
        "package_relative_path": entry.package_relative_path,
        "delivery": entry.delivery,
    }
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def model_checkpoint_sha256(entry: ModelEntry, model_dir: Path) -> str:
    """Bind exact local runtime files without asserting upstream provenance."""

    digests = _runtime_file_digests(entry, model_dir)
    expected_files = {
        relative for relative in entry.runtime_files if (model_dir / relative).is_file()
    }
    if not digests or set(digests) != expected_files:
        raise ModelCacheError(
            f"Could not bind every local runtime file for {entry.role}:{entry.key}."
        )
    encoded = json.dumps(
        {
            "model_id": entry.model_id,
            "runtime_file_sha256": digests,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def model_provenance_payload(
    entry: ModelEntry,
    model_dir: Path | None = None,
    *,
    acquired_at: str | None = None,
    acquisition_tool_version: str | None = None,
) -> dict[str, Any]:
    if not entry.revision:
        raise ModelCacheError(f"Cannot record an unpinned model revision for {entry.key}.")
    file_digests = _runtime_file_digests(entry, model_dir) if model_dir is not None else {}
    return {
        "schema_version": "2.0",
        "key": entry.key,
        "role": entry.role,
        "model_id": entry.model_id,
        "revision": entry.revision,
        "manifest_digest": model_manifest_digest(entry),
        "file_sha256": file_digests,
        "acquired_at": acquired_at or datetime.now(UTC).isoformat(),
        "acquisition_tool_version": acquisition_tool_version or _tool_version(),
    }


def write_model_provenance(entry: ModelEntry, model_dir: Path) -> Path:
    if not entry.pullable:
        raise ModelCacheError(
            f"Cannot attest an Auralign pull for manual-only model {entry.role}:{entry.key}."
        )
    output = model_dir / MODEL_PROVENANCE_FILENAME
    write_json(output, model_provenance_payload(entry, model_dir))
    return output


def _provenance_status(entry: ModelEntry, path: Path) -> tuple[bool, str]:
    if not entry.pullable:
        return False, "revision linkage not asserted for manual-only local assets"
    digest_problem = _repository_digest_contract_problem(entry)
    if digest_problem:
        return False, digest_problem
    installed_status = _installed_delivery_status(entry, path)
    if installed_status is not None:
        return installed_status
    marker = path / MODEL_PROVENANCE_FILENAME
    if not marker.exists():
        return False, f"missing immutable-revision linkage marker: {MODEL_PROVENANCE_FILENAME}"
    try:
        descriptor = open_regular_nofollow(marker, os.O_RDONLY)
        try:
            if os.fstat(descriptor).st_nlink != 1:
                return (
                    False,
                    f"unsafe immutable-revision linkage marker: {MODEL_PROVENANCE_FILENAME}",
                )
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=True) as handle:
                descriptor = -1
                payload = json.load(handle)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except (OSError, RuntimeError, ValueError):
        return False, f"unreadable immutable-revision linkage marker: {MODEL_PROVENANCE_FILENAME}"
    if not isinstance(payload, dict):
        return False, f"unreadable immutable-revision linkage marker: {MODEL_PROVENANCE_FILENAME}"
    if payload.get("schema_version") != "2.0":
        return False, "obsolete immutable-revision linkage marker; sanctioned refresh required"
    expected_identity = {
        "key": entry.key,
        "role": entry.role,
        "model_id": entry.model_id,
        "revision": entry.revision,
        "manifest_digest": model_manifest_digest(entry),
    }
    if any(payload.get(key) != value for key, value in expected_identity.items()):
        return False, "immutable-revision linkage marker does not match the model manifest"
    if not isinstance(payload.get("acquired_at"), str) or not isinstance(
        payload.get("acquisition_tool_version"), str
    ):
        return False, "immutable-revision linkage marker is missing acquisition metadata"
    recorded_digests = payload.get("file_sha256")
    if not isinstance(recorded_digests, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in recorded_digests.items()
    ):
        return False, "immutable-revision linkage marker has an invalid file digest map"
    try:
        actual_digests = _runtime_file_digests(entry, path)
    except OSError:
        return False, "model runtime files could not be read for digest verification"
    if recorded_digests != actual_digests:
        return False, "model file digests do not match immutable-revision linkage marker"
    expected_digests = entry.expected_digest_map
    for relative_path, expected_digest in expected_digests.items():
        if actual_digests.get(relative_path) != expected_digest:
            return False, f"model file digest does not match manifest: {relative_path}"
    for relative_path, expected_size in entry.expected_size_map.items():
        try:
            actual_size = (path / relative_path).stat(follow_symlinks=False).st_size
        except OSError:
            return False, f"model file size could not be verified: {relative_path}"
        if actual_size != expected_size:
            return False, f"model file size does not match manifest: {relative_path}"
    return True, "immutable revision and file digests verified"


def _installed_delivery_status(
    entry: ModelEntry,
    model_path: Path,
) -> tuple[bool, str] | None:
    native_status = _installed_model_pack_status(entry, model_path)
    if native_status is not None:
        return native_status
    return _packaged_model_bundle_status(entry, model_path)


def _installed_model_pack_status(
    entry: ModelEntry,
    model_path: Path,
) -> tuple[bool, str] | None:
    """Recognize only one exact manifest-bound per-user native-download pack."""

    locations = _installed_model_pack_locations(entry)
    if locations is None:
        return None
    pack_root, expected_model_path = locations
    if not _same_lexical_path(model_path, expected_model_path):
        return None
    try:
        identity = _verify_stable_installed_model_pack(pack_root, entry)
    except Exception:
        return False, "installed native-download model pack failed deterministic verification"
    expected_identity = {
        "state": "verified",
        "bundle_id": entry.package_bundle_id,
        "bundle_version": entry.package_bundle_version,
        "package_relative_path": entry.package_relative_path,
        "model_key": entry.key,
        "model_id": entry.model_id,
        "revision": entry.revision,
    }
    if any(identity.get(key) != value for key, value in expected_identity.items()):
        return False, "installed native-download model pack does not match model authority"
    manifest_sha256 = identity.get("manifest_sha256")
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
        return False, "installed native-download model pack identity is malformed"
    return (
        True,
        "installed native-download model pack verified: "
        f"{identity['bundle_id']} {identity['bundle_version']} "
        f"manifest_sha256={manifest_sha256}",
    )


def _packaged_model_bundle_status(
    entry: ModelEntry,
    model_path: Path,
) -> tuple[bool, str] | None:
    """Recognize only the exact verified system-model bundle layout.

    Private acquisition caches continue to require ``.ltm-model.json``. The
    separately packaged component intentionally excludes that private marker
    and instead binds all four production models through one deterministic
    bundle manifest, verification record, and completion marker.
    """

    if (
        not entry.package_bundle_id
        or not entry.package_bundle_version
        or not entry.package_relative_path
    ):
        return None
    bundle_root = model_path.parent.parent
    if not _is_packaged_model_bundle_path(entry, model_path):
        return None
    expected_path = bundle_root.joinpath(*Path(entry.package_relative_path).parts)
    if model_path != expected_path:
        return False, "installed model path does not match the package authority"
    try:
        tree_fingerprint = _model_bundle_tree_fingerprint(bundle_root)
        identity = _verify_packaged_model_bundle_once(
            str(bundle_root),
            tree_fingerprint,
        )
    except Exception:
        return False, "installed model bundle failed deterministic verification"
    if (
        identity.get("state") != "verified"
        or identity.get("bundle_id") != entry.package_bundle_id
        or identity.get("bundle_version") != entry.package_bundle_version
        or entry.key not in identity.get("model_keys", [])
    ):
        return False, "installed model bundle does not authorize this model"
    return (
        True,
        "installed model bundle verified: "
        f"{identity['bundle_id']} {identity['bundle_version']} "
        f"manifest_sha256={identity['manifest_sha256']}",
    )


def _installed_model_pack_locations(entry: ModelEntry) -> tuple[Path, Path] | None:
    if (
        entry.delivery != "native-download"
        or entry.package_bundle_id is None
        or entry.package_bundle_version is None
        or entry.package_relative_path is None
        or not _safe_pack_identity(entry.package_bundle_id)
        or not _safe_pack_identity(entry.package_bundle_version)
    ):
        return None
    relative_path = _safe_pack_relative_path(entry.package_relative_path)
    if relative_path is None or entry.package_relative_path != f"models/{entry.directory_name}":
        return None
    pack_root = INSTALLED_MODEL_PACKS_ROOT / entry.package_bundle_id / entry.package_bundle_version
    return pack_root, pack_root.joinpath(*relative_path.parts)


def _installed_model_bundle_location(entry: ModelEntry) -> Path | None:
    if (
        entry.delivery != "system-component"
        or entry.package_bundle_id is None
        or entry.package_bundle_version is None
        or entry.package_relative_path is None
        or entry.package_bundle_id != INSTALLED_MODEL_BUNDLE_ROOT.name
        or not _safe_pack_identity(entry.package_bundle_id)
        or not _safe_pack_identity(entry.package_bundle_version)
    ):
        return None
    relative_path = _safe_pack_relative_path(entry.package_relative_path)
    if relative_path is None or entry.package_relative_path != f"models/{entry.directory_name}":
        return None
    return INSTALLED_MODEL_BUNDLE_ROOT.joinpath(*relative_path.parts)


def _safe_pack_identity(value: str) -> bool:
    return (
        0 < len(value) <= 128
        and value.isascii()
        and value[0].isalnum()
        and all(character.isalnum() or character in "._-" for character in value)
    )


def _safe_pack_relative_path(value: str) -> PurePosixPath | None:
    if not value or "\x00" in value or "\\" in value:
        return None
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} or part.startswith(".") for part in candidate.parts)
    ):
        return None
    return candidate


def _same_lexical_path(first: Path, second: Path) -> bool:
    return os.path.abspath(os.fspath(first.expanduser())) == os.path.abspath(
        os.fspath(second.expanduser())
    )


def _verify_stable_installed_model_pack(
    pack_root: Path,
    entry: ModelEntry,
) -> dict[str, Any]:
    """Verify a fixed pack against a stable no-follow tree fingerprint."""

    for _attempt in range(2):
        _validate_installed_model_pack_ancestry(pack_root)
        before = _model_bundle_tree_fingerprint(pack_root)
        identity = _verify_installed_model_pack_once(
            str(pack_root),
            before,
            entry,
        )
        after = _model_bundle_tree_fingerprint(pack_root)
        _validate_installed_model_pack_ancestry(pack_root)
        if before == after:
            return identity
    raise RuntimeError("installed native-download model pack changed during verification")


def _validate_installed_model_pack_ancestry(pack_root: Path) -> None:
    """Reject symlinked ancestors and unsafe user-owned namespace directories."""

    candidate = Path(os.path.abspath(os.fspath(pack_root.expanduser())))
    trusted_root = Path(os.path.abspath(os.fspath(INSTALLED_MODEL_PACKS_ROOT.expanduser())))
    candidate_text = os.fspath(candidate)
    trusted_text = os.fspath(trusted_root)
    if candidate_text != trusted_text and not candidate_text.startswith(trusted_text + os.sep):
        raise RuntimeError("installed model pack escapes the fixed user namespace")

    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeError("installed model pack has a linked or non-directory ancestor")
        current_text = os.path.abspath(os.fspath(current))
        if current_text == trusted_text or current_text.startswith(trusted_text + os.sep):
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & (
                stat.S_IWGRP | stat.S_IWOTH
            ):
                raise RuntimeError("installed model pack namespace ownership or mode is unsafe")


def _model_source_descriptor(
    entry: ModelEntry,
    model_path: Path,
) -> tuple[str, str, str]:
    if _is_installed_model_pack_path(entry, model_path):
        return (
            "model-pack-provenance.json",
            "installed_native_download_model_pack",
            "fixed_user_model_pack",
        )
    if _is_packaged_model_bundle_path(entry, model_path):
        return (
            "model-bundle-provenance.json",
            "installed_model_bundle",
            "fixed_system_model_component",
        )
    return (
        MODEL_PROVENANCE_FILENAME,
        "private_acquisition_marker",
        "private_acquisition_cache",
    )


def _is_installed_model_pack_path(entry: ModelEntry, model_path: Path) -> bool:
    locations = _installed_model_pack_locations(entry)
    return locations is not None and _same_lexical_path(model_path, locations[1])


def _is_packaged_model_bundle_path(entry: ModelEntry, model_path: Path) -> bool:
    if not entry.package_bundle_id or not entry.package_relative_path:
        return False
    expected = INSTALLED_MODEL_BUNDLE_ROOT.joinpath(*Path(entry.package_relative_path).parts)
    return _same_lexical_path(model_path, expected)


@lru_cache(maxsize=32)
def _verify_installed_model_pack_once(
    pack_root: str,
    tree_fingerprint: str,
    entry: ModelEntry,
) -> dict[str, Any]:
    """Hash an unchanged private pack once per short-lived engine process."""

    from .model_pack import verify_model_pack

    del tree_fingerprint
    return verify_model_pack(Path(pack_root), entry=entry)


@lru_cache(maxsize=8)
def _verify_packaged_model_bundle_once(
    bundle_root: str,
    tree_fingerprint: str,
) -> dict[str, Any]:
    """Hash an unchanged immutable bundle once per short-lived engine process."""

    from .model_bundle import verify_model_bundle

    del tree_fingerprint
    return verify_model_bundle(Path(bundle_root))


def _model_bundle_tree_fingerprint(bundle_root: Path) -> str:
    """Bind cache reuse to every path's no-follow filesystem identity."""

    records: list[tuple[object, ...]] = []
    pending = [bundle_root]
    while pending:
        current = pending.pop()
        metadata = current.lstat()
        relative = "." if current == bundle_root else current.relative_to(bundle_root).as_posix()
        records.append(
            (
                relative,
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_nlink,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
        )
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            with os.scandir(current) as children:
                pending.extend(
                    sorted(
                        (Path(child.path) for child in children),
                        key=lambda path: path.name,
                        reverse=True,
                    )
                )
    encoded = json.dumps(records, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _runtime_file_digests(entry: ModelEntry, model_dir: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for relative_path in entry.runtime_files:
        path = model_dir / relative_path
        try:
            reject_path_indirection(path)
            descriptor = open_regular_nofollow(path, os.O_RDONLY)
        except (OSError, RuntimeError):
            continue
        hasher = hashlib.sha256()
        try:
            if os.fstat(descriptor).st_nlink != 1:
                continue
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        digests[relative_path] = hasher.hexdigest()
    return digests


def _unexpected_runtime_assets(entry: ModelEntry, model_dir: Path) -> tuple[str, ...]:
    """Return files and links outside the reviewed local runtime contract."""

    try:
        reject_path_indirection(model_dir)
    except (OSError, RuntimeError):
        return (".",)
    if not model_dir.exists() or not model_dir.is_dir():
        return ()
    allowed_files = {*entry.runtime_files, MODEL_PROVENANCE_FILENAME}
    allowed_directories = {
        parent.as_posix()
        for relative in allowed_files
        for parent in Path(relative).parents
        if parent != Path(".")
    }
    unexpected: list[str] = []
    try:
        children = sorted(model_dir.rglob("*"), key=lambda child: child.as_posix())
    except OSError:
        return ("<unreadable-model-tree>",)
    for child in children:
        relative = child.relative_to(model_dir).as_posix()
        try:
            reject_path_indirection(child)
            metadata = child.stat(follow_symlinks=False)
            if child.is_file():
                if relative not in allowed_files or metadata.st_nlink != 1:
                    unexpected.append(relative)
            elif child.is_dir():
                if relative not in allowed_directories:
                    unexpected.append(relative)
            else:
                unexpected.append(relative)
        except (OSError, RuntimeError):
            unexpected.append(relative)
    return tuple(unexpected)


def _repository_digest_contract_problem(entry: ModelEntry) -> str | None:
    """Require repository-reviewed hashes and byte counts for every runtime file."""

    if not entry.pullable:
        return None
    runtime_files = set(entry.runtime_files)
    expected_files = set(entry.expected_digest_map)
    if runtime_files != expected_files:
        missing = ", ".join(sorted(runtime_files - expected_files)) or "none"
        unexpected = ", ".join(sorted(expected_files - runtime_files)) or "none"
        return (
            f"repository digest contract incomplete for {entry.role}:{entry.key}; "
            f"missing expected SHA-256 files: {missing}; "
            f"unexpected digest entries: {unexpected}. "
            "A human-reviewed manifest update is required before acquisition or verification."
        )
    expected_size_files = set(entry.expected_size_map)
    if runtime_files != expected_size_files or any(
        size <= 0 for size in entry.expected_size_map.values()
    ):
        missing = ", ".join(sorted(runtime_files - expected_size_files)) or "none"
        unexpected = ", ".join(sorted(expected_size_files - runtime_files)) or "none"
        return (
            f"repository size contract incomplete for {entry.role}:{entry.key}; "
            f"missing expected byte-count files: {missing}; "
            f"unexpected size entries: {unexpected}. "
            "A human-reviewed manifest update is required before acquisition or verification."
        )
    return None


def _mismatched_size_files(entry: ModelEntry, model_dir: Path) -> tuple[str, ...]:
    mismatches: list[str] = []
    for relative_path, expected_size in entry.expected_size_map.items():
        path = model_dir / relative_path
        try:
            if path.exists() and path.stat(follow_symlinks=False).st_size != expected_size:
                mismatches.append(relative_path)
        except OSError:
            continue
    return tuple(mismatches)


def _product_model_unavailability_reason(entry: ModelEntry) -> str:
    """Return the most concrete fail-closed product blocker available."""

    return (
        _repository_digest_contract_problem(entry)
        or entry.blocker
        or f"classification={entry.product_classification}"
    )


def _tool_version() -> str:
    try:
        return version("local-transcript-max")
    except PackageNotFoundError:
        return "source-tree"


def _quarantine_path(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f".{path.name}.quarantine-{timestamp}-{uuid4().hex[:8]}")


@contextmanager
def _model_pull_lock(cache_dir: Path, entry: ModelEntry) -> Iterator[None]:
    """Serialize acquisition of one model without putting a lock inside its cache."""

    lock_path = cache_dir / f".{entry.directory_name}.pull.lock"
    try:
        with HeldFileLock(lock_path):
            yield
    except LockUnavailableError as exc:
        raise ModelCacheError(
            f"Model acquisition is already active for {entry.role}:{entry.key}."
        ) from exc


def shareable_model_path(path: Path, cache_dir: Path) -> str:
    expanded_path = path.expanduser()
    expanded_cache = cache_dir.expanduser()
    try:
        relative = expanded_path.relative_to(expanded_cache)
    except ValueError:
        return "<local-model-path>"
    return f"<model-cache>:{relative.as_posix()}"
