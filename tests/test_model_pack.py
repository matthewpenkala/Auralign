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
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator

from local_transcript_max import model_pack as pack_module
from local_transcript_max.config import MODEL_MANIFEST, ModelEntry
from local_transcript_max.model_pack import (
    MODEL_PACK_COMPLETION_FILENAME,
    MODEL_PACK_MANIFEST_FILENAME,
    MODEL_PACK_NOTICES_FILENAME,
    MODEL_PACK_PROVENANCE_FILENAME,
    MODEL_PACK_SBOM_FILENAME,
    MODEL_PACK_VERIFICATION_FILENAME,
    ModelPackError,
    ModelPackPublishedError,
    assemble_model_pack,
    model_pack_contract,
    model_pack_sbom,
    verify_model_pack,
)


@pytest.fixture
def pack_inputs(tmp_path: Path) -> tuple[Path, Path, ModelEntry]:
    source = tmp_path / "native-download-staging"
    source.mkdir()
    contents = {
        "config.json": b'{"model":"test"}\n',
        "model.bin": b"deterministic native-download model bytes\n",
        "tokenizer/vocabulary.json": b'{"hello":1}\n',
    }
    expected_sha256: list[tuple[str, str]] = []
    expected_size: list[tuple[str, int]] = []
    for relative_path, content in contents.items():
        output = source / relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        expected_sha256.append((relative_path, hashlib.sha256(content).hexdigest()))
        expected_size.append((relative_path, len(content)))

    entry = ModelEntry(
        key="native-test",
        role="asr-mlx",
        model_id="example/model-native-test",
        required_files=tuple(contents),
        expected_sha256=tuple(expected_sha256),
        expected_size=tuple(expected_size),
        aliases=("native-test-alias",),
        revision="1" * 40,
        license_expression="MIT",
        source_url="https://example.invalid/model-native-test",
        terms_url="https://example.invalid/model-native-test/terms",
        attribution="Example Model Authors",
        acquisition_status="approved_connected_exact_revision",
        runtime_status="approved_local_verified",
        redistribution_status="permitted_with_attribution",
        review_status="verified_owner_2026-07-25",
        reviewed_at="2026-07-25",
        product_classification="optional",
        blocker=None,
        backend_compatibility="mlx-whisper local path",
        modification_status="unmodified",
        package_bundle_id="com.auralign.studio.models.native-test.v1",
        package_bundle_version="1.2.3",
        package_relative_path="models/example--model-native-test",
        delivery="native-download",
    )

    licenses = tmp_path / "licenses"
    licenses.mkdir()
    canonical_license = Path(__file__).resolve().parents[1] / "LICENSES/MIT.txt"
    (licenses / "MIT.txt").write_bytes(canonical_license.read_bytes())
    return source, licenses, entry


def _assemble(
    tmp_path: Path,
    inputs: tuple[Path, Path, ModelEntry],
    *,
    name: str = "native-test.pack",
) -> tuple[Path, dict[str, Any]]:
    source, licenses, entry = inputs
    destination = tmp_path / name
    result = assemble_model_pack(
        source,
        destination,
        entry=entry,
        license_directory=licenses,
    )
    return destination, result


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int, bytes]]:
    snapshot = {
        ".": (
            "directory",
            stat.S_IMODE(root.lstat().st_mode),
            b"",
        )
    }
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            snapshot[relative] = ("directory", stat.S_IMODE(metadata.st_mode), b"")
        else:
            snapshot[relative] = (
                "file",
                stat.S_IMODE(metadata.st_mode),
                path.read_bytes(),
            )
    return snapshot


def _model_file(pack: Path, entry: ModelEntry) -> Path:
    assert entry.package_relative_path is not None
    return pack / entry.package_relative_path / "model.bin"


def _replace_with_canonical_json(path: Path, payload: dict[str, Any]) -> None:
    path.chmod(0o600)
    path.write_text(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o400)


