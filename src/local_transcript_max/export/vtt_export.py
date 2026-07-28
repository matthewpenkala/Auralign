# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""WebVTT exporter."""

from __future__ import annotations

from local_transcript_max.config import SubtitleExportConfig
from local_transcript_max.export.subtitle import (
    cue_millisecond_interval,
    format_cue_text,
    iter_subtitle_cues,
    overlay_cue_word_surfaces,
    timestamp_milliseconds,
)
from local_transcript_max.schemas import Transcript


def vtt_timestamp(seconds: float) -> str:
    return _vtt_milliseconds(timestamp_milliseconds(seconds))


def _vtt_milliseconds(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def render_vtt(
    transcript: Transcript,
    options: SubtitleExportConfig | None = None,
    *,
    word_surfaces: list[str] | None = None,
) -> str:
    resolved_options = options or SubtitleExportConfig()
    cues = iter_subtitle_cues(transcript, resolved_options)
    if word_surfaces is not None:
        cues = overlay_cue_word_surfaces(
            cues,
            word_surfaces,
            resolved_options,
            transcript.language,
        )
    lines = ["WEBVTT", ""]
    for cue in cues:
        start_ms, end_ms = cue_millisecond_interval(cue.start, cue.end)
        lines.append(f"{_vtt_milliseconds(start_ms)} --> {_vtt_milliseconds(end_ms)}")
        lines.append(format_cue_text(cue, resolved_options))
        lines.append("")
    return "\n".join(lines)
