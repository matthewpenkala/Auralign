# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fail-closed source contracts for the standalone macOS application release."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging/macos"
BUILDER = ROOT / "scripts/build_macos_standalone.py"
REQUIREMENTS = ROOT / "requirements/macos-app-arm64-cpu-py311.txt"
INSTALLER_DESIGN = PACKAGING / "installer-design"
BUILDER_SPEC = importlib.util.spec_from_file_location(
    "build_macos_standalone_contract",
    BUILDER,
)
assert BUILDER_SPEC is not None and BUILDER_SPEC.loader is not None
BUILDER_MODULE = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(BUILDER_MODULE)
verify_payload_safety = BUILDER_MODULE.verify_payload_safety


def test_runtime_sources_are_exact_for_model_inclusive_successor() -> None:
    sources = tomllib.loads((PACKAGING / "runtime-sources.toml").read_text(encoding="utf-8"))

    assert sources["schema_version"] == "auralign-macos-runtime-sources.v1"
    assert sources["product_version"] == "0.3.1"
    assert sources["build_number"] == "1"
    assert sources["architecture"] == "arm64"
    assert sources["minimum_macos"] == "14.0"
    assert sources["python"] == {
        "project": "python-build-standalone",
        "version": "3.11.15",
        "release": "20260510",
        "source_revision": "f0ae464f527498d6c4b13f19977447d603cd5edd",
        "asset": ("cpython-3.11.15+20260510-aarch64-apple-darwin-install_only.tar.gz"),
        "url": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260510/cpython-3.11.15%2B20260510-aarch64-apple-darwin-"
            "install_only.tar.gz"
        ),
        "sha256": ("03bcedae9b19a48888d7dc8ba064f73f6efaaf2b13f6a8e1a1bcc062df13e855"),
        "metadata_asset": ("cpython-3.11.15+20260510-aarch64-apple-darwin-pgo+lto-full.tar.zst"),
        "metadata_url": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260510/cpython-3.11.15%2B20260510-aarch64-apple-darwin-"
            "pgo%2Blto-full.tar.zst"
        ),
        "metadata_sha256": ("0fdbc1a816bb04d0c8244c51ef623372f92870c6e8f7e6c603575976cbb0d1ea"),
        "build_options": "pgo+lto",
        "apple_sdk_deployment_target": "11.0",
        "project_license_expression": "MPL-2.0",
        "runtime_license_expressions": ["Python-2.0", "CNRI-Python"],
        "license_url": (
            "https://github.com/astral-sh/python-build-standalone/blob/"
            "f0ae464f527498d6c4b13f19977447d603cd5edd/LICENSE"
        ),
    }
    assert sources["ffmpeg"]["version"] == "7.1.5"
    assert sources["ffmpeg"]["signing_key_fingerprint"] == (
        "FCF986EA15E6E293A5644F10B4322F04D67658D8"
    )
    assert sources["ffmpeg"]["license_expression"] == "LGPL-2.1-or-later"
    flags = set(sources["ffmpeg"]["configure"])
    assert {
        "--enable-shared",
        "--disable-static",
        "--disable-network",
        "--disable-gpl",
        "--disable-nonfree",
        "--disable-version3",
    } <= flags
    assert not any(flag.startswith("--enable-gpl") for flag in flags)
    assert not any(flag.startswith("--enable-nonfree") for flag in flags)
    assert sources["pyav"]["version"] == "15.1.0"
    assert sources["pure_python_sdist_builds"] == [
        {
            "name": "antlr4-python3-runtime",
            "version": "4.9.3",
            "asset": "antlr4-python3-runtime-4.9.3.tar.gz",
            "sha256": ("f224469b4168294902bb1efa80a8bf7855f24c99aef99cbefc1bcd3cce77881b"),
        },
        {
            "name": "lightning",
            "version": "2.6.2",
            "asset": "d710d689510d50e800f53b3cd773cbca20b1f86f.tar.gz",
            "sha256": ("059f9272bf684f391f52c4e175a6a8e0d5cfa3ba4b3a0fde9aa4ff13dd72e11e"),
        },
    ]


def test_standalone_requirements_are_hash_locked_and_scope_limited() -> None:
    requirements = REQUIREMENTS.read_text(encoding="utf-8")

    assert "--hash=sha256:" in requirements
    assert "torch==" in requirements
    assert "torchcodec==" in requirements
    assert "whisperx==" in requirements
    assert "pyannote-audio==" in requirements
    assert "speechbrain==" in requirements
    assert "mlx==0.31.2" in requirements
    assert "mlx-metal==0.31.2" in requirements
    assert "mlx-whisper==0.4.3" in requirements
    for prohibited in (
        "apple-fm-sdk==",
        "build==",
        "fastapi==",
        "foundation-models-c",
        "libfoundationmodels",
        "pyinstaller==",
        "pyproject-hooks==",
        "uvicorn==",
    ):
        assert prohibited not in requirements.lower()
    assert "/Users/" not in requirements
    assert ".venv" not in requirements


