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
import re
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
_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"
_SID_PATTERN = re.compile(r"^S-\d+(?:-\d+)+$")


def _windows_extended_path(path: Path) -> str:
    """Return an absolute Win32 extended path for UNC and long-path safety."""

    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return absolute
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def _windows_dll(name: str) -> Any:
    constructor_name = "".join(("Win", "DLL"))
    constructor = getattr(ctypes, constructor_name)
    return constructor(name, use_last_error=True)


def _windows_last_error() -> int:
    attribute = "_".join(("get", "last", "error"))
    return int(getattr(ctypes, attribute)())


def _windows_current_user_sid() -> str:
    """Return the SID of the account backing the current Windows process."""

    advapi32 = _windows_dll("advapi32")
    kernel32 = _windows_dll("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    token = ctypes.c_void_p()
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.restype = ctypes.c_void_p
    open_process_token = advapi32.OpenProcessToken
    open_process_token.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    open_process_token.restype = ctypes.c_int
    if not open_process_token(get_current_process(), 0x0008, ctypes.byref(token)):
        error = _windows_last_error()
        raise OSError(error, "could not open the current Windows process token")

    class SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", ctypes.c_uint32)]

    try:
        get_token_information = advapi32.GetTokenInformation
        get_token_information.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_token_information.restype = ctypes.c_int
        required = ctypes.c_uint32()
        get_token_information(token, 1, None, 0, ctypes.byref(required))
        error = _windows_last_error()
        if error != 122 or required.value == 0:
            raise OSError(error, "could not size the current Windows token user")
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token_information(
            token,
            1,
            buffer,
            required.value,
            ctypes.byref(required),
        ):
            error = _windows_last_error()
            raise OSError(error, "could not read the current Windows token user")
        token_user = ctypes.cast(buffer, ctypes.POINTER(SidAndAttributes)).contents
        convert = advapi32.ConvertSidToStringSidW
        convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        convert.restype = ctypes.c_int
        sid_pointer = ctypes.c_wchar_p()
        if not convert(token_user.sid, ctypes.byref(sid_pointer)):
            error = _windows_last_error()
            raise OSError(error, "could not render the current Windows token user SID")
        try:
            sid = sid_pointer.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(sid_pointer, ctypes.c_void_p))
    finally:
        kernel32.CloseHandle(token)
    if _SID_PATTERN.fullmatch(sid) is None:
        raise UnsafePathError("current Windows token returned an invalid SID")
    return sid


def _windows_private_trustees(current_sid: str) -> tuple[str, ...]:
    """Return the exact current-user/system/admin trustee set without duplicates."""

    if _SID_PATTERN.fullmatch(current_sid) is None:
        raise ValueError("invalid Windows current-user SID")
    return tuple(dict.fromkeys((current_sid, _SYSTEM_SID, _ADMINISTRATORS_SID)))


def _windows_private_sddl(current_sid: str, *, directory: bool) -> str:
    ace_flags = "OICI" if directory else ""
    return "D:P" + "".join(
        f"(A;{ace_flags};FA;;;{trustee})" for trustee in _windows_private_trustees(current_sid)
    )


