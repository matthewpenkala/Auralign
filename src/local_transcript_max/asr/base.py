# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""ASR backend contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from local_transcript_max.config import PipelineConfig
from local_transcript_max.schemas import ASRResult, VADRegion


class ASRBackend(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        config: PipelineConfig,
        *,
        vad_regions: list[VADRegion] | None = None,
    ) -> ASRResult:
        """Transcribe an audio file."""


class NullASRBackend:
    def transcribe(
        self,
        audio_path: Path,
        config: PipelineConfig,
        *,
        vad_regions: list[VADRegion] | None = None,
    ) -> ASRResult:
        raise RuntimeError("No ASR backend configured.")
