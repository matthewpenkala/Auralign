# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic, offline assembly and verification for the bootstrap model component.

The component deliberately does not preserve a Hugging Face cache layout or copy
``.ltm-model.json`` acquisition markers.  Source caches are first accepted by the
existing model-cache validator, then only the exact repository-authorized runtime
files are copied into a separately versioned, immutable bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as distribution_version
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast

from .config import MODEL_MANIFEST, ModelEntry
from .models import status_for
from .runtime_assets import (
    EMPTY_FILE_SHA256,
    PUNKT_FILES,
    PUNKT_GENERATOR,
    PUNKT_MARKER,
    PUNKT_TRAINING_DATA,
)

MODEL_BUNDLE_IDENTIFIER: Final = "com.auralign.studio.models.bootstrap.v1"
MODEL_BUNDLE_VERSION: Final = "1.0.0"
MODEL_BUNDLE_SCHEMA_VERSION: Final = "1.0"
MODEL_BUNDLE_MANIFEST_FILENAME: Final = "model-bundle-manifest.json"
MODEL_BUNDLE_VERIFICATION_FILENAME: Final = "model-bundle-verification.json"
MODEL_BUNDLE_PROVENANCE_FILENAME: Final = "model-bundle-provenance.json"
MODEL_BUNDLE_COMPLETION_FILENAME: Final = "model-bundle-complete.json"
MODEL_BUNDLE_NOTICES_FILENAME: Final = "THIRD-PARTY-NOTICES.txt"
BOOTSTRAP_MODEL_KEYS: Final = ("community-1", "en", "ecapa")
# Compatibility export for the builder while the successor package vocabulary
# is reconciled. It now identifies the exact compact bootstrap closure.
STRICT_MODEL_KEYS: Final = BOOTSTRAP_MODEL_KEYS
MODEL_BUNDLE_NLTK_VERSION: Final = "3.9.4"

_BOOTSTRAP_MODEL_ROLES: Final = {
    "community-1": "diarization",
    "en": "alignment",
    "ecapa": "speaker-embedding",
}
_LICENSE_INPUTS: Final = (
    ("Apache-2.0", "Apache-2.0.txt"),
    ("CC-BY-4.0", "CC-BY-4.0.txt"),
)
MODEL_BUNDLE_LICENSE_FILENAMES: Final = tuple(
    filename for _, filename in _LICENSE_INPUTS
)
_LICENSE_SHA256: Final = {
    "Apache-2.0": "074e6e32c86a4c0ef8b3ed25b721ca23aca83df277cd88106ef7177c354615ff",
    "CC-BY-4.0": "d557539df68e771cc1eedcc91d13f70fca930e508d11eedcafa4b15db49e3744",
    "MIT": "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5",
}
_FILE_MODE: Final = 0o444
_DIRECTORY_MODE: Final = 0o555
_SOURCE_UNSAFE_MODE_BITS: Final = 0o022 | stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
_MAX_TEXT_INPUT_BYTES: Final = 4 * 1024 * 1024
_MAX_JSON_BYTES: Final = 4 * 1024 * 1024
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_PUNKT_SCHEMA_VERSION: Final = "ltm-generated-punkt.v1"
_PUNKT_RUNTIME_ASSET_KEY: Final = "nltk-punkt-tab-english-corpus-free"
_PUNKT_SOURCE_ROOT: Final = PurePosixPath("runtime-assets/nltk_data")
_PUNKT_BUNDLE_ROOT: Final = PurePosixPath("models") / _PUNKT_SOURCE_ROOT
_PUNKT_LANGUAGE_ROOT: Final = _PUNKT_BUNDLE_ROOT / "tokenizers/punkt_tab/english"
_PUNKT_MARKER_PATH: Final = _PUNKT_BUNDLE_ROOT / PUNKT_MARKER
_PRIVATE_UNIX_HOME_PATTERN: Final = "/" + "Users/" + "|" + "/" + "home/"
_PRIVATE_PATH_PATTERN: Final = re.compile(
    r"(?:^|[\\s\"'`(])(?:"
    + _PRIVATE_UNIX_HOME_PATTERN
    + r"|/private/(?:tmp|var/folders)/|/tmp/|[A-Za-z]:\\\\|file://|~/)"
)
_SECRET_MARKERS: Final = (
    "authorization: bearer",
    "api_key=",
    "apikey=",
    "hf_token",
    "ltm_hf_token",
    "password=",
    "secret=",
    "token=",
)
_VERIFICATION_CHECKS: Final = (
    "canonical-json",
    "exact-model-authority",
    "exact-payload-allowlist",
    "exact-sha256-and-size",
    "immutable-file-modes",
    "single-owner-tree",
    "single-filesystem-tree",
    "no-symbolic-links",
    "no-hardlinks",
    "no-private-acquisition-metadata",
    "locked-nltk-punkt-contract",
)
_MODEL_POLICY_FIELDS: Final = (
    "product_classification",
    "blocker",
    "backend_compatibility",
    "acquisition_status",
    "runtime_status",
    "redistribution_status",
    "review_status",
    "modification_status",
    "package_bundle_id",
    "package_bundle_version",
    "package_relative_path",
    "delivery",
)


class ModelBundleError(RuntimeError):
    """Raised when model-component assembly or verification fails closed."""


def model_bundle_notices_bytes() -> bytes:
    """Return the deterministic public notice for the exact bootstrap component."""

    entries = _validated_entries(STRICT_MODEL_KEYS, MODEL_MANIFEST)
    lines = [
        "Auralign Offline Bootstrap Model Component",
        f"Bundle: {MODEL_BUNDLE_IDENTIFIER} {MODEL_BUNDLE_VERSION}",
        "",
        (
            "This component redistributes only the exact pinned, unmodified model "
            "files listed below. Each model retains its upstream license and "
            "attribution requirements; complete applicable license texts are "
            "included in the component."
        ),
        "",
    ]
    lines.extend(
        (
            f"- {entry.key}: {entry.model_id} at revision {entry.revision}; "
            f"license {entry.license_expression}; {entry.modification_status}."
        )
        for entry in entries
    )
    lines.extend(
        [
            (
                f"- {_PUNKT_RUNTIME_ASSET_KEY}: corpus-free English Punkt data "
                f"generated by locked NLTK {MODEL_BUNDLE_NLTK_VERSION}; "
                "training data none; license Apache-2.0."
            ),
            "",
            (
                "The component contains no ASR weights, executable code, "
                "credentials, acquisition metadata, private paths, or mutable cache "
                "state."
            ),
        ]
    )
    return ("\n".join(lines) + "\n").encode()


