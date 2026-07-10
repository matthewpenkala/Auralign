# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Explicit model acquisition and cache validation."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
from .redaction import redact_obj
from .utils import read_json, write_json

MODEL_PROVENANCE_FILENAME = ".ltm-model.json"


@dataclass(frozen=True)
class ModelStatus:
    entry: ModelEntry
    path: Path
    exists: bool
    files_complete: bool
    present: bool
    missing_files: tuple[str, ...]
    zero_size_files: tuple[str, ...]
    inaccessible_files: tuple[str, ...]
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
        displayed_path = (
            shareable_model_path(self.path, cache_dir) if shareable else str(self.path)
        )
        if not self.exists:
            details.append(f"directory missing at {displayed_path}")
        if self.missing_files:
            details.append(f"missing files: {', '.join(self.missing_files)}")
        if self.zero_size_files:
            details.append(f"empty files: {', '.join(self.zero_size_files)}")
        if self.inaccessible_files:
            details.append(f"inaccessible files: {', '.join(self.inaccessible_files)}")
        if self.exists and self.entry.pullable and not self.revision_verified:
            details.append(self.provenance_problem)
        if self.entry.gated and not self.token_present:
            details.append("HF_TOKEN or LTM_HF_TOKEN is required for gated model access")
        if self.entry.pullable:
            command_cache = "<model-cache>" if shareable else str(cache_dir)
            details.append(f"run: {self.entry.pull_command(command_cache)}")
        else:
            details.append(
                "provide an existing local model directory; runtime downloads are disabled"
            )
        return "; ".join(details)

    def to_audit_record(self, cache_dir: Path) -> dict[str, Any]:
        return {
            "role": self.entry.role,
            "key": self.entry.key,
            "model_id": self.entry.model_id,
            "aliases": list(self.entry.aliases),
            "directory_aliases": list(self.entry.directory_aliases),
            "revision": self.entry.revision,
            "revision_verified": self.revision_verified,
            "provenance_file": MODEL_PROVENANCE_FILENAME,
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
            "missing_files": list(self.missing_files),
            "suspicious_files": list(self.zero_size_files),
            "zero_size_files": list(self.zero_size_files),
            "inaccessible_files": list(self.inaccessible_files),
            "gated": self.entry.gated,
            "token_required": self.entry.gated,
            "token_present": self.token_present,
            "offline_usable": self.offline_usable,
            "classification": "optional" if self.entry.optional else "required",
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
    exists = path.exists()
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
        not entry.required_files or (not missing and not zero_size and not inaccessible)
    )
    if entry.any_required_files:
        files_complete = files_complete and _any_required_file_present(entry, path)
    revision_verified, provenance_problem = _provenance_status(entry, path)
    present = files_complete and (revision_verified if entry.pullable else True)
    return ModelStatus(
        entry=entry,
        path=path,
        exists=exists,
        files_complete=files_complete,
        present=present,
        missing_files=missing,
        zero_size_files=zero_size,
        inaccessible_files=inaccessible,
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
        if status.entry.pullable and (include_optional or not status.entry.optional)
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


def pull_model(entry: ModelEntry, cache_dir: Path, *, revision: str | None = None) -> Path:
    if not entry.pullable:
        raise ModelCacheError(
            f"{entry.role}:{entry.key} ({entry.model_id}) is audit-only and cannot be "
            "downloaded by ltm models pull. Provide an existing local model directory."
        )
    if not entry.revision or not entry.review_status.startswith("verified"):
        raise ModelCacheError(
            f"{entry.role}:{entry.key} is not approved for acquisition: "
            f"compliance_status={entry.review_status}."
        )
    if revision is not None and revision != entry.revision:
        raise ModelCacheError(
            f"Refusing unapproved revision override for {entry.role}:{entry.key}. "
            f"Approved revision: {entry.revision}."
        )
    current_status = status_for(entry, cache_dir)
    if current_status.present:
        return current_status.path
    if entry.gated and not token_present():
        raise ModelCacheError(
            f"{entry.model_id} is gated. Accept the Hugging Face terms and export "
            "HF_TOKEN or LTM_HF_TOKEN before running this pull."
        )
    cache_dir.expanduser().mkdir(parents=True, exist_ok=True)
    local_dir = model_local_path(entry, cache_dir)
    local_dir.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=entry.model_id,
            revision=entry.revision,
            local_dir=local_dir,
            local_dir_use_symlinks=False,
            token=hf_token(),
        )
    except (GatedRepoError, HfHubHTTPError, LocalEntryNotFoundError) as exc:
        raise ModelCacheError(friendly_hf_error(entry, exc, cache_dir)) from exc
    write_model_provenance(entry, local_dir)
    final_status = status_for(entry, cache_dir)
    if not final_status.present:
        raise ModelCacheError(
            f"Model cache is partial or corrupted for {entry.model_id}.\n"
            f"Expected: {final_status.path}\n"
            f"Problem: {final_status.problem_summary(cache_dir)}"
        )
    return local_dir


