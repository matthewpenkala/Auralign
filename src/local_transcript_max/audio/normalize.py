# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Conservative FFmpeg normalization into canonical working WAV."""

from __future__ import annotations

from pathlib import Path

from local_transcript_max.config import AudioConfig
from local_transcript_max.errors import LTMError
from local_transcript_max.native_runtime import reviewed_media_executable
from local_transcript_max.schemas import AudioMetadata
from local_transcript_max.utils import run_command

from .inspect import inspect_media


def build_ffmpeg_command(
    input_path: Path,
    output_path: Path,
    config: AudioConfig,
    *,
    stream_index: int | None = None,
) -> list[str]:
    if config.separate_vocals != "off":
        raise LTMError(
            "audio.separate_vocals=experimental is not implemented; refusing to "
            "silently publish an unseparated canonical audio artifact."
        )
    args = [reviewed_media_executable("ffmpeg"), "-y", "-i", str(input_path)]
    selected_index = stream_index if stream_index is not None else config.stream_index
    # Never let FFmpeg select another microphone by default disposition or
    # channel count. A resolved probe supplies the absolute index in production.
    args.extend(["-map", f"0:{selected_index}" if selected_index is not None else "0:a:0"])
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


def normalize_audio(
    input_path: Path,
    output_path: Path,
    config: AudioConfig,
    *,
    input_metadata: AudioMetadata | None = None,
) -> list[str]:
    metadata = input_metadata or inspect_media(input_path, stream_index=config.stream_index)
    selected_index = metadata.selected_audio_stream_index
    if selected_index is None:
        # Older callers may supply metadata without stream identity. Re-probe
        # rather than permit FFmpeg's independent automatic stream selection.
        metadata = inspect_media(input_path, stream_index=config.stream_index)
        selected_index = metadata.selected_audio_stream_index
    if config.stream_index is not None and selected_index != config.stream_index:
        raise LTMError("Inspected audio stream does not match audio.stream_index.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(input_path, output_path, config, stream_index=selected_index)
    run_command(command)
    return command
