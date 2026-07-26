# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic assembly and verification for one native-download model pack.

The native application may download an exact, pinned model into a private
temporary directory.  This module performs no networking.  It accepts only the
complete file contract recorded by a supplied :class:`ModelEntry`, copies those
files into a new sibling staging directory, adds deterministic public
compliance records, verifies the result, and atomically publishes it.

Unlike the root-owned system model component, a model pack is private to the
current user.  Published directories use mode ``0500`` and regular files use
mode ``0400``.  The sibling staging root alone remains ``0700`` until Darwin's
exclusive rename completes, then is sealed and durably synced to ``0500``
before strict acceptance.  Acquisition markers, cache metadata, paths,
timestamps, and credentials are neither accepted nor preserved.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, cast

from .config import ModelEntry

MODEL_PACK_SCHEMA_VERSION: Final = "1.0"
MODEL_PACK_LAYOUT_VERSION: Final = "1"
MODEL_PACK_MANIFEST_FILENAME: Final = "model-pack-manifest.json"
MODEL_PACK_VERIFICATION_FILENAME: Final = "model-pack-verification.json"
MODEL_PACK_PROVENANCE_FILENAME: Final = "model-pack-provenance.json"
MODEL_PACK_SBOM_FILENAME: Final = "model-pack.cdx.json"
MODEL_PACK_COMPLETION_FILENAME: Final = "model-pack-complete.json"
MODEL_PACK_NOTICES_FILENAME: Final = "THIRD-PARTY-NOTICES.txt"
MODEL_PACK_LICENSES_DIRECTORY: Final = "licenses"

