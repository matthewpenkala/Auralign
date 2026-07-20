# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.native_runtime import (
    PYAV_FFMPEG7_ALIASES,
    probe_torchcodec,
    reviewed_media_executable,
)


def test_macos_torchcodec_probe_scopes_dyld_to_reviewed_wheel_libraries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "av"
    dylibs = package / ".dylibs"
    dylibs.mkdir(parents=True)
    for name in PYAV_FFMPEG7_ALIASES:
        (dylibs / name).write_bytes(b"reviewed")
    av = ModuleType("av")
    av.__file__ = str(package / "__init__.py")
    monkeypatch.setitem(__import__("sys").modules, "av", av)
    monkeypatch.setattr("local_transcript_max.native_runtime.platform.system", lambda: "Darwin")
    observed: dict[str, object] = {}

    def completed(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="0.7.0\n")

    monkeypatch.setattr("local_transcript_max.native_runtime.subprocess.run", completed)

    assert probe_torchcodec() == "0.7.0"
    command = observed["command"]
    assert isinstance(command, list)
    assert command[:4] == [sys.executable, "-B", "-I", "-c"]
    assert command[4:]
    assert set(command[5:]) == {
        str((dylibs / name).resolve()) for name in PYAV_FFMPEG7_ALIASES
    }
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["DYLD_LIBRARY_PATH"] == str(dylibs.resolve())
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"


def test_macos_torchcodec_probe_accepts_standalone_runtime_library_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    executable = runtime / "bin/python3"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    library = runtime / "lib"
    library.mkdir()
    for name in PYAV_FFMPEG7_ALIASES:
        (library / name).write_bytes(b"reviewed")
    package = runtime / "lib/python3.11/site-packages/av"
    package.mkdir(parents=True)
    av = ModuleType("av")
    av.__file__ = str(package / "__init__.py")
    monkeypatch.setitem(sys.modules, "av", av)
    monkeypatch.setattr("local_transcript_max.native_runtime.sys.executable", str(executable))
    monkeypatch.setattr("local_transcript_max.native_runtime.platform.system", lambda: "Darwin")
    observed: dict[str, object] = {}

    def completed(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="0.7.0\n")

    monkeypatch.setattr("local_transcript_max.native_runtime.subprocess.run", completed)

    assert probe_torchcodec() == "0.7.0"
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["env"] is None


def test_macos_torchcodec_probe_fails_closed_on_missing_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "av"
    package.mkdir()
    av = ModuleType("av")
    av.__file__ = str(package / "__init__.py")
    monkeypatch.setitem(__import__("sys").modules, "av", av)
    monkeypatch.setattr("local_transcript_max.native_runtime.platform.system", lambda: "Darwin")

    with pytest.raises(BackendUnavailableError, match="contract is incomplete"):
        probe_torchcodec()


def test_macos_torchcodec_probe_translates_subprocess_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "av"
    dylibs = package / ".dylibs"
    dylibs.mkdir(parents=True)
    for name in PYAV_FFMPEG7_ALIASES:
        (dylibs / name).write_bytes(b"reviewed")
    av = ModuleType("av")
    av.__file__ = str(package / "__init__.py")
    monkeypatch.setitem(__import__("sys").modules, "av", av)
    monkeypatch.setattr("local_transcript_max.native_runtime.platform.system", lambda: "Darwin")

    def time_out(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(cmd=["python"], timeout=60)

    monkeypatch.setattr("local_transcript_max.native_runtime.subprocess.run", time_out)

    with pytest.raises(BackendUnavailableError, match="TorchCodec could not load"):
        probe_torchcodec()


def test_standalone_runtime_resolves_media_tools_relative_to_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime"
    executable = runtime / "bin/python3"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    ffmpeg = runtime / "bin/ffmpeg"
    ffmpeg.write_bytes(b"reviewed")
    monkeypatch.setattr("local_transcript_max.native_runtime.sys.executable", str(executable))

    assert reviewed_media_executable("ffmpeg") == str(ffmpeg)
    assert reviewed_media_executable("ffprobe") == "ffprobe"


def test_reviewed_media_executable_rejects_unreviewed_tool() -> None:
    with pytest.raises(ValueError, match="unsupported reviewed media executable"):
        reviewed_media_executable("curl")