def test_builder_is_transparent_scriptless_offline_and_release_gated() -> None:
    builder = BUILDER.read_text(encoding="utf-8")

    assert "python-build-standalone" in builder
    assert "PyInstaller" not in builder
    assert "require_clean_main()" in builder
    assert "refs/remotes/origin/main^{commit}" in builder
    assert "local main and fetched origin/main to match exactly" in builder
    assert '"--platform"' in builder
    assert '"macosx_14_0_arm64"' in builder
    assert '"--python-version"' in builder
    assert '"--implementation"' in builder
    assert '"--abi"' in builder
    assert '"--no-deps"' in builder
    assert "GITHUB_RELEASE_ASSET_MAX_BYTES = 2 * 1024**3" in builder
    assert "for license_name in MODEL_BUNDLE_LICENSE_FILENAMES:" in builder
    assert "model_notices_path.write_bytes(model_bundle_notices_bytes())" in builder
    assert "final product PKG exceeds GitHub's per-release-asset size limit" in builder
    assert '"-I"' in builder
    assert '"--prefix=/"' in builder
    assert '"--disable-network"' in builder
    assert "-headerpad_max_install_names" in builder
    assert "verify_ffmpeg_signature(" in builder
    assert "python-build-standalone-PYTHON.json" in builder
    assert "python-build-standalone-licenses" in builder
    assert "verify_payload_safety(runtime" in builder
    assert "MACHO_MAGICS" in builder
    assert "normalize_native_install_names(runtime, evidence)" in builder
    assert "auralign-native-install-names.v1" in builder
    assert '"original_install_name": originals[0]' in builder
    assert '"original_install_names": originals' in builder
    assert '"normalized_install_name": normalized' in builder
    assert "normalize_native_rpaths(runtime, evidence)" in builder
    assert "auralign-native-rpaths.v1" in builder
    assert '"removed_rpaths": invalid' in builder
    assert '"remaining_rpaths": remaining' in builder
    assert "prune_unbundled_native_backends(runtime, evidence)" in builder
    assert "auralign-native-backend-pruning.v1" in builder
    assert '"bundled_ffmpeg_major": 7' in builder
    assert "prune_unreachable_third_party_test_content(" in builder
    assert "auralign-unreachable-test-content-pruning.v3" in builder
    assert '"credential_sha256": hashlib.sha256(token).hexdigest()' in builder
    assert "'transformers.testing_utils' in sys.modules" in builder
    assert '"joblib.test"' in builder
    assert "prune_unbundled_model_assets(runtime, evidence)" in builder
    assert "auralign-unbundled-model-assets.v1" in builder
    assert '"model_weight_files_removed": len(weight_files)' in builder
    assert "verify_unbundled_model_assets_absent(runtime)" in builder
    assert "'mlx.core'" in builder
    assert "'mlx_whisper'" in builder
    assert "numba.njit(cache=False)" in builder
    assert "production Numba JIT smoke failed" in builder
    assert "whisperx.alignment" in builder
    assert "validate_notarization_log(" in builder
    assert "Apple notarization reported" in builder
    assert "strip_upstream_native_signatures(runtime, evidence)" in builder
    assert "auralign-upstream-signature-stripping.v1" in builder
    assert '"signatures_removed": len(rows)' in builder
    assert "sign_runtime_ad_hoc_for_verification(" in builder
    assert "auralign-diagnostic-adhoc-signing.v1" in builder
    assert '"final_distribution_signature": False' in builder
    assert "add_unique_loader_rpaths(runtime, evidence)" in builder
    assert "auralign-native-loader-rpaths.v1" in builder
    assert "thin_runtime_to_arm64(runtime, evidence)" in builder
    assert "auralign-native-thinning.v1" in builder
    assert '"original_architectures": architectures' in builder
    assert '"original_sha256": original_digest' in builder
    assert '"arm64_sha256": sha256(path)' in builder
    assert '"lib/python3.11/tkinter"' in builder
    assert '"/usr/bin/xattr"' in builder
    assert 'architectures == ["arm64"]' in builder
    assert "_resolve_dependency(" in builder
    assert "runtime_smoke(" in builder
    assert '"PYTHONDONTWRITEBYTECODE": "1"' in builder
    assert "write_embedded_python_launcher(" in builder
    assert "export PYTHONDONTWRITEBYTECODE=1" in builder
    assert ' -B -I "$@"' in builder
    assert '"Auralign-0.2.0-1-macos-arm64.cdx.json"' not in builder
    assert (
        builder.count(
            "f\"Auralign-{sources['product_version']}-{sources['build_number']}-macos-arm64"
        )
        == 4
    )
    assert builder.count('-macos-arm64.cdx.json"') == 3
    assert builder.count("prune_runtime(runtime)") == 4
    assert "install_hash_locked_pure_python_sdists(" in builder
    assert '"SOURCE_DATE_EPOCH"' in builder
    assert "pure-python-sdist-builds.json" in builder
    assert "verify_expanded_package(" in builder
    assert "verify_component_bom(" in builder
    assert '"lsbom", "-p", "fmug"' in builder
    assert "BOM and expanded Payload path set differ" in builder
    assert "model_inference_smoke(" in builder
    assert "auralign-packaged-bootstrap-inference-smoke.v1" in builder
    assert "AURALIGN_SMOKE_JSON=" in builder
    assert "signed-app-manifest.json" in builder
    assert "INSTALLER_RESOURCES_PATH" in builder
    assert '"--resources"' in builder
    assert "Developer ID Application identity is required" in builder
    assert "Developer ID Installer identity is required" in builder
    assert "notary Keychain profile is required" in builder
    assert "notarytool" in builder
    assert "stapler" in builder
    assert '"--cache-dir"' in builder
    assert '"--no-cache"' in builder
    assert "Developer ID release builds require --no-cache" in builder
    assert "DIAGNOSTIC_CACHE_SCHEMA_VERSION" in builder
    assert "NORMALIZATION_IMPLEMENTATION_VERSION" in builder
    assert "verified_cache_payload(" in builder
    assert "publish_cache_payload(" in builder
    assert "models_included" in builder
    assert "preinstall" not in builder
    assert "postinstall" not in builder
    assert '".venv"' in builder
    assert "pip install" not in builder


