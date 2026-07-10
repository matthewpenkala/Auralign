# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Compatibility imports for the energy VAD's former, misleading module path.

This module does not contain or load a Silero model. New code should import the
backend from :mod:`local_transcript_max.vad.energy`.
"""

from __future__ import annotations

from .energy import (
    EnergyVAD,
    EnergyVADAudioMetadata,
    EnergyVADConfig,
    EnergyVADMetadata,
    EnergyVADResult,
    merge_short_gaps,
)

__all__ = [
    "EnergyVAD",
    "EnergyVADAudioMetadata",
    "EnergyVADConfig",
    "EnergyVADMetadata",
    "EnergyVADResult",
    "merge_short_gaps",
]