_FILE_MODE: Final = 0o400
_DIRECTORY_MODE: Final = 0o500
_SOURCE_UNSAFE_MODE_BITS: Final = 0o022 | stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
_MAX_TEXT_INPUT_BYTES: Final = 4 * 1024 * 1024
_MAX_JSON_BYTES: Final = 4 * 1024 * 1024
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRIVATE_UNIX_HOME_PATTERN: Final = "/" + "Users/" + "|" + "/" + "home/"
_PRIVATE_PATH_PATTERN: Final = re.compile(
    r"(?:^|[\s\"'`(])(?:"
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
_PRIVATE_ACQUISITION_MARKERS: Final = (
    ".ltm-model.json",
    '"acquired_at"',
    '"acquisition_tool_version"',
    '"cache_dir"',
    '"download_path"',
    '"snapshot_path"',
)
_LICENSE_INPUTS: Final = {
    "Apache-2.0": (
        "Apache-2.0.txt",
        "074e6e32c86a4c0ef8b3ed25b721ca23aca83df277cd88106ef7177c354615ff",
    ),
    "CC-BY-4.0": (
        "CC-BY-4.0.txt",
        "d557539df68e771cc1eedcc91d13f70fca930e508d11eedcafa4b15db49e3744",
    ),
    "MIT": (
        "MIT.txt",
        "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5",
    ),
}
_VERIFICATION_CHECKS: Final = (
    "canonical-json",
    "exact-model-authority",
    "exact-payload-allowlist",
    "exact-sha256-and-size",
    "cyclonedx-1.6-model-sbom",
    "current-user-ownership",
    "private-read-only-modes",
    "single-filesystem-tree",
    "no-symbolic-links",
    "no-hardlinks",
    "no-private-acquisition-metadata",
    "same-filesystem-atomic-publication",
    "published-root-read-only-seal",
)


class ModelPackError(RuntimeError):
    """Raised when model-pack assembly or verification fails closed."""


class ModelPackPublishedError(ModelPackError):
    """Raised when publication committed but a post-commit check failed.

    The destination exists in this state.  Callers must verify it again and
    either accept it or quarantine that exact versioned pack; retrying assembly
    against the same path is intentionally not allowed.
    """

    def __init__(self, message: str, identity: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.identity = dict(identity)


@dataclass(frozen=True)
class _PackAuthority:
    entry: ModelEntry
    bundle_id: str
    bundle_version: str
    package_relative_path: str
    package_root: PurePosixPath
    required_files: tuple[str, ...]
    license_expression: str
    license_filename: str
    license_sha256: str


def model_pack_contract() -> dict[str, Any]:
    """Return the deterministic public contract shared with the native client."""

    return {
        "schema_version": MODEL_PACK_SCHEMA_VERSION,
        "layout_version": MODEL_PACK_LAYOUT_VERSION,
        "filenames": {
            "completion": MODEL_PACK_COMPLETION_FILENAME,
            "licenses_directory": MODEL_PACK_LICENSES_DIRECTORY,
            "manifest": MODEL_PACK_MANIFEST_FILENAME,
            "notices": MODEL_PACK_NOTICES_FILENAME,
            "provenance": MODEL_PACK_PROVENANCE_FILENAME,
            "sbom": MODEL_PACK_SBOM_FILENAME,
            "verification": MODEL_PACK_VERIFICATION_FILENAME,
        },
        "licenses": {
            expression: {
                "filename": filename,
                "sha256": sha256,
            }
            for expression, (filename, sha256) in sorted(_LICENSE_INPUTS.items())
        },
        "sbom": {
            "bom_format": "CycloneDX",
            "spec_version": "1.6",
        },
        "filesystem": {
            "directory_mode": f"{_DIRECTORY_MODE:04o}",
            "file_mode": f"{_FILE_MODE:04o}",
            "staging_root_mode": "0700",
            "published_root_mode": f"{_DIRECTORY_MODE:04o}",
            "publication": "exclusive-rename-then-read-only-root-seal",
        },
    }


def model_pack_sbom(entry: ModelEntry) -> dict[str, Any]:
    """Return the exact deterministic CycloneDX 1.6 SBOM for one pack."""

    return _sbom_payload(_validated_authority(entry))


def assemble_model_pack(
    source_model: Path,
    destination: Path,
    *,
    entry: ModelEntry,
    license_directory: Path,
) -> dict[str, Any]:
    """Assemble, verify, and atomically publish one private model pack.

    ``source_model`` is a native downloader's already-complete private staging
    directory.  It must contain exactly the model files authorized by
    ``entry`` and no cache metadata.  ``destination`` must be absent and its
    parent must be a real, current-user-owned directory.  Publication is one
    same-filesystem rename from a verified sibling staging directory.
    """

    authority = _validated_authority(entry)
    source_model = Path(source_model)
    destination = Path(destination)
    if os.path.lexists(destination):
        raise ModelPackError("model pack destination must be absent")

    parent = destination.parent
    parent_metadata = _lstat(parent, "model pack destination parent")
    expected_owner = _current_user_identity()
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or stat.S_ISLNK(parent_metadata.st_mode)
        or _owner_identity(parent_metadata) != expected_owner
        or stat.S_IMODE(parent_metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
    ):
        raise ModelPackError(
            "model pack destination parent must be a safe current-user directory"
        )

    source_files = _validated_source_tree(source_model, authority)
    license_bytes = _read_standard_license(Path(license_directory), authority)
    notices_bytes = _notices_bytes(authority)
    sbom_payload = _sbom_payload(authority)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.model-pack-", dir=str(parent))
    )
    published = False
    try:
        staging_metadata = _lstat(staging, "model pack staging root")
        if (
            staging_metadata.st_dev != parent_metadata.st_dev
            or _owner_identity(staging_metadata) != expected_owner
        ):
            raise ModelPackError("model pack staging root is not on the publication filesystem")

        package_root = staging.joinpath(*authority.package_root.parts)
        package_root.mkdir(mode=0o700, parents=True)
        licenses_root = staging / MODEL_PACK_LICENSES_DIRECTORY
        licenses_root.mkdir(mode=0o700)

        model_records: list[dict[str, Any]] = []
        for relative_path in authority.required_files:
            logical_path = (
                authority.package_root / PurePosixPath(relative_path)
            ).as_posix()
            output = staging.joinpath(*PurePosixPath(logical_path).parts)
            output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            model_records.append(
                _copy_exact_model_file(
                    source_files[relative_path],
                    output,
                    logical_path=logical_path,
                    expected_sha256=entry.expected_digest_map[relative_path],
                    expected_size=entry.expected_size_map[relative_path],
                    expected_owner=expected_owner,
                )
            )

        license_logical_path = (
            PurePosixPath(MODEL_PACK_LICENSES_DIRECTORY) / authority.license_filename
        ).as_posix()
        license_record = _write_new_bytes(
            staging.joinpath(*PurePosixPath(license_logical_path).parts),
            license_bytes,
            logical_path=license_logical_path,
        )
        notice_record = _write_new_bytes(
            staging / MODEL_PACK_NOTICES_FILENAME,
            notices_bytes,
            logical_path=MODEL_PACK_NOTICES_FILENAME,
        )
        sbom_record, _ = _write_canonical_json(
            staging / MODEL_PACK_SBOM_FILENAME,
            sbom_payload,
            logical_path=MODEL_PACK_SBOM_FILENAME,
        )
        provenance_record, _ = _write_canonical_json(
            staging / MODEL_PACK_PROVENANCE_FILENAME,
            _provenance_payload(authority, sbom_record),
            logical_path=MODEL_PACK_PROVENANCE_FILENAME,
        )

        manifest_payload = _manifest_payload(
            authority,
            model_records,
            license_record,
            notice_record,
            sbom_record,
            provenance_record,
        )
        _, manifest_bytes = _write_canonical_json(
            staging / MODEL_PACK_MANIFEST_FILENAME,
            manifest_payload,
            logical_path=MODEL_PACK_MANIFEST_FILENAME,
        )
        manifest_sha256 = _sha256_bytes(manifest_bytes)
        verification_payload = _verification_payload(
            authority,
            manifest_payload,
            manifest_sha256,
        )
        _, verification_bytes = _write_canonical_json(
            staging / MODEL_PACK_VERIFICATION_FILENAME,
            verification_payload,
            logical_path=MODEL_PACK_VERIFICATION_FILENAME,
        )
        _write_canonical_json(
            staging / MODEL_PACK_COMPLETION_FILENAME,
            _completion_payload(
                authority,
                manifest_sha256,
                _sha256_bytes(verification_bytes),
            ),
            logical_path=MODEL_PACK_COMPLETION_FILENAME,
        )

        _fsync_tree_directories(staging)
        _make_pack_tree_read_only(staging, writable_root=True)
        _fsync_tree_directories(staging)
        staged_identity = _verify_model_pack(
            staging,
            entry=entry,
            allow_writable_root=True,
        )
        if os.path.lexists(destination):
            raise ModelPackError("model pack destination appeared during assembly")
        if _lstat(staging, "model pack staging root").st_dev != parent_metadata.st_dev:
            raise ModelPackError("model pack staging root crossed publication filesystem")
        _rename_exclusive(staging, destination)
        published = True
        try:
            _seal_published_pack_root(destination)
            _fsync_directory(parent)
            return verify_model_pack(destination, entry=entry)
        except ModelPackError as exc:
            raise ModelPackPublishedError(
                "model pack was atomically published, but a post-commit check failed",
                staged_identity,
            ) from exc
    except ModelPackError:
        raise
    except (OSError, ValueError) as exc:
        raise ModelPackError("model pack assembly failed closed") from exc
    finally:
        if not published and os.path.lexists(staging):
            _remove_staging_tree(staging)


def verify_model_pack(
    pack_root: Path,
    *,
    entry: ModelEntry,
) -> dict[str, Any]:
    """Verify one staged or installed private model pack without modifying it."""

    return _verify_model_pack(
        pack_root,
        entry=entry,
        allow_writable_root=False,
    )


def _verify_model_pack(
    pack_root: Path,
    *,
    entry: ModelEntry,
    allow_writable_root: bool,
) -> dict[str, Any]:
    """Verify a pack, with one narrow prepublication root-mode exception."""

    authority = _validated_authority(entry)
    expected_directories, expected_files = _expected_pack_layout(authority)
    root = Path(pack_root)
    actual_directories, actual_files, initial_signature = _scan_pack_tree(
        root,
        allow_writable_root=allow_writable_root,
    )
    if actual_directories != expected_directories or set(actual_files) != expected_files:
        raise ModelPackError("model pack contains missing or unexpected paths")

    model_records: list[dict[str, Any]] = []
    for relative_path in authority.required_files:
        logical_path = (
            authority.package_root / PurePosixPath(relative_path)
        ).as_posix()
        record = _hash_existing_file(actual_files[logical_path], logical_path)
        if record["sha256"] != entry.expected_digest_map[relative_path]:
            raise ModelPackError(
                f"model pack digest does not match authority: {entry.key}:{relative_path}"
            )
        if record["size"] != entry.expected_size_map[relative_path]:
            raise ModelPackError(
                f"model pack size does not match authority: {entry.key}:{relative_path}"
            )
        model_records.append(record)

    license_logical_path = (
        PurePosixPath(MODEL_PACK_LICENSES_DIRECTORY) / authority.license_filename
    ).as_posix()
    license_bytes = _read_existing_bytes(
        actual_files[license_logical_path],
        license_logical_path,
    )
    _validate_standard_license(authority, license_bytes)
    license_record = _file_record(license_logical_path, license_bytes)

    notices_bytes = _read_existing_bytes(
        actual_files[MODEL_PACK_NOTICES_FILENAME],
        MODEL_PACK_NOTICES_FILENAME,
    )
    _validate_public_text(notices_bytes, MODEL_PACK_NOTICES_FILENAME)
    if notices_bytes != _notices_bytes(authority):
        raise ModelPackError("model pack notices are inconsistent with model authority")
    notice_record = _file_record(MODEL_PACK_NOTICES_FILENAME, notices_bytes)

    sbom_payload, sbom_bytes = _read_canonical_json(
        actual_files[MODEL_PACK_SBOM_FILENAME],
        MODEL_PACK_SBOM_FILENAME,
    )
    if sbom_payload != _sbom_payload(authority):
        raise ModelPackError("model pack SBOM is inconsistent with model authority")
    sbom_record = _file_record(MODEL_PACK_SBOM_FILENAME, sbom_bytes)

    provenance_payload, provenance_bytes = _read_canonical_json(
        actual_files[MODEL_PACK_PROVENANCE_FILENAME],
        MODEL_PACK_PROVENANCE_FILENAME,
    )
    if provenance_payload != _provenance_payload(authority, sbom_record):
        raise ModelPackError("model pack provenance authority is inconsistent")
    provenance_record = _file_record(
        MODEL_PACK_PROVENANCE_FILENAME,
        provenance_bytes,
    )

    expected_manifest = _manifest_payload(
        authority,
        model_records,
        license_record,
        notice_record,
        sbom_record,
        provenance_record,
    )
    manifest_payload, manifest_bytes = _read_canonical_json(
        actual_files[MODEL_PACK_MANIFEST_FILENAME],
        MODEL_PACK_MANIFEST_FILENAME,
    )
    if manifest_payload != expected_manifest:
        raise ModelPackError("model pack manifest is inconsistent")
    manifest_sha256 = _sha256_bytes(manifest_bytes)

    expected_verification = _verification_payload(
        authority,
        expected_manifest,
        manifest_sha256,
    )
    verification_payload, verification_bytes = _read_canonical_json(
        actual_files[MODEL_PACK_VERIFICATION_FILENAME],
        MODEL_PACK_VERIFICATION_FILENAME,
    )
    if verification_payload != expected_verification:
        raise ModelPackError("model pack verification record is inconsistent")
    verification_sha256 = _sha256_bytes(verification_bytes)

    completion_payload, _ = _read_canonical_json(
        actual_files[MODEL_PACK_COMPLETION_FILENAME],
        MODEL_PACK_COMPLETION_FILENAME,
    )
    if completion_payload != _completion_payload(
        authority,
        manifest_sha256,
        verification_sha256,
    ):
        raise ModelPackError("model pack completion marker is inconsistent")

    final_directories, final_files, final_signature = _scan_pack_tree(
        root,
        allow_writable_root=allow_writable_root,
    )
    if (
        final_directories != expected_directories
        or set(final_files) != expected_files
        or final_signature != initial_signature
    ):
        raise ModelPackError("model pack changed during verification")

    payload = cast(dict[str, Any], expected_manifest["payload"])
    return {
        "schema_version": MODEL_PACK_SCHEMA_VERSION,
        "bundle_id": authority.bundle_id,
        "bundle_version": authority.bundle_version,
        "package_relative_path": authority.package_relative_path,
        "state": (
            "staged-verified-unaccepted"
            if allow_writable_root
            else "verified"
        ),
        "model_key": entry.key,
        "model_id": entry.model_id,
        "revision": entry.revision,
        "manifest_sha256": manifest_sha256,
        "sbom_sha256": sbom_record["sha256"],
        "verification_record_sha256": verification_sha256,
        "payload_file_count": payload["file_count"],
        "payload_size": payload["size"],
    }


def _validated_authority(entry: ModelEntry) -> _PackAuthority:
    if not isinstance(entry, ModelEntry):
        raise ModelPackError("model pack authority must be one ModelEntry")
    if entry.revision is None or _REVISION_PATTERN.fullmatch(entry.revision) is None:
        raise ModelPackError("model pack revision must be one immutable commit")
    if (
        entry.delivery != "native-download"
        or not entry.production_selectable
        or entry.product_classification not in {"production", "optional"}
        or entry.blocker is not None
    ):
        raise ModelPackError("model is not production-selectable for native download")
    if entry.any_required_files or tuple(entry.runtime_files) != tuple(entry.required_files):
        raise ModelPackError("model pack requires one exact required-file allowlist")
    if not entry.required_files or len(entry.required_files) != len(set(entry.required_files)):
        raise ModelPackError("model pack required-file authority is invalid")
    for relative_path in entry.required_files:
        _validate_relative_path(relative_path, "model pack required file")

    expected_digests = entry.expected_digest_map
    if (
        len(entry.expected_sha256) != len(expected_digests)
        or set(expected_digests) != set(entry.required_files)
        or any(_SHA256_PATTERN.fullmatch(value) is None for value in expected_digests.values())
    ):
        raise ModelPackError("model pack digest authority is incomplete or malformed")
    expected_sizes = entry.expected_size_map
    if (
        len(entry.expected_size) != len(expected_sizes)
        or set(expected_sizes) != set(entry.required_files)
        or any(size <= 0 for size in expected_sizes.values())
    ):
        raise ModelPackError("model pack size authority is incomplete or malformed")

    bundle_id = entry.package_bundle_id
    bundle_version = entry.package_bundle_version
    package_relative_path = entry.package_relative_path
    if (
        bundle_id is None
        or bundle_version is None
        or package_relative_path is None
        or _IDENTIFIER_PATTERN.fullmatch(bundle_id) is None
        or _IDENTIFIER_PATTERN.fullmatch(bundle_version) is None
    ):
        raise ModelPackError("model pack package identity authority is incomplete")
    package_root = _validate_relative_path(
        package_relative_path,
        "model pack package destination",
    )
    if package_relative_path != f"models/{entry.directory_name}":
        raise ModelPackError("model pack package destination is inconsistent")

    license_input = _LICENSE_INPUTS.get(entry.license_expression)
    if license_input is None:
        raise ModelPackError("model pack license has no reviewed standard text")
    _validate_public_metadata(entry)
    return _PackAuthority(
        entry=entry,
        bundle_id=bundle_id,
        bundle_version=bundle_version,
        package_relative_path=package_relative_path,
        package_root=package_root,
        required_files=entry.required_files,
        license_expression=entry.license_expression,
        license_filename=license_input[0],
        license_sha256=license_input[1],
    )


def _validate_public_metadata(entry: ModelEntry) -> None:
    values = (
        ("key", entry.key),
        ("role", entry.role),
        ("model id", entry.model_id),
        ("license", entry.license_expression),
        ("attribution", entry.attribution),
        ("acquisition status", entry.acquisition_status),
        ("runtime status", entry.runtime_status),
        ("redistribution status", entry.redistribution_status),
        ("review status", entry.review_status),
        ("product classification", entry.product_classification),
        ("backend compatibility", entry.backend_compatibility),
        ("modification status", entry.modification_status),
        ("delivery", entry.delivery),
        ("package bundle id", cast(str, entry.package_bundle_id)),
        ("package bundle version", cast(str, entry.package_bundle_version)),
        ("package relative path", cast(str, entry.package_relative_path)),
    )
    for label, value in values:
        _validate_public_string(value, label)
    for alias in (*entry.aliases, *entry.directory_aliases):
        _validate_public_string(alias, "model alias")
    if entry.reviewed_at is not None:
        _validate_public_string(entry.reviewed_at, "model review date")
    for label, value in (("source URL", entry.source_url), ("terms URL", entry.terms_url)):
        _validate_public_string(value, label)
        authority = value.partition("://")[2].partition("/")[0]
        if (
            not value.startswith("https://")
            or "@" in authority
            or "?" in value
            or "#" in value
        ):
            raise ModelPackError(f"model {label} must be a public canonical HTTPS URL")


def _validate_public_string(value: str, label: str) -> None:
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise ModelPackError(f"model {label} is empty or non-canonical")
    lowered = value.casefold()
    if (
        _PRIVATE_PATH_PATTERN.search(value)
        or any(marker in lowered for marker in _SECRET_MARKERS)
        or any(marker in lowered for marker in _PRIVATE_ACQUISITION_MARKERS)
    ):
        raise ModelPackError(f"model {label} contains private acquisition metadata")


def _validated_source_tree(
    source_root: Path,
    authority: _PackAuthority,
) -> dict[str, Path]:
    root_metadata = _lstat(source_root, "source model root")
    expected_owner = _current_user_identity()
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or _owner_identity(root_metadata) != expected_owner
        or stat.S_IMODE(root_metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
    ):
        raise ModelPackError("source model root is unsafe or not owned by the current user")
    root_device = root_metadata.st_dev

    expected_directories, expected_files = _expected_source_layout(authority)
    actual_directories = {"."}
    actual_files: dict[str, Path] = {}
    pending: list[tuple[Path, PurePosixPath]] = [(source_root, PurePosixPath("."))]
    while pending:
        directory, logical_directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise ModelPackError("source model directory is unreadable") from exc
        for child in children:
            metadata = _lstat(child, "source model entry")
            relative = (
                PurePosixPath(child.name)
                if logical_directory == PurePosixPath(".")
                else logical_directory / child.name
            )
            logical_path = relative.as_posix()
            if (
                metadata.st_dev != root_device
                or _owner_identity(metadata) != expected_owner
                or stat.S_ISLNK(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
            ):
                raise ModelPackError(
                    "source model tree is linked, mutable, or inconsistently owned"
                )
            if stat.S_ISDIR(metadata.st_mode):
                actual_directories.add(logical_path)
                pending.append((child, relative))
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise ModelPackError("source model tree contains a hardlinked file")
                actual_files[logical_path] = child
            else:
                raise ModelPackError("source model tree contains a special filesystem object")
    if actual_directories != expected_directories or set(actual_files) != expected_files:
        raise ModelPackError("source model contains missing or unexpected paths")

    for relative_path, path in actual_files.items():
        record = _hash_source_file(
            path,
            relative_path,
            expected_owner=expected_owner,
        )
        if (
            record["sha256"] != authority.entry.expected_digest_map[relative_path]
            or record["size"] != authority.entry.expected_size_map[relative_path]
        ):
            raise ModelPackError(
                f"source model does not match exact authority: {authority.entry.key}:"
                f"{relative_path}"
            )
    return actual_files


def _expected_source_layout(authority: _PackAuthority) -> tuple[set[str], set[str]]:
    directories = {"."}
    files = set(authority.required_files)
    for relative_path in authority.required_files:
        parent = PurePosixPath(relative_path).parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories, files


def _expected_pack_layout(authority: _PackAuthority) -> tuple[set[str], set[str]]:
    directories = {".", MODEL_PACK_LICENSES_DIRECTORY}
    files = {
        MODEL_PACK_MANIFEST_FILENAME,
        MODEL_PACK_VERIFICATION_FILENAME,
        MODEL_PACK_PROVENANCE_FILENAME,
        MODEL_PACK_SBOM_FILENAME,
        MODEL_PACK_COMPLETION_FILENAME,
        MODEL_PACK_NOTICES_FILENAME,
        (
            PurePosixPath(MODEL_PACK_LICENSES_DIRECTORY) / authority.license_filename
        ).as_posix(),
    }
    parent = authority.package_root
    while parent != PurePosixPath("."):
        directories.add(parent.as_posix())
        parent = parent.parent
    for relative_path in authority.required_files:
        output = authority.package_root / PurePosixPath(relative_path)
        files.add(output.as_posix())
        parent = output.parent
        while parent != PurePosixPath("."):
            directories.add(parent.as_posix())
            parent = parent.parent
    return directories, files


def _scan_pack_tree(
    root: Path,
    *,
    allow_writable_root: bool,
) -> tuple[set[str], dict[str, Path], dict[str, tuple[int, ...]]]:
    root_metadata = _lstat(root, "model pack root")
    expected_owner = _current_user_identity()
    expected_root_mode = 0o700 if allow_writable_root else _DIRECTORY_MODE
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or stat.S_IMODE(root_metadata.st_mode) != expected_root_mode
        or _owner_identity(root_metadata) != expected_owner
    ):
        raise ModelPackError("model pack root mode or current-user ownership is inconsistent")
    root_device = root_metadata.st_dev

    directories = {"."}
    files: dict[str, Path] = {}
    signature = {".": _stat_signature(root_metadata)}
    pending: list[tuple[Path, PurePosixPath]] = [(root, PurePosixPath("."))]
    while pending:
        directory, logical_directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError as exc:
            raise ModelPackError("model pack directory is unreadable") from exc
        for child in children:
            metadata = _lstat(child, "model pack entry")
            relative = (
                PurePosixPath(child.name)
                if logical_directory == PurePosixPath(".")
                else logical_directory / child.name
            )
            logical_path = relative.as_posix()
            _validate_relative_path(logical_path, "model pack path")
            if metadata.st_dev != root_device:
                raise ModelPackError("model pack crosses a filesystem boundary")
            if _owner_identity(metadata) != expected_owner:
                raise ModelPackError("model pack current-user ownership is inconsistent")
            if stat.S_ISLNK(metadata.st_mode):
                raise ModelPackError("model pack contains a symbolic link")
            if stat.S_ISDIR(metadata.st_mode):
                if stat.S_IMODE(metadata.st_mode) != _DIRECTORY_MODE:
                    raise ModelPackError("model pack directory mode is inconsistent")
                directories.add(logical_path)
                signature[logical_path] = _stat_signature(metadata)
                pending.append((child, relative))
            elif stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise ModelPackError("model pack contains a hardlinked file")
                if stat.S_IMODE(metadata.st_mode) != _FILE_MODE:
                    raise ModelPackError("model pack file mode is inconsistent")
                files[logical_path] = child
                signature[logical_path] = _stat_signature(metadata)
            else:
                raise ModelPackError("model pack contains a special filesystem object")
    return directories, files, signature


def _read_standard_license(
    license_directory: Path,
    authority: _PackAuthority,
) -> bytes:
    metadata = _lstat(license_directory, "license input directory")
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
    ):
        raise ModelPackError("license input directory is unsafe")
    content = _read_public_input(
        license_directory / authority.license_filename,
        f"license {authority.license_expression}",
    )
    _validate_standard_license(authority, content)
    return content