def test_assembly_is_deterministic_private_and_authority_bound(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
) -> None:
    first, first_result = _assemble(tmp_path, pack_inputs, name="first.pack")
    second, second_result = _assemble(tmp_path, pack_inputs, name="second.pack")
    _, _, entry = pack_inputs

    assert first_result == second_result
    assert verify_model_pack(first, entry=entry) == first_result
    assert first_result == {
        **first_result,
        "bundle_id": entry.package_bundle_id,
        "bundle_version": entry.package_bundle_version,
        "package_relative_path": entry.package_relative_path,
        "state": "verified",
        "model_key": entry.key,
        "model_id": entry.model_id,
        "revision": entry.revision,
    }
    assert _tree_snapshot(first) == _tree_snapshot(second)

    root_metadata = first.lstat()
    assert stat.S_IMODE(root_metadata.st_mode) == 0o500
    assert (root_metadata.st_uid, root_metadata.st_gid) == (os.geteuid(), os.getegid())
    root_device = root_metadata.st_dev
    for path in first.rglob("*"):
        metadata = path.lstat()
        assert (metadata.st_uid, metadata.st_gid) == (os.geteuid(), os.getegid())
        assert metadata.st_dev == root_device
        assert metadata.st_nlink == 1 or stat.S_ISDIR(metadata.st_mode)
        expected_mode = 0o500 if stat.S_ISDIR(metadata.st_mode) else 0o400
        assert stat.S_IMODE(metadata.st_mode) == expected_mode

    snapshot = _tree_snapshot(first)
    joined = b"\n".join(record[2] for record in snapshot.values())
    assert str(tmp_path).encode() not in joined
    assert b".ltm-model.json" not in joined
    assert b"acquired_at" not in joined
    assert b"acquisition_tool_version" not in joined
    assert b"native-download-staging" not in joined
    assert set(snapshot) >= {
        MODEL_PACK_MANIFEST_FILENAME,
        MODEL_PACK_VERIFICATION_FILENAME,
        MODEL_PACK_PROVENANCE_FILENAME,
        MODEL_PACK_SBOM_FILENAME,
        MODEL_PACK_COMPLETION_FILENAME,
        MODEL_PACK_NOTICES_FILENAME,
        "licenses/MIT.txt",
        f"{entry.package_relative_path}/model.bin",
    }

    manifest = json.loads(snapshot[MODEL_PACK_MANIFEST_FILENAME][2])
    provenance = json.loads(snapshot[MODEL_PACK_PROVENANCE_FILENAME][2])
    sbom_bytes = snapshot[MODEL_PACK_SBOM_FILENAME][2]
    sbom = json.loads(sbom_bytes)
    assert manifest["filesystem_contract"]["directory_mode"] == "0500"
    assert manifest["filesystem_contract"]["file_mode"] == "0400"
    assert manifest["filesystem_contract"]["publication"] == (
        "exclusive-rename-then-read-only-root-seal"
    )
    assert provenance["model"]["delivery"] == "native-download"
    assert provenance["bundle_version"] == entry.package_bundle_version
    assert provenance["sbom"] == {
        "path": MODEL_PACK_SBOM_FILENAME,
        "size": len(sbom_bytes),
        "sha256": hashlib.sha256(sbom_bytes).hexdigest(),
        "bom_format": "CycloneDX",
        "spec_version": "1.6",
    }
    assert manifest["compliance"]["sbom"]["sha256"] == provenance["sbom"]["sha256"]
    assert model_pack_sbom(entry) == sbom
    assert (
        JsonStrictValidator(SchemaVersion.V1_6).validate_str(
            json.dumps(sbom, sort_keys=True, separators=(",", ":"))
        )
        is None
    )
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.6"
    assert "timestamp" not in sbom["metadata"]
    components = {component["name"]: component for component in sbom["components"]}
    assert set(components) == set(entry.required_files)
    for relative_path in entry.required_files:
        component = components[relative_path]
        assert component["hashes"] == [
            {
                "alg": "SHA-256",
                "content": entry.expected_digest_map[relative_path],
            }
        ]
        properties = {
            row["name"]: row["value"] for row in component["properties"]
        }
        assert properties["auralign:model-file-size"] == str(
            entry.expected_size_map[relative_path]
        )
        assert properties["auralign:model-package-path"] == (
            f"{entry.package_relative_path}/{relative_path}"
        )


