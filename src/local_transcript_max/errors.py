# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Project-specific exceptions with user-actionable messages."""

from __future__ import annotations


class LTMError(RuntimeError):
    """Base class for Local Transcript Max errors."""


class BackendUnavailableError(LTMError):
    """Raised when an optional backend dependency is not installed."""


class ModelCacheError(LTMError):
    """Raised when a required model is not present in the local cache."""


class OfflineModeError(ModelCacheError):
    """Raised when offline mode prevents model or metadata access."""


class CommandExecutionError(LTMError):
    """Raised when a required local command is missing or fails."""
