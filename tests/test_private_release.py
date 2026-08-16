# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import gzip
import hashlib
import importlib
import io
import json
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
assemble = importlib.import_module("assemble_private_release")
verify = importlib.import_module("verify_distribution")
dependency_sbom = importlib.import_module("validate_dependency_sbom")


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, verify.DETERMINISTIC_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100000 | 0o644) << 16
            archive.writestr(info, data)


def _write_tar(path: Path, files: dict[str, bytes], _staging: Path) -> None:
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name, data in sorted(files.items()):
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o755 if name.endswith((".sh", "install-macos.sh")) else 0o644
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    archive.addfile(info, io.BytesIO(data))


def _native_common(wheel_name: str, model_manifest: bytes) -> tuple[set[str], dict[str, bytes]]:
    names = {Path(name).as_posix() for name in assemble.COMMON_BUNDLE_FILES} | {
        wheel_name,
        "model-manifest.json",
    }
    files = {
        name: model_manifest
        if name == "model-manifest.json"
        else b"synthetic wheel"
        if name == wheel_name
        else (REPO_ROOT / name).read_bytes()
        for name in names
    }
    return names, files


def _native_environment_sbom(platform: str, profile: str, commit: str) -> bytes:
    versions = dependency_sbom.NATIVE_PACKAGE_VERSIONS[platform]
    components = [
        {
            "bom-ref": f"{name}=={versions[name]}",
            "name": name,
            "version": versions[name],
        }
        for name in sorted(versions)
    ]
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "properties": [
                {"name": "auralign:commit", "value": commit},
                {"name": "auralign:platform", "value": platform},
                {"name": "auralign:profile", "value": profile},
                {"name": "auralign:python-version", "value": "3.11.13"},
                {"name": "auralign:sbom-kind", "value": "installed-environment"},
            ]
        },
        "components": components,
        "dependencies": [{"ref": item["bom-ref"], "dependsOn": []} for item in components],
    }
    return json.dumps(payload, sort_keys=True).encode()


def _build_synthetic_release(directory: Path, commit: str) -> None:
    version = "0.2.0"
    wheel_name = f"local_transcript_max-{version}-py3-none-any.whl"
    model_manifest = json.dumps(
        {
            "schema_version": "auralign-release-model-manifest.v1",
            "models": [
                {
                    "role": role,
                    "key": role,
                    "model_id": f"example/{role}",
                    "revision": "a" * 40,
                    "repository_manifest_digest": "b" * 64,
                    "runtime_files": {"config.json": "c" * 64},
                }
                for role in ("asr", "diarization", "alignment", "speaker-embedding")
            ],
        }
    ).encode()
    common_names, common = _native_common(wheel_name, model_manifest)
    windows_root = f"Auralign-{version}-windows-amd64"
    windows_extra = {
        "install-windows.ps1",
        "bootstrap.ps1",
        "examples/config.strict-windows-amd64-cpu.yaml",
        "examples/config.strict-windows-amd64-cu128.yaml",
        "requirements/windows-cpu-py311.txt",
        "requirements/windows-cu128-py311.txt",
    }
    macos_root = f"Auralign-{version}-darwin-arm64"
    macos_extra = {
        "install-macos.sh",
        "bootstrap.sh",
        "examples/config.strict-macos-arm64-cpu.yaml",
        "requirements/macos-arm64-cpu-py311.txt",
    }
    _write_zip(
        directory / f"Auralign-{version}-windows-amd64.zip",
        {
            f"{windows_root}/{name}": common[name]
            if name in common
            else (REPO_ROOT / name).read_bytes()
            for name in common_names | windows_extra
        },
    )
    _write_tar(
        directory / f"Auralign-{version}-darwin-arm64.tar.gz",
        {
            f"{macos_root}/{name}": common[name]
            if name in common
            else (REPO_ROOT / name).read_bytes()
            for name in common_names | macos_extra
        },
        directory,
    )
    sboms = {
        "source.spdx": (
            b"SPDXVersion: SPDX-2.1\nDocumentName: Auralign\nCreator: Tool: reuse-6.2.0\n"
        ),
        "wheel.spdx.json": b'{"spdxVersion":"SPDX-2.3","packages":[],"files":[]}',
        "sdist.spdx.json": b'{"spdxVersion":"SPDX-2.3","packages":[],"files":[]}',
        "darwin-arm64-cpu.cdx.json": _native_environment_sbom("darwin-arm64-cpu", "cpu", commit),
        "windows-amd64-cpu.cdx.json": _native_environment_sbom("windows-amd64-cpu", "cpu", commit),
        "windows-amd64-cu128.cdx.json": _native_environment_sbom(
            "windows-amd64-cu128", "cu128", commit
        ),
        "linux-amd64-cu126.cdx.json": _native_environment_sbom(
            "linux-amd64-cu126", "cu126", commit
        ),
    }
    _write_zip(directory / f"Auralign-{version}-sboms.zip", sboms)
    (directory / wheel_name).write_bytes(b"synthetic wheel")
    (directory / f"local_transcript_max-{version}.tar.gz").write_bytes(b"synthetic sdist")
    (directory / "model-manifest.json").write_bytes(model_manifest)
    payload_names = {
        wheel_name,
        f"local_transcript_max-{version}.tar.gz",
        f"Auralign-{version}-windows-amd64.zip",
        f"Auralign-{version}-darwin-arm64.tar.gz",
        "model-manifest.json",
        f"Auralign-{version}-sboms.zip",
    }
    release_manifest = {
        "schema_version": "auralign-private-release.v1",
        "version": version,
        "commit": commit,
        "repository_visibility": "private",
        "artifacts": [
            {
                "name": name,
                "size": (directory / name).stat().st_size,
                "sha256": hashlib.sha256((directory / name).read_bytes()).hexdigest(),
            }
            for name in sorted(payload_names)
        ],
    }
    (directory / "release-manifest.json").write_text(json.dumps(release_manifest), encoding="utf-8")
    checksum_names = payload_names | {"release-manifest.json"}
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
            for name in sorted(checksum_names)
        ),
        encoding="utf-8",
    )