def _validate_standard_license(authority: _PackAuthority, content: bytes) -> None:
    if _sha256_bytes(content) != authority.license_sha256:
        raise ModelPackError(
            f"license input does not match exact reviewed text: "
            f"{authority.license_expression}"
        )


def _notices_bytes(authority: _PackAuthority) -> bytes:
    entry = authority.entry
    text = "\n".join(
        (
            "Auralign Native-Download Model Pack",
            "",
            f"Model: {entry.model_id}",
            f"Model key: {entry.key}",
            f"Immutable revision: {entry.revision}",
            f"License: {entry.license_expression}",
            f"Source: {entry.source_url}",
            f"Terms: {entry.terms_url}",
            f"Attribution: {entry.attribution}",
            f"Modification status: {entry.modification_status}",
            "",
            "This pack was assembled locally from exact verified files.",
            "It contains no authentication token or private acquisition metadata.",
            "",
        )
    )
    encoded = text.encode("utf-8")
    _validate_public_text(encoded, MODEL_PACK_NOTICES_FILENAME)
    return encoded


def _sbom_payload(authority: _PackAuthority) -> dict[str, Any]:
    """Build one timestamp-free, exact-file CycloneDX 1.6 model SBOM."""

    entry = authority.entry
    model_reference = f"model:{entry.model_id}@{entry.revision}"
    file_components: list[dict[str, Any]] = []
    file_references: list[str] = []
    for relative_path in sorted(authority.required_files):
        file_reference = f"file:{entry.key}/{relative_path}"
        package_path = (
            authority.package_root / PurePosixPath(relative_path)
        ).as_posix()
        file_references.append(file_reference)
        file_components.append(
            {
                "type": "file",
                "bom-ref": file_reference,
                "name": relative_path,
                "hashes": [
                    {
                        "alg": "SHA-256",
                        "content": entry.expected_digest_map[relative_path],
                    }
                ],
                "properties": [
                    {
                        "name": "auralign:model-file-size",
                        "value": str(entry.expected_size_map[relative_path]),
                    },
                    {
                        "name": "auralign:model-key",
                        "value": entry.key,
                    },
                    {
                        "name": "auralign:model-package-path",
                        "value": package_path,
                    },
                ],
            }
        )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "type": "machine-learning-model",
                "bom-ref": model_reference,
                "name": entry.model_id,
                "version": cast(str, entry.revision),
                "licenses": [
                    {
                        "license": {
                            "id": entry.license_expression,
                        }
                    }
                ],
                "externalReferences": [
                    {
                        "type": "distribution",
                        "url": entry.source_url,
                    },
                    {
                        "type": "website",
                        "url": entry.terms_url,
                    },
                ],
                "properties": [
                    {
                        "name": "auralign:model-key",
                        "value": entry.key,
                    },
                    {
                        "name": "auralign:model-role",
                        "value": entry.role,
                    },
                    {
                        "name": "auralign:model-pack-id",
                        "value": authority.bundle_id,
                    },
                    {
                        "name": "auralign:model-pack-version",
                        "value": authority.bundle_version,
                    },
                    {
                        "name": "auralign:model-package-relative-path",
                        "value": authority.package_relative_path,
                    },
                ],
            }
        },
        "components": file_components,
        "dependencies": [
            {
                "ref": model_reference,
                "dependsOn": file_references,
            }
        ],
    }


