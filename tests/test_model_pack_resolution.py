# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from local_transcript_max import models as models_module
from local_transcript_max.config import MODEL_MANIFEST, ModelEntry
from local_transcript_max.errors import ModelCacheError
from local_transcript_max.model_pack import assemble_model_pack
from local_transcript_max.models import (
    MODEL_PROVENANCE_FILENAME,
    candidate_model_paths,
    status_for,
    write_model_provenance,
)


@pytest.fixture
def resolution_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, Path, ModelEntry]:
    packs_root = tmp_path / "user-application-support/Models/Packs"
    packs_root.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(models_module, "INSTALLED_MODEL_PACKS_ROOT", packs_root)
    models_module._verify_installed_model_pack_once.cache_clear()

    source = tmp_path / "download-staging"
    source.mkdir()
    contents = {
        "config.json": b'{"model":"resolution-test"}\n',
        "model.bin": b"verified model resolution bytes\n",
        "tokenizer/vocabulary.json": b'{"test":1}\n',
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
        key="native-resolution",
        role="asr-mlx",
        model_id="example/model-native-resolution",
        required_files=tuple(contents),
        expected_sha256=tuple(expected_sha256),
        expected_size=tuple(expected_size),
        aliases=("native-resolution-alias",),
        revision="3" * 40,
        license_expression="MIT",
        source_url="https://example.invalid/model-native-resolution",
        terms_url="https://example.invalid/model-native-resolution/terms",
        attribution="Example Resolution Model Authors",
        acquisition_status="approved_connected_exact_revision",
        runtime_status="approved_local_verified",
        redistribution_status="permitted_with_attribution",
        review_status="verified_owner_2026-07-25",
        reviewed_at="2026-07-25",
        product_classification="optional",
        blocker=None,
        backend_compatibility="mlx-whisper local path",
        modification_status="unmodified",
        package_bundle_id="com.auralign.studio.modelpack.native-resolution.v1",
        package_bundle_version="1.0.0",
        package_relative_path="models/example--model-native-resolution",
        delivery="native-download",
    )
    licenses = tmp_path / "licenses"
    licenses.mkdir()
    canonical_license = Path(__file__).resolve().parents[1] / "LICENSES/MIT.txt"
    (licenses / "MIT.txt").write_bytes(canonical_license.read_bytes())
    yield packs_root, source, licenses, entry
    models_module._verify_installed_model_pack_once.cache_clear()


def _installed_pack_root(packs_root: Path, entry: ModelEntry) -> Path:
    assert entry.package_bundle_id is not None
    assert entry.package_bundle_version is not None
    return packs_root / entry.package_bundle_id / entry.package_bundle_version


def _installed_model_path(packs_root: Path, entry: ModelEntry) -> Path:
    assert entry.package_relative_path is not None
    return _installed_pack_root(packs_root, entry) / entry.package_relative_path


def _assemble_installed_pack(
    packs_root: Path,
    source: Path,
    licenses: Path,
    entry: ModelEntry,
    *,
    destination: Path | None = None,
) -> Path:
    pack_root = destination or _installed_pack_root(packs_root, entry)
    pack_root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    assemble_model_pack(
        source,
        pack_root,
        entry=entry,
        license_directory=licenses,
    )
    return pack_root


def _model_payload_files(source: Path, destination: Path, entry: ModelEntry) -> None:
    destination.mkdir(parents=True)
    for relative_path in entry.required_files:
        output = destination / relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes((source / relative_path).read_bytes())


def test_status_prefers_exact_verified_fixed_user_pack_and_audits_source(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
) -> None:
    packs_root, source, licenses, entry = resolution_inputs
    pack_root = _assemble_installed_pack(packs_root, source, licenses, entry)
    expected_model = _installed_model_path(packs_root, entry)
    private_cache = tmp_path / "private-cache"

    status = status_for(entry, private_cache)
    record = status.to_audit_record(private_cache)

    assert candidate_model_paths(entry, private_cache)[0] == expected_model
    assert status.path == expected_model
    assert status.present is True
    assert status.revision_verified is True
    assert status.provenance_problem.startswith(
        "installed native-download model pack verified:"
    )
    assert record["delivery"] == "native-download"
    assert record["provenance_type"] == "installed_native_download_model_pack"
    assert record["provenance_file"] == "model-pack-provenance.json"
    assert record["resolved_source"] == "fixed_user_model_pack"
    assert record["package_bundle_id"] == entry.package_bundle_id
    assert record["package_bundle_version"] == entry.package_bundle_version
    assert record["pack_id"] == entry.package_bundle_id
    assert record["pack_version"] == entry.package_bundle_version
    assert record["package_relative_path"] == entry.package_relative_path
    assert record["production_selectable"] is True
    assert record["product_classification"] == entry.product_classification
    assert pack_root in expected_model.parents
    assert not (expected_model / MODEL_PROVENANCE_FILENAME).exists()


