# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""WebVTT exporter."""

from __future__ import annotations

from local_transcript_max.config import SubtitleExportConfig
from local_transcript_max.export.subtitle import format_cue_text, iter_subtitle_cues
from local_transcript_max.schemas import Transcript


def vtt_timestamp(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}.{millis:03}"


def render_vtt(transcript: Transcript, options: SubtitleExportConfig | None = None) -> str:
    cues = iter_subtitle_cues(transcript, options)
    lines = ["WEBVTT", ""]
    for cue in cues:
        lines.append(f"{vtt_timestamp(cue.start)} --> {vtt_timestamp(cue.end)}")
        lines.append(format_cue_text(cue, options))
        lines.append("")
    return "\n".join(lines)
