# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""JSONL exporter for word timelines."""

from __future__ import annotations

import json
from pathlib import Path

from local_transcript_max.schemas import Transcript
from local_transcript_max.utils import atomic_text_writer


def write_words_jsonl(path: Path, transcript: Transcript) -> None:
    with atomic_text_writer(path) as handle:
        for word in transcript.words:
            handle.write(json.dumps(word.model_dump(mode="json"), sort_keys=True))
            handle.write("\n")