@pytest.mark.parametrize(
    "identity_change",
    [
        {"package_bundle_id": "com.auralign.studio.modelpack.wrong.v1"},
        {"package_bundle_version": "9.9.9"},
    ],
)
def test_exact_location_rejects_pack_with_wrong_id_or_version(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
    identity_change: dict[str, object],
) -> None:
    packs_root, source, licenses, entry = resolution_inputs
    foreign_entry = replace(entry, **identity_change)
    destination = _installed_pack_root(packs_root, entry)
    _assemble_installed_pack(
        packs_root,
        source,
        licenses,
        foreign_entry,
        destination=destination,
    )

    status = status_for(entry, tmp_path / "private-cache")

    assert status.path == _installed_model_path(packs_root, entry)
    assert status.files_complete is True
    assert status.present is False
    assert status.revision_verified is False
    assert "failed deterministic verification" in status.provenance_problem


def test_pack_cache_invalidates_after_payload_hash_tamper(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
) -> None:
    packs_root, source, licenses, entry = resolution_inputs
    _assemble_installed_pack(packs_root, source, licenses, entry)
    private_cache = tmp_path / "private-cache"
    assert status_for(entry, private_cache).present is True

    model_file = _installed_model_path(packs_root, entry) / "model.bin"
    model_file.chmod(0o600)
    model_file.write_bytes(model_file.read_bytes() + b"tamper")
    model_file.chmod(0o400)

    status = status_for(entry, private_cache)

    assert status.present is False
    assert status.revision_verified is False
    assert "failed deterministic verification" in status.provenance_problem