def _windows_sddl_trustee_spelling(sid: str) -> str:
    """Return Win32's canonical SDDL spelling for one concrete SID."""

    if _SID_PATTERN.fullmatch(sid) is None:
        raise ValueError("invalid Windows SID")
    advapi32 = _windows_dll("advapi32")
    kernel32 = _windows_dll("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    parse = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    parse.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    parse.restype = ctypes.c_int
    descriptor = ctypes.c_void_p()
    if not parse(f"D:(A;;FA;;;{sid})", 1, ctypes.byref(descriptor), None):
        error = _windows_last_error()
        raise OSError(error, "could not canonicalize Windows SID")
    try:
        render = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
        render.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        render.restype = ctypes.c_int
        rendered = ctypes.c_wchar_p()
        rendered_length = ctypes.c_uint32()
        if not render(
            descriptor,
            1,
            0x00000004,
            ctypes.byref(rendered),
            ctypes.byref(rendered_length),
        ):
            error = _windows_last_error()
            raise OSError(error, "could not render canonical Windows SID")
        try:
            canonical = str(rendered.value or "")
        finally:
            kernel32.LocalFree(ctypes.cast(rendered, ctypes.c_void_p))
    finally:
        kernel32.LocalFree(descriptor)
    aces = re.findall(r"\(([^()]*)\)", canonical)
    if len(aces) != 1 or len(aces[0].split(";")) != 6:
        raise UnsafePathError("Windows returned malformed canonical SDDL")
    return str(aces[0].split(";")[5])


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
    attributes = int(get_attributes(_windows_extended_path(path)))
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


def _normalize_platform_alias(path: Path) -> Path:
    """Canonicalize only Apple's root-owned standard temporary aliases."""

    candidate = Path(path)
    if sys.platform != "darwin":
        return candidate
    absolute = candidate.absolute()
    parts = absolute.parts
    if len(parts) < 2 or parts[1] not in {"tmp", "var"}:
        return candidate
    root = Path("/") / parts[1]
    resolved_root = root.resolve(strict=True)
    expected_root = Path("/private") / parts[1]
    if resolved_root != expected_root:
        raise UnsafePathError(f"unexpected macOS filesystem alias: {root.name}")
    return resolved_root.joinpath(*parts[2:])


def _reject_existing_ancestor_indirection(path: Path) -> None:
    absolute = path.absolute()
    chain = tuple(reversed(absolute.parents)) + (absolute,)
    for candidate in chain:
        if os.path.lexists(candidate):
            reject_reparse_or_symlink(candidate)


def reject_path_indirection(path: Path) -> Path:
    """Return a platform-normalized path after rejecting linked existing ancestors."""

    candidate = _normalize_platform_alias(Path(path).absolute())
    _reject_existing_ancestor_indirection(candidate)
    return candidate


def _protect_windows_path(path: Path) -> None:
    """Install a protected current-user/system/admin DACL."""

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
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # D:P blocks inherited access. Bind access to the account actually running
    # Auralign, not to an unrelated owner of a pre-provisioned runner path.
    directory = path.is_dir()
    current_sid = _windows_current_user_sid()
    sddl = _windows_private_sddl(current_sid, directory=directory)
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        error = _windows_last_error()
        raise OSError(error, "could not construct private Windows DACL", str(path))
    try:
        dacl_security_information = 0x00000004
        protected_dacl_security_information = 0x80000000
        if not set_security(
            _windows_extended_path(path),
            dacl_security_information | protected_dacl_security_information,
            descriptor,
        ):
            error = _windows_last_error()
            raise OSError(error, "could not apply private Windows DACL", str(path))
    finally:
        kernel32.LocalFree(descriptor)
    _verify_windows_private_dacl(path, directory=directory, current_sid=current_sid)


def _verify_windows_private_dacl(
    path: Path,
    *,
    directory: bool,
    current_sid: str | None = None,
) -> None:
    """Read back the protected current-user/system/admin DACL after publication."""

    advapi32 = _windows_dll("advapi32")
    kernel32 = _windows_dll("kernel32")
    descriptor = ctypes.c_void_p()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    get_security = advapi32.GetNamedSecurityInfoW
    get_security.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_security.restype = ctypes.c_uint32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    result = int(
        get_security(
            _windows_extended_path(path),
            1,
            0x00000001 | 0x00000004,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
    )
    if result:
        raise OSError(result, "could not read back private Windows DACL", path.name)
    try:
        control = ctypes.c_uint16()
        revision = ctypes.c_uint32()
        get_control = advapi32.GetSecurityDescriptorControl
        get_control.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_control.restype = ctypes.c_int
        if not get_control(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            error = _windows_last_error()
            raise OSError(error, "could not verify private Windows DACL", path.name)
        if not control.value & 0x1000 or not owner.value or not dacl.value:
            raise UnsafePathError(f"private Windows DACL is not protected: {path.name}")
        convert = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
        convert.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_wchar_p),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        convert.restype = ctypes.c_int
        sddl_pointer = ctypes.c_wchar_p()
        sddl_length = ctypes.c_uint32()
        if not convert(
            descriptor,
            1,
            0x00000004,
            ctypes.byref(sddl_pointer),
            ctypes.byref(sddl_length),
        ):
            error = _windows_last_error()
            raise OSError(error, "could not inspect private Windows DACL", path.name)
        try:
            sddl = sddl_pointer.value or ""
        finally:
            kernel32.LocalFree(ctypes.cast(sddl_pointer, ctypes.c_void_p))
        expected_current_sid = current_sid or _windows_current_user_sid()
        expected = set(_windows_private_trustees(expected_current_sid))
        trustee_aliases = {sid: sid for sid in expected}
        trustee_aliases.update({_windows_sddl_trustee_spelling(sid): sid for sid in expected})
        observed: list[str] = []
        for ace in re.findall(r"\(([^()]*)\)", sddl):
            fields = ace.split(";")
            if len(fields) != 6:
                raise UnsafePathError(f"private Windows DACL is malformed: {path.name}")
            ace_type, ace_flags, rights, object_guid, inherit_guid, trustee = fields
            normalized = trustee_aliases.get(trustee, trustee)
            parsed_flags = {ace_flags[index : index + 2] for index in range(0, len(ace_flags), 2)}
            expected_flags = {"OI", "CI"} if directory else set()
            if (
                ace_type != "A"
                or len(ace_flags) % 2
                or parsed_flags != expected_flags
                or rights != "FA"
                or object_guid
                or inherit_guid
                or normalized not in expected
            ):
                raise UnsafePathError(f"private Windows DACL grants unexpected access: {path.name}")
            observed.append(normalized)
        if len(observed) != len(expected) or set(observed) != expected:
            raise UnsafePathError(f"private Windows DACL is incomplete: {path.name}")
    finally:
        kernel32.LocalFree(descriptor)


def _normalize_windows_final_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.normpath(value))


def _validate_windows_descriptor_path(descriptor: int, candidate: Path) -> None:
    """Validate reparse attributes and the final path through the opened handle."""

    msvcrt = importlib.import_module("msvcrt")
    handle = msvcrt.get_osfhandle(descriptor)
    kernel32 = _windows_dll("kernel32")

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("file_attributes", ctypes.c_uint32), ("reparse_tag", ctypes.c_uint32)]

    attributes = FileAttributeTagInfo()
    get_info = kernel32.GetFileInformationByHandleEx
    get_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    get_info.restype = ctypes.c_int
    if not get_info(
        ctypes.c_void_p(handle),
        9,
        ctypes.byref(attributes),
        ctypes.sizeof(attributes),
    ):
        error = _windows_last_error()
        raise OSError(error, "could not validate private Windows handle", candidate.name)
    if attributes.file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafePathError(f"private path must not be a reparse point: {candidate.name}")

    get_final = kernel32.GetFinalPathNameByHandleW
    get_final.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    get_final.restype = ctypes.c_uint32
    size = int(get_final(ctypes.c_void_p(handle), None, 0, 0))
    if size <= 0:
        error = _windows_last_error()
        raise OSError(error, "could not resolve private Windows handle", candidate.name)
    buffer = ctypes.create_unicode_buffer(size + 1)
    if not get_final(ctypes.c_void_p(handle), buffer, len(buffer), 0):
        error = _windows_last_error()
        raise OSError(error, "could not resolve private Windows handle", candidate.name)
    expected = _normalize_windows_final_path(os.path.abspath(candidate))
    actual = _normalize_windows_final_path(buffer.value)
    if actual != expected:
        raise UnsafePathError(f"private path changed while opening: {candidate.name}")


