# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

from local_transcript_max.redaction import (
    privacy_safe_obj,
    privacy_safe_text,
    redact_obj,
    redact_text,
)


def test_redact_text_masks_hf_and_bearer_tokens() -> None:
    hf_token = "hf_" + "abcdefghijklmnopqrstuvwxyz"
    bearer_token = "abcdefghijklmnop"
    text = f"HF_TOKEN={hf_token} bearer {bearer_token}"
    redacted = redact_text(text)
    assert "hf_" not in redacted
    assert bearer_token not in redacted
    assert "<redacted>" in redacted


def test_redact_text_masks_url_userinfo_and_sensitive_query_params() -> None:
    text = (
        "endpoint=https://user:pass@example.invalid/v1?token=abc123&safe=true "
        "next=http://example.invalid/path?api_key=secret"
    )
    redacted = redact_text(text)
    assert "user:pass" not in redacted
    assert "abc123" not in redacted
    assert "api_key=secret" not in redacted
    assert "safe=true" in redacted


def test_redact_obj_masks_secret_keys() -> None:
    hf_token = "hf_" + "abcdefghijklmnopqrstuvwxyz"
    bearer_token = "Bearer " + "abcdefghijklmnop"
    payload = {
        "HF_TOKEN": hf_token,
        "nested": {"authorization": bearer_token},
        "path": "/Users/example/audio.wav",
    }
    redacted = redact_obj(payload)
    assert redacted["HF_TOKEN"] == "<redacted>"
    assert redacted["nested"]["authorization"] == "<redacted>"
    assert redacted["path"] == "/Users/example/audio.wav"


def test_redact_obj_masks_container_values_under_secret_keys() -> None:
    payload = {
        "api_key": {"primary": "secret", "fallback": "also-secret"},
        "credentials": ["first-secret", "second-secret"],
        "authorization": ("Bearer", "secret"),
        "nested": {"safe": ["visible"]},
    }

    assert redact_obj(payload) == {
        "api_key": "<redacted>",
        "credentials": "<redacted>",
        "authorization": "<redacted>",
        "nested": {"safe": ["visible"]},
    }


def test_privacy_safe_text_masks_posix_windows_and_explicit_paths() -> None:
    private = Path("/private/jobs/customer-a")
    text = (
        "failed at /Users/example/recordings/call.wav and "
        r"C:\\Users\\example\\recordings\\call.wav in /private/jobs/customer-a"
    )

    safe = privacy_safe_text(text, private_paths=(private,))

    assert "/Users/example" not in safe
    assert "C:\\Users\\example" not in safe
    assert str(private) not in safe
    assert safe.count("<local-path>") == 3


def test_privacy_safe_obj_masks_nested_secrets_and_paths() -> None:
    private_path = Path("/Users/example/private/models")
    secret = "hf_" + "abcdefghijklmnopqrstuvwxyz"
    payload = {
        "ok": True,
        "model_cache": private_path,
        "checks": [
            {
                "message": f"failed at {private_path}/model.bin; HF_TOKEN={secret}"
            }
        ],
        "credentials": {"user": "private", "password": "private"},
    }

    safe = privacy_safe_obj(payload)

    assert safe["ok"] is True
    assert safe["model_cache"] == "<local-path>"
    assert safe["credentials"] == "<redacted>"
    assert str(private_path) not in str(safe)
    assert secret not in str(safe)
    assert "<local-path>" in safe["checks"][0]["message"]
    assert "<redacted>" in safe["checks"][0]["message"]
