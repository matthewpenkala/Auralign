# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""SubRip exporter."""

from __future__ import annotations

from local_transcript_max.config import SubtitleExportConfig
from local_transcript_max.export.subtitle import (
    cue_millisecond_interval,
    format_cue_text,
    iter_subtitle_cues,
    normalize_cues,
    overlay_cue_word_surfaces,
    timestamp_milliseconds,
)
from local_transcript_max.schemas import Transcript

__all__ = ["normalize_cues", "render_srt", "srt_timestamp"]


def srt_timestamp(seconds: float) -> str:
    return _srt_milliseconds(timestamp_milliseconds(seconds))


def _srt_milliseconds(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def render_srt(
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
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        start_ms, end_ms = cue_millisecond_interval(cue.start, cue.end)
        blocks.append(
            f"{index}\n"
            f"{_srt_milliseconds(start_ms)} --> {_srt_milliseconds(end_ms)}\n"
            f"{format_cue_text(cue, resolved_options)}\n"
        )
    return "\n".join(blocks)