@pytest.mark.skipif(os.name == "nt", reason="the release launcher is POSIX-only")
def test_embedded_python_launcher_forces_both_bytecode_controls(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "runtime with spaces/bin/python3"
    executable.parent.mkdir(parents=True)
    executable.write_text(
        ("#!/bin/sh\nprintf '%s\\n' \"$PYTHONDONTWRITEBYTECODE\"\nprintf '%s\\n' \"$@\"\n"),
        encoding="utf-8",
    )
    executable.chmod(0o700)
    launcher = BUILDER_MODULE.write_embedded_python_launcher(
        executable,
        tmp_path / "launcher/python",
    )

    completed = subprocess.run(
        [str(launcher), "-c", "pass"],
        check=True,
        capture_output=True,
        env={},
        text=True,
    )

    assert completed.stdout.splitlines() == [
        "1",
        "-B",
        "-I",
        "-c",
        "pass",
    ]
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o700
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        BUILDER_MODULE.write_embedded_python_launcher(executable, launcher)


def _diagnostic_cache_inputs() -> dict[str, object]:
    return {
        "schema_version": BUILDER_MODULE.DIAGNOSTIC_CACHE_SCHEMA_VERSION,
        "stage": "runtime",
        "source_revision": "a" * 40,
        "source_tree_sha256": "b" * 64,
        "runtime_sources_sha256": "c" * 64,
        "requirements_sha256": "d" * 64,
        "builder_sha256": "e" * 64,
        "normalization_implementation_version": "fixture.v1",
        "toolchain": {
            "xcode": "fixture",
            "macos_sdk_version": "fixture",
        },
    }


def _publish_diagnostic_cache_fixture(
    root: Path,
) -> tuple[Path, Path, dict[str, object]]:
    cache = root / "cache"
    source = root / "source"
    source.mkdir(parents=True)
    (source / "payload.txt").write_text("verified\n", encoding="utf-8")
    key_inputs = _diagnostic_cache_inputs()
    published = BUILDER_MODULE.publish_cache_payload(
        cache,
        "runtime",
        key_inputs,
        {"fixture": source},
    )
    return cache, published, key_inputs


def test_diagnostic_cache_is_atomic_content_addressed_and_fail_closed(
    tmp_path: Path,
) -> None:
    cache, published, key_inputs = _publish_diagnostic_cache_fixture(tmp_path)
    assert (published / "fixture/payload.txt").read_text(encoding="utf-8") == "verified\n"
    key = BUILDER_MODULE.canonical_json_sha256(key_inputs)
    manifest = json.loads((cache / "runtime" / key / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["key"] == key
    assert manifest["key_inputs"] == key_inputs
    assert manifest["payload_inventory"] == BUILDER_MODULE.inventory_rows(
        cache / "runtime" / key / "payload"
    )
    assert not list((cache / "runtime").glob(f".{key}.*"))
    entry = cache / "runtime" / key
    assert stat.S_IMODE(entry.stat().st_mode) == 0o555
    assert stat.S_IMODE(published.stat().st_mode) == 0o555
    cached_file = published / "fixture/payload.txt"
    assert stat.S_IMODE(cached_file.stat().st_mode) == 0o444
    with pytest.raises(PermissionError):
        cached_file.write_text("tampered\n", encoding="utf-8")

    cached_file.chmod(0o644)
    cached_file.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="mutable or has an invalid mode"):
        BUILDER_MODULE.verified_cache_payload(cache, "runtime", key_inputs)


def test_diagnostic_cache_rejects_missing_malformed_or_incomplete_entries(
    tmp_path: Path,
) -> None:
    key_inputs = _diagnostic_cache_inputs()
    key = BUILDER_MODULE.canonical_json_sha256(key_inputs)

    missing_manifest = tmp_path / "missing/cache/runtime" / key
    (missing_manifest / "payload").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="incomplete cache manifest"):
        BUILDER_MODULE.verified_cache_payload(
            tmp_path / "missing/cache",
            "runtime",
            key_inputs,
        )

    malformed_manifest = tmp_path / "malformed/cache/runtime" / key
    (malformed_manifest / "payload").mkdir(parents=True)
    (malformed_manifest / "manifest.json").write_text("{", encoding="utf-8")
    (malformed_manifest / "payload").chmod(0o555)
    (malformed_manifest / "manifest.json").chmod(0o444)
    malformed_manifest.chmod(0o555)
    with pytest.raises(RuntimeError, match="malformed cache manifest"):
        BUILDER_MODULE.verified_cache_payload(
            tmp_path / "malformed/cache",
            "runtime",
            key_inputs,
        )

    non_object_manifest = tmp_path / "non-object/cache/runtime" / key
    (non_object_manifest / "payload").mkdir(parents=True)
    (non_object_manifest / "manifest.json").write_text("[]\n", encoding="utf-8")
    (non_object_manifest / "payload").chmod(0o555)
    (non_object_manifest / "manifest.json").chmod(0o444)
    non_object_manifest.chmod(0o555)
    with pytest.raises(RuntimeError, match="malformed cache manifest"):
        BUILDER_MODULE.verified_cache_payload(
            tmp_path / "non-object/cache",
            "runtime",
            key_inputs,
        )

    missing_payload = tmp_path / "missing-payload/cache/runtime" / key
    missing_payload.mkdir(parents=True)
    (missing_payload / "manifest.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="incomplete cache payload"):
        BUILDER_MODULE.verified_cache_payload(
            tmp_path / "missing-payload/cache",
            "runtime",
            key_inputs,
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("schema_version", "wrong-schema", "cache schema drift"),
        ("stage", "native", "cache stage drift"),
        ("key", "0" * 64, "cache key drift"),
        ("key_inputs", {"unexpected": True}, "cache input manifest drift"),
    ],
)
def test_diagnostic_cache_rejects_manifest_identity_drift(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    cache, published, key_inputs = _publish_diagnostic_cache_fixture(tmp_path / field)
    manifest_path = published.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = replacement
    manifest_path.chmod(0o644)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o444)

    with pytest.raises(RuntimeError, match=message):
        BUILDER_MODULE.verified_cache_payload(cache, "runtime", key_inputs)


@pytest.mark.parametrize(
    "field",
    [
        "source_revision",
        "source_tree_sha256",
        "runtime_sources_sha256",
        "toolchain",
    ],
)
def test_diagnostic_cache_input_mismatch_forces_cache_miss(
    tmp_path: Path,
    field: str,
) -> None:
    cache, _published, key_inputs = _publish_diagnostic_cache_fixture(tmp_path)
    changed = json.loads(json.dumps(key_inputs))
    changed[field] = {"changed": True} if field == "toolchain" else "f" * 64

    assert BUILDER_MODULE.verified_cache_payload(cache, "runtime", changed) is None


def test_diagnostic_cache_ignores_partial_stage_and_recomputes(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    key_inputs = _diagnostic_cache_inputs()
    key = BUILDER_MODULE.canonical_json_sha256(key_inputs)
    stage_root = cache / "runtime"
    partial = stage_root / f".{key}.interrupted"
    (partial / "payload").mkdir(parents=True)

    assert BUILDER_MODULE.verified_cache_payload(cache, "runtime", key_inputs) is None

    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("recomputed\n", encoding="utf-8")
    published = BUILDER_MODULE.publish_cache_payload(
        cache,
        "runtime",
        key_inputs,
        {"fixture": source},
    )

    assert (published / "fixture/payload.txt").read_text(encoding="utf-8") == ("recomputed\n")
    assert partial.is_dir()


def test_release_cli_rejects_omitted_no_cache_before_runtime_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "diagnostic-cache"
    work = tmp_path / "work"
    monkeypatch.setattr(BUILDER_MODULE, "require_host", lambda: None)
    monkeypatch.setattr(BUILDER_MODULE, "load_sources", lambda: {})
    monkeypatch.setattr(
        BUILDER_MODULE,
        "verify_packaging_contract",
        lambda _sources: None,
    )

    def unexpected_build(*_args: object, **_kwargs: object) -> Path:
        pytest.fail("release runtime build started without --no-cache")

    monkeypatch.setattr(BUILDER_MODULE, "build_runtime", unexpected_build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(BUILDER),
            "--work-dir",
            str(work),
            "--cache-dir",
            str(cache),
            "--model-cache",
            str(tmp_path / "models"),
        ],
    )

    with pytest.raises(
        RuntimeError,
        match="Developer ID release builds require --no-cache",
    ):
        BUILDER_MODULE.main()

    assert not work.exists()
    assert not cache.exists()


def test_release_cli_no_cache_bypasses_diagnostic_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopAfterCacheDecision(Exception):
        pass

    cache = tmp_path / "diagnostic-cache"
    work = tmp_path / "work"
    observed: dict[str, object] = {}
    monkeypatch.setattr(BUILDER_MODULE, "require_host", lambda: None)
    monkeypatch.setattr(
        BUILDER_MODULE,
        "require_clean_main",
        lambda: "f0ae464f527498d6c4b13f19977447d603cd5edd",
    )
    monkeypatch.setattr(BUILDER_MODULE, "load_sources", lambda: {})
    monkeypatch.setattr(
        BUILDER_MODULE,
        "verify_packaging_contract",
        lambda _sources: None,
    )
    model_identity = {
        "bundle_id": "com.auralign.studio.models.bootstrap.v2",
        "bundle_version": "2.0.0",
    }
    monkeypatch.setattr(
        BUILDER_MODULE,
        "assemble_model_bundle",
        lambda *_args, **_kwargs: model_identity,
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "verify_model_bundle",
        lambda _path: model_identity,
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "verify_payload_safety",
        lambda *_args, **_kwargs: None,
    )

    def stop_build(
        work_root: Path,
        sources: dict[str, object],
        *,
        allow_dirty: bool,
        cache_root: Path | None,
    ) -> Path:
        observed.update(
            {
                "work": work_root,
                "sources": sources,
                "allow_dirty": allow_dirty,
                "cache_root": cache_root,
            }
        )
        raise StopAfterCacheDecision

    monkeypatch.setattr(BUILDER_MODULE, "build_runtime", stop_build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(BUILDER),
            "--work-dir",
            str(work),
            "--cache-dir",
            str(cache),
            "--model-cache",
            str(tmp_path / "models"),
            "--no-cache",
        ],
    )

    with pytest.raises(StopAfterCacheDecision):
        BUILDER_MODULE.main()

    assert observed == {
        "work": work.resolve(),
        "sources": {},
        "allow_dirty": False,
        "cache_root": None,
    }
    assert not cache.exists()


def test_release_cli_rejects_dirty_source_before_creating_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = tmp_path / "authoritative-work"
    model_cache = tmp_path / "models"
    model_cache.mkdir()
    monkeypatch.setattr(
        BUILDER_MODULE,
        "require_clean_main",
        lambda: (_ for _ in ()).throw(RuntimeError("release requires a clean index")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(BUILDER),
            "--work-dir",
            str(work),
            "--model-cache",
            str(model_cache),
            "--no-cache",
        ],
    )

    with pytest.raises(RuntimeError, match="release requires a clean index"):
        BUILDER_MODULE.main()

    assert not work.exists()


def test_component_bom_requires_root_wheel_and_read_only_model_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = tmp_path / "models.pkg"
    component.mkdir()
    (component / "Bom").write_bytes(b"fixture")
    prefix = (
        "Library/Application Support/com.auralign.studio/Models/"
        "com.auralign.studio.models.bootstrap.v2"
    )
    payload_file = component / "Payload" / prefix / "models/model.bin"
    payload_file.parent.mkdir(parents=True)
    payload_file.write_bytes(b"model")
    listing = "\n".join(
        [
            ".\t40755\t0\t0",
            "./Library\t40755\t0\t0",
            "./Library/Application Support\t40755\t0\t0",
            "./Library/Application Support/com.auralign.studio\t40755\t0\t0",
            ("./Library/Application Support/com.auralign.studio/Models\t40755\t0\t0"),
            f"./{prefix}\t40555\t0\t0",
            f"./{prefix}/models\t40555\t0\t0",
            f"./{prefix}/models/model.bin\t100444\t0\t0",
        ]
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=listing + "\n",
            stderr="",
        ),
    )

    rows = BUILDER_MODULE.verify_component_bom(
        component,
        label="offline-model component",
        required_prefix=prefix,
        require_read_only=True,
    )

    assert rows[-1] == {
        "path": f"{prefix}/models/model.bin",
        "mode": "100444",
        "uid": "0",
        "gid": "0",
    }