def assemble_model_bundle(
    source_cache: Path,
    destination: Path,
    *,
    license_directory: Path,
    notices_path: Path,
) -> dict[str, Any]:
    """Assemble and verify an absent bootstrap bundle without network access.

    The returned record contains only deterministic, shareable artifact identity.
    The destination must not exist.  A private sibling staging directory is
    removed on failure and published only after the completion marker verifies.
    """

    entries = _validated_entries(STRICT_MODEL_KEYS, MODEL_MANIFEST)
    destination = Path(destination)
    if os.path.lexists(destination):
        raise ModelBundleError("model bundle destination must be absent")
    parent = destination.parent
    parent_metadata = _lstat(parent, "bundle destination parent")
    if not stat.S_ISDIR(parent_metadata.st_mode):
        raise ModelBundleError("model bundle destination parent must be a directory")
    if stat.S_ISLNK(parent_metadata.st_mode):
        raise ModelBundleError("model bundle destination parent must not be a symbolic link")

    sources: dict[str, Path] = {}
    for entry in entries:
        source_status = status_for(entry, Path(source_cache))
        if not (
            source_status.offline_usable
            and source_status.files_complete
            and source_status.revision_verified
            and not source_status.unexpected_files
        ):
            raise ModelBundleError(
                f"source model failed immutable cache validation: {entry.role}:{entry.key}"
            )
        _validate_source_model_root(source_status.path, entry)
        sources[entry.key] = source_status.path

    punkt_sources, punkt_contract, punkt_source_owner = _validated_punkt_sources(Path(source_cache))
    license_inputs = _read_license_inputs(Path(license_directory))
    notices_bytes = _read_public_input(Path(notices_path), "third-party notices")
    _validate_notices(notices_bytes, entries)

    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.model-bundle-", dir=str(parent)))
    published = False
    try:
        models_root = staging / "models"
        licenses_root = staging / "licenses"
        models_root.mkdir(mode=0o700)
        licenses_root.mkdir(mode=0o700)

        model_records: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            output_root = models_root / entry.directory_name
            output_root.mkdir(mode=0o700)
            records: list[dict[str, Any]] = []
            for relative_path in entry.required_files:
                source = sources[entry.key] / relative_path
                output = output_root.joinpath(*PurePosixPath(relative_path).parts)
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                records.append(
                    _copy_model_file(
                        source,
                        output,
                        logical_path=f"models/{entry.directory_name}/{relative_path}",
                        expected_sha256=entry.expected_digest_map[relative_path],
                        expected_size=entry.expected_size_map[relative_path],
                        expected_owner=_owner_identity(
                            sources[entry.key],
                            _lstat(sources[entry.key], f"source model {entry.key}"),
                        ),
                    )
                )
            model_records[entry.key] = records

        punkt_records: list[dict[str, Any]] = []
        for filename in PUNKT_FILES:
            output = staging / _PUNKT_LANGUAGE_ROOT / filename
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            punkt_records.append(
                _copy_model_file(
                    punkt_sources[filename],
                    output,
                    logical_path=(_PUNKT_LANGUAGE_ROOT / filename).as_posix(),
                    expected_sha256=EMPTY_FILE_SHA256,
                    expected_size=0,
                    expected_owner=punkt_source_owner,
                )
            )
        punkt_marker_record, _ = _write_canonical_json(
            staging / _PUNKT_MARKER_PATH,
            punkt_contract,
            logical_path=_PUNKT_MARKER_PATH.as_posix(),
        )
        runtime_asset_record = _punkt_runtime_asset_record(
            punkt_contract,
            punkt_marker_record,
            punkt_records,
        )

        license_records: list[dict[str, Any]] = []
        for expression, filename in _LICENSE_INPUTS:
            output = licenses_root / filename
            record = _write_new_bytes(
                output,
                license_inputs[filename],
                logical_path=f"licenses/{filename}",
            )
            license_records.append({"license_expression": expression, "file": record})

        notice_record = _write_new_bytes(
            staging / MODEL_BUNDLE_NOTICES_FILENAME,
            notices_bytes,
            logical_path=MODEL_BUNDLE_NOTICES_FILENAME,
        )
        provenance_payload = _provenance_payload(entries)
        provenance_record, _ = _write_canonical_json(
            staging / MODEL_BUNDLE_PROVENANCE_FILENAME,
            provenance_payload,
            logical_path=MODEL_BUNDLE_PROVENANCE_FILENAME,
        )

        manifest_payload = _manifest_payload(
            entries,
            model_records,
            runtime_asset_record,
            license_records,
            notice_record,
            provenance_record,
        )
        _, manifest_bytes = _write_canonical_json(
            staging / MODEL_BUNDLE_MANIFEST_FILENAME,
            manifest_payload,
            logical_path=MODEL_BUNDLE_MANIFEST_FILENAME,
        )
        manifest_sha256 = _sha256_bytes(manifest_bytes)
        verification_payload = _verification_payload(manifest_payload, manifest_sha256)
        _, verification_bytes = _write_canonical_json(
            staging / MODEL_BUNDLE_VERIFICATION_FILENAME,
            verification_payload,
            logical_path=MODEL_BUNDLE_VERIFICATION_FILENAME,
        )
        completion_payload = _completion_payload(
            manifest_sha256,
            _sha256_bytes(verification_bytes),
        )
        _write_canonical_json(
            staging / MODEL_BUNDLE_COMPLETION_FILENAME,
            completion_payload,
            logical_path=MODEL_BUNDLE_COMPLETION_FILENAME,
        )

        _make_bundle_tree_read_only(staging)
        verify_model_bundle(staging)
        if os.path.lexists(destination):
            raise ModelBundleError("model bundle destination appeared during assembly")
        os.rename(staging, destination)
        published = True
        _fsync_directory(parent)
        return verify_model_bundle(destination)
    except ModelBundleError:
        raise
    except (OSError, ValueError) as exc:
        raise ModelBundleError("model bundle assembly failed closed") from exc
    finally:
        if not published and os.path.lexists(staging):
            _remove_staging_tree(staging)