def ensure_private_directory(path: Path) -> Path:
    """Create or validate an owner-private, non-linked directory."""

    directory = _normalize_platform_alias(Path(path))
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


def private_path_is_secure(path: Path, *, directory: bool = False) -> bool:
    """Return whether an existing path has the native owner-private contract."""

    try:
        candidate = _normalize_platform_alias(Path(path))
        _reject_existing_ancestor_indirection(candidate)
        metadata = candidate.lstat()
        expected_type = (
            stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        )
        if not expected_type:
            return False
        if _WINDOWS:
            _verify_windows_private_dacl(candidate, directory=directory)
        else:
            expected_mode = 0o700 if directory else 0o600
            if stat.S_IMODE(metadata.st_mode) != expected_mode:
                return False
    except (OSError, UnsafePathError):
        return False
    return True


def open_regular_nofollow(path: Path, flags: int, mode: int = 0o600) -> int:
    """Open a regular file without following its final path component."""

    candidate = _normalize_platform_alias(Path(path))
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
            _validate_windows_descriptor_path(descriptor, candidate)
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


def _windows_directory_flush_is_unsupported(error: int) -> bool:
    """Return whether a write-capable directory handle cannot be flushed."""

    return error in {1, 5, 6, 50}


def fsync_directory(path: Path) -> None:
    """Flush directory metadata where the native platform supports it."""

    directory = _normalize_platform_alias(Path(path))
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
        flush_file_buffers = kernel32.FlushFileBuffers
        flush_file_buffers.argtypes = [ctypes.c_void_p]
        flush_file_buffers.restype = ctypes.c_int
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = create_file(
            _windows_extended_path(directory),
            0x80000000 | 0x40000000,
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
            if not flush_file_buffers(handle):
                error = _windows_last_error()
                # Windows does not document directory handles as valid inputs
                # to FlushFileBuffers. Because CreateFileW already succeeded
                # with GENERIC_WRITE, access-denied/invalid-handle here describe
                # that directory-only limitation; open and real I/O errors
                # remain fatal.
                if not _windows_directory_flush_is_unsupported(error):
                    raise OSError(error, "could not flush directory", directory.name)
        finally:
            close_handle(handle)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Durably publish private bytes with same-directory atomic replacement."""

    target = _normalize_platform_alias(Path(path))
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
    return _normalize_platform_alias(Path(path))


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
    peak_resident_bytes: int


def current_process_usage() -> ProcessUsage:
    """Read process usage through native POSIX or Win32 facilities."""

    if not _WINDOWS:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        scale = 1 if sys.platform == "darwin" else 1024
        peak = int(usage.ru_maxrss) * scale
        resident = _posix_current_resident_bytes() or peak
        return ProcessUsage(usage.ru_utime, usage.ru_stime, resident, peak)

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

    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    process = kernel32.GetCurrentProcess()
    created, exited, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
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
    psapi.GetProcessMemoryInfo.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ProcessMemoryCounters),
        ctypes.c_uint32,
    ]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    if not psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
        error = _windows_last_error()
        raise OSError(error, "could not read Windows process memory")
    return ProcessUsage(
        user.seconds(),
        kernel.seconds(),
        int(memory.working_set_size),
        int(memory.peak_working_set_size),
    )


def _posix_current_resident_bytes() -> int | None:
    if sys.platform.startswith("linux"):
        try:
            fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
            return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform == "darwin":
        try:
            libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")

            class TimeValue(ctypes.Structure):
                _fields_ = [("seconds", ctypes.c_int32), ("microseconds", ctypes.c_int32)]

            class MachTaskBasicInfo(ctypes.Structure):
                _fields_ = [
                    ("virtual_size", ctypes.c_uint64),
                    ("resident_size", ctypes.c_uint64),
                    ("resident_size_max", ctypes.c_uint64),
                    ("user_time", TimeValue),
                    ("system_time", TimeValue),
                    ("policy", ctypes.c_int32),
                    ("suspend_count", ctypes.c_int32),
                ]

            info = MachTaskBasicInfo()
            count = ctypes.c_uint32(ctypes.sizeof(info) // ctypes.sizeof(ctypes.c_int32))
            task = libsystem.mach_task_self()
            result = libsystem.task_info(
                task,
                20,
                ctypes.byref(info),
                ctypes.byref(count),
            )
            return int(info.resident_size) if result == 0 and info.resident_size > 0 else None
        except (AttributeError, OSError):
            return None
    return None