def _provenance_payload(
    authority: _PackAuthority,
    sbom_record: Mapping[str, Any],
) -> dict[str, Any]:
    entry = authority.entry
    return {
        "schema_version": MODEL_PACK_SCHEMA_VERSION,
        "record_type": "auralign-model-pack-provenance",
        "authority": "supplied-local_transcript_max.config.ModelEntry",
        "bundle_id": authority.bundle_id,
        "bundle_version": authority.bundle_version,
        "package_relative_path": authority.package_relative_path,
        "sbom": {
            "path": sbom_record["path"],
            "size": sbom_record["size"],
            "sha256": sbom_record["sha256"],
            "bom_format": "CycloneDX",
            "spec_version": "1.6",
        },
        "model": {
            "key": entry.key,
            "role": entry.role,
            "model_id": entry.model_id,
            "aliases": list(entry.aliases),
            "directory_aliases": list(entry.directory_aliases),
            "revision": entry.revision,
            "gated": entry.gated,
            "license_expression": entry.license_expression,
            "source_url": entry.source_url,
            "terms_url": entry.terms_url,
            "attribution": entry.attribution,
            "acquisition_status": entry.acquisition_status,
            "runtime_status": entry.runtime_status,
            "redistribution_status": entry.redistribution_status,
            "review_status": entry.review_status,
            "reviewed_at": entry.reviewed_at,
            "product_classification": entry.product_classification,
            "backend_compatibility": entry.backend_compatibility,
            "modification_status": entry.modification_status,
            "delivery": entry.delivery,
            "required_files": [
                {
                    "path": relative_path,
                    "size": entry.expected_size_map[relative_path],
                    "sha256": entry.expected_digest_map[relative_path],
                }
                for relative_path in authority.required_files
            ],
        },
    }


