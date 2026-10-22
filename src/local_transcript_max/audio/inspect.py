# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""FFprobe-backed media inspection."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from local_transcript_max.errors import LTMError
from local_transcript_max.native_runtime import reviewed_media_executable
from local_transcript_max.schemas import AudioMetadata
from local_transcript_max.utils import run_command


def inspect_media(path: Path, *, stream_index: int | None = None) -> AudioMetadata:
    """Inspect the first audio stream or an explicit absolute FFprobe index.

    An absolute index includes video/subtitle streams; it is not an audio-only
    ordinal. The selected index also binds canonical decoding to this probe.
    """

    if stream_index is not None and (type(stream_index) is not int or stream_index < 0):
        raise LTMError("Audio stream index must be a non-negative absolute FFprobe index.")
    args = [
        reviewed_media_executable("ffprobe"),
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    completed = run_command(args)
    payload: dict[str, Any] = json.loads(completed.stdout)
    streams: list[dict[str, Any]] = payload.get("streams", [])
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio_streams:
        raise LTMError("Input media contains no audio streams.")
    audio_stream = next(
        (stream for stream in audio_streams if stream.get("index") == stream_index),
        None,
    ) if stream_index is not None else audio_streams[0]
    if audio_stream is None:
        raise LTMError(
            f"Selected audio stream index {stream_index} is absent or is not an audio stream. "
            "Use an absolute stream index from ffprobe -show_streams."
        )
    selected_index = _int_or_none(audio_stream.get("index"))
    if selected_index is None or selected_index < 0:
        raise LTMError("FFprobe did not report a valid absolute audio stream index.")
    fmt: dict[str, Any] = payload.get("format", {})
    duration = _float_or_none(audio_stream.get("duration"))
    if duration is None:
        duration = _float_or_none(fmt.get("duration"))
    bit_rate = _int_or_none(audio_stream.get("bit_rate") or fmt.get("bit_rate"))
    return AudioMetadata(
        path=str(path),
        duration_seconds=duration,
        sample_rate=_int_or_none(audio_stream.get("sample_rate")),
        channels=_int_or_none(audio_stream.get("channels")),
        codec=audio_stream.get("codec_name"),
        bit_rate=bit_rate,
        format_name=fmt.get("format_name"),
        selected_audio_stream_index=selected_index,
        audio_stream_count=len(audio_streams),
        time_base=audio_stream.get("time_base"),
        start_time_seconds=_float_or_none(audio_stream.get("start_time")),
        metadata={k: v for k, v in fmt.get("tags", {}).items()},
    )


def _float_or_none(value: object) -> float | None:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
