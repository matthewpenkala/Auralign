# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

import local_transcript_max.platform_security as platform_security
from local_transcript_max.platform_security import (
    HeldFileLock,
    LockUnavailableError,
    UnsafePathError,
    atomic_write_bytes,
    atomic_write_text,
    current_process_usage,
    ensure_private_directory,
    open_regular_nofollow,
    private_path_is_secure,
    reject_reparse_or_symlink,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_private_directory_and_atomic_publication_are_owner_only(tmp_path: Path) -> None:
    private = ensure_private_directory(tmp_path / "private")
    target = private / "status.txt"
    atomic_write_text(target, "complete\n")

    assert target.read_text(encoding="utf-8") == "complete\n"
    assert private_path_is_secure(private, directory=True)
    assert private_path_is_secure(target)


def test_private_path_security_treats_alias_normalization_failure_as_unsafe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_alias(_path: Path) -> Path:
        raise UnsafePathError("unexpected platform alias")

    monkeypatch.setattr(platform_security, "_normalize_platform_alias", fail_alias)
    assert not private_path_is_secure(Path("untrusted"))


def test_reject_path_indirection_returns_the_absolute_checked_path(tmp_path: Path) -> None:
    checked = platform_security.reject_path_indirection(tmp_path)

    assert checked.is_absolute()
    assert checked == platform_security._normalize_platform_alias(tmp_path.absolute())


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


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_private_directory_rejects_windows_junction(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("host did not permit creation of a test junction")

    with pytest.raises(UnsafePathError, match="reparse point"):
        ensure_private_directory(junction / "private")
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
    with pytest.raises((OSError, UnsafePathError)):
        descriptor = open_regular_nofollow(tmp_path, os.O_RDONLY)
        os.close(descriptor)


@pytest.mark.parametrize("error", [1, 5, 6, 50])
def test_windows_directory_flush_recognizes_unsupported_errors(error: int) -> None:
    assert platform_security._windows_directory_flush_is_unsupported(error)


@pytest.mark.parametrize("error", [23, 32])
def test_windows_directory_flush_does_not_hide_other_errors(error: int) -> None:
    assert not platform_security._windows_directory_flush_is_unsupported(error)


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


def test_descriptor_lock_recovers_after_process_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / "private" / ".crash.lock"
    code = (
        "import os, sys; "
        "from pathlib import Path; "
        "from local_transcript_max.platform_security import HeldFileLock; "
        "HeldFileLock(Path(sys.argv[1])).acquire(); os._exit(0)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, str(lock_path)],
        check=False,
    )
    assert completed.returncode == 0
    with HeldFileLock(lock_path):
        assert lock_path.exists()


def test_windows_private_sddl_binds_current_account_not_unrelated_owner() -> None:
    current_sid = "S-1-5-21-100-200-300-400"

    directory = platform_security._windows_private_sddl(current_sid, directory=True)
    regular_file = platform_security._windows_private_sddl(current_sid, directory=False)

    assert current_sid in directory and current_sid in regular_file
    assert ";;;OW)" not in directory and ";;;OW)" not in regular_file
    assert "OICI" in directory and "OICI" not in regular_file
    assert platform_security._SYSTEM_SID in directory
    assert platform_security._ADMINISTRATORS_SID in directory


@pytest.mark.skipif(os.name != "nt", reason="Windows SDDL canonicalization contract")
def test_windows_sddl_aliases_are_derived_from_concrete_sids() -> None:
    assert platform_security._windows_sddl_trustee_spelling("S-1-5-19") in {
        "LS",
        "S-1-5-19",
    }
    assert platform_security._windows_sddl_trustee_spelling("S-1-5-20") in {
        "NS",
        "S-1-5-20",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows path normalization contract")
def test_windows_extended_paths_cover_drive_unc_long_and_case_normalization() -> None:
    drive = Path("C:/Auralign/Models")
    unc = Path("//server/share/Auralign/Models")
    long_child = drive / ("a" * 280)

    assert platform_security._windows_extended_path(drive).startswith("\\\\?\\C:\\")
    assert platform_security._windows_extended_path(unc).startswith("\\\\?\\UNC\\")
    assert len(platform_security._windows_extended_path(long_child)) > 260
    assert platform_security._normalize_windows_final_path("C:\\AURALIGN\\MODELS") == (
        platform_security._normalize_windows_final_path("c:\\auralign\\models")
    )


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
    assert "LTM_ACCEPTANCE_EVIDENCE_DIR" in workflow
    assert "scripts/generate_environment_sbom.py" in workflow
    assert workflow.index("Protect runner-local paths before checkout cleanup") < workflow.index(
        "actions/checkout@"
    )
    assert "actions/upload-artifact" not in workflow
    assert "actions/cache" not in workflow
    assert "setup-uv" not in workflow
    assert "release" not in workflow.casefold()
