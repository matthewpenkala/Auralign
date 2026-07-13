# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
assemble = importlib.import_module("assemble_private_release")
verify = importlib.import_module("verify_distribution")


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)


def _write_tar(path: Path, files: dict[str, bytes], staging: Path) -> None:
    root = staging / "tar-files"
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with tarfile.open(path, "w:gz") as archive:
        for target in sorted(root.rglob("*")):
            if target.is_file():
                archive.add(target, arcname=target.relative_to(root))


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
    cdx = b'{"bomFormat":"CycloneDX","specVersion":"1.6","components":[]}'
    sboms = {
        "source.spdx": (
            b"SPDXVersion: SPDX-2.1\nDocumentName: Auralign\nCreator: Tool: reuse-6.2.0\n"
        ),
        "wheel.spdx.json": b'{"spdxVersion":"SPDX-2.3","packages":[],"files":[]}',
        "sdist.spdx.json": b'{"spdxVersion":"SPDX-2.3","packages":[],"files":[]}',
        "macos-arm64-cpu.cdx.json": cdx,
        "windows-amd64-cpu.cdx.json": cdx,
        "windows-amd64-cu128.cdx.json": cdx,
        "linux-amd64-cu126.cdx.json": cdx,
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


def test_private_release_recursive_closed_allowlist_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = "a" * 40
    _build_synthetic_release(tmp_path, commit)
    wheel_name = "local_transcript_max-0.2.0-py3-none-any.whl"
    common_names, _files = _native_common(
        wheel_name, (tmp_path / "model-manifest.json").read_bytes()
    )

    monkeypatch.setattr(verify, "read_wheel", lambda _path: {})
    monkeypatch.setattr(verify, "verify_wheel", lambda _files: None)
    monkeypatch.setattr(verify, "read_sdist", lambda _path: {"root/file": b"x"})
    monkeypatch.setattr(verify, "verify_sdist", lambda _files: None)
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


def test_release_requirements_match_lock() -> None:
    checker = importlib.import_module("check_release_requirements")
    checker.verify_or_write(write=False)
