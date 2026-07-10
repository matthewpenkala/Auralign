# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Alignment backend contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from local_transcript_max.config import PipelineConfig
from local_transcript_max.schemas import AlignedWord, ASRResult


class AlignmentBackend(Protocol):
    def align(
        self,
        audio_path: Path,
        asr_result: ASRResult,
        config: PipelineConfig,
    ) -> list[AlignedWord]:
        """Produce aligned word timestamps."""
