# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

from local_transcript_max.platform_security import (
    HeldFileLock,
    LockUnavailableError,
    UnsafePathError,
    atomic_write_bytes,
    atomic_write_text,
    current_process_usage,
    ensure_private_directory,
    open_regular_nofollow,
    reject_reparse_or_symlink,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_private_directory_and_atomic_publication_are_owner_only(tmp_path: Path) -> None:
    private = ensure_private_directory(tmp_path / "private")
    target = private / "status.txt"
    atomic_write_text(target, "complete\n")

    assert target.read_text(encoding="utf-8") == "complete\n"
    if os.name != "nt":
        assert stat.S_IMODE(private.stat().st_mode) == 0o700
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_publication_replaces_complete_file_and_removes_temporary(
    tmp_path: Path,
) -> None:
    target = tmp_path / "private" / "artifact.bin"
    atomic_write_bytes(target, b"old")
    atomic_write_bytes(target, b"new")

    assert target.read_bytes() == b"new"
    assert sorted(path.name for path in target.parent.iterdir()) == ["artifact.bin"]


def test_no_follow_open_rejects_symbolic_link(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows link creation requires host-specific developer privileges")
    target = tmp_path / "target"
    target.write_text("private", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)

    with pytest.raises((OSError, UnsafePathError)):
        descriptor = open_regular_nofollow(link, os.O_RDONLY)
        os.close(descriptor)
    with pytest.raises(UnsafePathError):
        reject_reparse_or_symlink(link)


def test_private_directory_rejects_linked_ancestor(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows link creation requires host-specific developer privileges")
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked"
    linked_parent.symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafePathError, match="symbolic link"):
        ensure_private_directory(linked_parent / "private")
    assert not (outside / "private").exists()


def test_macos_standard_tmp_alias_is_canonicalized() -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS standard alias contract")
    root = Path(tempfile.mkdtemp(prefix="auralign-platform-security-"))
    try:
        private = ensure_private_directory(root / "private")
        assert str(private).startswith("/private/")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_follow_open_rejects_directory(tmp_path: Path) -> None:
    with pytest.raises(UnsafePathError, match="not a regular file"):
        descriptor = open_regular_nofollow(tmp_path, os.O_RDONLY)
        os.close(descriptor)


def test_descriptor_lock_blocks_concurrent_entry_and_recovers(tmp_path: Path) -> None:
    lock_path = tmp_path / "private" / ".operation.lock"
    first = HeldFileLock(lock_path).acquire()
    try:
        assert lock_path.exists()
        with pytest.raises(LockUnavailableError, match="already active"):
            HeldFileLock(lock_path).acquire()
    finally:
        first.release()

    with HeldFileLock(lock_path):
        assert lock_path.exists()


def test_process_usage_is_non_negative() -> None:
    usage = current_process_usage()

    assert usage.user_seconds >= 0
    assert usage.system_seconds >= 0
    assert usage.resident_bytes > 0
    assert usage.peak_resident_bytes >= usage.resident_bytes


def test_release_accelerator_profiles_are_native_and_mutually_exclusive() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["version"] == "0.2.0"
    groups = project["dependency-groups"]
    assert all("sys_platform == 'win32'" in item for item in groups["cu128"])
    assert all("sys_platform == 'linux'" in item for item in groups["cu126"])
    conflict = project["tool"]["uv"]["conflicts"][0]
    assert {item["group"] for item in conflict} == {"cpu", "cu126", "cu128"}
    required = project["tool"]["uv"]["required-environments"]
    assert any("darwin" in marker and "arm64" in marker for marker in required)
    assert any("win32" in marker and "AMD64" in marker for marker in required)


def test_native_installers_keep_models_external_and_pin_release_python() -> None:
    windows = (REPO_ROOT / "install-windows.ps1").read_text(encoding="utf-8")
    macos = (REPO_ROOT / "install-macos.sh").read_text(encoding="utf-8")
    bootstrap = (REPO_ROOT / "bootstrap.ps1").read_text(encoding="utf-8")

    assert "Python 3.11" in windows
    assert "Cuda128" in windows and "CUDA 12.8" in windows
    assert "--require-hashes" in windows
    assert "macOS 14" in macos and "arm64" in macos
    assert "--require-hashes" in macos
    assert "models pull" not in windows
    assert "models pull" not in macos
    assert '"prepare-runtime"' in bootstrap


def test_trusted_windows_workflow_cannot_upload_or_cache_private_evidence() -> None:
    workflow = (REPO_ROOT / ".github/workflows/trusted-windows-validation.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "[self-hosted, Windows, X64, auralign-gpu]" in workflow
    assert "github.actor == github.repository_owner" in workflow
    assert "LTM_VALIDATION_CAPSULE_DIR" in workflow
    assert "actions/upload-artifact" not in workflow
    assert "actions/cache" not in workflow
    assert "setup-uv" not in workflow
    assert "release" not in workflow.casefold()
