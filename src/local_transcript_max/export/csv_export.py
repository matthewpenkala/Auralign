# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""CSV exporter for speaker turns."""

from __future__ import annotations

import csv
from pathlib import Path

from local_transcript_max.schemas import Transcript
from local_transcript_max.utils import atomic_text_writer


def write_segments_csv(path: Path, transcript: Transcript) -> None:
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["speaker", "start", "end", "text"])
        writer.writeheader()
        for turn in transcript.speaker_turns:
            writer.writerow(
                {
                    "speaker": turn.speaker,
                    "start": f"{turn.start:.3f}",
                    "end": f"{turn.end:.3f}",
                    "text": turn.text,
                }
            )


def write_words_csv(path: Path, transcript: Transcript) -> None:
    fieldnames = [
        "text",
        "start",
        "end",
        "speaker",
        "uncertain_speaker",
        "alignment_source",
        "confidence",
        "assignment_source",
    ]
    with atomic_text_writer(path, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for word in transcript.words:
            writer.writerow(
                {
                    "text": word.text,
                    "start": "" if word.start is None else f"{word.start:.3f}",
                    "end": "" if word.end is None else f"{word.end:.3f}",
                    "speaker": word.speaker or "",
                    "uncertain_speaker": str(word.uncertain_speaker).lower(),
                    "alignment_source": word.alignment_source,
                    "confidence": "" if word.confidence is None else f"{word.confidence:.6f}",
                    "assignment_source": word.assignment_source or "",
                }
            )
