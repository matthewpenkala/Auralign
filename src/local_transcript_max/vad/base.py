# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""VAD backend contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from local_transcript_max.schemas import VADRegion


class VADBackend(Protocol):
    def detect(self, audio_path: Path) -> list[VADRegion]:
        """Return speech regions for an audio file."""
