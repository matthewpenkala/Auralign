# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Diarization backend contracts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from local_transcript_max.config import PipelineConfig
from local_transcript_max.schemas import DiarizationResult


class DiarizationBackend(Protocol):
    def diarize(self, audio_path: Path, config: PipelineConfig) -> DiarizationResult:
        """Diarize an audio file."""


class ProgressDiarizationBackend(DiarizationBackend, Protocol):
    def diarize_with_progress(
        self,
        audio_path: Path,
        config: PipelineConfig,
        progress: Callable[[dict[str, Any]], None],
    ) -> DiarizationResult:
        """Diarize an audio file with backend progress events."""


def diarize_backend(
    backend: DiarizationBackend,
    audio_path: Path,
    config: PipelineConfig,
    *,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> DiarizationResult:
    diarize_with_progress = getattr(backend, "diarize_with_progress", None)
    if callable(diarize_with_progress) and progress is not None:
        progress_backend = cast(ProgressDiarizationBackend, backend)
        return progress_backend.diarize_with_progress(audio_path, config, progress)
    return backend.diarize(audio_path, config)
