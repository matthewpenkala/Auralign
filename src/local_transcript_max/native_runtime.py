# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Native dependency probes for the reviewed macOS FFmpeg 7 closure."""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from .errors import BackendUnavailableError

PYAV_FFMPEG7_ALIASES = (
    "libavutil.59.dylib",
    "libswresample.5.dylib",
    "libswscale.8.dylib",
    "libavcodec.61.dylib",
    "libavformat.61.dylib",
    "libavfilter.10.dylib",
    "libavdevice.61.dylib",
)


def reviewed_media_executable(name: str) -> str:
    """Return an embedded media tool when running from a standalone runtime."""

    if name not in {"ffmpeg", "ffprobe"}:
        raise ValueError(f"unsupported reviewed media executable: {name}")
    executable = Path(sys.executable).resolve()
    if len(executable.parents) >= 2:
        bundled = executable.parents[1] / "bin" / name
        if bundled.is_file() and not bundled.is_symlink():
            return str(bundled)
    return name


def probe_torchcodec() -> str:
    """Actually import TorchCodec against the reviewed local FFmpeg 7 ABI."""

    if platform.system() != "Darwin":
        import torchcodec

        return str(getattr(torchcodec, "__version__", "installed"))

    try:
        import av
    except Exception as exc:
        raise BackendUnavailableError(
            "PyAV is unavailable for the reviewed macOS TorchCodec FFmpeg 7 boundary."
        ) from exc
    dylibs = _reviewed_ffmpeg_library_directory(Path(av.__file__).resolve().parent)
    libraries, aliases_complete = _reviewed_ffmpeg_libraries(dylibs)

    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("DYLD_")
    }
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if dylibs.name == ".dylibs":
        environment["DYLD_LIBRARY_PATH"] = str(dylibs)

    if not aliases_complete:
        with tempfile.TemporaryDirectory(prefix="auralign-ffmpeg7-aliases-") as temporary:
            alias_directory = Path(temporary)
            for name, library in zip(PYAV_FFMPEG7_ALIASES, libraries, strict=True):
                (alias_directory / name).symlink_to(library)
            environment["DYLD_LIBRARY_PATH"] = str(alias_directory)
            completed = _run_torchcodec_probe(libraries, environment)
    else:
        completed = _run_torchcodec_probe(libraries, environment)
    if completed.returncode != 0:
        raise BackendUnavailableError(
            "TorchCodec could not load against the reviewed PyAV FFmpeg 7 ABI in a clean "
            "macOS subprocess. Reinstall Auralign or repair the reviewed native runtime."
        )
    return completed.stdout.strip() or "installed"


def _reviewed_ffmpeg_libraries(dylibs: Path) -> tuple[list[Path], bool]:
    """Resolve exact aliases or the wheel's unique fully versioned equivalents."""

    root = dylibs.resolve(strict=True)
    libraries: list[Path] = []
    aliases_complete = True
    for name in PYAV_FFMPEG7_ALIASES:
        candidate = root / name
        if not candidate.exists() and dylibs.name == ".dylibs":
            aliases_complete = False
            prefix = name.removesuffix(".dylib")
            pattern = re.compile(rf"{re.escape(prefix)}(?:\.\d+)+\.dylib")
            matches = sorted(
                path
                for path in root.iterdir()
                if pattern.fullmatch(path.name) and path.is_file() and not path.is_symlink()
            )
            if len(matches) == 1:
                candidate = matches[0]
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise BackendUnavailableError(
                "The reviewed PyAV FFmpeg 7 runtime contract is incomplete."
            ) from exc
        if not resolved.is_file():
            raise BackendUnavailableError(
                "The reviewed PyAV FFmpeg 7 runtime contract is incomplete."
            )
        libraries.append(resolved)
    return libraries, aliases_complete


def _run_torchcodec_probe(
    libraries: list[Path], environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-I",
                "-c",
                (
                    "import ctypes,sys;"
                    "[ctypes.CDLL(path,mode=ctypes.RTLD_GLOBAL) for path in sys.argv[1:]];"
                    "import torchcodec;"
                    "print(getattr(torchcodec,'__version__','installed'))"
                ),
                *(str(path) for path in libraries),
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackendUnavailableError(
            "TorchCodec could not load against the reviewed PyAV FFmpeg 7 ABI in a clean "
            "macOS subprocess. Reinstall Auralign or repair the reviewed native runtime."
        ) from exc


def _reviewed_ffmpeg_library_directory(pyav_package: Path) -> Path:
    """Resolve either the development-wheel or standalone-app FFmpeg closure."""

    wheel_dylibs = pyav_package / ".dylibs"
    if wheel_dylibs.is_dir():
        return wheel_dylibs.resolve(strict=True)

    executable = Path(sys.executable).resolve()
    if len(executable.parents) >= 2:
        standalone = executable.parents[1] / "lib"
        if standalone.is_dir():
            return standalone.resolve(strict=True)

    raise BackendUnavailableError(
        "The reviewed PyAV FFmpeg 7 runtime contract is incomplete."
    )