def test_component_bom_rejects_payload_path_set_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = tmp_path / "models.pkg"
    component.mkdir()
    (component / "Bom").write_bytes(b"fixture")
    prefix = "Models/exact"
    payload_file = component / "Payload" / prefix / "model.bin"
    payload_file.parent.mkdir(parents=True)
    payload_file.write_bytes(b"model")
    listing = "\n".join(
        [
            ".\t40755\t0\t0",
            "./Models\t40755\t0\t0",
            f"./{prefix}\t40555\t0\t0",
        ]
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=listing + "\n",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="BOM and expanded Payload path set differ"):
        BUILDER_MODULE.verify_component_bom(
            component,
            label="offline-model component",
            required_prefix=prefix,
            require_read_only=True,
        )


@pytest.mark.parametrize(
    ("payload_row", "message"),
    [
        (
            "Library/Application Support/com.auralign.studio/Models/"
            "com.auralign.studio.models.bootstrap.v2\t40755\t0\t0",
            "mutable or irregular",
        ),
        (
            "Library/Application Support/com.auralign.studio/Models/"
            "com.auralign.studio.models.bootstrap.v2\t40555\t501\t20",
            "not root:wheel",
        ),
    ],
)
def test_component_bom_rejects_mutable_or_non_root_model_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload_row: str,
    message: str,
) -> None:
    component = tmp_path / "models.pkg"
    component.mkdir()
    (component / "Bom").write_bytes(b"fixture")
    prefix = (
        "Library/Application Support/com.auralign.studio/Models/"
        "com.auralign.studio.models.bootstrap.v2"
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f".\t40755\t0\t0\n{payload_row}\n",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match=message):
        BUILDER_MODULE.verify_component_bom(
            component,
            label="offline-model component",
            required_prefix=prefix,
            require_read_only=True,
        )


def test_component_bom_rejects_entries_outside_exact_component_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    component = tmp_path / "models.pkg"
    component.mkdir()
    (component / "Bom").write_bytes(b"fixture")
    prefix = (
        "Library/Application Support/com.auralign.studio/Models/"
        "com.auralign.studio.models.bootstrap.v2"
    )
    listing = "\n".join(
        [
            ".\t40755\t0\t0",
            "./Library\t40755\t0\t0",
            f"./{prefix}\t40555\t0\t0",
            "./Library/unreviewed.txt\t100444\t0\t0",
        ]
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=listing + "\n",
            stderr="",
        ),
    )

    with pytest.raises(RuntimeError, match="outside its exact payload"):
        BUILDER_MODULE.verify_component_bom(
            component,
            label="offline-model component",
            required_prefix=prefix,
            require_read_only=True,
        )


