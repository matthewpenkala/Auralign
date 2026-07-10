# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Plain text exporter."""

from __future__ import annotations

from local_transcript_max.schemas import Transcript


def render_txt(transcript: Transcript) -> str:
    if transcript.speaker_turns:
        body = "\n\n".join(f"{turn.speaker}: {turn.text}" for turn in transcript.speaker_turns)
        return body + "\n"
    return "\n".join(segment.text for segment in transcript.segments).strip() + "\n"
