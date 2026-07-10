# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Markdown exporter."""

from __future__ import annotations

from local_transcript_max.schemas import Transcript


def render_markdown(transcript: Transcript) -> str:
    lines = ["# Transcript", ""]
    for turn in transcript.speaker_turns:
        lines.append(f"**{turn.speaker}**  ")
        lines.append(f"{turn.text}")
        lines.append("")
    if not transcript.speaker_turns:
        lines.extend(segment.text for segment in transcript.segments)
    return "\n".join(lines).rstrip() + "\n"
