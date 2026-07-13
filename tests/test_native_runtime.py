# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.native_runtime import PYAV_FFMPEG7_ALIASES, probe_torchcodec


def test_macos_torchcodec_probe_uses_process_start_dyld_environment(
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
    observed: dict[str, str] = {}

    def completed(*_args: object, **kwargs: object) -> SimpleNamespace:
        observed.update(kwargs["env"])  # type: ignore[arg-type]
        return SimpleNamespace(returncode=0, stdout="0.7.0\n")

    monkeypatch.setattr("local_transcript_max.native_runtime.subprocess.run", completed)

    assert probe_torchcodec() == "0.7.0"
    assert observed["DYLD_LIBRARY_PATH"].split(":", maxsplit=1)[0] == str(dylibs)


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