def test_public_pack_contract_exposes_exact_compliance_resources() -> None:
    contract = model_pack_contract()

    assert contract["schema_version"] == pack_module.MODEL_PACK_SCHEMA_VERSION
    assert contract["layout_version"] == pack_module.MODEL_PACK_LAYOUT_VERSION
    assert contract["filenames"]["sbom"] == MODEL_PACK_SBOM_FILENAME
    assert contract["sbom"] == {
        "bom_format": "CycloneDX",
        "spec_version": "1.6",
    }
    assert contract["filesystem"] == {
        "directory_mode": "0500",
        "file_mode": "0400",
        "staging_root_mode": "0700",
        "published_root_mode": "0500",
        "publication": "exclusive-rename-then-read-only-root-seal",
    }
    assert contract["licenses"] == {
        "Apache-2.0": {
            "filename": "Apache-2.0.txt",
            "sha256": "074e6e32c86a4c0ef8b3ed25b721ca23aca83df277cd88106ef7177c354615ff",
        },
        "CC-BY-4.0": {
            "filename": "CC-BY-4.0.txt",
            "sha256": "d557539df68e771cc1eedcc91d13f70fca930e508d11eedcafa4b15db49e3744",
        },
        "MIT": {
            "filename": "MIT.txt",
            "sha256": "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5",
        },
    }


@pytest.mark.parametrize(
    "model_key",
    [
        key
        for key, entry in MODEL_MANIFEST.items()
        if entry.delivery == "native-download"
    ],
)
def test_every_native_delivery_model_has_valid_exact_cyclonedx_sbom(
    model_key: str,
) -> None:
    entry = MODEL_MANIFEST[model_key]
    sbom = model_pack_sbom(entry)
    encoded = json.dumps(sbom, sort_keys=True, separators=(",", ":"))

    assert JsonStrictValidator(SchemaVersion.V1_6).validate_str(encoded) is None
    assert "timestamp" not in encoded
    assert "/Users/" not in encoded
    assert {
        component["name"]: component["hashes"][0]["content"]
        for component in sbom["components"]
    } == entry.expected_digest_map
    assert {
        component["name"]: int(
            {
                row["name"]: row["value"]
                for row in component["properties"]
            }["auralign:model-file-size"]
        )
        for component in sbom["components"]
    } == entry.expected_size_map


@pytest.mark.parametrize("mutation", ["missing", "corrupt", "extra", "mode"])
def test_verifier_rejects_missing_corrupt_extra_and_mutable_content(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    mutation: str,
) -> None:
    pack, _ = _assemble(tmp_path, pack_inputs)
    _, _, entry = pack_inputs
    target = _model_file(pack, entry)

    if mutation == "missing":
        target.parent.chmod(0o700)
        target.unlink()
        target.parent.chmod(0o500)
    elif mutation == "corrupt":
        target.chmod(0o600)
        target.write_bytes(target.read_bytes() + b"corruption")
        target.chmod(0o400)
    elif mutation == "extra":
        target.parent.chmod(0o700)
        extra = target.parent / "unexpected.bin"
        extra.write_bytes(b"unexpected")
        extra.chmod(0o400)
        target.parent.chmod(0o500)
    else:
        target.chmod(0o600)

    with pytest.raises(ModelPackError):
        verify_model_pack(pack, entry=entry)