def test_expanded_component_payload_layout_rejects_sibling_content(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "Payload"
    required = payload / "Applications/Auralign.app"
    required.mkdir(parents=True)
    (required / "Contents").mkdir()

    BUILDER_MODULE.verify_component_payload_layout(
        payload,
        label="application component",
        required_prefix="Applications/Auralign.app",
    )

    unexpected = payload / "Applications/unreviewed.txt"
    unexpected.write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unexpected path"):
        BUILDER_MODULE.verify_component_payload_layout(
            payload,
            label="application component",
            required_prefix="Applications/Auralign.app",
        )


def test_expanded_component_payload_layout_allows_only_exact_internal_symlinks(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "Payload"
    required = payload / "Applications/Auralign.app"
    runtime_bin = required / "Contents/Runtime/bin"
    runtime_bin.mkdir(parents=True)
    (runtime_bin / "python3.11").write_bytes(b"python")
    alias = runtime_bin / "python3"
    alias.symlink_to("python3.11")
    alias_relative = alias.relative_to(payload).as_posix()
    contract = {alias_relative: "python3.11"}

    BUILDER_MODULE.verify_component_payload_layout(
        payload,
        label="application component",
        required_prefix="Applications/Auralign.app",
        allowed_symlinks=contract,
    )

    with pytest.raises(RuntimeError, match="unapproved symlink"):
        BUILDER_MODULE.verify_component_payload_layout(
            payload,
            label="application component",
            required_prefix="Applications/Auralign.app",
        )

    alias.unlink()
    alias.symlink_to("../../../../outside")
    with pytest.raises(RuntimeError, match="unapproved symlink"):
        BUILDER_MODULE.verify_component_payload_layout(
            payload,
            label="application component",
            required_prefix="Applications/Auralign.app",
            allowed_symlinks=contract,
        )

    outside = payload / "outside"
    outside.write_bytes(b"outside")
    escaping_target = "../../../../../outside"
    alias.unlink()
    alias.symlink_to(escaping_target)
    with pytest.raises(RuntimeError, match="escapes or is irregular"):
        BUILDER_MODULE.verify_component_payload_layout(
            payload,
            label="application component",
            required_prefix="Applications/Auralign.app",
            allowed_symlinks={alias_relative: escaping_target},
        )


def test_expanded_component_payload_layout_rejects_omitted_approved_symlink(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "Payload"
    required = payload / "Applications/Auralign.app"
    required.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="omits an approved symlink"):
        BUILDER_MODULE.verify_component_payload_layout(
            payload,
            label="application component",
            required_prefix="Applications/Auralign.app",
            allowed_symlinks={
                "Applications/Auralign.app/Contents/Runtime/bin/python3": "python3.11"
            },
        )


def test_installable_payload_modes_are_normalized_and_fail_closed(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    private_directory = payload / "private"
    private_directory.mkdir(parents=True)
    private_file = private_directory / "metadata.json"
    executable = payload / "bin/tool"
    executable.parent.mkdir()
    private_file.write_text("{}\n", encoding="utf-8")
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    payload.chmod(0o700)
    private_directory.chmod(0o700)
    private_file.chmod(0o640)
    executable.chmod(0o700)
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    with pytest.raises(RuntimeError, match="non-installable payload modes"):
        BUILDER_MODULE.verify_installable_payload_modes(payload)

    result = BUILDER_MODULE.normalize_installable_payload_modes(payload, evidence)

    assert stat.S_IMODE(payload.stat().st_mode) == 0o755
    assert stat.S_IMODE(private_directory.stat().st_mode) == 0o755
    assert stat.S_IMODE(private_file.stat().st_mode) == 0o644
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755
    assert result["schema_version"] == "auralign-installable-payload-modes.v1"
    assert {row["path"] for row in result["changes"]} == {
        ".",
        "bin/tool",
        "private",
        "private/metadata.json",
    }
    BUILDER_MODULE.verify_installable_payload_modes(payload)

    private_file.chmod(0o646)
    with pytest.raises(RuntimeError, match=r"private/metadata\.json"):
        BUILDER_MODULE.verify_installable_payload_modes(payload)


def test_exact_download_cache_reuse_never_contacts_source(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    expected = b"immutable source archive\n"
    digest = hashlib.sha256(expected).hexdigest()
    url = "https://invalid.example.invalid/source.tar"
    source = tmp_path / "source"
    source.write_bytes(expected)
    key_inputs = {
        "schema_version": BUILDER_MODULE.DIAGNOSTIC_CACHE_SCHEMA_VERSION,
        "stage": "downloads",
        "url": url,
        "expected_sha256": digest,
    }
    BUILDER_MODULE.publish_cache_payload(
        cache,
        "downloads",
        key_inputs,
        {"source": source},
    )

    destination = tmp_path / "work/source.tar"
    observed = BUILDER_MODULE.download(
        url,
        digest,
        destination,
        cache_root=cache,
    )
    assert observed.read_bytes() == expected
    assert source.read_bytes() == expected


def test_runtime_rpaths_allow_only_loader_relative_or_apple_system_paths() -> None:
    for allowed in (
        "@loader_path",
        "@loader_path/../lib",
        "@executable_path",
        "@executable_path/../Frameworks",
        "/usr/lib/swift",
        "/usr/lib/swift/subdirectory",
    ):
        assert BUILDER_MODULE.allowed_runtime_rpath(allowed)
    for rejected in (
        "/usr/lib",
        "/usr/lib/swift-evil",
        "/usr/lib/swift/../../" + "Users/developer/lib",
        "/System/Library/Frameworks",
        "/opt/homebrew/lib",
        "/usr/local/lib",
        "/" + "Users/developer/project/lib",
        "/private/tmp/build/lib",
        "@rpath",
        "relative/lib",
    ):
        assert not BUILDER_MODULE.allowed_runtime_rpath(rejected)


def test_rpath_dependency_resolves_only_to_unique_payload_or_swift_runtime(
    tmp_path: Path,
) -> None:
    owner = tmp_path / "owner"
    rpaths = {owner: ["/usr/lib/swift"]}
    assert BUILDER_MODULE._resolve_dependency(
        tmp_path,
        owner,
        "@rpath/libswiftFutureRuntime.dylib",
        executables=[],
        rpaths_by_owner=rpaths,
    ) == Path("/usr/lib/swift/libswiftFutureRuntime.dylib")
    with pytest.raises(RuntimeError, match="unresolved or ambiguous"):
        BUILDER_MODULE._resolve_dependency(
            tmp_path,
            owner,
            "@rpath/libavdevice.61.dylib",
            executables=[],
            rpaths_by_owner=rpaths,
        )

    for rejected_rpath in (
        "/usr/lib/swift-evil",
        "/usr/lib/swift/../../" + "Users/developer/lib",
        "/opt/homebrew/lib",
        "/" + "Users/developer/project/lib",
    ):
        with pytest.raises(RuntimeError, match="unresolved or ambiguous"):
            BUILDER_MODULE._resolve_dependency(
                tmp_path,
                owner,
                "@rpath/libswiftCore.dylib",
                executables=[],
                rpaths_by_owner={owner: [rejected_rpath]},
            )
    with pytest.raises(RuntimeError, match="unsafe @rpath dependency suffix"):
        BUILDER_MODULE._resolve_dependency(
            tmp_path,
            owner,
            "@rpath/../../" + "Users/developer/libInjected.dylib",
            executables=[],
            rpaths_by_owner=rpaths,
        )


def test_payload_safety_rejects_local_paths_not_upstream_build_metadata(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "upstream-build-metadata.bin"
    candidate.write_bytes(
        b"/private/var/folders/upstream/T/build/Python/Modules/getbuildinfo.c\x00"
        + b"/"
        + b"Users/runner/work/project/source.c\x00"
        b"Asterisk-linking-protocols-exception\x00"
        b"base64prefixaAKIAAQAAAAAABAAHAM0A\x00"
    )
    verify_payload_safety(candidate, (ROOT,))

    candidate.write_bytes(str(Path(tempfile.gettempdir()).resolve()).encode())
    with pytest.raises(RuntimeError, match="private build path leaked"):
        verify_payload_safety(candidate, (ROOT,))

    candidate.write_bytes(b"OPENAI_API_KEY=sk-" + (b"A" * 32))
    with pytest.raises(RuntimeError, match="token-shaped secret leaked"):
        verify_payload_safety(candidate, (ROOT,))

    candidate.write_bytes(b"AWS_ACCESS_KEY_ID=AKIA" + (b"A" * 16))
    with pytest.raises(RuntimeError, match="token-shaped secret leaked"):
        verify_payload_safety(candidate, (ROOT,))


def test_unreachable_third_party_test_content_is_pruned(
    tmp_path: Path,
) -> None:
    transformers_relative = Path("lib/python3.11/site-packages/transformers/testing_utils.py")
    transformers_candidate = tmp_path / transformers_relative
    transformers_candidate.parent.mkdir(parents=True)
    credential = b"hf_" + (b"A" * 32)
    transformers_candidate.write_bytes(b'TOKEN = "' + credential + b'"\n')
    joblib_relative = Path("lib/python3.11/site-packages/joblib/test")
    joblib_candidate = tmp_path / joblib_relative
    (joblib_candidate / "data").mkdir(parents=True)
    fixture = b"notarization-fixture"
    (joblib_candidate / "__init__.py").write_bytes(b"")
    (joblib_candidate / "data/legacy.gz").write_bytes(fixture)
    networkx_atlas = tmp_path / "lib/python3.11/site-packages/networkx/generators/atlas.dat.gz"
    networkx_atlas.parent.mkdir(parents=True)
    networkx_atlas.write_bytes(b"sample-atlas")
    pyannote_sample = tmp_path / "lib/python3.11/site-packages/pyannote/audio/sample"
    pyannote_sample.mkdir(parents=True)
    (pyannote_sample / "sample.wav").write_bytes(b"fixture-media")
    for relative in BUILDER_MODULE.REVIEWED_DEVELOPMENT_CONTENT_DIRECTORIES:
        directory = tmp_path / relative
        directory.mkdir(parents=True)
        (directory / "development-only.py").write_bytes(b"fixture")
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    with pytest.raises(RuntimeError, match="reviewed test-content root count drift"):
        BUILDER_MODULE.prune_unreachable_third_party_test_content(
            tmp_path,
            evidence,
        )

    result = BUILDER_MODULE.prune_unreachable_third_party_test_content(
        tmp_path,
        evidence,
        enforce_reviewed_contract=False,
    )

    assert result["directories_removed"] == 19
    assert result["files_removed"] == 22
    assert not transformers_candidate.exists()
    assert not joblib_candidate.exists()
    assert not networkx_atlas.exists()
    assert not pyannote_sample.exists()
    for relative in BUILDER_MODULE.REVIEWED_DEVELOPMENT_CONTENT_DIRECTORIES:
        assert not (tmp_path / relative).exists()
    recorded = (evidence / "unreachable-test-content-pruning.json").read_bytes()
    assert credential not in recorded
    assert hashlib.sha256(credential).hexdigest().encode() in recorded
    assert hashlib.sha256(fixture).hexdigest().encode() in recorded
    payload = json.loads(recorded)
    assert payload["schema_version"] == "auralign-unreachable-test-content-pruning.v3"
    assert payload["source_wheels_archives_and_repository_untouched"] is True
    assert all(row["result"] == "absent" for row in payload["removed"])


def test_unbundled_model_assets_are_inventory_bound_and_pruned(
    tmp_path: Path,
) -> None:
    assert BUILDER_MODULE.APPROVED_RUNTIME_DATA[
        "lib/python3.11/site-packages/mlx_whisper/assets/mel_filters.npz"
    ] == {
        "bytes": 4_271,
        "sha256": "7450ae70723a5ef9d341e3cee628c7cb0177f36ce42c44b7ed2bf3325f0f6d4c",
        "classification": "MLX Whisper mel-filter numerical data",
    }
    site_packages = tmp_path / "lib/python3.11/site-packages"
    asset_payloads = {
        "faster_whisper/assets/silero_vad_v6.onnx": b"silero",
        "onnxruntime/datasets/__init__.py": b"",
        "onnxruntime/datasets/logreg_iris.onnx": b"iris",
        "onnxruntime/datasets/mul_1.onnx": b"mul",
        "onnxruntime/datasets/sigmoid.onnx": b"sigmoid",
        "torchmetrics/functional/image/dists_models/weights.pt": b"dists",
        "torchmetrics/functional/image/lpips_models/alex.pth": b"alex",
        "torchmetrics/functional/image/lpips_models/squeeze.pth": b"squeeze",
        "torchmetrics/functional/image/lpips_models/vgg.pth": b"vgg",
        "whisperx/assets/pytorch_model.bin": b"whisperx-vad",
    }
    for relative, payload in asset_payloads.items():
        destination = site_packages / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    retained = {
        site_packages / "whisperx/assets/mel_filters.npz",
        site_packages / "mlx_whisper/assets/mel_filters.npz",
    }
    for path in retained:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mel-filter-data")
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    result = BUILDER_MODULE.prune_unbundled_model_assets(
        tmp_path,
        evidence,
        enforce_retained_data_contract=False,
    )

    assert result["files_removed"] == 10
    assert result["model_weight_files_removed"] == 9
    assert all(path.is_file() for path in retained)
    assert all(not (site_packages / relative).exists() for relative in asset_payloads)
    payload = json.loads((evidence / "unbundled-model-assets.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "auralign-unbundled-model-assets.v1"
    assert payload["source_wheels_archives_and_repository_untouched"] is True
    assert payload["model_weight_files"] == 9
    assert payload["result"] == "absent"
    assert {row["path"]: row["sha256"] for row in payload["files"]} == {
        (Path("lib/python3.11/site-packages") / relative).as_posix(): hashlib.sha256(
            original
        ).hexdigest()
        for relative, original in asset_payloads.items()
    }


def test_unbundled_model_asset_verification_rejects_unreviewed_weight(
    tmp_path: Path,
) -> None:
    unexpected = tmp_path / "lib/python3.11/site-packages/unreviewed/assets/unexpected-model.onnx"
    unexpected.parent.mkdir(parents=True)
    unexpected.write_bytes(b"weight")

    with pytest.raises(RuntimeError, match="bundled model-weight paths remain"):
        BUILDER_MODULE.verify_unbundled_model_assets_absent(tmp_path)


def test_notarization_log_must_be_accepted_and_issue_free() -> None:
    BUILDER_MODULE.validate_notarization_log(
        {
            "status": "Accepted",
            "issues": [],
        }
    )
    BUILDER_MODULE.validate_notarization_log(
        {
            "status": "Accepted",
            "issues": None,
        }
    )
    with pytest.raises(RuntimeError, match="does not report Accepted"):
        BUILDER_MODULE.validate_notarization_log(
            {
                "status": "Invalid",
                "issues": [],
            }
        )
    with pytest.raises(RuntimeError, match="issue list is missing"):
        BUILDER_MODULE.validate_notarization_log({"status": "Accepted"})
    with pytest.raises(RuntimeError, match="reported 1 warning or error"):
        BUILDER_MODULE.validate_notarization_log(
            {
                "status": "Accepted",
                "issues": [{"severity": "warning"}],
            }
        )


@pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64",
    reason="requires Apple Silicon and the macOS native toolchain",
)
def test_universal_macho_bundle_is_not_misread_as_a_dylib(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    source = tmp_path / "fixture.c"
    source.write_text("int fixture(void) { return 7; }\n", encoding="utf-8")
    slices: list[Path] = []
    for architecture in ("arm64", "x86_64"):
        destination = tmp_path / f"fixture-{architecture}.so"
        subprocess.run(
            [
                "xcrun",
                "clang",
                "-arch",
                architecture,
                "-mmacosx-version-min=14.0",
                "-bundle",
                str(source),
                "-o",
                str(destination),
            ],
            check=True,
        )
        slices.append(destination)
    bundle = root / "fixture-extension.so"
    subprocess.run(
        [
            "lipo",
            "-create",
            *(str(path) for path in slices),
            "-output",
            str(bundle),
        ],
        check=True,
    )
    original_digest = BUILDER_MODULE.sha256(bundle)

    assert BUILDER_MODULE.install_name(bundle) is None
    result = BUILDER_MODULE.normalize_native_install_names(root, evidence)

    assert result["objects_scanned"] == 1
    assert result["objects_normalized"] == 0
    assert BUILDER_MODULE.sha256(bundle) == original_digest
    assert (
        subprocess.run(
            ["lipo", "-archs", str(bundle)],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        == "x86_64 arm64"
    )


def test_app_signing_seals_nested_code_before_the_outer_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = tmp_path / "Auralign.app"
    main = app / "Contents/MacOS/Auralign"
    nested = (
        app
        / "Contents/Frameworks/AuralignEngineRuntime.bundle"
        / "Contents/Resources/Runtime/lib/native.dylib"
    )
    runtime_python = (
        app
        / "Contents/Frameworks/AuralignEngineRuntime.bundle"
        / "Contents/Resources/Runtime/bin/python3.11"
    )
    runtime_python.parent.mkdir(parents=True)
    runtime_python.touch()
    runtime_python.with_name("python3").symlink_to("python3.11")
    runtime_bundle = BUILDER_MODULE.app_runtime_bundle(app)
    nested.parent.mkdir(parents=True)
    main.parent.mkdir(parents=True)
    main.touch()
    nested.touch()
    subprocess_commands: list[list[str]] = []
    run_commands: list[list[str]] = []

    monkeypatch.setattr(
        BUILDER_MODULE,
        "macho_files",
        lambda _root: [main, nested, runtime_python],
    )

    def fake_subprocess_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        subprocess_commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        run_commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(BUILDER_MODULE.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(BUILDER_MODULE, "run", fake_run)
    monkeypatch.setattr(
        BUILDER_MODULE,
        "verify_app_signatures",
        lambda *_args, **_kwargs: {"Identifier": "com.auralign.studio"},
    )

    identity = "Developer ID Application: Fixture (TEAMID)"
    result = BUILDER_MODULE.sign_app(
        app,
        identity,
        structure_only=False,
        expected_team="TEAMID",
    )

    assert result == {"Identifier": "com.auralign.studio"}
    assert len(subprocess_commands) == 2
    nested_command = next(command for command in subprocess_commands if command[-1] == str(nested))
    python_command = next(
        command for command in subprocess_commands if command[-1] == str(runtime_python)
    )
    assert "--entitlements" not in nested_command
    assert "--entitlements" in python_command
    assert python_command[python_command.index("--entitlements") + 1] == str(
        PACKAGING / "AuralignEnginePython.entitlements"
    )
    assert all(str(main) not in command for command in subprocess_commands)
    assert run_commands[0][-1] == str(runtime_bundle)
    assert run_commands[1][-1] == str(app)
    assert run_commands[1].count(identity) == 1
    assert "--entitlements" not in run_commands[0]
    assert "--entitlements" in run_commands[1]


@pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64",
    reason="requires Apple Silicon and the macOS native toolchain",
)
def test_copied_signed_universal_stage_is_normalized_and_resigned(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "bin").mkdir()
    (source / "lib").mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    c_source = tmp_path / "fixture.c"
    c_source.write_text("int fixture(void) { return 7; }\n", encoding="utf-8")
    main_source = tmp_path / "main.c"
    main_source.write_text(
        "int fixture(void); int main(void) { return fixture() == 7 ? 0 : 1; }\n",
        encoding="utf-8",
    )
    slices: list[Path] = []
    for architecture in ("arm64", "x86_64"):
        destination = tmp_path / f"libfixture-{architecture}.dylib"
        subprocess.run(
            [
                "xcrun",
                "clang",
                "-arch",
                architecture,
                "-mmacosx-version-min=14.0",
                "-dynamiclib",
                "-Wl,-headerpad_max_install_names",
                (f"-Wl,-install_name,/tmp/upstream/{architecture}/libfixture.dylib"),
                "-Wl,-rpath,/tmp/upstream",
                str(c_source),
                "-o",
                str(destination),
            ],
            check=True,
        )
        slices.append(destination)
    subprocess.run(
        [
            "lipo",
            "-create",
            *(str(path) for path in slices),
            "-output",
            str(source / "lib/libfixture.dylib"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "xcrun",
            "clang",
            "-arch",
            "arm64",
            "-mmacosx-version-min=14.0",
            str(main_source),
            "-L",
            str(source / "lib"),
            "-lfixture",
            "-o",
            str(source / "bin/fixture"),
        ],
        check=True,
    )
    for path in (source / "lib/libfixture.dylib", source / "bin/fixture"):
        subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True)

    mutation_probe = tmp_path / "install-name-mutation-probe"
    shutil.copytree(source, mutation_probe)
    BUILDER_MODULE.normalize_native_install_names(mutation_probe, evidence)
    invalidated = subprocess.run(
        [
            "codesign",
            "--verify",
            "--strict",
            str(mutation_probe / "lib/libfixture.dylib"),
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    assert invalidated.returncode != 0
    assert (
        subprocess.run(
            ["lipo", "-archs", str(mutation_probe / "lib/libfixture.dylib")],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        == "x86_64 arm64"
    )

    stage = tmp_path / "copied-stage"
    shutil.copytree(source, stage)
    original_digest = BUILDER_MODULE.sha256(source / "lib/libfixture.dylib")
    BUILDER_MODULE.strip_upstream_native_signatures(stage, evidence)
    BUILDER_MODULE.normalize_native_install_names(stage, evidence)
    BUILDER_MODULE.normalize_native_rpaths(stage, evidence)
    subprocess.run(
        [
            "install_name_tool",
            "-change",
            "/tmp/upstream/arm64/libfixture.dylib",
            "@loader_path/../lib/libfixture.dylib",
            str(stage / "bin/fixture"),
        ],
        check=True,
    )
    thinning = BUILDER_MODULE.thin_runtime_to_arm64(stage, evidence)
    BUILDER_MODULE.sign_runtime_ad_hoc_for_verification(stage, evidence)
    BUILDER_MODULE.verify_native_closure(stage)

    assert thinning["objects_thinned"] == 1
    assert (
        subprocess.run(
            ["lipo", "-archs", str(stage / "lib/libfixture.dylib")],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        == "arm64"
    )
    assert BUILDER_MODULE.install_name(stage / "lib/libfixture.dylib") == "@rpath/libfixture.dylib"
    assert BUILDER_MODULE.rpaths(stage / "lib/libfixture.dylib") == []
    assert BUILDER_MODULE.sha256(source / "lib/libfixture.dylib") == original_digest
    subprocess.run(
        ["codesign", "--verify", "--strict", str(stage / "lib/libfixture.dylib")],
        check=True,
    )
    subprocess.run([str(stage / "bin/fixture")], check=True)


def test_package_definition_is_scriptless_system_only_arm64_macos14() -> None:
    root = ET.fromstring((PACKAGING / "Distribution.xml").read_text(encoding="utf-8"))
    domains = root.find("domains")
    options = root.find("options")
    allowed = root.find("allowed-os-versions/os-version")
    packages = {package.attrib["id"]: package for package in root.findall("pkg-ref")}
    assert domains is not None
    assert domains.attrib == {
        "enable_localSystem": "true",
        "enable_currentUserHome": "false",
        "enable_anywhere": "false",
    }
    assert options is not None
    assert options.attrib["require-scripts"] == "false"
    assert options.attrib["hostArchitectures"] == "arm64"
    assert allowed is not None and allowed.attrib["min"] == "14.0"
    assert set(packages) == {
        "com.auralign.studio.pkg.app",
        "com.auralign.studio.pkg.models.bootstrap-v2",
    }
    assert packages["com.auralign.studio.pkg.app"].attrib["version"] == "0.3.1.1"
    assert (packages["com.auralign.studio.pkg.app"].text or "").strip() == (
        "Auralign-application-component.pkg"
    )
    assert packages["com.auralign.studio.pkg.models.bootstrap-v2"].attrib["version"] == "2.0.0"
    assert (
        packages["com.auralign.studio.pkg.models.bootstrap-v2"].text or ""
    ).strip() == "Auralign-offline-models-component.pkg"
    background = root.find("background")
    dark_background = root.find("background-darkAqua")
    welcome = root.find("welcome")
    readme = root.find("readme")
    license_resource = root.find("license")
    conclusion = root.find("conclusion")
    assert background is not None and background.attrib["file"] == "background.png"
    assert dark_background is not None
    assert dark_background.attrib["file"] == "background-darkAqua.png"
    assert welcome is not None and welcome.attrib["file"] == "welcome.html"
    assert readme is not None and readme.attrib["file"] == "readme.html"
    assert license_resource is not None and license_resource.attrib["file"] == "license.html"
    assert conclusion is not None and conclusion.attrib["file"] == "conclusion.html"

    with (PACKAGING / "component.plist").open("rb") as handle:
        component = plistlib.load(handle)
    assert component == [
        {
            "BundleHasStrictIdentifier": True,
            "BundleIsRelocatable": False,
            "BundleIsVersionChecked": True,
            "BundleOverwriteAction": "upgrade",
            "RootRelativeBundlePath": "Applications/Auralign.app",
        }
    ]
    with (PACKAGING / "Auralign.entitlements").open("rb") as handle:
        assert plistlib.load(handle) == {}
    with (PACKAGING / "AuralignEnginePython.entitlements").open("rb") as handle:
        assert plistlib.load(handle) == {
            "com.apple.security.cs.allow-unsigned-executable-memory": True,
        }

    builder = (ROOT / "scripts/build_macos_standalone.py").read_text(encoding="utf-8")
    assert 'component / "Scripts"' in builder
    assert 'rglob("Scripts")' not in builder
    assert "Library/Application Support/Auralign/Models" not in builder
    assert '"Library/Application Support/com.auralign.studio/Models"' in builder


def test_final_product_package_uses_exact_component_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "Auralign-0.3.1-1-macos-arm64.pkg"
    package.write_bytes(b"product package fixture")
    signed_app = tmp_path / "signed/Auralign.app"
    model_bundle = tmp_path / "model-bundle"
    expanded_app = tmp_path / "expanded/Auralign.app"
    expanded_model_bundle = tmp_path / "expanded/model-bundle"
    for directory in (
        signed_app,
        model_bundle,
        expanded_app,
        expanded_model_bundle,
        tmp_path / "evidence",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        stdout = (
            "Status: signed by a certificate trusted by macOS\n"
            "Developer ID Installer: Auralign (TEAM123)\n"
            if command[:2] == ["pkgutil", "--check-signature"]
            else ""
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    expansion_calls: list[tuple[Path, Path]] = []

    def fake_verify_expanded_package(
        observed_package: Path,
        destination: Path,
        **_kwargs: object,
    ) -> tuple[Path, Path]:
        expansion_calls.append((observed_package, destination))
        return expanded_app, expanded_model_bundle

    monkeypatch.setattr(BUILDER_MODULE, "run", fake_run)
    monkeypatch.setattr(
        BUILDER_MODULE,
        "verify_expanded_package",
        fake_verify_expanded_package,
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "verify_app_signatures",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "runtime_smoke",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "model_runtime_smoke",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        BUILDER_MODULE,
        "model_inference_smoke",
        lambda *_args, **_kwargs: {"ok": True},
    )

    BUILDER_MODULE.verify_final_package(
        package,
        app=signed_app,
        model_bundle=model_bundle,
        sources={"product_version": "0.3.1", "build_number": "1"},
        expected_team="TEAM123",
        structure_only=False,
    )

    assert expansion_calls == [
        (package, package.parent / "expanded-final-pkg"),
    ]
    assert not any(command[:2] == ["pkgutil", "--payload-files"] for command in commands)
    assert ["pkgutil", "--check-signature", str(package)] in commands
    assert [
        "spctl",
        "--assess",
        "--type",
        "install",
        "--verbose=4",
        str(package),
    ] in commands
    assert ["xcrun", "stapler", "validate", "-v", str(package)] in commands


def test_distribution_contract_accepts_only_reviewed_productbuild_normalization(
    tmp_path: Path,
) -> None:
    sources = BUILDER_MODULE.load_sources()
    source = PACKAGING / "Distribution.xml"
    BUILDER_MODULE.verify_distribution_contract(
        source,
        sources=sources,
        productbuild_normalized=False,
    )

    tree = ET.parse(source)
    root = tree.getroot()
    for package in root.findall("pkg-ref"):
        package.attrib["installKBytes"] = "123"
        package.attrib["updateKBytes"] = "456"
        package.text = f"#{(package.text or '').strip()}"
    for identifier in (
        BUILDER_MODULE.APP_RECEIPT_IDENTIFIER,
        BUILDER_MODULE.MODEL_RECEIPT_IDENTIFIER,
    ):
        generated = ET.SubElement(root, "pkg-ref", {"id": identifier})
        bundle_version = ET.SubElement(generated, "bundle-version")
        if identifier == BUILDER_MODULE.APP_RECEIPT_IDENTIFIER:
            ET.SubElement(
                bundle_version,
                "bundle",
                {
                    "CFBundleShortVersionString": sources["product_version"],
                    "CFBundleVersion": sources["build_number"],
                    "id": BUILDER_MODULE.APP_IDENTIFIER,
                    "path": "Applications/Auralign.app",
                },
            )
    normalized = tmp_path / "Distribution"
    tree.write(normalized, encoding="utf-8", xml_declaration=True)

    BUILDER_MODULE.verify_distribution_contract(
        normalized,
        sources=sources,
        productbuild_normalized=True,
    )

    app_generated = next(
        package
        for package in root.findall("pkg-ref")
        if package.attrib == {"id": BUILDER_MODULE.APP_RECEIPT_IDENTIFIER}
    )
    app_bundle = app_generated.find("bundle-version/bundle")
    assert app_bundle is not None
    app_bundle.attrib["path"] = "Applications/Other.app"
    tree.write(normalized, encoding="utf-8", xml_declaration=True)
    with pytest.raises(RuntimeError, match="generated component metadata drift"):
        BUILDER_MODULE.verify_distribution_contract(
            normalized,
            sources=sources,
            productbuild_normalized=True,
        )
    app_bundle.attrib["path"] = "Applications/Auralign.app"

    ET.SubElement(root, "choice", {"id": "unexpected"})
    tree.write(normalized, encoding="utf-8", xml_declaration=True)
    with pytest.raises(RuntimeError, match="top-level element set drift"):
        BUILDER_MODULE.verify_distribution_contract(
            normalized,
            sources=sources,
            productbuild_normalized=True,
        )


def test_installer_resource_contract_rejects_extra_content(tmp_path: Path) -> None:
    copied = tmp_path / "Resources"
    shutil.copytree(INSTALLER_DESIGN / "Resources", copied)
    BUILDER_MODULE.verify_installer_resources(copied)

    (copied / "unexpected.txt").write_text("not allowlisted", encoding="utf-8")
    with pytest.raises(RuntimeError, match="resource allowlist drift"):
        BUILDER_MODULE.verify_installer_resources(copied)


def test_installer_design_is_internalized_adapted_and_verification_only() -> None:
    expected = {
        "Resources/background.png": (
            76_961,
            "b9e1e34a58ce6a6f250cea4fe79db7cdf165f359e9b0fe1efe398496e4bf1014",
        ),
        "Resources/background-darkAqua.png": (
            76_966,
            "c7c9ba8bfed462b8cc5afe84250fc7177f0ee8033de96a3ceeded8206757deef",
        ),
        "Sources/installer-background-light.svg": (
            19_458,
            "78701511adef20903dd4c82672b511df1344e6fedf1429d5fcba01d20545bd25",
        ),
        "Sources/installer-background-dark.svg": (
            19_459,
            "baae4aa1190d9d95035a827ff9e686964e0ba027191e2e1b9846280c5cb73be2",
        ),
        "BrandAssets/auralign-styled-symbol.svg": (
            36_463,
            "8ed79fd4430b534862a90d224fd03c6aac8e1fae32c580b6716146c27b8167b7",
        ),
        "BrandAssets/auralign-styled-symbol-tight.png": (
            453_041,
            "6f556cd43c2c30f3658490da933e1e345a52320f223af47b3de192cae1f63963",
        ),
    }
    for relative, (size, digest) in expected.items():
        payload = (INSTALLER_DESIGN / relative).read_bytes()
        assert len(payload) == size
        assert hashlib.sha256(payload).hexdigest() == digest

    manifest = (INSTALLER_DESIGN / "manifest.json").read_text(encoding="utf-8")
    verifier = (INSTALLER_DESIGN / "build.py").read_text(encoding="utf-8")
    makefile = (INSTALLER_DESIGN / "Makefile").read_text(encoding="utf-8")
    assert "bd94520ecf106b7502ad04e19e4997dbb74d05dd" in manifest
    assert '"production_artwork_modified": true' in manifest
    assert "icon_pipeline" not in verifier
    assert "src/spec/icon-design.json" not in verifier
    assert "rm -rf" not in makefile
    assert "/Users/" not in verifier
    assert 'sys.executable,\n        "-B",' in verifier
    assert 'environment["PYTHONDONTWRITEBYTECODE"] = "1"' in verifier
    for appearance in ("light", "dark"):
        source = (INSTALLER_DESIGN / f"Sources/installer-background-{appearance}.svg").read_text(
            encoding="utf-8"
        )
        assert 'id="superseded-vector-symbol"' in source
        assert 'display="none"' in source
        assert 'id="styled-symbol" x="-30" y="211" width="270" height="222.14"' in source
        assert "../BrandAssets/auralign-styled-symbol-tight.png" in source
        assert 'x="8.5" y="204" width="194" height="224"' not in source
    brand_manifest = json.loads(
        (INSTALLER_DESIGN / "BrandAssets/manifest.json").read_text(encoding="utf-8")
    )
    assert brand_manifest["source_project_commit"] == ("ae319eb89650b9c8b0ce4ce5bac5e8adf2a0bb48")
    assert brand_manifest["placement"]["border_or_card"] is False
    assert brand_manifest["placement"]["intentional_crop"] == [
        "left",
        "right",
        "bottom",
    ]
    welcome = (INSTALLER_DESIGN / "Resources/en.lproj/welcome.html").read_text(encoding="utf-8")
    conclusion = (INSTALLER_DESIGN / "Resources/en.lproj/conclusion.html").read_text(
        encoding="utf-8"
    )
    assert "-apple-system" in welcome
    assert "-apple-system" in conclusion
    assert "PolyForm Internal Use License 1.0.0" in welcome
    assert "redistribution-cleared model files" in welcome
    assert "no telemetry or media" in welcome


def test_release_swift_uses_embedded_runtime_and_verified_system_models() -> None:
    location = (ROOT / "macos/Auralign/Bridge/EngineLocation.swift").read_text(encoding="utf-8")
    bridge = (ROOT / "macos/Auralign/Bridge/EngineBridge.swift").read_text(encoding="utf-8")
    settings = (ROOT / "macos/Auralign/Views/SettingsView.swift").read_text(encoding="utf-8")
    project = (ROOT / "macos/Auralign.xcodeproj/project.pbxproj").read_text(encoding="utf-8")

    assert 'static let runtimeDirectoryName = "AuralignEngineRuntime"' in location
    assert 'static let runtimeBundleName = "\\(runtimeDirectoryName).bundle"' in location
    assert '.appendingPathComponent("Contents/Frameworks"' in location
    assert ".appendingPathComponent(runtimeBundleName" in location
    assert '.appendingPathComponent("Contents/Resources/Runtime"' in location
    assert "origin: .embeddedRuntime" in location
    assert 'applicationSupportDirectoryName = "com.auralign.studio"' in location
    assert 'applicationSupportRoot.appendingPathComponent("Models"' in location
    assert 'location.usesEmbeddedRuntime ? ["-B", "-I"] : []' in bridge
    assert '"PYTHONDONTWRITEBYTECODE": "1"' in bridge
    assert '"LTM_OFFLINE": "1"' in bridge
    assert '"HF_HUB_OFFLINE": "1"' in bridge
    assert '"TRANSFORMERS_OFFLINE": "1"' in bridge
    assert 'key.hasPrefix("DYLD_")' in bridge
    assert "#if DEBUG" in settings
    assert "normal use is offline" in settings
    assert "requires no Hugging Face account" in settings
    assert project.count("MARKETING_VERSION = 0.3.1;") == 6
    assert "MARKETING_VERSION = 0.2.1;" not in project
