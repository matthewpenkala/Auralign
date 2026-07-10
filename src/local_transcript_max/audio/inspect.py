# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""FFprobe-backed media inspection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from local_transcript_max.schemas import AudioMetadata
from local_transcript_max.utils import run_command


def inspect_media(path: Path) -> AudioMetadata:
    args = [
        "ffprobe",
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
    audio_stream: dict[str, Any] = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"),
        {},
    )
    fmt: dict[str, Any] = payload.get("format", {})
    duration = _float_or_none(audio_stream.get("duration") or fmt.get("duration"))
    bit_rate = _int_or_none(audio_stream.get("bit_rate") or fmt.get("bit_rate"))
    return AudioMetadata(
        path=str(path),
        duration_seconds=duration,
        sample_rate=_int_or_none(audio_stream.get("sample_rate")),
        channels=_int_or_none(audio_stream.get("channels")),
        codec=audio_stream.get("codec_name"),
        bit_rate=bit_rate,
        format_name=fmt.get("format_name"),
        metadata={k: v for k, v in fmt.get("tags", {}).items()},
    )


def _float_or_none(value: object) -> float | None:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
