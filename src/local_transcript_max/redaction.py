# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Secret redaction utilities for logs and debug reports."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SECRET_KEY_RE = re.compile(
    r"(token|api[_-]?key|authorization|bearer|secret|password|credential)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"hf_[A-Za-z0-9_=-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_=-]{20,}"),
    re.compile(r"sk-proj-[A-Za-z0-9_=-]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(HF_TOKEN|LTM_HF_TOKEN|API_KEY)=([^,\s;]+)"),
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s?#@]+@)"),
    re.compile(
        r"(?i)([?&](?:access_)?token|[?&]api[_-]?key|[?&]apikey|[?&]secret|"
        r"[?&]password|[?&]authorization|[?&]credential)=([^&#\s]+)"
    ),
)
ABSOLUTE_LOCAL_PATH_RE = re.compile(r"(?<![A-Za-z0-9:/])/(?:[^\s,;\"'()\[\]{}]+/?)*")
WINDOWS_LOCAL_PATH_RE = re.compile(r"(?i)(?<![A-Za-z0-9])\b[A-Z]:\\[^\s,;\"']+")


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.pattern.startswith("(?i)(HF_TOKEN"):
            redacted = pattern.sub(r"\1=<redacted>", redacted)
        elif pattern.pattern.startswith("(?i)\\b([a-z]"):
            redacted = pattern.sub(r"\1<redacted>@", redacted)
        elif pattern.pattern.startswith("(?i)([?&]"):
            redacted = pattern.sub(r"\1=<redacted>", redacted)
        else:
            redacted = pattern.sub("<redacted>", redacted)
    return redacted


def privacy_safe_text(text: str, *, private_paths: tuple[Path, ...] = ()) -> str:
    """Redact credentials and local absolute paths from public error text."""
    safe = redact_text(text)
    for path in sorted((str(item) for item in private_paths), key=len, reverse=True):
        safe = safe.replace(path, "<local-path>")
    if Path(safe).is_absolute():
        return "<local-path>"
    safe = ABSOLUTE_LOCAL_PATH_RE.sub("<local-path>", safe)
    return WINDOWS_LOCAL_PATH_RE.sub("<local-path>", safe)


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                if isinstance(item, bool) or item is None or (isinstance(item, str) and not item):
                    result[key_text] = item
                else:
                    result[key_text] = "<redacted>"
            else:
                result[key_text] = redact_obj(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [redact_obj(item) for item in value]
    return value


def privacy_safe_obj(value: Any) -> Any:
    """Redact secrets and local paths throughout a machine-readable value."""

    return _privacy_safe_obj(redact_obj(value))


def _privacy_safe_obj(value: Any) -> Any:
    if isinstance(value, str):
        return privacy_safe_text(value)
    if isinstance(value, Path):
        return "<local-path>"
    if isinstance(value, Mapping):
        return {
            privacy_safe_text(str(key)): _privacy_safe_obj(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_privacy_safe_obj(item) for item in value]
    return value