def verify_model_bundle(bundle_root: Path) -> dict[str, Any]:
    """Verify an existing staged or installed bundle without modifying it."""

    entries = _validated_entries(STRICT_MODEL_KEYS, MODEL_MANIFEST)
    root = Path(bundle_root)
    expected_directories, expected_files = _expected_layout(entries)
    actual_directories, actual_files = _scan_bundle_tree(root)
    if actual_directories != expected_directories or set(actual_files) != expected_files:
        raise ModelBundleError("model bundle contains missing or unexpected paths")

    model_records: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        records: list[dict[str, Any]] = []
        for relative_path in entry.required_files:
            logical_path = f"models/{entry.directory_name}/{relative_path}"
            record = _hash_existing_file(actual_files[logical_path], logical_path)
            if record["sha256"] != entry.expected_digest_map[relative_path]:
                raise ModelBundleError(
                    f"model bundle digest does not match authority: {entry.key}:{relative_path}"
                )
            if record["size"] != entry.expected_size_map[relative_path]:
                raise ModelBundleError(
                    f"model bundle size does not match authority: {entry.key}:{relative_path}"
                )
            records.append(record)
        model_records[entry.key] = records

    punkt_contract = _punkt_contract_payload()
    punkt_marker_payload, punkt_marker_bytes = _read_canonical_json(
        actual_files[_PUNKT_MARKER_PATH.as_posix()],
        _PUNKT_MARKER_PATH.as_posix(),
    )
    if punkt_marker_payload != punkt_contract:
        raise ModelBundleError("packaged Punkt marker does not match the locked NLTK contract")
    punkt_marker_record = _file_record(
        _PUNKT_MARKER_PATH.as_posix(),
        punkt_marker_bytes,
    )
    punkt_records: list[dict[str, Any]] = []
    for filename in PUNKT_FILES:
        logical_path = (_PUNKT_LANGUAGE_ROOT / filename).as_posix()
        record = _hash_existing_file(actual_files[logical_path], logical_path)
        if record["sha256"] != EMPTY_FILE_SHA256:
            raise ModelBundleError(
                f"packaged corpus-free Punkt asset failed integrity verification: {filename}"
            )
        punkt_records.append(record)
    runtime_asset_record = _punkt_runtime_asset_record(
        punkt_contract,
        punkt_marker_record,
        punkt_records,
    )

    license_records: list[dict[str, Any]] = []
    for expression, filename in _LICENSE_INPUTS:
        logical_path = f"licenses/{filename}"
        content = _read_existing_bytes(actual_files[logical_path], logical_path)
        _validate_public_text(content, logical_path)
        _validate_license_text(expression, content)
        license_records.append(
            {
                "license_expression": expression,
                "file": _file_record(logical_path, content),
            }
        )

    notices_bytes = _read_existing_bytes(
        actual_files[MODEL_BUNDLE_NOTICES_FILENAME],
        MODEL_BUNDLE_NOTICES_FILENAME,
    )
    _validate_public_text(notices_bytes, MODEL_BUNDLE_NOTICES_FILENAME)
    _validate_notices(notices_bytes, entries)
    notice_record = _file_record(MODEL_BUNDLE_NOTICES_FILENAME, notices_bytes)

    provenance_payload, provenance_bytes = _read_canonical_json(
        actual_files[MODEL_BUNDLE_PROVENANCE_FILENAME],
        MODEL_BUNDLE_PROVENANCE_FILENAME,
    )
    if provenance_payload != _provenance_payload(entries):
        raise ModelBundleError("model bundle provenance authority is inconsistent")
    provenance_record = _file_record(MODEL_BUNDLE_PROVENANCE_FILENAME, provenance_bytes)

    expected_manifest = _manifest_payload(
        entries,
        model_records,
        runtime_asset_record,
        license_records,
        notice_record,
        provenance_record,
    )
    manifest_payload, manifest_bytes = _read_canonical_json(
        actual_files[MODEL_BUNDLE_MANIFEST_FILENAME],
        MODEL_BUNDLE_MANIFEST_FILENAME,
    )
    if manifest_payload != expected_manifest:
        raise ModelBundleError("model bundle manifest is inconsistent")
    manifest_sha256 = _sha256_bytes(manifest_bytes)

    expected_verification = _verification_payload(expected_manifest, manifest_sha256)
    verification_payload, verification_bytes = _read_canonical_json(
        actual_files[MODEL_BUNDLE_VERIFICATION_FILENAME],
        MODEL_BUNDLE_VERIFICATION_FILENAME,
    )
    if verification_payload != expected_verification:
        raise ModelBundleError("model bundle verification record is inconsistent")
    verification_sha256 = _sha256_bytes(verification_bytes)

    completion_payload, _ = _read_canonical_json(
        actual_files[MODEL_BUNDLE_COMPLETION_FILENAME],
        MODEL_BUNDLE_COMPLETION_FILENAME,
    )
    if completion_payload != _completion_payload(manifest_sha256, verification_sha256):
        raise ModelBundleError("model bundle completion marker is inconsistent")

    payload = cast(dict[str, Any], expected_manifest["payload"])
    return {
        "schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "bundle_id": MODEL_BUNDLE_IDENTIFIER,
        "bundle_version": MODEL_BUNDLE_VERSION,
        "state": "verified",
        "model_keys": list(STRICT_MODEL_KEYS),
        "runtime_asset_keys": [_PUNKT_RUNTIME_ASSET_KEY],
        "manifest_sha256": manifest_sha256,
        "verification_record_sha256": verification_sha256,
        "payload_file_count": payload["file_count"],
        "payload_size": payload["size"],
    }


