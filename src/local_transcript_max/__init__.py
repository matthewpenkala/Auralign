# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Local Transcript Max package."""

from importlib import metadata as _metadata


def _resolve_version() -> str:
    """Return installed distribution metadata without duplicating the release version."""

    try:
        return _metadata.version("local-transcript-max")
    except _metadata.PackageNotFoundError:
        # Source checkouts may be imported before the project is installed.
        return "0+unknown"


__version__ = _resolve_version()

__all__ = ["__version__"]