def import_mlx_model(
    source_dir: Path,
    cache_dir: Path,
    *,
    model: str = "mlx-large-v3-turbo",
    symlink: bool = False,
    overwrite: bool = False,
) -> ModelStatus:
    entry = resolve_model_entry("asr-mlx", model)
    if entry is None:
        raise ModelCacheError(f"Unknown MLX ASR model '{model}'.")
    source = source_dir.expanduser()
    source_status = status_for_path(entry, source)
    if not source_status.present:
        raise ModelCacheError(
            f"MLX model source is incomplete: {source}.\n"
            f"Problem: {source_status.problem_summary(cache_dir)}"
        )
    destination = model_local_path(entry, cache_dir)
    if destination.exists() or destination.is_symlink():
        if not overwrite:
            raise ModelCacheError(
                f"Destination already exists: {destination}. "
                "Re-run with --overwrite to replace it."
            )
        _remove_cache_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if symlink:
        destination.symlink_to(source.resolve(), target_is_directory=True)
    else:
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
        if not entry.revision or not entry.review_status.startswith("verified"):
            blocked.append(
                f"{entry.role}:{entry.key} ({entry.model_id}) is not compliance-approved "
                f"for acquisition: {entry.review_status}."
            )
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


def selected_entries(
    *,
    all_models: bool = False,
    include_optional: bool = False,
    asr: str | None = None,
    diarization: str | None = None,
    alignment: str | None = None,
    speaker_embedding: str | None = None,
) -> list[ModelEntry]:
    if all_models:
        return [
            entry
            for entry in MODEL_MANIFEST.values()
            if entry.pullable and (include_optional or not entry.optional)
        ]
    requested: list[tuple[str, str | None]] = [
        ("asr", asr),
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
        if not entry.pullable:
            raise ModelCacheError(
                f"{entry.role}:{entry.key} ({entry.model_id}) is audit-only and cannot be "
                "downloaded by ltm models pull."
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
    root = cache_dir.expanduser()
    paths = [model_local_path(entry, cache_dir)]
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
    if not path.exists():
        return False
    for required in entry.required_files:
        if not required:
            continue
        required_path = path / required
        try:
            if (
                not required_path.exists()
                or required_path.stat().st_size == 0
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
            if required_path.exists() and required_path.stat().st_size > 0 and _can_read(
                required_path
            ):
                return True
        except OSError:
            return False
    return False


def model_provenance_payload(entry: ModelEntry) -> dict[str, str]:
    if not entry.revision:
        raise ModelCacheError(f"Cannot record an unpinned model revision for {entry.key}.")
    return {
        "schema_version": "1.0",
        "key": entry.key,
        "role": entry.role,
        "model_id": entry.model_id,
        "revision": entry.revision,
    }


def write_model_provenance(entry: ModelEntry, model_dir: Path) -> Path:
    if not entry.pullable:
        raise ModelCacheError(
            f"Cannot attest an Auralign pull for manual-only model {entry.role}:{entry.key}."
        )
    output = model_dir / MODEL_PROVENANCE_FILENAME
    write_json(output, model_provenance_payload(entry))
    return output


def _provenance_status(entry: ModelEntry, path: Path) -> tuple[bool, str]:
    if not entry.pullable:
        return False, "revision linkage not asserted for manual-only local assets"
    marker = path / MODEL_PROVENANCE_FILENAME
    if not marker.exists():
        return False, f"missing immutable-revision linkage marker: {MODEL_PROVENANCE_FILENAME}"
    if not marker.is_file() or marker.is_symlink():
        return False, f"unsafe immutable-revision linkage marker: {MODEL_PROVENANCE_FILENAME}"
    try:
        payload = read_json(marker)
    except (OSError, ValueError):
        return False, f"unreadable immutable-revision linkage marker: {MODEL_PROVENANCE_FILENAME}"
    if payload != model_provenance_payload(entry):
        return False, "immutable-revision linkage marker does not match the model manifest"
    return True, "immutable revision linkage verified"


def shareable_model_path(path: Path, cache_dir: Path) -> str:
    expanded_path = path.expanduser()
    expanded_cache = cache_dir.expanduser()
    try:
        relative = expanded_path.relative_to(expanded_cache)
    except ValueError:
        return "<local-model-path>"
    return f"<model-cache>:{relative.as_posix()}"