def test_cached_pack_identity_cannot_survive_mid_lookup_mutation(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packs_root, source, licenses, entry = resolution_inputs
    pack_root = _assemble_installed_pack(packs_root, source, licenses, entry)
    model_path = _installed_model_path(packs_root, entry)
    assert models_module._installed_model_pack_status(entry, model_path)[0] is True

    model_file = model_path / "model.bin"
    original_fingerprint = models_module._model_bundle_tree_fingerprint(pack_root)
    original = models_module._model_bundle_tree_fingerprint
    mutated = False

    def mutate_after_initial_fingerprint(root: Path) -> str:
        nonlocal mutated
        if not mutated:
            mutated = True
            content = model_file.read_bytes()
            model_file.chmod(0o600)
            model_file.write_bytes(bytes([content[0] ^ 1]) + content[1:])
            model_file.chmod(0o400)
            return original_fingerprint
        return original(root)

    monkeypatch.setattr(
        models_module,
        "_model_bundle_tree_fingerprint",
        mutate_after_initial_fingerprint,
    )
    status = models_module._installed_model_pack_status(entry, model_path)

    assert status is not None
    assert status[0] is False
    assert "failed deterministic verification" in status[1]


@pytest.mark.parametrize("mutation", ["partial", "unexpected", "symlink"])
def test_pack_rejects_partial_unexpected_and_linked_content(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
    mutation: str,
) -> None:
    packs_root, source, licenses, entry = resolution_inputs
    pack_root = _assemble_installed_pack(packs_root, source, licenses, entry)
    model_file = _installed_model_path(packs_root, entry) / "model.bin"

    if mutation == "partial":
        model_file.parent.chmod(0o700)
        model_file.unlink()
        model_file.parent.chmod(0o500)
    elif mutation == "unexpected":
        pack_root.chmod(0o700)
        extra = pack_root / "unexpected.bin"
        extra.write_bytes(b"unexpected")
        extra.chmod(0o400)
        pack_root.chmod(0o500)
    else:
        outside = tmp_path / "outside.bin"
        outside.write_bytes(model_file.read_bytes())
        model_file.parent.chmod(0o700)
        model_file.unlink()
        try:
            model_file.symlink_to(outside)
        except OSError:
            pytest.skip("symbolic links unavailable")
        model_file.parent.chmod(0o500)

    status = status_for(entry, tmp_path / "private-cache")

    assert status.present is False
    assert status.revision_verified is False
    assert "failed deterministic verification" in status.provenance_problem


def test_pack_rejects_symlinked_fixed_namespace_ancestor(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
) -> None:
    packs_root, source, licenses, entry = resolution_inputs
    assert entry.package_bundle_id is not None
    assert entry.package_bundle_version is not None
    outside_id_root = tmp_path / "outside-pack-id"
    outside_id_root.mkdir(mode=0o700)
    _assemble_installed_pack(
        packs_root,
        source,
        licenses,
        entry,
        destination=outside_id_root / entry.package_bundle_version,
    )
    linked_id_root = packs_root / entry.package_bundle_id
    try:
        linked_id_root.symlink_to(outside_id_root, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links unavailable")

    status = status_for(entry, tmp_path / "private-cache")

    assert status.path == _installed_model_path(packs_root, entry)
    assert status.present is False
    assert status.revision_verified is False
    assert "failed deterministic verification" in status.provenance_problem


def test_pack_tree_outside_fixed_namespace_is_not_a_trusted_pack(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
) -> None:
    packs_root, source, licenses, entry = resolution_inputs
    lookalike_root = tmp_path / "arbitrary-lookalike-pack"
    _assemble_installed_pack(
        packs_root,
        source,
        licenses,
        entry,
        destination=lookalike_root,
    )
    assert entry.package_relative_path is not None
    arbitrary_cache = lookalike_root / "models"

    status = status_for(entry, arbitrary_cache)
    record = status.to_audit_record(arbitrary_cache)

    assert status.path == lookalike_root / entry.package_relative_path
    assert status.files_complete is True
    assert status.present is False
    assert MODEL_PROVENANCE_FILENAME in status.provenance_problem
    assert record["provenance_type"] == "private_acquisition_marker"
    assert record["resolved_source"] == "private_acquisition_cache"


def test_arbitrary_familiar_directory_requires_private_acquisition_marker(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
) -> None:
    _, source, _, entry = resolution_inputs
    private_cache = tmp_path / "private-cache"
    arbitrary = private_cache / entry.directory_name
    _model_payload_files(source, arbitrary, entry)

    untrusted = status_for(entry, private_cache)

    assert untrusted.path == arbitrary
    assert untrusted.files_complete is True
    assert untrusted.present is False
    assert MODEL_PROVENANCE_FILENAME in untrusted.provenance_problem

    write_model_provenance(entry, arbitrary)
    trusted = status_for(entry, private_cache)
    record = trusted.to_audit_record(private_cache)

    assert trusted.present is True
    assert trusted.path == arbitrary
    assert record["provenance_type"] == "private_acquisition_marker"
    assert record["resolved_source"] == "private_acquisition_cache"


def test_installed_product_rejects_legacy_private_cache_even_with_valid_marker(
    tmp_path: Path,
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packs_root, source, _, entry = resolution_inputs
    private_cache = tmp_path / "private-cache"
    arbitrary = private_cache / entry.directory_name
    _model_payload_files(source, arbitrary, entry)
    write_model_provenance(entry, arbitrary)
    monkeypatch.setenv(models_module.INSTALLED_PRODUCT_ENV, "1")
    monkeypatch.setenv(
        models_module.SYSTEM_MODEL_BUNDLE_ROOT_ENV,
        str(models_module.INSTALLED_MODEL_BUNDLE_ROOT),
    )
    monkeypatch.setenv(models_module.USER_MODEL_PACKS_ROOT_ENV, str(packs_root))

    expected_pack_model = _installed_model_path(packs_root, entry)
    assert candidate_model_paths(entry, private_cache) == [expected_pack_model]
    status = status_for(entry, private_cache)
    assert status.path == expected_pack_model
    assert status.present is False
    with pytest.raises(ModelCacheError, match="exact verified system component"):
        models_module.status_for_path(entry, arbitrary)


def test_installed_product_rejects_redirected_root_contract(
    resolution_inputs: tuple[Path, Path, Path, ModelEntry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    packs_root, _, _, entry = resolution_inputs
    monkeypatch.setenv(models_module.INSTALLED_PRODUCT_ENV, "1")
    monkeypatch.setenv(
        models_module.SYSTEM_MODEL_BUNDLE_ROOT_ENV,
        str(models_module.INSTALLED_MODEL_BUNDLE_ROOT),
    )
    monkeypatch.setenv(
        models_module.USER_MODEL_PACKS_ROOT_ENV,
        str(tmp_path / "redirected-packs-root"),
    )

    with pytest.raises(ModelCacheError, match="LTM_USER_MODEL_PACKS_ROOT"):
        candidate_model_paths(entry, packs_root)


def test_system_bootstrap_bundle_verification_remains_distinct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = MODEL_MANIFEST["en"]
    contents = {
        relative_path: f"system:{relative_path}\n".encode()
        for relative_path in authority.required_files
    }
    entry = replace(
        authority,
        expected_sha256=tuple(
            (path, hashlib.sha256(content).hexdigest())
            for path, content in contents.items()
        ),
        expected_size=tuple((path, len(content)) for path, content in contents.items()),
    )
    bundle_root = tmp_path / str(entry.package_bundle_id)
    assert entry.package_relative_path is not None
    model_path = bundle_root / entry.package_relative_path
    model_path.mkdir(parents=True)
    for relative_path, content in contents.items():
        output = model_path / relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    monkeypatch.setattr(models_module, "INSTALLED_MODEL_BUNDLE_ROOT", bundle_root)

    calls: list[tuple[str, str]] = []

    def verified(root: str, fingerprint: str) -> dict[str, Any]:
        calls.append((root, fingerprint))
        return {
            "state": "verified",
            "bundle_id": entry.package_bundle_id,
            "bundle_version": entry.package_bundle_version,
            "model_keys": [entry.key],
            "manifest_sha256": "a" * 64,
        }

    monkeypatch.setattr(models_module, "_verify_packaged_model_bundle_once", verified)
    unrelated_cache = tmp_path / "unrelated-user-cache"
    status = status_for(entry, unrelated_cache)
    record = status.to_audit_record(unrelated_cache)

    assert entry.delivery == "system-component"
    assert candidate_model_paths(entry, unrelated_cache)[0] == model_path
    assert status.path == model_path
    assert status.present is True
    assert calls
    assert record["provenance_type"] == "installed_model_bundle"
    assert record["resolved_source"] == "fixed_system_model_component"
