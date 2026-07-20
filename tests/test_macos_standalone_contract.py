# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fail-closed source contracts for the standalone macOS application release."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import platform
import plistlib
import re
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


def test_runtime_sources_are_exact_and_models_remain_external() -> None:
    sources = tomllib.loads(
        (PACKAGING / "runtime-sources.toml").read_text(encoding="utf-8")
    )

    assert sources["schema_version"] == "auralign-macos-runtime-sources.v1"
    assert sources["product_version"] == "0.2.0"
    assert sources["build_number"] == "1"
    assert sources["architecture"] == "arm64"
    assert sources["minimum_macos"] == "14.0"
    assert sources["python"] == {
        "project": "python-build-standalone",
        "version": "3.11.15",
        "release": "20260510",
        "source_revision": "f0ae464f527498d6c4b13f19977447d603cd5edd",
        "asset": (
            "cpython-3.11.15+20260510-aarch64-apple-darwin-install_only.tar.gz"
        ),
        "url": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260510/cpython-3.11.15%2B20260510-aarch64-apple-darwin-"
            "install_only.tar.gz"
        ),
        "sha256": (
            "03bcedae9b19a48888d7dc8ba064f73f6efaaf2b13f6a8e1a1bcc062df13e855"
        ),
        "metadata_asset": (
            "cpython-3.11.15+20260510-aarch64-apple-darwin-pgo+lto-full.tar.zst"
        ),
        "metadata_url": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/"
            "20260510/cpython-3.11.15%2B20260510-aarch64-apple-darwin-"
            "pgo%2Blto-full.tar.zst"
        ),
        "metadata_sha256": (
            "0fdbc1a816bb04d0c8244c51ef623372f92870c6e8f7e6c603575976cbb0d1ea"
        ),
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
            "sha256": (
                "f224469b4168294902bb1efa80a8bf7855f24c99aef99cbefc1bcd3cce77881b"
            ),
        }
    ]


def test_standalone_requirements_are_hash_locked_and_scope_limited() -> None:
    requirements = REQUIREMENTS.read_text(encoding="utf-8")

    assert "--hash=sha256:" in requirements
    assert "torch==" in requirements
    assert "torchcodec==" in requirements
    assert "whisperx==" in requirements
    assert "pyannote-audio==" in requirements
    assert "speechbrain==" in requirements
    for prohibited in ("fastapi==", "uvicorn==", "mlx==", "pyinstaller=="):
        assert prohibited not in requirements.lower()
    assert "/Users/" not in requirements
    assert ".venv" not in requirements


def test_builder_is_transparent_scriptless_offline_and_release_gated() -> None:
    builder = BUILDER.read_text(encoding="utf-8")

    assert "python-build-standalone" in builder
    assert "PyInstaller" not in builder
    assert "require_clean_main()" in builder
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
    assert "auralign-unreachable-test-content-pruning.v1" in builder
    assert '"credential_sha256": hashlib.sha256(token).hexdigest()' in builder
    assert "'transformers.testing_utils' in sys.modules" in builder
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
    assert builder.count("prune_runtime(runtime)") == 4
    assert "install_hash_locked_pure_python_sdists(" in builder
    assert '"SOURCE_DATE_EPOCH"' in builder
    assert "pure-python-sdist-builds.json" in builder
    assert "verify_expanded_package(" in builder
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


