# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Native security and durability primitives for private local artifacts.

The functions in this module intentionally avoid network services and optional
dependencies.  Windows paths are validated with handles and protected DACLs;
POSIX paths use no-follow opens and owner-only modes.
"""

from __future__ import annotations

import ctypes
import errno
import importlib
import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

_WINDOWS = os.name == "nt"
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _windows_dll(name: str) -> Any:
    constructor_name = "".join(("Win", "DLL"))
    constructor = getattr(ctypes, constructor_name)
    return constructor(name, use_last_error=True)


def _windows_last_error() -> int:
    attribute = "_".join(("get", "last", "error"))
    return int(getattr(ctypes, attribute)())


def _windows_lock(descriptor: int, *, unlock: bool) -> None:
    msvcrt = importlib.import_module("msvcrt")
    operation_name = "LK_UNLCK" if unlock else "LK_NBLCK"
    operation = getattr(msvcrt, operation_name)
    locking_name = "".join(("lock", "ing"))
    getattr(msvcrt, locking_name)(descriptor, operation, 1)


class UnsafePathError(RuntimeError):
    """Raised when a private path is a link, reparse point, or wrong type."""


class LockUnavailableError(RuntimeError):
    """Raised when a descriptor-backed lock is already held."""


def _windows_file_attributes(path: Path) -> int:
    kernel32 = _windows_dll("kernel32")
    get_attributes = kernel32.GetFileAttributesW
    get_attributes.argtypes = [ctypes.c_wchar_p]
    get_attributes.restype = ctypes.c_uint32
    attributes = int(get_attributes(str(path)))
    if attributes == _INVALID_FILE_ATTRIBUTES:
        error = _windows_last_error()
        raise OSError(error, os.strerror(error), str(path))
    return attributes


def reject_reparse_or_symlink(path: Path) -> None:
    """Reject filesystem indirection before a private path is trusted."""

    candidate = Path(path)
    if _WINDOWS:
        if _windows_file_attributes(candidate) & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise UnsafePathError(f"private path must not be a reparse point: {candidate.name}")
        return
    if candidate.is_symlink():
        raise UnsafePathError(f"private path must not be a symbolic link: {candidate.name}")


def _reject_existing_ancestor_indirection(path: Path) -> None:
    absolute = path.absolute()
    chain = tuple(reversed(absolute.parents)) + (absolute,)
    for candidate in chain:
        if os.path.lexists(candidate):
            reject_reparse_or_symlink(candidate)


def _protect_windows_path(path: Path) -> None:
    """Install an inherited-access-blocking owner/system/admin DACL."""

    advapi32 = _windows_dll("advapi32")
    kernel32 = _windows_dll("kernel32")
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    convert.restype = ctypes.c_int
    set_security = advapi32.SetFileSecurityW
    set_security.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    set_security.restype = ctypes.c_int
    descriptor = ctypes.c_void_p()
    # D:P blocks inherited access. OW is the object's owner; local system and
    # administrators retain recovery access on an owner-controlled workstation.
    sddl = "D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FA;;;BA)"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        error = _windows_last_error()
        raise OSError(error, "could not construct private Windows DACL", str(path))
    try:
        dacl_security_information = 0x00000004
        protected_dacl_security_information = 0x80000000
        if not set_security(
            str(path),
            dacl_security_information | protected_dacl_security_information,
            descriptor,
        ):
            error = _windows_last_error()
            raise OSError(error, "could not apply private Windows DACL", str(path))
    finally:
        kernel32.LocalFree(descriptor)


def ensure_private_directory(path: Path) -> Path:
    """Create or validate an owner-private, non-linked directory."""

    directory = Path(path)
    _reject_existing_ancestor_indirection(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _reject_existing_ancestor_indirection(directory)
    if not directory.is_dir():
        raise UnsafePathError(f"private path is not a directory: {directory.name}")
    if _WINDOWS:
        _protect_windows_path(directory)
    else:
        directory.chmod(0o700)
    return directory


def open_regular_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open a regular file without following its final path component."""

    candidate = Path(path)
    _reject_existing_ancestor_indirection(candidate.parent)
    if _WINDOWS:
        # Python's Windows open ultimately uses CreateFile. Validate the path
        # immediately before and the resulting descriptor immediately after;
        # callers publish only within an already protected directory.
        if flags & os.O_TRUNC:
            raise ValueError("Windows no-follow opens must not truncate before handle validation")
        if os.path.lexists(candidate):
            reject_reparse_or_symlink(candidate)
        descriptor = os.open(candidate, flags | getattr(os, "O_BINARY", 0), mode)
    else:
        descriptor = os.open(
            candidate,
            flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafePathError(f"private path is not a regular file: {candidate.name}")
        if _WINDOWS:
            reject_reparse_or_symlink(candidate)
            path_metadata = candidate.lstat()
            if (metadata.st_dev, metadata.st_ino) != (path_metadata.st_dev, path_metadata.st_ino):
                raise UnsafePathError(f"private path changed while opening: {candidate.name}")
            _protect_windows_path(candidate)
        else:
            os.fchmod(descriptor, mode)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def fsync_directory(path: Path) -> None:
    """Flush directory metadata where the native platform supports it."""

    directory = Path(path)
    reject_reparse_or_symlink(directory)
    if _WINDOWS:
        kernel32 = _windows_dll("kernel32")
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        create_file.restype = ctypes.c_void_p
        handle = create_file(
            str(directory),
            0x80000000,
            0x1 | 0x2 | 0x4,
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            error = _windows_last_error()
            raise OSError(error, "could not open directory for flush", directory.name)
        try:
            if not kernel32.FlushFileBuffers(handle):
                error = _windows_last_error()
                # Some Windows filesystems do not implement directory flush.
                if error not in {1, 50}:
                    raise OSError(error, "could not flush directory", directory.name)
        finally:
            kernel32.CloseHandle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Durably publish private bytes with same-directory atomic replacement."""

    target = Path(path)
    ensure_private_directory(target.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        if _WINDOWS:
            _protect_windows_path(temporary)
        else:
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if _WINDOWS:
            _protect_windows_path(target)
        fsync_directory(target.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    """Durably publish UTF-8 text using :func:`atomic_write_bytes`."""

    atomic_write_bytes(path, text.encode("utf-8"))


def ensure_private_file(path: Path) -> Path:
    """Validate and protect an existing regular private file."""

    descriptor = open_regular_nofollow(path, os.O_RDONLY, 0o600)
    os.close(descriptor)
    return Path(path)


class HeldFileLock:
    """Cross-platform, descriptor-backed exclusive advisory lock.

    The zero-length lock file is intentionally persistent. Ownership is the
    live descriptor lock, so crashes cannot leave a false PID-based lock.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._handle: BinaryIO | None = None

    def acquire(self) -> HeldFileLock:
        ensure_private_directory(self.path.parent)
        descriptor = open_regular_nofollow(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b", closefd=True)
        try:
            if _WINDOWS:
                handle.seek(0)
                if handle.read(1) == b"":
                    handle.seek(0)
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                _windows_lock(handle.fileno(), unlock=False)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise LockUnavailableError(
                    f"private operation is already active: {self.path.name}"
                ) from exc
            raise
        self._handle = handle
        return self

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if _WINDOWS:
                handle.seek(0)
                _windows_lock(handle.fileno(), unlock=True)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> HeldFileLock:
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


@contextmanager
def held_file_lock(path: Path) -> Iterator[HeldFileLock]:
    """Context-manager wrapper for :class:`HeldFileLock`."""

    with HeldFileLock(path) as lock:
        yield lock


@dataclass(frozen=True)
class ProcessUsage:
    """Current-process CPU seconds and resident-memory bytes."""

    user_seconds: float
    system_seconds: float
    resident_bytes: int


def current_process_usage() -> ProcessUsage:
    """Read process usage through native POSIX or Win32 facilities."""

    if not _WINDOWS:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        scale = 1 if sys.platform == "darwin" else 1024
        return ProcessUsage(usage.ru_utime, usage.ru_stime, int(usage.ru_maxrss) * scale)

    kernel32 = _windows_dll("kernel32")
    psapi = _windows_dll("psapi")

    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

        def seconds(self) -> float:
            return ((int(self.high) << 32) | int(self.low)) / 10_000_000

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_uint32),
            ("page_fault_count", ctypes.c_uint32),
            ("peak_working_set_size", ctypes.c_size_t),
            ("working_set_size", ctypes.c_size_t),
            ("quota_peak_paged_pool_usage", ctypes.c_size_t),
            ("quota_paged_pool_usage", ctypes.c_size_t),
            ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
            ("quota_non_paged_pool_usage", ctypes.c_size_t),
            ("pagefile_usage", ctypes.c_size_t),
            ("peak_pagefile_usage", ctypes.c_size_t),
        ]

    process = kernel32.GetCurrentProcess()
    created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
    if not kernel32.GetProcessTimes(
        process,
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        error = _windows_last_error()
        raise OSError(error, "could not read Windows process times")
    memory = ProcessMemoryCounters()
    memory.cb = ctypes.sizeof(memory)
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
        error = _windows_last_error()
        raise OSError(error, "could not read Windows process memory")
    return ProcessUsage(user.seconds(), kernel.seconds(), int(memory.working_set_size))