def _validated_entries(
    model_keys: Sequence[str],
    model_manifest: Mapping[str, ModelEntry],
) -> tuple[ModelEntry, ...]:
    if isinstance(model_keys, (str, bytes)):
        raise ModelBundleError("model keys must be a sequence")
    supplied = tuple(model_keys)
    if len(supplied) != len(set(supplied)) or set(supplied) != set(STRICT_MODEL_KEYS):
        raise ModelBundleError("bootstrap model bundle requires the exact three model keys")

    entries: list[ModelEntry] = []
    directory_names: set[str] = set()
    for key in STRICT_MODEL_KEYS:
        entry = model_manifest.get(key)
        if entry is None or entry.key != key or entry.role != _BOOTSTRAP_MODEL_ROLES[key]:
            raise ModelBundleError(f"model authority is missing or inconsistent: {key}")
        if entry.revision is None or _REVISION_PATTERN.fullmatch(entry.revision) is None:
            raise ModelBundleError(f"model revision is not an immutable commit: {key}")
        _validate_relative_path(entry.directory_name, f"model directory for {key}")
        if entry.directory_name in directory_names:
            raise ModelBundleError("model directory authority is ambiguous")
        directory_names.add(entry.directory_name)
        if entry.any_required_files or tuple(entry.runtime_files) != tuple(entry.required_files):
            raise ModelBundleError(f"model runtime allowlist is not exact: {key}")
        if not entry.required_files or len(entry.required_files) != len(set(entry.required_files)):
            raise ModelBundleError(f"model required-file contract is invalid: {key}")
        for relative_path in entry.required_files:
            _validate_relative_path(relative_path, f"model required file for {key}")
        expected_digests = entry.expected_digest_map
        if len(entry.expected_sha256) != len(expected_digests) or set(expected_digests) != set(
            entry.required_files
        ):
            raise ModelBundleError(f"model digest authority is incomplete: {key}")
        if any(_SHA256_PATTERN.fullmatch(value) is None for value in expected_digests.values()):
            raise ModelBundleError(f"model digest authority is malformed: {key}")
        expected_sizes = entry.expected_size_map
        if len(entry.expected_size) != len(expected_sizes) or set(expected_sizes) != set(
            entry.required_files
        ):
            raise ModelBundleError(f"model size authority is incomplete: {key}")
        if any(size <= 0 for size in expected_sizes.values()):
            raise ModelBundleError(f"model size authority is malformed: {key}")
        if entry.license_expression == "NOASSERTION":
            raise ModelBundleError(f"model license authority is incomplete: {key}")
        if not entry.redistribution_status.startswith("permitted_"):
            raise ModelBundleError(f"model redistribution authority is not allowlisted: {key}")
        if not entry.review_status.startswith("verified"):
            raise ModelBundleError(f"model review authority is not verified: {key}")
        if (
            not entry.production_selectable
            or entry.product_classification != "production"
            or entry.blocker is not None
        ):
            raise ModelBundleError(f"model is not production-selectable: {key}")
        if entry.package_bundle_id != MODEL_BUNDLE_IDENTIFIER:
            raise ModelBundleError(f"model package bundle authority is inconsistent: {key}")
        if entry.package_bundle_version != MODEL_BUNDLE_VERSION:
            raise ModelBundleError(f"model package version authority is inconsistent: {key}")
        if entry.delivery != "system-component":
            raise ModelBundleError(f"model delivery authority is inconsistent: {key}")
        expected_package_path = f"models/{entry.directory_name}"
        if entry.package_relative_path != expected_package_path:
            raise ModelBundleError(f"model package destination is inconsistent: {key}")
        _validate_relative_path(entry.package_relative_path, f"model package destination for {key}")
        _validate_public_metadata(entry)
        entries.append(entry)
    return tuple(entries)


def _validate_public_metadata(entry: ModelEntry) -> None:
    for label, value in (
        ("key", entry.key),
        ("role", entry.role),
        ("model id", entry.model_id),
        ("license", entry.license_expression),
        ("attribution", entry.attribution),
        ("runtime status", entry.runtime_status),
        ("redistribution status", entry.redistribution_status),
        ("review status", entry.review_status),
        ("product classification", entry.product_classification),
        ("backend compatibility", entry.backend_compatibility),
        ("modification status", entry.modification_status),
        ("package bundle id", cast(str, entry.package_bundle_id)),
        ("package bundle version", cast(str, entry.package_bundle_version)),
        ("package relative path", cast(str, entry.package_relative_path)),
        ("delivery", entry.delivery),
    ):
        _validate_public_string(value, label)
    for label, value in (("source URL", entry.source_url), ("terms URL", entry.terms_url)):
        _validate_public_string(value, label)
        if not value.startswith("https://") or "@" in value.partition("://")[2].partition("/")[0]:
            raise ModelBundleError(f"model {label} must be a public HTTPS URL")
        if "?" in value or "#" in value:
            raise ModelBundleError(f"model {label} must not contain query or fragment data")


def _validate_public_string(value: str, label: str) -> None:
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ModelBundleError(f"model {label} is empty or non-canonical")
    lowered = value.casefold()
    if _PRIVATE_PATH_PATTERN.search(value) or any(marker in lowered for marker in _SECRET_MARKERS):
        raise ModelBundleError(f"model {label} contains private metadata")


def _validate_relative_path(value: str, label: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise ModelBundleError(f"{label} is unsafe")
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} or part.startswith(".") for part in candidate.parts)
    ):
        raise ModelBundleError(f"{label} is unsafe")
    return candidate