def test_verifier_rejects_symlink_and_hardlink(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
) -> None:
    symlink_pack, _ = _assemble(tmp_path, pack_inputs, name="symlink.pack")
    _, _, entry = pack_inputs
    target = _model_file(symlink_pack, entry)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(target.read_bytes())
    target.parent.chmod(0o700)
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links unavailable")
    target.parent.chmod(0o500)
    with pytest.raises(ModelPackError, match="symbolic link|regular file safely"):
        verify_model_pack(symlink_pack, entry=entry)

    hardlink_pack, _ = _assemble(tmp_path, pack_inputs, name="hardlink.pack")
    hardlink_target = _model_file(hardlink_pack, entry)
    outside_hardlink = tmp_path / "outside-hardlink.bin"
    try:
        os.link(hardlink_target, outside_hardlink)
    except OSError:
        pytest.skip("hardlinks unavailable")
    with pytest.raises(ModelPackError, match="hardlinked"):
        verify_model_pack(hardlink_pack, entry=entry)


def test_verifier_rejects_non_current_owner_contract(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack, _ = _assemble(tmp_path, pack_inputs)
    _, _, entry = pack_inputs
    monkeypatch.setattr(
        pack_module,
        "_current_user_identity",
        lambda: (os.geteuid() + 1, os.getegid()),
    )

    with pytest.raises(ModelPackError, match="ownership"):
        verify_model_pack(pack, entry=entry)


def test_verifier_rejects_completion_marker_tamper(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
) -> None:
    pack, _ = _assemble(tmp_path, pack_inputs)
    _, _, entry = pack_inputs
    marker = pack / MODEL_PACK_COMPLETION_FILENAME
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload["bundle_version"] = "9.9.9"
    _replace_with_canonical_json(marker, payload)

    with pytest.raises(ModelPackError, match="completion marker"):
        verify_model_pack(pack, entry=entry)


@pytest.mark.parametrize("mutation", ["missing", "digest", "private-path", "timestamp"])
def test_verifier_rejects_missing_or_noncanonical_sbom_authority(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    mutation: str,
) -> None:
    pack, _ = _assemble(tmp_path, pack_inputs, name=f"sbom-{mutation}.pack")
    _, _, entry = pack_inputs
    sbom_path = pack / MODEL_PACK_SBOM_FILENAME

    if mutation == "missing":
        pack.chmod(0o700)
        sbom_path.unlink()
        pack.chmod(0o500)
    else:
        payload = json.loads(sbom_path.read_text(encoding="utf-8"))
        if mutation == "digest":
            payload["components"][0]["hashes"][0]["content"] = "0" * 64
        elif mutation == "private-path":
            payload["components"][0]["properties"][2]["value"] = (
                "/" "Users/private/model.bin"
            )
        else:
            payload["metadata"]["timestamp"] = "2026-07-25T00:00:00Z"
        _replace_with_canonical_json(sbom_path, payload)

    with pytest.raises(ModelPackError, match="missing or unexpected|SBOM"):
        verify_model_pack(pack, entry=entry)


@pytest.mark.parametrize(
    "entry_change",
    [
        {"revision": "2" * 40},
        {"package_bundle_id": "com.auralign.studio.models.other.v1"},
        {"package_bundle_version": "9.9.9"},
    ],
)
def test_verifier_rejects_revision_and_bundle_authority_mismatch(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    entry_change: dict[str, object],
) -> None:
    pack, _ = _assemble(tmp_path, pack_inputs)
    _, _, entry = pack_inputs
    changed = replace(entry, **entry_change)

    with pytest.raises(ModelPackError, match="inconsistent"):
        verify_model_pack(pack, entry=changed)


def test_source_rejects_missing_corrupt_private_marker_and_links(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
) -> None:
    source, licenses, entry = pack_inputs
    model_file = source / "model.bin"
    original = model_file.read_bytes()

    model_file.unlink()
    with pytest.raises(ModelPackError, match="missing or unexpected"):
        assemble_model_pack(
            source,
            tmp_path / "missing.pack",
            entry=entry,
            license_directory=licenses,
        )
    model_file.write_bytes(original)

    model_file.write_bytes(original + b"x")
    with pytest.raises(ModelPackError, match="exact authority"):
        assemble_model_pack(
            source,
            tmp_path / "corrupt.pack",
            entry=entry,
            license_directory=licenses,
        )
    model_file.write_bytes(original)

    marker = source / ".ltm-model.json"
    marker.write_text('{"acquired_at":"private"}\n', encoding="utf-8")
    with pytest.raises(ModelPackError, match="missing or unexpected"):
        assemble_model_pack(
            source,
            tmp_path / "marker.pack",
            entry=entry,
            license_directory=licenses,
        )
    marker.unlink()

    outside = tmp_path / "outside-source.bin"
    outside.write_bytes(original)
    model_file.unlink()
    try:
        model_file.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links unavailable")
    with pytest.raises(ModelPackError, match="linked"):
        assemble_model_pack(
            source,
            tmp_path / "symlink-source.pack",
            entry=entry,
            license_directory=licenses,
        )
    model_file.unlink()
    model_file.write_bytes(original)

    outside_hardlink = tmp_path / "outside-source-hardlink.bin"
    try:
        os.link(model_file, outside_hardlink)
    except OSError:
        pytest.skip("hardlinks unavailable")
    with pytest.raises(ModelPackError, match="hardlinked"):
        assemble_model_pack(
            source,
            tmp_path / "hardlink-source.pack",
            entry=entry,
            license_directory=licenses,
        )


def test_destination_must_be_absent_and_failed_assembly_leaves_no_staging(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
) -> None:
    source, licenses, entry = pack_inputs
    destination = tmp_path / "existing.pack"
    destination.mkdir()
    with pytest.raises(ModelPackError, match="destination must be absent"):
        assemble_model_pack(
            source,
            destination,
            entry=entry,
            license_directory=licenses,
        )

    destination.rmdir()
    (source / "model.bin").write_bytes(b"wrong")
    with pytest.raises(ModelPackError):
        assemble_model_pack(
            source,
            destination,
            entry=entry,
            license_directory=licenses,
        )
    assert not destination.exists()
    assert not list(tmp_path.glob(".existing.pack.model-pack-*"))


def test_atomic_publication_never_replaces_racing_destination(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, licenses, entry = pack_inputs
    destination = tmp_path / "raced.pack"
    original = pack_module._rename_exclusive

    def race_then_publish(staging: Path, requested_destination: Path) -> None:
        requested_destination.mkdir()
        (requested_destination / "sentinel").write_text("owned by racer\n", encoding="utf-8")
        original(staging, requested_destination)

    monkeypatch.setattr(pack_module, "_rename_exclusive", race_then_publish)
    with pytest.raises(ModelPackError, match="destination appeared"):
        assemble_model_pack(
            source,
            destination,
            entry=entry,
            license_directory=licenses,
        )

    assert (destination / "sentinel").read_text(encoding="utf-8") == "owned by racer\n"
    assert not list(tmp_path.glob(".raced.pack.model-pack-*"))


def test_post_commit_failure_reports_published_recoverable_identity(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, licenses, entry = pack_inputs
    destination = tmp_path / "published.pack"
    original = pack_module._fsync_directory

    def fail_parent_sync(path: Path) -> None:
        if path == tmp_path and destination.exists():
            raise ModelPackError("simulated parent fsync failure")
        original(path)

    monkeypatch.setattr(pack_module, "_fsync_directory", fail_parent_sync)
    with pytest.raises(ModelPackPublishedError) as captured:
        assemble_model_pack(
            source,
            destination,
            entry=entry,
            license_directory=licenses,
        )

    assert captured.value.identity["state"] == "staged-verified-unaccepted"
    assert captured.value.identity["bundle_id"] == entry.package_bundle_id
    assert destination.exists()
    assert verify_model_pack(destination, entry=entry)["state"] == "verified"
    assert not list(tmp_path.glob(".published.pack.model-pack-*"))


def test_publication_keeps_staging_root_writable_then_seals_before_acceptance(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = pack_module._rename_exclusive
    observed_staging_modes: list[int] = []

    def inspect_then_publish(staging: Path, destination: Path) -> None:
        observed_staging_modes.append(stat.S_IMODE(staging.lstat().st_mode))
        assert all(
            stat.S_IMODE(path.lstat().st_mode)
            == (0o500 if path.is_dir() else 0o400)
            for path in staging.rglob("*")
        )
        original(staging, destination)

    monkeypatch.setattr(pack_module, "_rename_exclusive", inspect_then_publish)
    pack, identity = _assemble(tmp_path, pack_inputs, name="mode-transition.pack")

    assert observed_staging_modes == [0o700]
    assert stat.S_IMODE(pack.lstat().st_mode) == 0o500
    assert identity["state"] == "verified"


def test_seal_failure_never_returns_an_accepted_pack_identity(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, licenses, entry = pack_inputs
    destination = tmp_path / "unsealed.pack"

    def fail_seal(_path: Path) -> None:
        raise ModelPackError("simulated root seal failure")

    monkeypatch.setattr(pack_module, "_seal_published_pack_root", fail_seal)
    with pytest.raises(ModelPackPublishedError) as captured:
        assemble_model_pack(
            source,
            destination,
            entry=entry,
            license_directory=licenses,
        )

    assert captured.value.identity["state"] == "staged-verified-unaccepted"
    assert stat.S_IMODE(destination.lstat().st_mode) == 0o700
    with pytest.raises(ModelPackError, match="root mode"):
        verify_model_pack(destination, entry=entry)


@pytest.mark.parametrize("mutation", ["chmod", "replace"])
def test_verifier_rejects_tree_change_after_payload_hashing(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    pack, _ = _assemble(tmp_path, pack_inputs, name=f"mid-verify-{mutation}.pack")
    _, _, entry = pack_inputs
    model_file = _model_file(pack, entry)
    original_read = pack_module._read_existing_bytes
    mutated = False

    def mutate_after_model_hash(path: Path, logical_path: str) -> bytes:
        nonlocal mutated
        if not mutated:
            mutated = True
            if mutation == "chmod":
                model_file.chmod(0o600)
            else:
                content = model_file.read_bytes()
                model_file.parent.chmod(0o700)
                model_file.unlink()
                model_file.write_bytes(content)
                model_file.chmod(0o400)
                model_file.parent.chmod(0o500)
        return original_read(path, logical_path)

    monkeypatch.setattr(pack_module, "_read_existing_bytes", mutate_after_model_hash)
    with pytest.raises(ModelPackError, match="mode is inconsistent|changed during verification"):
        verify_model_pack(pack, entry=entry)


def test_license_text_and_native_download_authority_fail_closed(
    tmp_path: Path,
    pack_inputs: tuple[Path, Path, ModelEntry],
) -> None:
    source, licenses, entry = pack_inputs
    license_path = licenses / "MIT.txt"
    license_path.write_bytes(license_path.read_bytes() + b"\n")
    with pytest.raises(ModelPackError, match="exact reviewed text"):
        assemble_model_pack(
            source,
            tmp_path / "license-drift.pack",
            entry=entry,
            license_directory=licenses,
        )

    canonical_license = Path(__file__).resolve().parents[1] / "LICENSES/MIT.txt"
    license_path.write_bytes(canonical_license.read_bytes())
    with pytest.raises(ModelPackError, match="native download"):
        assemble_model_pack(
            source,
            tmp_path / "wrong-delivery.pack",
            entry=replace(entry, delivery="system-component"),
            license_directory=licenses,
        )
