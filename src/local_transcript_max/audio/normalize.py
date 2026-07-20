# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Conservative FFmpeg normalization into canonical working WAV."""

from __future__ import annotations

from pathlib import Path

from local_transcript_max.config import AudioConfig
from local_transcript_max.errors import LTMError
from local_transcript_max.native_runtime import reviewed_media_executable
from local_transcript_max.utils import run_command


def build_ffmpeg_command(input_path: Path, output_path: Path, config: AudioConfig) -> list[str]:
    if config.separate_vocals != "off":
        raise LTMError(
            "audio.separate_vocals=experimental is not implemented; refusing to "
            "silently publish an unseparated canonical audio artifact."
        )
    args = [reviewed_media_executable("ffmpeg"), "-y", "-i", str(input_path)]
    filters: list[str] = []
    if config.normalize_loudness:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if config.denoise != "off":
        # Conservative built-in FFT denoise. It is intentionally opt-in.
        strength = "0.08" if config.denoise == "mild" else "0.16"
        filters.append(f"afftdn=nr={strength}")
    if filters:
        args.extend(["-af", ",".join(filters)])
    args.extend(["-vn", "-acodec", "pcm_s16le", "-ar", str(config.sample_rate)])
    if config.mono and not config.preserve_channels:
        args.extend(["-ac", "1"])
    args.append(str(output_path))
    return args


def normalize_audio(input_path: Path, output_path: Path, config: AudioConfig) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(input_path, output_path, config)
    run_command(command)
    return command