def _manifest_payload(
    authority: _PackAuthority,
    model_records: Sequence[dict[str, Any]],
    license_record: dict[str, Any],
    notice_record: dict[str, Any],
    sbom_record: dict[str, Any],
    provenance_record: dict[str, Any],
) -> dict[str, Any]:
    ordered_model_records = sorted(
        model_records,
        key=lambda record: cast(str, record["path"]),
    )
    payload_records = [
        *ordered_model_records,
        license_record,
        notice_record,
        sbom_record,
        provenance_record,
    ]
    payload_records.sort(key=lambda record: cast(str, record["path"]))
    entry = authority.entry
    return {
        "schema_version": MODEL_PACK_SCHEMA_VERSION,
        "layout_version": MODEL_PACK_LAYOUT_VERSION,
        "record_type": "auralign-model-pack-manifest",
        "bundle_id": authority.bundle_id,
        "bundle_version": authority.bundle_version,
        "package_relative_path": authority.package_relative_path,
        "filesystem_contract": {
            "directory_mode": f"{_DIRECTORY_MODE:04o}",
            "file_mode": f"{_FILE_MODE:04o}",
            "staging_root_mode": "0700",
            "published_root_mode": f"{_DIRECTORY_MODE:04o}",
            "ownership": "current-effective-user",
            "regular_file_link_count": 1,
            "single_filesystem": True,
            "symbolic_links": "forbidden",
            "publication": "exclusive-rename-then-read-only-root-seal",
        },
        "model": {
            "key": entry.key,
            "role": entry.role,
            "model_id": entry.model_id,
            "aliases": list(entry.aliases),
            "revision": entry.revision,
            "license_expression": entry.license_expression,
            "product_classification": entry.product_classification,
            "backend_compatibility": entry.backend_compatibility,
            "runtime_status": entry.runtime_status,
            "review_status": entry.review_status,
            "modification_status": entry.modification_status,
            "delivery": entry.delivery,
            "files": ordered_model_records,
        },
        "compliance": {
            "license": {
                "license_expression": authority.license_expression,
                "file": license_record,
            },
            "notices": notice_record,
            "sbom": sbom_record,
            "provenance": provenance_record,
        },
        "payload": {
            "file_count": len(payload_records),
            "size": sum(cast(int, record["size"]) for record in payload_records),
            "sha256": _payload_sha256(payload_records),
        },
    }


