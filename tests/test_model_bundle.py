# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from local_transcript_max import model_bundle as bundle_module
from local_transcript_max import models as models_module
from local_transcript_max.config import MODEL_MANIFEST, ModelEntry
from local_transcript_max.doctor import path_status
from local_transcript_max.model_bundle import (
    MODEL_BUNDLE_COMPLETION_FILENAME,
    MODEL_BUNDLE_IDENTIFIER,
    MODEL_BUNDLE_MANIFEST_FILENAME,
    MODEL_BUNDLE_NLTK_VERSION,
    MODEL_BUNDLE_NOTICES_FILENAME,
    MODEL_BUNDLE_PROVENANCE_FILENAME,
    MODEL_BUNDLE_VERIFICATION_FILENAME,
    MODEL_BUNDLE_VERSION,
    STRICT_MODEL_KEYS,
    ModelBundleError,
    assemble_model_bundle,
    verify_model_bundle,
)
from local_transcript_max.models import (
    MODEL_PROVENANCE_FILENAME,
    status_for,
    verify_entries,
    write_model_provenance,
)
from local_transcript_max.runtime_assets import (
    PUNKT_FILES,
    PUNKT_MARKER,
    prepare_generated_punkt,
    verify_generated_punkt,
)


@pytest.fixture
def bundle_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, dict[str, ModelEntry]]:
    cache = tmp_path / "cache"
    cache.mkdir()
    prepare_generated_punkt(cache)
    manifest: dict[str, ModelEntry] = {}
    for key in STRICT_MODEL_KEYS:
        authority = MODEL_MANIFEST[key]
        model_dir = cache / authority.directory_name
        model_dir.mkdir()
        expected: list[tuple[str, str]] = []
        expected_sizes: list[tuple[str, int]] = []
        for relative_path in authority.required_files:
            content = f"deterministic-test-model:{key}:{relative_path}\n".encode()
            output = model_dir / relative_path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
            expected.append((relative_path, hashlib.sha256(content).hexdigest()))
            expected_sizes.append((relative_path, len(content)))
        entry = replace(
            authority,
            expected_sha256=tuple(expected),
            expected_size=tuple(expected_sizes),
        )
        manifest[key] = entry
        write_model_provenance(entry, model_dir)

    licenses = tmp_path / "licenses"
    licenses.mkdir()
    canonical_licenses = Path(__file__).resolve().parents[1] / "LICENSES"
    for filename in ("Apache-2.0.txt", "CC-BY-4.0.txt"):
        (licenses / filename).write_bytes((canonical_licenses / filename).read_bytes())
    notices = tmp_path / "MODEL_NOTICES.md"
    notices.write_text(
        "\n".join(
            f"{entry.model_id} {entry.revision} {entry.license_expression}"
            for entry in manifest.values()
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bundle_module, "MODEL_MANIFEST", manifest)
    monkeypatch.setattr(
        models_module,
        "INSTALLED_MODEL_BUNDLE_ROOT",
        tmp_path / MODEL_BUNDLE_IDENTIFIER,
    )
    return cache, licenses, notices, manifest


def _assemble(
    tmp_path: Path,
    inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    *,
    name: str = "AuralignModels.bundle",
) -> tuple[Path, dict[str, Any]]:
    cache, licenses, notices, _ = inputs
    destination = tmp_path / name
    result = assemble_model_bundle(
        cache,
        destination,
        license_directory=licenses,
        notices_path=notices,
    )
    return destination, result


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _first_model_file(bundle: Path, manifest: dict[str, ModelEntry]) -> Path:
    entry = manifest[STRICT_MODEL_KEYS[0]]
    return bundle / "models" / entry.directory_name / entry.required_files[0]


def _make_parent_writable(path: Path) -> None:
    path.parent.chmod(0o755)


def test_assembly_is_deterministic_runtime_compatible_and_read_only(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
) -> None:
    first, first_result = _assemble(tmp_path, bundle_inputs, name="first.bundle")
    second, second_result = _assemble(tmp_path, bundle_inputs, name="second.bundle")
    _, _, _, manifest = bundle_inputs

    assert first_result == second_result
    assert first_result["bundle_id"] == MODEL_BUNDLE_IDENTIFIER
    assert first_result["bundle_version"] == MODEL_BUNDLE_VERSION
    assert first_result["state"] == "verified"
    assert first_result["model_keys"] == list(STRICT_MODEL_KEYS)
    assert first_result["runtime_asset_keys"] == ["nltk-punkt-tab-english-corpus-free"]
    assert verify_model_bundle(first) == first_result
    assert _file_snapshot(first) == _file_snapshot(second)
    punkt_contract = verify_generated_punkt(first / "models")
    assert punkt_contract["nltk_version"] == MODEL_BUNDLE_NLTK_VERSION
    assert punkt_contract["training_data"] == "none"

    for entry in manifest.values():
        assert (first / "models" / entry.directory_name).is_dir()
        assert not (first / "models" / entry.key).exists()
    assert not list(first.rglob(MODEL_PROVENANCE_FILENAME))

    snapshot = _file_snapshot(first)
    joined = b"\n".join(snapshot.values())
    assert str(tmp_path).encode() not in joined
    assert b"acquired_at" not in joined
    assert b"acquisition_tool_version" not in joined
    assert b"HF_TOKEN" not in joined
    assert set(snapshot) >= {
        MODEL_BUNDLE_MANIFEST_FILENAME,
        MODEL_BUNDLE_VERIFICATION_FILENAME,
        MODEL_BUNDLE_PROVENANCE_FILENAME,
        MODEL_BUNDLE_COMPLETION_FILENAME,
        MODEL_BUNDLE_NOTICES_FILENAME,
    }
    provenance = json.loads(snapshot[MODEL_BUNDLE_PROVENANCE_FILENAME])
    manifest_payload = json.loads(snapshot[MODEL_BUNDLE_MANIFEST_FILENAME])
    verification = json.loads(snapshot[MODEL_BUNDLE_VERIFICATION_FILENAME])
    runtime_asset = manifest_payload["runtime_assets"]
    assert [record["key"] for record in runtime_asset] == ["nltk-punkt-tab-english-corpus-free"]
    assert runtime_asset[0]["nltk_version"] == MODEL_BUNDLE_NLTK_VERSION
    assert runtime_asset[0]["runtime_cache_relative_root"] == "runtime-assets/nltk_data"
    assert runtime_asset[0]["package_relative_root"] == "models/runtime-assets/nltk_data"
    assert {record["path"] for record in runtime_asset[0]["files"]} == {
        f"models/runtime-assets/nltk_data/tokenizers/punkt_tab/english/{filename}"
        for filename in PUNKT_FILES
    }
    assert runtime_asset[0]["marker"]["path"] == (f"models/runtime-assets/nltk_data/{PUNKT_MARKER}")
    assert verification["runtime_asset_keys"] == ["nltk-punkt-tab-english-corpus-free"]
    assert provenance["runtime_assets"][0]["nltk_version"] == MODEL_BUNDLE_NLTK_VERSION
    policy_fields = {
        "product_classification",
        "blocker",
        "backend_compatibility",
        "acquisition_status",
        "runtime_status",
        "redistribution_status",
        "review_status",
        "modification_status",
        "package_bundle_id",
        "package_relative_path",
    }
    for payload in (provenance["models"], manifest_payload["models"]):
        assert all(policy_fields <= set(record) for record in payload)
    assert all(policy_fields <= set(record) for record in verification["model_policy"])

    before = {
        path.relative_to(first).as_posix(): (path.lstat().st_mode, path.lstat().st_mtime_ns)
        for path in first.rglob("*")
    }
    verify_model_bundle(first)
    after = {
        path.relative_to(first).as_posix(): (path.lstat().st_mode, path.lstat().st_mtime_ns)
        for path in first.rglob("*")
    }
    assert before == after
    assert stat.S_IMODE(first.lstat().st_mode) == 0o555
    assert all(
        stat.S_IMODE(path.lstat().st_mode) == (0o555 if path.is_dir() else 0o444)
        for path in first.rglob("*")
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing", "digest", "extra", "marker-version", "locked-version"],
)
def test_punkt_runtime_asset_verification_fails_closed(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    bundle, _ = _assemble(tmp_path, bundle_inputs)
    language_dir = bundle / "models/runtime-assets/nltk_data/tokenizers/punkt_tab/english"
    marker = bundle / f"models/runtime-assets/nltk_data/{PUNKT_MARKER}"

    if mutation == "missing":
        target = language_dir / PUNKT_FILES[0]
        _make_parent_writable(target)
        target.unlink()
        target.parent.chmod(0o555)
    elif mutation == "digest":
        target = language_dir / PUNKT_FILES[0]
        target.chmod(0o644)
        target.write_bytes(b"not corpus-free")
        target.chmod(0o444)
    elif mutation == "extra":
        extra = language_dir / "unexpected.pickle"
        _make_parent_writable(extra)
        extra.write_bytes(b"unexpected")
        extra.chmod(0o444)
        extra.parent.chmod(0o555)
    elif mutation == "marker-version":
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["nltk_version"] = "0.0.0"
        marker.chmod(0o644)
        marker.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        marker.chmod(0o444)
    else:
        monkeypatch.setattr(bundle_module, "distribution_version", lambda _name: "0.0.0")

    with pytest.raises(ModelBundleError):
        verify_model_bundle(bundle)


def test_source_punkt_tree_rejects_missing_or_private_marker_metadata(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
) -> None:
    cache, licenses, notices, _ = bundle_inputs
    marker = cache / f"runtime-assets/nltk_data/{PUNKT_MARKER}"
    original = marker.read_bytes()
    marker.unlink()

    with pytest.raises(ModelBundleError, match="missing or unexpected"):
        assemble_model_bundle(
            cache,
            tmp_path / "missing-punkt.bundle",
            license_directory=licenses,
            notices_path=notices,
        )

    marker.write_bytes(original)
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["acquired_at"] = "private acquisition metadata"
    marker.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ModelBundleError, match="locked NLTK contract"):
        assemble_model_bundle(
            cache,
            tmp_path / "private-punkt.bundle",
            license_directory=licenses,
            notices_path=notices,
        )


def test_doctor_accepts_only_exact_verified_read_only_system_model_cache(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
) -> None:
    bundle, _ = _assemble(
        tmp_path,
        bundle_inputs,
        name=MODEL_BUNDLE_IDENTIFIER,
    )

    status, message = path_status(bundle / "models", create_ok=True)

    assert status == "ok"
    assert "read-only and verified" in message

    arbitrary = tmp_path / "arbitrary-read-only-cache"
    arbitrary.mkdir(mode=0o555)
    try:
        arbitrary_status, arbitrary_message = path_status(arbitrary, create_ok=True)
    finally:
        arbitrary.chmod(0o755)
    assert arbitrary_status == "error"
    assert "not writable" in arbitrary_message


def test_doctor_rejects_corrupt_exact_system_model_cache(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
) -> None:
    bundle, _ = _assemble(
        tmp_path,
        bundle_inputs,
        name=MODEL_BUNDLE_IDENTIFIER,
    )
    marker = bundle / MODEL_BUNDLE_COMPLETION_FILENAME
    marker.chmod(0o644)
    marker.write_bytes(marker.read_bytes() + b" ")
    marker.chmod(0o444)

    status, message = path_status(bundle / "models", create_ok=True)

    assert status == "error"
    assert "failed deterministic installed-bundle verification" in message


def test_packaged_cache_discovery_requires_exact_verified_bundle(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, identity = _assemble(
        tmp_path,
        bundle_inputs,
        name=MODEL_BUNDLE_IDENTIFIER,
    )
    _, _, _, manifest = bundle_inputs
    entries = [manifest[key] for key in STRICT_MODEL_KEYS]
    monkeypatch.setattr(
        "local_transcript_max.models.INSTALLED_MODEL_BUNDLE_ROOT",
        bundle,
    )

    statuses = verify_entries(entries, bundle / "models")

    assert all(status.present and status.revision_verified for status in statuses)
    assert all(
        status.provenance_problem.startswith("installed model bundle verified:")
        for status in statuses
    )
    assert {
        status.to_audit_record(bundle / "models")["provenance_type"] for status in statuses
    } == {"installed_model_bundle"}
    assert identity["manifest_sha256"] in statuses[0].provenance_problem


def test_packaged_cache_discovery_fails_after_completion_record_tamper(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _assemble(
        tmp_path,
        bundle_inputs,
        name=MODEL_BUNDLE_IDENTIFIER,
    )
    _, _, _, manifest = bundle_inputs
    monkeypatch.setattr(
        "local_transcript_max.models.INSTALLED_MODEL_BUNDLE_ROOT",
        bundle,
    )
    marker = bundle / MODEL_BUNDLE_COMPLETION_FILENAME
    marker.chmod(0o644)
    marker.write_bytes(marker.read_bytes() + b" ")
    marker.chmod(0o444)

    status = status_for(manifest[STRICT_MODEL_KEYS[0]], bundle / "models")

    assert status.present is False
    assert status.revision_verified is False
    assert status.provenance_problem == ("installed model bundle failed deterministic verification")


def test_source_cache_must_pass_existing_revision_validation(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
) -> None:
    cache, licenses, notices, manifest = bundle_inputs
    entry = manifest[STRICT_MODEL_KEYS[0]]
    marker = cache / entry.directory_name / MODEL_PROVENANCE_FILENAME
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["revision"] = "0" * 40
    marker.write_text(json.dumps(payload), encoding="utf-8")
    destination = tmp_path / "rejected.bundle"

    with pytest.raises(ModelBundleError, match="immutable cache validation"):
        assemble_model_bundle(
            cache,
            destination,
            license_directory=licenses,
            notices_path=notices,
        )

    assert not destination.exists()
    assert not list(tmp_path.glob(".rejected.bundle.model-bundle-*"))


def test_license_and_notice_inputs_are_exact_and_privacy_safe(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
) -> None:
    cache, licenses, notices, _ = bundle_inputs
    (licenses / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ModelBundleError, match="exact license allowlist"):
        assemble_model_bundle(
            cache,
            tmp_path / "extra-license.bundle",
            license_directory=licenses,
            notices_path=notices,
        )

    (licenses / "unexpected.txt").unlink()
    notices.write_text("/" "Users/private/checkouts must not escape\n", encoding="utf-8")
    with pytest.raises(ModelBundleError, match="private acquisition metadata"):
        assemble_model_bundle(
            cache,
            tmp_path / "private-notice.bundle",
            license_directory=licenses,
            notices_path=notices,
        )


def test_license_input_rejects_phrase_preserving_byte_drift(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
) -> None:
    cache, licenses, notices, _ = bundle_inputs
    apache = licenses / "Apache-2.0.txt"
    apache.write_bytes(apache.read_bytes() + b"\n")

    with pytest.raises(ModelBundleError, match="exact reviewed text"):
        assemble_model_bundle(
            cache,
            tmp_path / "modified-license.bundle",
            license_directory=licenses,
            notices_path=notices,
        )


@pytest.mark.parametrize("mutation", ["missing", "extra", "digest", "size", "mode"])
def test_verifier_rejects_missing_extra_hash_size_and_mode(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    mutation: str,
) -> None:
    bundle, _ = _assemble(tmp_path, bundle_inputs)
    _, _, _, manifest = bundle_inputs
    model_file = _first_model_file(bundle, manifest)

    if mutation == "missing":
        _make_parent_writable(model_file)
        model_file.unlink()
        model_file.parent.chmod(0o555)
    elif mutation == "extra":
        extra = model_file.parent / "unexpected.bin"
        _make_parent_writable(extra)
        extra.write_bytes(b"unexpected")
        extra.chmod(0o444)
        extra.parent.chmod(0o555)
    elif mutation == "digest":
        original = model_file.read_bytes()
        model_file.chmod(0o644)
        model_file.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        model_file.chmod(0o444)
    elif mutation == "size":
        model_file.chmod(0o644)
        model_file.write_bytes(model_file.read_bytes() + b"x")
        model_file.chmod(0o444)
    else:
        model_file.chmod(0o640)

    with pytest.raises(ModelBundleError):
        verify_model_bundle(bundle)


def test_verifier_rejects_symlinks_and_hardlinks(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
) -> None:
    bundle, _ = _assemble(tmp_path, bundle_inputs, name="symlink.bundle")
    _, _, _, manifest = bundle_inputs
    model_file = _first_model_file(bundle, manifest)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(model_file.read_bytes())
    _make_parent_writable(model_file)
    model_file.unlink()
    try:
        model_file.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    model_file.parent.chmod(0o555)
    with pytest.raises(ModelBundleError, match="symbolic link"):
        verify_model_bundle(bundle)

    hardlink_bundle, _ = _assemble(tmp_path, bundle_inputs, name="hardlink.bundle")
    hardlinked_model = _first_model_file(hardlink_bundle, manifest)
    hardlink = tmp_path / "external-hardlink.bin"
    try:
        os.link(hardlinked_model, hardlink)
    except OSError:
        pytest.skip("hardlinks unavailable")
    with pytest.raises(ModelBundleError, match="hardlinked"):
        verify_model_bundle(hardlink_bundle)


def test_verifier_rejects_relative_ownership_mismatch(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _assemble(tmp_path, bundle_inputs)
    _, _, _, manifest = bundle_inputs
    target_name = _first_model_file(bundle, manifest).name
    original = bundle_module._owner_identity

    def inconsistent_owner(path: Path, metadata: os.stat_result) -> tuple[int, int]:
        owner = original(path, metadata)
        if path.name == target_name:
            return owner[0] + 1, owner[1]
        return owner

    monkeypatch.setattr(bundle_module, "_owner_identity", inconsistent_owner)
    with pytest.raises(ModelBundleError, match="ownership contract"):
        verify_model_bundle(bundle)


def test_verifier_rejects_revision_and_completion_marker_inconsistency(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _ = _assemble(tmp_path, bundle_inputs, name="revision.bundle")
    _, _, _, manifest = bundle_inputs
    changed = dict(manifest)
    first_key = STRICT_MODEL_KEYS[0]
    changed[first_key] = replace(changed[first_key], revision="0" * 40)
    monkeypatch.setattr(bundle_module, "MODEL_MANIFEST", changed)
    with pytest.raises(ModelBundleError, match="model authority|provenance authority"):
        verify_model_bundle(bundle)

    monkeypatch.setattr(bundle_module, "MODEL_MANIFEST", manifest)
    marker_bundle, _ = _assemble(tmp_path, bundle_inputs, name="marker.bundle")
    marker = marker_bundle / MODEL_BUNDLE_COMPLETION_FILENAME
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["bundle_id"] = "wrong.bundle"
    marker.chmod(0o644)
    marker.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    marker.chmod(0o444)
    with pytest.raises(ModelBundleError, match="completion marker"):
        verify_model_bundle(marker_bundle)


def test_unsafe_manifest_required_path_fails_before_assembly(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache, licenses, notices, manifest = bundle_inputs
    unsafe = dict(manifest)
    first_key = STRICT_MODEL_KEYS[0]
    unsafe[first_key] = replace(
        unsafe[first_key],
        required_files=("../escape",),
        expected_sha256=(("../escape", "0" * 64),),
        expected_size=(("../escape", 1),),
    )
    monkeypatch.setattr(bundle_module, "MODEL_MANIFEST", unsafe)
    destination = tmp_path / "unsafe.bundle"

    with pytest.raises(ModelBundleError, match="unsafe"):
        assemble_model_bundle(
            cache,
            destination,
            license_directory=licenses,
            notices_path=notices,
        )

    assert not destination.exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"product_classification": "blocked"}, "production-selectable"),
        ({"blocker": "unresolved"}, "production-selectable"),
        ({"backend_compatibility": ""}, "production-selectable"),
        ({"modification_status": "unreviewed"}, "production-selectable"),
        ({"package_bundle_id": "wrong.bundle"}, "package bundle authority"),
        ({"package_relative_path": "models/wrong"}, "package destination"),
    ],
)
def test_production_and_package_authority_fields_fail_closed(
    tmp_path: Path,
    bundle_inputs: tuple[Path, Path, Path, dict[str, ModelEntry]],
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
    message: str,
) -> None:
    cache, licenses, notices, manifest = bundle_inputs
    changed = dict(manifest)
    first_key = STRICT_MODEL_KEYS[0]
    changed[first_key] = replace(changed[first_key], **change)
    monkeypatch.setattr(bundle_module, "MODEL_MANIFEST", changed)

    with pytest.raises(ModelBundleError, match=message):
        assemble_model_bundle(
            cache,
            tmp_path / "authority-rejected.bundle",
            license_directory=licenses,
            notices_path=notices,
        )
