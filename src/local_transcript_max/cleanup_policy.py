# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Network-free validation policy for optional transcript-cleanup endpoints."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlparse


class CleanupRejectedError(RuntimeError):
    """Raised when cleanup configuration or output violates safety constraints."""


_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SENSITIVE_QUERY_PARAMETERS = frozenset(
    {
        "access_token",
        "apikey",
        "api_key",
        "auth",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    }
)


def validate_cleanup_endpoint(
    endpoint: str,
    *,
    allow_remote_cleanup: bool,
    offline: bool = False,
) -> None:
    """Validate cleanup egress policy without resolving or contacting the endpoint."""

    parsed = urlparse(endpoint)
    host = parsed.hostname or ""
    if parsed.username or parsed.password or _has_sensitive_query_param(parsed.query):
        raise CleanupRejectedError(
            "Cleanup endpoint must not contain inline credentials. "
            "Use local configuration outside the URL."
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise CleanupRejectedError("Cleanup endpoint contains an invalid port.") from exc
    if parsed.scheme not in {"http", "https"} or not host:
        raise CleanupRejectedError("Cleanup endpoint must be an absolute HTTP(S) URL.")
    if parsed.fragment:
        raise CleanupRejectedError("Cleanup endpoint must not contain a URL fragment.")
    if offline and host not in _LOCAL_HOSTS:
        raise CleanupRejectedError("Offline mode forbids non-local cleanup endpoints.")
    if host not in _LOCAL_HOSTS and not allow_remote_cleanup:
        raise CleanupRejectedError(
            "Cleanup endpoint must be localhost unless --allow-remote-cleanup is set."
        )


def validate_cleanup_policy(
    *,
    mode: str,
    endpoint: str,
    allow_remote_cleanup: bool,
    offline: bool = False,
) -> None:
    """Validate the complete cleanup egress policy without performing I/O."""

    if mode == "off":
        return
    if mode == "apple-foundation":
        # Apple's system model is invoked in-process. There is no endpoint and
        # therefore no cleanup-network egress surface to validate.
        return
    if mode != "local-llm":
        raise CleanupRejectedError(f"Unsupported cleanup mode '{mode}'.")
    validate_cleanup_endpoint(
        endpoint,
        allow_remote_cleanup=allow_remote_cleanup,
        offline=offline,
    )


def _has_sensitive_query_param(query: str) -> bool:
    for key, _value in parse_qsl(query, keep_blank_values=True):
        normalized = key.strip().lower().replace("-", "_")
        if normalized in _SENSITIVE_QUERY_PARAMETERS:
            return True
    return False