def test_diagnostic_cache_is_atomic_content_addressed_and_fail_closed(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "cache"
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload.txt").write_text("verified\n", encoding="utf-8")
    key_inputs = {
        "schema_version": BUILDER_MODULE.DIAGNOSTIC_CACHE_SCHEMA_VERSION,
        "stage": "test",
        "input_sha256": hashlib.sha256(b"input").hexdigest(),
    }

    published = BUILDER_MODULE.publish_cache_payload(
        cache,
        "test",
        key_inputs,
        {"fixture": source},
    )
    assert (published / "fixture/payload.txt").read_text(encoding="utf-8") == "verified\n"
    key = BUILDER_MODULE.canonical_json_sha256(key_inputs)
    manifest = json.loads(
        (cache / "test" / key / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["key"] == key
    assert manifest["key_inputs"] == key_inputs
    assert manifest["payload_inventory"] == BUILDER_MODULE.inventory_rows(
        cache / "test" / key / "payload"
    )
    assert not list((cache / "test").glob(f".{key}.*"))

    (published / "fixture/payload.txt").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cache payload hash or mode drift"):
        BUILDER_MODULE.verified_cache_payload(cache, "test", key_inputs)


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
    assert {
        row["path"]
        for row in result["changes"]
    } == {".", "bin/tool", "private", "private/metadata.json"}
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


def test_unreachable_third_party_test_credential_module_is_pruned(
    tmp_path: Path,
) -> None:
    relative = Path("lib/python3.11/site-packages/transformers/testing_utils.py")
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True)
    credential = b"hf_" + (b"A" * 32)
    candidate.write_bytes(b'TOKEN = "' + credential + b'"\n')
    evidence = tmp_path / "evidence"
    evidence.mkdir()

    result = BUILDER_MODULE.prune_unreachable_third_party_test_content(
        tmp_path,
        evidence,
    )

    assert result["files_removed"] == 1
    assert not candidate.exists()
    recorded = (evidence / "unreachable-test-content-pruning.json").read_bytes()
    assert credential not in recorded
    assert hashlib.sha256(credential).hexdigest().encode() in recorded


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
        lambda _root: [main, nested],
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
    assert len(subprocess_commands) == 1
    assert subprocess_commands[0][-1] == str(nested)
    assert str(main) not in subprocess_commands[0]
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
                (
                    "-Wl,-install_name,"
                    f"/tmp/upstream/{architecture}/libfixture.dylib"
                ),
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
    assert (
        BUILDER_MODULE.install_name(stage / "lib/libfixture.dylib")
        == "@rpath/libfixture.dylib"
    )
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
    package = root.find("pkg-ref")
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
    assert package is not None
    assert package.attrib["id"] == "com.auralign.studio.pkg.app"
    assert package.attrib["version"] == "0.2.0.1"
    background = root.find("background")
    dark_background = root.find("background-darkAqua")
    welcome = root.find("welcome")
    conclusion = root.find("conclusion")
    assert background is not None and background.attrib["file"] == "background.png"
    assert dark_background is not None
    assert dark_background.attrib["file"] == "background-darkAqua.png"
    assert welcome is not None and welcome.attrib["file"] == "welcome.html"
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

    builder = (ROOT / "scripts/build_macos_standalone.py").read_text(
        encoding="utf-8"
    )
    assert 'component / "Scripts"' in builder
    assert 'rglob("Scripts")' not in builder


def test_installer_design_is_internalized_exact_and_verification_only() -> None:
    expected = {
        "Resources/background.png": (
            51_852,
            "1df7830cbfab4da98f80e5c89a8ed31fc7321a5d95a0e16a305a05f307e6c2ee",
        ),
        "Resources/background-darkAqua.png": (
            52_496,
            "513c9cf18b978409ec2c5ed85e08a856abfd38389a503c232dc78c1a412fd366",
        ),
        "Sources/installer-background-light.svg": (
            13_379,
            "b4dbf9587022f998fb16d57e73dfe141d8fede631b385f0941c438ff8bbf17ae",
        ),
        "Sources/installer-background-dark.svg": (
            13_378,
            "fe24491644f2c6193b48bc65108c92bf7de942ba93a1f3f19b6f8f79729aa416",
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
    assert '"production_artwork_modified": false' in manifest
    assert "icon_pipeline" not in verifier
    assert "src/spec/icon-design.json" not in verifier
    assert "rm -rf" not in makefile
    assert "/Users/" not in verifier


def test_release_swift_uses_embedded_runtime_and_external_models() -> None:
    location = (
        ROOT / "macos/Auralign/Bridge/EngineLocation.swift"
    ).read_text(encoding="utf-8")
    bridge = (ROOT / "macos/Auralign/Bridge/EngineBridge.swift").read_text(
        encoding="utf-8"
    )
    settings = (ROOT / "macos/Auralign/Views/SettingsView.swift").read_text(
        encoding="utf-8"
    )
    project = (ROOT / "macos/Auralign.xcodeproj/project.pbxproj").read_text(
        encoding="utf-8"
    )

    assert 'static let runtimeDirectoryName = "AuralignEngineRuntime"' in location
    assert 'static let runtimeBundleName = "\\(runtimeDirectoryName).bundle"' in location
    assert ".appendingPathComponent(\"Contents/Frameworks\"" in location
    assert ".appendingPathComponent(runtimeBundleName" in location
    assert '.appendingPathComponent(\"Contents/Resources/Runtime\"' in location
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
    assert "never acquires gated assets" in settings
    assert re.search(r"MARKETING_VERSION = 0\.2\.0;", project)
