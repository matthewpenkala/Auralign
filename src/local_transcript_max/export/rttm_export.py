# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""RTTM exporter."""

from __future__ import annotations

from local_transcript_max.schemas import Transcript


def render_rttm(transcript: Transcript, file_id: str = "audio") -> str:
    lines: list[str] = []
    for turn in transcript.diarization_turns:
        duration = max(0.0, turn.end - turn.start)
        if duration <= 0 or not turn.speaker:
            continue
        line = (
            f"SPEAKER {file_id} 1 {turn.start:.3f} {duration:.3f} "
            f"<NA> <NA> {turn.speaker} <NA> <NA>"
        )
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")
