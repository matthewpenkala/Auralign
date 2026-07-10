# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""SubRip exporter."""

from __future__ import annotations

from local_transcript_max.config import SubtitleExportConfig
from local_transcript_max.export.subtitle import (
    format_cue_text,
    iter_subtitle_cues,
    normalize_cues,
)
from local_transcript_max.schemas import Transcript

__all__ = ["normalize_cues", "render_srt", "srt_timestamp"]


def srt_timestamp(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def render_srt(transcript: Transcript, options: SubtitleExportConfig | None = None) -> str:
    cues = iter_subtitle_cues(transcript, options)
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{index}\n"
            f"{srt_timestamp(cue.start)} --> {srt_timestamp(cue.end)}\n"
            f"{format_cue_text(cue, options)}\n"
        )
    return "\n".join(blocks)
