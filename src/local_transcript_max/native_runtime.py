# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Native dependency probes that need process-start environment on macOS."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
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


def probe_torchcodec() -> str:
    """Actually import TorchCodec, using a clean macOS ABI probe subprocess."""

    if platform.system() != "Darwin":
        import torchcodec

        return str(getattr(torchcodec, "__version__", "installed"))

    try:
        import av
    except Exception as exc:
        raise BackendUnavailableError(
            "PyAV is unavailable for the reviewed macOS TorchCodec FFmpeg 7 boundary."
        ) from exc
    dylibs = Path(av.__file__).resolve().parent / ".dylibs"
    for name in PYAV_FFMPEG7_ALIASES:
        candidate = dylibs / name
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(dylibs.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise BackendUnavailableError(
                "The reviewed PyAV FFmpeg 7 runtime contract is incomplete."
            ) from exc
        if not resolved.is_file():
            raise BackendUnavailableError(
                "The reviewed PyAV FFmpeg 7 runtime contract is incomplete."
            )

    environment = os.environ.copy()
    current = environment.get("DYLD_LIBRARY_PATH")
    environment["DYLD_LIBRARY_PATH"] = f"{dylibs}{os.pathsep + current if current else ''}"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import torchcodec; print(getattr(torchcodec, '__version__', 'installed'))",
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise BackendUnavailableError(
            "TorchCodec could not load against the reviewed PyAV FFmpeg 7 ABI in a clean "
            "macOS subprocess. Run the native installer and source auralign-env.sh."
        ) from exc
    if completed.returncode != 0:
        raise BackendUnavailableError(
            "TorchCodec could not load against the reviewed PyAV FFmpeg 7 ABI in a clean "
            "macOS subprocess. Run the native installer and source auralign-env.sh."
        )
    return completed.stdout.strip() or "installed"