def _build_synthetic_acceptance(directory: Path, commit: str, contract_digest: str) -> None:
    directory.mkdir(mode=0o700)
    validator_digest = assemble.sha256_file(
        REPO_ROOT / "scripts/run_trusted_platform_validation.py"
    )
    for platform_name, (profile, config) in assemble.ACCEPTANCE_CONFIGS.items():
        sbom_name = f"{platform_name}.cdx.json"
        sbom_path = directory / sbom_name
        sbom_path.write_bytes(_native_environment_sbom(platform_name, profile, commit))
        sbom_path.chmod(0o600)
        payload = {
            "schema_version": "auralign-platform-acceptance.v2",
            "platform": platform_name,
            "profile": profile,
            "commit": commit,
            "passed": True,
            "owner_attested": True,
            "completed_at": "2026-07-13T12:00:00Z",
            "python_version": "3.11",
            "validator": "scripts/run_trusted_platform_validation.py",
            "validator_sha256": validator_digest,
            "config": config,
            "config_sha256": assemble.sha256_file(REPO_ROOT / config),
            "model_contract_sha256": contract_digest,
            "capsule_id": assemble.CAPSULE_ID,
            "dependency_sbom": sbom_name,
            "dependency_sbom_sha256": assemble.sha256_file(sbom_path),
        }
        record = directory / f"{platform_name}.json"
        record.write_text(json.dumps(payload), encoding="utf-8")
        record.chmod(0o600)
    linux = directory / "linux-amd64-cu126.cdx.json"
    linux.write_bytes(_native_environment_sbom("linux-amd64-cu126", "cu126", commit))
    linux.chmod(0o600)


def test_private_release_recursive_closed_allowlist_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = "a" * 40
    _build_synthetic_release(tmp_path, commit)
    wheel_name = "local_transcript_max-0.2.0-py3-none-any.whl"
    common_names, _files = _native_common(
        wheel_name, (tmp_path / "model-manifest.json").read_bytes()
    )
    expected_model_manifest = json.loads(
        (tmp_path / "model-manifest.json").read_text(encoding="utf-8")
    )

    monkeypatch.setattr(verify, "read_wheel", lambda _path: {})
    monkeypatch.setattr(verify, "verify_wheel", lambda _files: None)
    monkeypatch.setattr(verify, "read_sdist", lambda _path: {"root/file": b"x"})
    monkeypatch.setattr(verify, "verify_sdist", lambda _files: None)
    monkeypatch.setattr(
        verify,
        "expected_release_model_manifest",
        lambda: expected_model_manifest,
    )
    verify.verify_private_release(
        tmp_path,
        version="0.2.0",
        commit=commit,
        native_common_files=common_names,
    )

    (tmp_path / "model-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        verify.verify_private_release(
            tmp_path,
            version="0.2.0",
            commit=commit,
            native_common_files=common_names,
        )


def test_native_archive_rejects_unexpected_private_member(tmp_path: Path) -> None:
    archive = tmp_path / "bundle.zip"
    _write_zip(archive, {"Auralign/private.wav": b"private"})

    with pytest.raises(RuntimeError, match="member allowlist drift"):
        verify.verify_native_bundle(
            archive,
            root_name="Auralign",
            expected_relative_content={},
        )


@pytest.mark.parametrize(
    "private_path",
    [
        b"/" + b"Users/privateowner",
        b"/" + b"home/privateowner",
        b"C:" + b"\\Users\\privateowner",
        b"c:" + b"\\USERS\\privateowner\\secret",
    ],
)
def test_distribution_scan_rejects_terminal_and_case_varied_home_paths(
    private_path: bytes,
) -> None:
    with pytest.raises(RuntimeError, match="private home path"):
        verify.verify_no_prohibited_content({"payload.json": private_path}, "synthetic")


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("history.bundle", b"ordinary"),
        ("history.dat", b"# v2 git bundle\n"),
        ("objects.dat", b"PACK\x00\x00\x00\x02"),
    ],
)
def test_distribution_scan_rejects_archived_git_history(name: str, payload: bytes) -> None:
    with pytest.raises(RuntimeError, match="private file|archived Git history"):
        verify.verify_no_prohibited_content({name: payload}, "synthetic")