def _verification_payload(
    authority: _PackAuthority,
    manifest_payload: Mapping[str, Any],
    manifest_sha256: str,
) -> dict[str, Any]:
    payload = cast(Mapping[str, Any], manifest_payload["payload"])
    compliance = cast(Mapping[str, Any], manifest_payload["compliance"])
    sbom = cast(Mapping[str, Any], compliance["sbom"])
    return {
        "schema_version": MODEL_PACK_SCHEMA_VERSION,
        "record_type": "auralign-model-pack-verification",
        "bundle_id": authority.bundle_id,
        "bundle_version": authority.bundle_version,
        "package_relative_path": authority.package_relative_path,
        "state": "verified",
        "model_key": authority.entry.key,
        "model_id": authority.entry.model_id,
        "revision": authority.entry.revision,
        "manifest_sha256": manifest_sha256,
        "sbom_sha256": sbom["sha256"],
        "payload_sha256": payload["sha256"],
        "payload_file_count": payload["file_count"],
        "payload_size": payload["size"],
        "checks": list(_VERIFICATION_CHECKS),
    }


def _completion_payload(
    authority: _PackAuthority,
    manifest_sha256: str,
    verification_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": MODEL_PACK_SCHEMA_VERSION,
        "record_type": "auralign-model-pack-completion",
        "bundle_id": authority.bundle_id,
        "bundle_version": authority.bundle_version,
        "package_relative_path": authority.package_relative_path,
        "model_key": authority.entry.key,
        "revision": authority.entry.revision,
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
        raise ModelPackError("model pack metadata is not canonical JSON") from exc
    return (text + "\n").encode("utf-8")


def _read_canonical_json(path: Path, logical_path: str) -> tuple[dict[str, Any], bytes]:
    encoded = _read_regular_file(
        path,
        logical_path,
        maximum_size=_MAX_JSON_BYTES,
        require_pack_contract=True,
    )
    try:
        payload = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ModelPackError(f"model pack JSON is invalid: {logical_path}") from exc
    if not isinstance(payload, dict) or _canonical_json_bytes(payload) != encoded:
        raise ModelPackError(f"model pack JSON is not canonical: {logical_path}")
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


def _copy_exact_model_file(
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
            source_metadata.st_nlink != 1
            or source_metadata.st_size != expected_size
            or _owner_identity(source_metadata) != expected_owner
            or stat.S_IMODE(source_metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
        ):
            raise ModelPackError(f"source model file changed or became unsafe: {logical_path}")
        destination_descriptor = _open_new_regular(destination)
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            _write_all(destination_descriptor, chunk)
        final_source_metadata = os.fstat(source_descriptor)
        if _stat_signature(final_source_metadata) != _stat_signature(source_metadata):
            raise ModelPackError(f"source model changed during copy: {logical_path}")
        if size != expected_size or digest.hexdigest() != expected_sha256:
            raise ModelPackError(f"source model changed during copy: {logical_path}")
        os.fchmod(destination_descriptor, _FILE_MODE)
        os.fsync(destination_descriptor)
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
    return _record(logical_path, size, digest.hexdigest())


def _hash_source_file(
    path: Path,
    logical_path: str,
    *,
    expected_owner: tuple[int, int],
) -> dict[str, Any]:
    descriptor = _open_readonly_nofollow(path, logical_path)
    digest = hashlib.sha256()
    size = 0
    try:
        metadata = os.fstat(descriptor)
        if (
            _owner_identity(metadata) != expected_owner
            or stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
        ):
            raise ModelPackError(f"source model file is unsafe: {logical_path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        final_metadata = os.fstat(descriptor)
        if _stat_signature(final_metadata) != _stat_signature(metadata):
            raise ModelPackError(f"source model changed during hashing: {logical_path}")
    finally:
        os.close(descriptor)
    return _record(logical_path, size, digest.hexdigest())


def _hash_existing_file(path: Path, logical_path: str) -> dict[str, Any]:
    descriptor = _open_readonly_nofollow(path, logical_path)
    digest = hashlib.sha256()
    size = 0
    try:
        metadata = os.fstat(descriptor)
        if (
            metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
            or _owner_identity(metadata) != _current_user_identity()
        ):
            raise ModelPackError(f"model pack file contract changed: {logical_path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        final_metadata = os.fstat(descriptor)
        if _stat_signature(final_metadata) != _stat_signature(metadata):
            raise ModelPackError(f"model pack file changed during verification: {logical_path}")
    finally:
        os.close(descriptor)
    return _record(logical_path, size, digest.hexdigest())


def _read_public_input(path: Path, label: str) -> bytes:
    metadata = _lstat(path, label)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & _SOURCE_UNSAFE_MODE_BITS
    ):
        raise ModelPackError(f"{label} must be one safe unlinked regular file")
    content = _read_regular_file(
        path,
        label,
        maximum_size=_MAX_TEXT_INPUT_BYTES,
        require_pack_contract=False,
    )
    _validate_public_text(content, label)
    return content


def _read_existing_bytes(path: Path, logical_path: str) -> bytes:
    return _read_regular_file(
        path,
        logical_path,
        maximum_size=_MAX_TEXT_INPUT_BYTES,
        require_pack_contract=True,
    )


def _read_regular_file(
    path: Path,
    logical_path: str,
    *,
    maximum_size: int,
    require_pack_contract: bool,
) -> bytes:
    descriptor = _open_readonly_nofollow(path, logical_path)
    try:
        metadata = os.fstat(descriptor)
        if require_pack_contract and (
            metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != _FILE_MODE
            or _owner_identity(metadata) != _current_user_identity()
        ):
            raise ModelPackError(f"model pack file contract changed: {logical_path}")
        if metadata.st_size > maximum_size:
            raise ModelPackError(f"model pack metadata is unexpectedly large: {logical_path}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_size + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum_size:
                raise ModelPackError(
                    f"model pack metadata is unexpectedly large: {logical_path}"
                )
        final_metadata = os.fstat(descriptor)
        if _stat_signature(final_metadata) != _stat_signature(metadata):
            raise ModelPackError(f"model pack file changed during read: {logical_path}")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _open_readonly_nofollow(path: Path, logical_path: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ModelPackError(f"could not open regular file safely: {logical_path}") from exc
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
            raise ModelPackError(f"file is linked, irregular, or changed: {logical_path}")
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
        raise ModelPackError("could not create deterministic model pack file") from exc


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise ModelPackError("short write while creating model pack")
        view = view[written:]


def _write_new_bytes(
    destination: Path,
    content: bytes,
    *,
    logical_path: str,
) -> dict[str, Any]:
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


def _file_record(logical_path: str, content: bytes) -> dict[str, Any]:
    return _record(logical_path, len(content), _sha256_bytes(content))


def _record(logical_path: str, size: int, sha256: str) -> dict[str, Any]:
    _validate_relative_path(logical_path, "model pack record path")
    if size < 0 or _SHA256_PATTERN.fullmatch(sha256) is None:
        raise ModelPackError("model pack record is malformed")
    return {
        "path": logical_path,
        "size": size,
        "sha256": sha256,
        "mode": f"{_FILE_MODE:04o}",
    }


def _validate_relative_path(value: str, label: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise ModelPackError(f"{label} is unsafe")
    candidate = PurePosixPath(value)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} or part.startswith(".") for part in candidate.parts)
    ):
        raise ModelPackError(f"{label} is unsafe")
    return candidate


def _validate_public_text(content: bytes, label: str) -> None:
    if not content:
        raise ModelPackError(f"{label} must not be empty")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ModelPackError(f"{label} must be UTF-8") from exc
    if "\x00" in text:
        raise ModelPackError(f"{label} contains binary data")
    lowered = text.casefold()
    if (
        _PRIVATE_PATH_PATTERN.search(text)
        or any(marker in lowered for marker in _SECRET_MARKERS)
        or any(marker in lowered for marker in _PRIVATE_ACQUISITION_MARKERS)
    ):
        raise ModelPackError(f"{label} contains private acquisition metadata")


def _make_pack_tree_read_only(root: Path, *, writable_root: bool) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        directory.chmod(_DIRECTORY_MODE)
    root.chmod(0o700 if writable_root else _DIRECTORY_MODE)


def _seal_published_pack_root(root: Path) -> None:
    """Seal an exclusively published root before it can be accepted."""

    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise ModelPackError("could not open published model pack root safely") from exc
    try:
        metadata = os.fstat(descriptor)
        path_metadata = root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(path_metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino)
            != (path_metadata.st_dev, path_metadata.st_ino)
            or _owner_identity(metadata) != _current_user_identity()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ModelPackError("published model pack root changed before sealing")
        os.fchmod(descriptor, _DIRECTORY_MODE)
        os.fsync(descriptor)
        sealed = os.fstat(descriptor)
        if (
            _owner_identity(sealed) != _current_user_identity()
            or stat.S_IMODE(sealed.st_mode) != _DIRECTORY_MODE
        ):
            raise ModelPackError("published model pack root did not seal read-only")
    except OSError as exc:
        raise ModelPackError("could not seal published model pack root") from exc
    finally:
        os.close(descriptor)


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
        # Retain only the uniquely named incomplete sibling if scoped cleanup
        # itself fails; never broaden cleanup to the parent.
        return


def _fsync_tree_directories(root: Path) -> None:
    directories = [path for path in root.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        _fsync_directory(directory)
    _fsync_directory(root)


def _rename_exclusive(source: Path, destination: Path) -> None:
    """Atomically rename without replacing any destination filesystem object."""

    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    function: Any
    arguments: tuple[int, bytes, int, bytes, int]
    if sys.platform == "darwin":
        function = getattr(library, "renameatx_np", None)
        if function is None:
            raise ModelPackError("exclusive atomic model pack publication is unavailable")
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        # Absolute paths ignore the directory descriptors.  macOS defines
        # AT_FDCWD as -2 and RENAME_EXCL as 0x00000004.
        arguments = (-2, source_bytes, -2, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        function = getattr(library, "renameat2", None)
        if function is None:
            raise ModelPackError("exclusive atomic model pack publication is unavailable")
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        # Linux defines AT_FDCWD as -100 and RENAME_NOREPLACE as 1.
        arguments = (-100, source_bytes, -100, destination_bytes, 1)
    else:
        raise ModelPackError("exclusive atomic model pack publication is unsupported")

    ctypes.set_errno(0)
    if function(*arguments) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ModelPackError("model pack destination appeared during atomic publication")
    detail = os.strerror(error_number) if error_number else "unknown operating-system error"
    raise ModelPackError(f"exclusive atomic model pack publication failed: {detail}")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ModelPackError("could not open model pack directory for durability sync") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ModelPackError("could not durably publish model pack") from exc
    finally:
        os.close(descriptor)


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise ModelPackError(f"{label} is missing or inaccessible") from exc


def _owner_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_uid, metadata.st_gid


def _stat_signature(metadata: os.stat_result) -> tuple[int, ...]:
    """Return mutation-sensitive identity without access-time noise."""

    return (
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


def _current_user_identity() -> tuple[int, int]:
    return os.geteuid(), os.getegid()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


__all__ = [
    "MODEL_PACK_COMPLETION_FILENAME",
    "MODEL_PACK_LICENSES_DIRECTORY",
    "MODEL_PACK_LAYOUT_VERSION",
    "MODEL_PACK_MANIFEST_FILENAME",
    "MODEL_PACK_NOTICES_FILENAME",
    "MODEL_PACK_PROVENANCE_FILENAME",
    "MODEL_PACK_SBOM_FILENAME",
    "MODEL_PACK_SCHEMA_VERSION",
    "MODEL_PACK_VERIFICATION_FILENAME",
    "ModelPackError",
    "ModelPackPublishedError",
    "assemble_model_pack",
    "model_pack_contract",
    "model_pack_sbom",
    "verify_model_pack",
]