def _validate_source_model_root(model_root: Path, entry: ModelEntry) -> None:
    root_metadata = _lstat(model_root, f"source model {entry.key}")
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise ModelBundleError(f"source model root is unsafe: {entry.key}")
    if stat.S_IMODE(root_metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS:
        raise ModelBundleError(f"source model root has unsafe mode: {entry.key}")
    owner = _owner_identity(model_root, root_metadata)
    for relative_path in entry.required_files:
        current = model_root
        for part in PurePosixPath(relative_path).parts[:-1]:
            current = current / part
            metadata = _lstat(current, f"source model directory for {entry.key}")
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ModelBundleError(f"source model directory is unsafe: {entry.key}")
            if _owner_identity(current, metadata) != owner:
                raise ModelBundleError(f"source model ownership is inconsistent: {entry.key}")
            if stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS:
                raise ModelBundleError(f"source model directory has unsafe mode: {entry.key}")
        file_path = model_root.joinpath(*PurePosixPath(relative_path).parts)
        metadata = _lstat(file_path, f"source model file for {entry.key}")
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise ModelBundleError(f"source model file is linked or irregular: {entry.key}")
        if _owner_identity(file_path, metadata) != owner:
            raise ModelBundleError(f"source model ownership is inconsistent: {entry.key}")
        if stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS:
            raise ModelBundleError(f"source model file has unsafe mode: {entry.key}")


def _punkt_contract_payload() -> dict[str, Any]:
    try:
        installed_nltk_version = distribution_version("nltk")
    except PackageNotFoundError as exc:
        raise ModelBundleError("locked NLTK is unavailable for model-bundle verification") from exc
    if installed_nltk_version != MODEL_BUNDLE_NLTK_VERSION:
        raise ModelBundleError(
            "installed NLTK version does not match the locked model-bundle authority"
        )
    return {
        "schema_version": _PUNKT_SCHEMA_VERSION,
        "generator": PUNKT_GENERATOR,
        "nltk_version": MODEL_BUNDLE_NLTK_VERSION,
        "training_data": PUNKT_TRAINING_DATA,
        "files": {filename: EMPTY_FILE_SHA256 for filename in PUNKT_FILES},
    }


def _validated_punkt_sources(
    source_cache: Path,
) -> tuple[dict[str, Path], dict[str, Any], tuple[int, int]]:
    """Validate the exact corpus-free Punkt source tree without trusting cache metadata."""

    source_cache = Path(source_cache)
    source_root = source_cache.joinpath(*_PUNKT_SOURCE_ROOT.parts)
    cache_metadata = _lstat(source_cache, "source model cache")
    if not stat.S_ISDIR(cache_metadata.st_mode) or stat.S_ISLNK(cache_metadata.st_mode):
        raise ModelBundleError("source model cache is unsafe")
    expected_owner = _owner_identity(source_cache, cache_metadata)
    expected_device = cache_metadata.st_dev
    current = source_cache
    for part in _PUNKT_SOURCE_ROOT.parts:
        current = current / part
        metadata = _lstat(current, "source Punkt directory")
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_dev != expected_device
            or _owner_identity(current, metadata) != expected_owner
            or stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
        ):
            raise ModelBundleError("source Punkt directory is unsafe")

    expected_directories = {
        ".",
        "tokenizers",
        "tokenizers/punkt_tab",
        "tokenizers/punkt_tab/english",
    }
    expected_files = {
        PUNKT_MARKER,
        *(
            (PurePosixPath("tokenizers/punkt_tab/english") / filename).as_posix()
            for filename in PUNKT_FILES
        ),
    }
    actual_directories = {"."}
    actual_files: dict[str, Path] = {}
    pending: list[tuple[Path, PurePosixPath]] = [(source_root, PurePosixPath("."))]
    while pending:
        directory, logical_directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise ModelBundleError("source Punkt directory is unreadable") from exc
        for child in children:
            metadata = _lstat(child, "source Punkt entry")
            relative = (
                PurePosixPath(child.name)
                if logical_directory == PurePosixPath(".")
                else logical_directory / child.name
            )
            logical_path = relative.as_posix()
            if logical_path != PUNKT_MARKER:
                _validate_relative_path(logical_path, "source Punkt path")
            if (
                metadata.st_dev != expected_device
                or _owner_identity(child, metadata) != expected_owner
                or stat.S_ISLNK(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
            ):
                raise ModelBundleError("source Punkt tree is unsafe")
            if stat.S_ISDIR(metadata.st_mode):
                actual_directories.add(logical_path)
                pending.append((child, relative))
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise ModelBundleError("source Punkt tree contains a hardlinked file")
                actual_files[logical_path] = child
            else:
                raise ModelBundleError("source Punkt tree contains a special filesystem object")
    if actual_directories != expected_directories or set(actual_files) != expected_files:
        raise ModelBundleError("source Punkt tree contains missing or unexpected paths")

    marker_bytes = _read_regular_file(
        actual_files[PUNKT_MARKER],
        "source Punkt marker",
        maximum_size=_MAX_JSON_BYTES,
    )
    try:
        marker_payload = json.loads(
            marker_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ModelBundleError("source Punkt marker is invalid") from exc
    expected_contract = _punkt_contract_payload()
    if marker_payload != expected_contract:
        raise ModelBundleError("source Punkt marker does not match the locked NLTK contract")

    sources: dict[str, Path] = {}
    for filename in PUNKT_FILES:
        logical_path = (PurePosixPath("tokenizers/punkt_tab/english") / filename).as_posix()
        source = actual_files[logical_path]
        content = _read_regular_file(
            source,
            f"source Punkt asset {filename}",
            maximum_size=1024,
        )
        if _sha256_bytes(content) != EMPTY_FILE_SHA256:
            raise ModelBundleError(
                f"source corpus-free Punkt asset failed integrity verification: {filename}"
            )
        sources[filename] = source
    return sources, expected_contract, expected_owner


def _punkt_runtime_asset_record(
    contract: Mapping[str, Any],
    marker_record: dict[str, Any],
    file_records: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "key": _PUNKT_RUNTIME_ASSET_KEY,
        "runtime_cache_relative_root": _PUNKT_SOURCE_ROOT.as_posix(),
        "package_relative_root": _PUNKT_BUNDLE_ROOT.as_posix(),
        "schema_version": contract["schema_version"],
        "generator": contract["generator"],
        "nltk_version": contract["nltk_version"],
        "training_data": contract["training_data"],
        "marker": marker_record,
        "files": sorted(file_records, key=lambda record: cast(str, record["path"])),
    }


def _read_license_inputs(license_directory: Path) -> dict[str, bytes]:
    metadata = _lstat(license_directory, "license input directory")
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ModelBundleError("license input directory is unsafe")
    expected_names = {filename for _, filename in _LICENSE_INPUTS}
    try:
        actual_names = {path.name for path in license_directory.iterdir()}
    except OSError as exc:
        raise ModelBundleError("license input directory is unreadable") from exc
    if actual_names != expected_names:
        raise ModelBundleError("license input directory must contain the exact license allowlist")

    inputs: dict[str, bytes] = {}
    for expression, filename in _LICENSE_INPUTS:
        content = _read_public_input(license_directory / filename, f"license {expression}")
        _validate_license_text(expression, content)
        inputs[filename] = content
    return inputs


def _validate_license_text(expression: str, content: bytes) -> None:
    expected_sha256 = _LICENSE_SHA256.get(expression)
    if (
        expected_sha256 is None
        or hashlib.sha256(content).hexdigest() != expected_sha256
    ):
        raise ModelBundleError(
            f"license input does not match the exact reviewed text: {expression}"
        )


def _read_public_input(path: Path, label: str) -> bytes:
    metadata = _lstat(path, label)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise ModelBundleError(f"{label} must be one unlinked regular file")
    if stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS:
        raise ModelBundleError(f"{label} has an unsafe source mode")
    data = _read_regular_file(path, label, maximum_size=_MAX_TEXT_INPUT_BYTES)
    _validate_public_text(data, label)
    return data


def _validate_public_text(content: bytes, label: str) -> None:
    if not content:
        raise ModelBundleError(f"{label} must not be empty")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelBundleError(f"{label} must be UTF-8") from exc
    if "\x00" in text:
        raise ModelBundleError(f"{label} contains binary data")
    lowered = text.casefold()
    if (
        _PRIVATE_PATH_PATTERN.search(text)
        or any(marker in lowered for marker in _SECRET_MARKERS)
        or ".ltm-model.json" in lowered
        or '"acquired_at"' in lowered
        or '"acquisition_tool_version"' in lowered
    ):
        raise ModelBundleError(f"{label} contains private acquisition metadata")


def _validate_notices(content: bytes, entries: Sequence[ModelEntry]) -> None:
    del entries
    if content != model_bundle_notices_bytes():
        raise ModelBundleError(
            "third-party notices do not match the exact model authority"
        )


def _copy_model_file(
    source: Path,
    destination: Path,
    *,
    logical_path: str,
    expected_sha256: str,
    expected_size: int,
    expected_owner: tuple[int, int],
) -> dict[str, Any]:
    source_descriptor = _open_readonly_nofollow(source, logical_path)
    destination_descriptor = -1
    digest = hashlib.sha256()
    size = 0
    try:
        source_metadata = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(source_metadata.st_mode)
            or source_metadata.st_nlink != 1
            or source_metadata.st_size != expected_size
            or _owner_identity(source, source_metadata) != expected_owner
            or stat.S_IMODE(source_metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
        ):
            raise ModelBundleError(f"source model file changed or became unsafe: {logical_path}")
        destination_descriptor = _open_new_regular(destination)
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            _write_all(destination_descriptor, chunk)
        final_source_metadata = os.fstat(source_descriptor)
        if (
            final_source_metadata.st_dev,
            final_source_metadata.st_ino,
            final_source_metadata.st_size,
        ) != (
            source_metadata.st_dev,
            source_metadata.st_ino,
            source_metadata.st_size,
        ):
            raise ModelBundleError(f"source model file changed during copy: {logical_path}")
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ModelBundleError(f"source model digest changed during copy: {logical_path}")
        if size != expected_size:
            raise ModelBundleError(f"source model size changed during copy: {logical_path}")
        os.fchmod(destination_descriptor, _FILE_MODE)
        os.fsync(destination_descriptor)
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
    return _record(logical_path, size, digest.hexdigest())


def _write_new_bytes(destination: Path, content: bytes, *, logical_path: str) -> dict[str, Any]:
    descriptor = _open_new_regular(destination)
    try:
        _write_all(descriptor, content)
        os.fchmod(descriptor, _FILE_MODE)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return _file_record(logical_path, content)


def _write_canonical_json(
    destination: Path,
    payload: Mapping[str, Any],
    *,
    logical_path: str,
) -> tuple[dict[str, Any], bytes]:
    encoded = _canonical_json_bytes(payload)
    return _write_new_bytes(destination, encoded, logical_path=logical_path), encoded


def _provenance_payload(entries: Sequence[ModelEntry]) -> dict[str, Any]:
    punkt_contract = _punkt_contract_payload()
    return {
        "schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "record_type": "auralign-model-bundle-provenance",
        "bundle_id": MODEL_BUNDLE_IDENTIFIER,
        "bundle_version": MODEL_BUNDLE_VERSION,
        "authority": "local_transcript_max.config.MODEL_MANIFEST",
        "models": [
            {
                "key": entry.key,
                "role": entry.role,
                "model_id": entry.model_id,
                "aliases": list(entry.aliases),
                "directory_name": entry.directory_name,
                "directory_aliases": list(entry.directory_aliases),
                "local_cache_relative_path": entry.directory_name,
                "revision": entry.revision,
                "gated": entry.gated,
                "license_expression": entry.license_expression,
                "source_url": entry.source_url,
                "terms_url": entry.terms_url,
                "attribution": entry.attribution,
                "runtime_status": entry.runtime_status,
                "redistribution_status": entry.redistribution_status,
                "review_status": entry.review_status,
                "product_classification": entry.product_classification,
                "blocker": entry.blocker,
                "backend_compatibility": entry.backend_compatibility,
                "acquisition_status": entry.acquisition_status,
                "modification_status": entry.modification_status,
                "package_bundle_id": entry.package_bundle_id,
                "package_bundle_version": entry.package_bundle_version,
                "package_relative_path": entry.package_relative_path,
                "delivery": entry.delivery,
                "required_file_sha256": [
                    {
                        "path": path,
                        "size": entry.expected_size_map[path],
                        "sha256": entry.expected_digest_map[path],
                    }
                    for path in entry.required_files
                ],
            }
            for entry in entries
        ],
        "runtime_assets": [
            {
                "key": _PUNKT_RUNTIME_ASSET_KEY,
                "runtime_cache_relative_root": _PUNKT_SOURCE_ROOT.as_posix(),
                "package_relative_root": _PUNKT_BUNDLE_ROOT.as_posix(),
                "schema_version": punkt_contract["schema_version"],
                "generator": punkt_contract["generator"],
                "nltk_version": punkt_contract["nltk_version"],
                "training_data": punkt_contract["training_data"],
                "file_sha256": punkt_contract["files"],
            }
        ],
    }


def _manifest_payload(
    entries: Sequence[ModelEntry],
    model_records: Mapping[str, list[dict[str, Any]]],
    runtime_asset_record: dict[str, Any],
    license_records: list[dict[str, Any]],
    notice_record: dict[str, Any],
    provenance_record: dict[str, Any],
) -> dict[str, Any]:
    flat_records = [record for entry in entries for record in model_records[entry.key]]
    flat_records.append(cast(dict[str, Any], runtime_asset_record["marker"]))
    flat_records.extend(cast(Sequence[dict[str, Any]], runtime_asset_record["files"]))
    flat_records.extend(cast(dict[str, Any], item["file"]) for item in license_records)
    flat_records.extend((notice_record, provenance_record))
    sorted_records = sorted(flat_records, key=lambda item: cast(str, item["path"]))
    return {
        "schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "layout_version": "1",
        "bundle_id": MODEL_BUNDLE_IDENTIFIER,
        "bundle_version": MODEL_BUNDLE_VERSION,
        "model_keys": list(STRICT_MODEL_KEYS),
        "filesystem_contract": {
            "directory_mode": f"{_DIRECTORY_MODE:04o}",
            "file_mode": f"{_FILE_MODE:04o}",
            "ownership": "uniform-with-bundle-root",
            "regular_file_link_count": 1,
            "single_filesystem": True,
            "symbolic_links": "forbidden",
        },
        "models": [
            {
                "key": entry.key,
                "role": entry.role,
                "model_id": entry.model_id,
                "aliases": list(entry.aliases),
                "directory_name": entry.directory_name,
                "directory_aliases": list(entry.directory_aliases),
                "local_cache_relative_path": entry.directory_name,
                "revision": entry.revision,
                "product_classification": entry.product_classification,
                "blocker": entry.blocker,
                "backend_compatibility": entry.backend_compatibility,
                "acquisition_status": entry.acquisition_status,
                "runtime_status": entry.runtime_status,
                "redistribution_status": entry.redistribution_status,
                "review_status": entry.review_status,
                "modification_status": entry.modification_status,
                "package_bundle_id": entry.package_bundle_id,
                "package_bundle_version": entry.package_bundle_version,
                "package_relative_path": entry.package_relative_path,
                "delivery": entry.delivery,
                "files": model_records[entry.key],
            }
            for entry in entries
        ],
        "runtime_assets": [runtime_asset_record],
        "compliance": {
            "license_inputs": license_records,
            "notice_input": notice_record,
            "provenance": provenance_record,
        },
        "payload": {
            "file_count": len(sorted_records),
            "size": sum(cast(int, record["size"]) for record in sorted_records),
            "sha256": _payload_sha256(sorted_records),
        },
    }


def _verification_payload(
    manifest_payload: Mapping[str, Any],
    manifest_sha256: str,
) -> dict[str, Any]:
    payload = cast(Mapping[str, Any], manifest_payload["payload"])
    models = cast(Sequence[Mapping[str, Any]], manifest_payload["models"])
    runtime_assets = cast(Sequence[Mapping[str, Any]], manifest_payload["runtime_assets"])
    return {
        "schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "record_type": "auralign-model-bundle-verification",
        "bundle_id": MODEL_BUNDLE_IDENTIFIER,
        "bundle_version": MODEL_BUNDLE_VERSION,
        "state": "verified",
        "model_keys": list(STRICT_MODEL_KEYS),
        "runtime_asset_keys": [_PUNKT_RUNTIME_ASSET_KEY],
        "manifest_sha256": manifest_sha256,
        "payload_sha256": payload["sha256"],
        "payload_file_count": payload["file_count"],
        "payload_size": payload["size"],
        "model_policy": [
            {
                "key": model["key"],
                **{field: model[field] for field in _MODEL_POLICY_FIELDS},
            }
            for model in models
        ],
        "runtime_asset_policy": [
            {
                "key": asset["key"],
                "runtime_cache_relative_root": asset["runtime_cache_relative_root"],
                "package_relative_root": asset["package_relative_root"],
                "schema_version": asset["schema_version"],
                "generator": asset["generator"],
                "nltk_version": asset["nltk_version"],
                "training_data": asset["training_data"],
            }
            for asset in runtime_assets
        ],
        "checks": list(_VERIFICATION_CHECKS),
    }


def _completion_payload(
    manifest_sha256: str,
    verification_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
        "record_type": "auralign-model-bundle-completion",
        "bundle_id": MODEL_BUNDLE_IDENTIFIER,
        "bundle_version": MODEL_BUNDLE_VERSION,
        "state": "complete",
        "manifest_sha256": manifest_sha256,
        "verification_record_sha256": verification_sha256,
    }


def _payload_sha256(records: Sequence[Mapping[str, Any]]) -> str:
    return _sha256_bytes(_canonical_json_bytes({"files": list(records)}))


def _canonical_json_bytes(payload: object) -> bytes:
    try:
        text = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ModelBundleError("model bundle metadata is not canonical JSON") from exc
    return (text + "\n").encode("utf-8")


def _read_canonical_json(path: Path, logical_path: str) -> tuple[dict[str, Any], bytes]:
    encoded = _read_regular_file(path, logical_path, maximum_size=_MAX_JSON_BYTES)
    try:
        text = encoded.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ModelBundleError(f"model bundle JSON is invalid: {logical_path}") from exc
    if not isinstance(payload, dict) or _canonical_json_bytes(payload) != encoded:
        raise ModelBundleError(f"model bundle JSON is not canonical: {logical_path}")
    return cast(dict[str, Any], payload), encoded


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _expected_layout(entries: Sequence[ModelEntry]) -> tuple[set[str], set[str]]:
    directories = {
        ".",
        "models",
        "licenses",
        "models/runtime-assets",
        _PUNKT_BUNDLE_ROOT.as_posix(),
        f"{_PUNKT_BUNDLE_ROOT.as_posix()}/tokenizers",
        f"{_PUNKT_BUNDLE_ROOT.as_posix()}/tokenizers/punkt_tab",
        _PUNKT_LANGUAGE_ROOT.as_posix(),
    }
    files = {
        MODEL_BUNDLE_NOTICES_FILENAME,
        MODEL_BUNDLE_PROVENANCE_FILENAME,
        MODEL_BUNDLE_MANIFEST_FILENAME,
        MODEL_BUNDLE_VERIFICATION_FILENAME,
        MODEL_BUNDLE_COMPLETION_FILENAME,
        _PUNKT_MARKER_PATH.as_posix(),
    }
    files.update((_PUNKT_LANGUAGE_ROOT / filename).as_posix() for filename in PUNKT_FILES)
    files.update(f"licenses/{filename}" for _, filename in _LICENSE_INPUTS)
    for entry in entries:
        model_directory = PurePosixPath("models") / entry.directory_name
        directories.add(model_directory.as_posix())
        for relative_path in entry.required_files:
            output = model_directory / PurePosixPath(relative_path)
            files.add(output.as_posix())
            parent = output.parent
            while parent != model_directory and parent.as_posix() not in directories:
                directories.add(parent.as_posix())
                parent = parent.parent
    return directories, files


def _scan_bundle_tree(root: Path) -> tuple[set[str], dict[str, Path]]:
    root_metadata = _lstat(root, "model bundle root")
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise ModelBundleError("model bundle root must be one real directory")
    if stat.S_IMODE(root_metadata.st_mode) != _DIRECTORY_MODE:
        raise ModelBundleError("model bundle root mode is inconsistent")
    root_owner = _owner_identity(root, root_metadata)
    root_device = root_metadata.st_dev

    directories = {"."}
    files: dict[str, Path] = {}
    pending: list[tuple[Path, PurePosixPath]] = [(root, PurePosixPath("."))]
    while pending:
        directory, logical_directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise ModelBundleError("model bundle directory is unreadable") from exc
        for child in children:
            metadata = _lstat(child, "model bundle entry")
            relative = (
                PurePosixPath(child.name)
                if logical_directory == PurePosixPath(".")
                else logical_directory / child.name
            )
            logical_path = relative.as_posix()
            if logical_path != _PUNKT_MARKER_PATH.as_posix():
                _validate_relative_path(logical_path, "model bundle path")
            if metadata.st_dev != root_device:
                raise ModelBundleError("model bundle crosses filesystem ownership boundary")
            if _owner_identity(child, metadata) != root_owner:
                raise ModelBundleError("model bundle ownership contract is inconsistent")
            if stat.S_ISLNK(metadata.st_mode):
                raise ModelBundleError("model bundle contains a symbolic link")
            if stat.S_ISDIR(metadata.st_mode):
                if stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE:
                    raise ModelBundleError("model bundle directory mode is inconsistent")
                directories.add(logical_path)
                pending.append((child, relative))
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise ModelBundleError("model bundle contains a hardlinked file")
                if stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
                    raise ModelBundleError("model bundle file mode is inconsistent")
                files[logical_path] = child
            else:
                raise ModelBundleError("model bundle contains a special filesystem object")
    return directories, files


def _hash_existing_file(path: Path, logical_path: str) -> dict[str, Any]:
    descriptor = _open_readonly_nofollow(path, logical_path)
    digest = hashlib.sha256()
    size = 0
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
            raise ModelBundleError(f"model bundle file contract changed: {logical_path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        final_metadata = os.fstat(descriptor)
        if (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_size,
        ) != (metadata.st_dev, metadata.st_ino, metadata.st_size):
            raise ModelBundleError(f"model bundle file changed during verification: {logical_path}")
    finally:
        os.close(descriptor)
    return _record(logical_path, size, digest.hexdigest())


def _read_existing_bytes(path: Path, logical_path: str) -> bytes:
    return _read_regular_file(path, logical_path, maximum_size=_MAX_TEXT_INPUT_BYTES)


def _read_regular_file(path: Path, logical_path: str, *, maximum_size: int) -> bytes:
    descriptor = _open_readonly_nofollow(path, logical_path)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_size > maximum_size:
            raise ModelBundleError(f"model bundle metadata is unexpectedly large: {logical_path}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_size + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum_size:
                raise ModelBundleError(
                    f"model bundle metadata is unexpectedly large: {logical_path}"
                )
        final_metadata = os.fstat(descriptor)
        if (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_size,
        ) != (metadata.st_dev, metadata.st_ino, metadata.st_size):
            raise ModelBundleError(f"model bundle file changed during read: {logical_path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _open_readonly_nofollow(path: Path, logical_path: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ModelBundleError(f"could not open regular file safely: {logical_path}") from exc
    try:
        descriptor_metadata = os.fstat(descriptor)
        path_metadata = path.lstat()
        if (
            not stat.S_ISREG(descriptor_metadata.st_mode)
            or stat.S_ISLNK(path_metadata.st_mode)
            or descriptor_metadata.st_nlink != 1
            or (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise ModelBundleError(f"file is linked, irregular, or changed: {logical_path}")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_new_regular(path: Path) -> int:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        return os.open(path, flags, 0o600)
    except OSError as exc:
        raise ModelBundleError("could not create deterministic model bundle file") from exc


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ModelBundleError("short write while creating model bundle")
        view = view[written:]


def _file_record(logical_path: str, content: bytes) -> dict[str, Any]:
    return _record(logical_path, len(content), _sha256_bytes(content))


def _record(logical_path: str, size: int, sha256: str) -> dict[str, Any]:
    if logical_path != _PUNKT_MARKER_PATH.as_posix():
        _validate_relative_path(logical_path, "model bundle record path")
    if size < 0 or _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ModelBundleError("model bundle record is malformed")
    return {
        "path": logical_path,
        "size": size,
        "sha256": sha256,
        "mode": f"{_FILE_MODE:04o}",
    }


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _make_bundle_tree_read_only(root: Path) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        directory.chmod(_DIRECTORY_MODE)
    root.chmod(_DIRECTORY_MODE)


def _remove_staging_tree(root: Path) -> None:
    try:
        paths = sorted(
            root.rglob("*"),
            key=lambda candidate: len(candidate.parts),
            reverse=True,
        )
        for path in paths:
            try:
                metadata = path.lstat()
                if stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
                    path.chmod(0o600, follow_symlinks=False)
                elif stat.S_ISDIR(metadata.st_mode):
                    path.chmod(0o700)
            except OSError:
                continue
        root.chmod(0o700)
        shutil.rmtree(root)
    except OSError:
        # Retain a uniquely named, incomplete staging directory rather than
        # broadening cleanup scope after a filesystem failure.
        return


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ModelBundleError("could not open bundle parent for durability sync") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ModelBundleError("could not durably publish model bundle") from exc
    finally:
        os.close(descriptor)


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise ModelBundleError(f"{label} is missing or inaccessible") from exc


def _owner_identity(path: Path, metadata: os.stat_result) -> tuple[int, int]:
    """Return the relative ownership identity used by the bundle contract."""

    del path
    return metadata.st_uid, metadata.st_gid


__all__ = [
    "MODEL_BUNDLE_COMPLETION_FILENAME",
    "MODEL_BUNDLE_IDENTIFIER",
    "MODEL_BUNDLE_MANIFEST_FILENAME",
    "MODEL_BUNDLE_NLTK_VERSION",
    "MODEL_BUNDLE_NOTICES_FILENAME",
    "MODEL_BUNDLE_PROVENANCE_FILENAME",
    "MODEL_BUNDLE_SCHEMA_VERSION",
    "MODEL_BUNDLE_VERIFICATION_FILENAME",
    "MODEL_BUNDLE_VERSION",
    "ModelBundleError",
    "STRICT_MODEL_KEYS",
    "assemble_model_bundle",
    "verify_model_bundle",
]