def test_native_archive_writers_are_reproducible_and_metadata_normalized(
    tmp_path: Path,
) -> None:
    stage = tmp_path / "Auralign-test"
    stage.mkdir()
    regular = stage / "README.md"
    regular.write_text("private release\n", encoding="utf-8")
    executable = stage / "install-macos.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"
    zip_files = [
        (path, f"{stage.name}/{path.relative_to(stage).as_posix()}") for path in stage.iterdir()
    ]
    assemble.write_deterministic_zip(first_zip, zip_files)
    assemble.write_deterministic_zip(second_zip, zip_files)
    assert first_zip.read_bytes() == second_zip.read_bytes()
    assert set(verify.read_zip(first_zip)) == {
        "Auralign-test/README.md",
        "Auralign-test/install-macos.sh",
    }

    first_tar = tmp_path / "first.tar.gz"
    second_tar = tmp_path / "second.tar.gz"
    assemble.write_deterministic_tar_gz(first_tar, stage)
    assemble.write_deterministic_tar_gz(second_tar, stage)
    assert first_tar.read_bytes() == second_tar.read_bytes()
    assert set(verify.read_tar(first_tar)) == {
        "Auralign-test/README.md",
        "Auralign-test/install-macos.sh",
    }


def test_platform_acceptance_binds_exact_native_environment_sboms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = "a" * 40
    contract_digest = "b" * 64
    monkeypatch.setattr(assemble, "repository_model_contract_digest", lambda: contract_digest)
    acceptance = tmp_path / "acceptance"
    _build_synthetic_acceptance(acceptance, commit, contract_digest)

    assemble.verify_acceptance(acceptance, commit)

    sbom = acceptance / "windows-amd64-cpu.cdx.json"
    payload = json.loads(sbom.read_text(encoding="utf-8"))
    properties = payload["metadata"]["properties"]
    next(row for row in properties if row["name"] == "auralign:commit")["value"] = "b" * 40
    sbom.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="binding drift"):
        assemble.verify_acceptance(acceptance, commit)


def test_release_requirements_match_lock() -> None:
    checker = importlib.import_module("check_release_requirements")
    checker.verify_or_write(write=False)


def test_native_release_requirements_bind_patched_lightning_source() -> None:
    for versions in dependency_sbom.NATIVE_PACKAGE_VERSIONS.values():
        assert versions["lightning"] == "2.6.2"


def test_native_release_requirements_reject_patched_lightning_hash_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        dependency_sbom.PATCHED_LIGHTNING_REQUIREMENT
        + " \\\n"
        + "    --hash=sha256:"
        + "0" * 64
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dependency_sbom, "ROOT", tmp_path)
    monkeypatch.setitem(
        dependency_sbom.PLATFORM_REQUIREMENTS,
        "darwin-arm64-cpu",
        "requirements.txt",
    )

    with pytest.raises(RuntimeError, match="patched Lightning archive hash drift"):
        dependency_sbom._locked_native_versions("darwin-arm64-cpu")


def test_apple_cleanup_sdk_is_explicit_source_headless_only() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    assert extras["cleanup-apple"] == [
        "apple-fm-sdk==0.2.1; sys_platform == 'darwin' and platform_machine == 'arm64'"
    ]
    assert not any("apple-fm-sdk" in requirement for requirement in extras["full"])

    checker = importlib.import_module("check_release_requirements")
    _, standalone_extras, _ = checker.TARGETS[checker.MACOS_APP_TARGET]
    assert "cleanup-apple" not in standalone_extras


def test_sat_scorer_is_explicit_source_headless_only() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    assert extras["presentation-sat"] == ["wtpsplit==2.2.1"]
    assert not any("wtpsplit" in requirement for requirement in extras["full"])

    checker = importlib.import_module("check_release_requirements")
    _, standalone_extras, _ = checker.TARGETS[checker.MACOS_APP_TARGET]
    assert "presentation-sat" not in standalone_extras
    checker.validate_full_requirements("numpy==2.3.5\n")
    with pytest.raises(RuntimeError, match="source/headless-only proposal"):
        checker.validate_full_requirements("wtpsplit==2.2.1\n")


@pytest.mark.parametrize(
    "requirement",
    [
        "apple-fm-sdk==0.2.1",
        "build==1.5.0",
        "pyproject-hooks==1.2.0",
        "native-helper @ file:///tmp/foundation-models-c",
        "native-helper @ file:///tmp/libFoundationModels.dylib",
        "wtpsplit==2.2.1",
    ],
)
def test_macos_app_requirements_reject_apple_cleanup_closure(
    requirement: str,
) -> None:
    checker = importlib.import_module("check_release_requirements")

    with pytest.raises(RuntimeError, match="source/headless-only Apple cleanup SDK"):
        checker.validate_macos_app_requirements(requirement + "\n")
