# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Optional local transcript cleanup with strict preservation guardrails."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .cleanup_policy import (
    CleanupRejectedError,
    validate_cleanup_policy,
)
from .cleanup_policy import (
    validate_cleanup_endpoint as validate_cleanup_endpoint,
)
from .config import PipelineConfig
from .export.json_export import write_json_artifact
from .export.markdown_export import render_markdown
from .redaction import redact_obj
from .schemas import Transcript
from .utils import atomic_text_write

MAX_CLEANUP_RESPONSE_BYTES = 4 * 1024 * 1024


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise CleanupRejectedError("Cleanup endpoint redirects are not allowed.")


def run_cleanup(
    transcript: Transcript,
    out_dir: Path,
    config: PipelineConfig,
) -> dict[str, object]:
    if config.cleanup.mode == "off":
        return {"enabled": False, "reason": "cleanup disabled"}
    validate_cleanup_policy(
        mode=config.cleanup.mode,
        endpoint=config.cleanup.endpoint,
        allow_remote_cleanup=config.cleanup.allow_remote_cleanup,
        offline=config.offline,
    )
    raw_text = "\n".join(turn.text for turn in transcript.speaker_turns) or " ".join(
        segment.text for segment in transcript.segments
    )
    cleaned_text = request_local_cleanup(
        config.cleanup.endpoint,
        model=config.cleanup.model,
        scope=config.cleanup.scope,
        text=raw_text,
    )
    accepted, reason = cleanup_is_safe(
        raw_text,
        cleaned_text,
        max_word_delta_ratio=config.cleanup.max_word_delta_ratio,
    )
    diff = cleanup_diff(raw_text, cleaned_text, accepted=accepted, reason=reason)
    if config.cleanup.write_diff:
        write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
    if not accepted:
        write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
        return {"enabled": True, "accepted": False, "reason": reason, "diff": diff}
    cleaned = transcript.model_copy(update={"cleaned_text": cleaned_text}, deep=True)
    write_json_artifact(out_dir / "transcript.cleaned.json", cleaned)
    atomic_text_write(out_dir / "transcript.cleaned.md", render_markdown(cleaned))
    return {"enabled": True, "accepted": True, "diff": diff}


def request_local_cleanup(endpoint: str, *, model: str | None, scope: str, text: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Improve punctuation/casing/paragraphing only. Preserve timestamps, "
                    "speaker labels, facts, word meaning, and do not summarize."
                ),
            },
            {"role": "user", "content": f"Scope: {scope}\n\n{text}"},
        ],
        "temperature": 0,
    }
    request = Request(
        endpoint.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    opener = build_opener(_RejectRedirects())
    try:
        with opener.open(request, timeout=60) as response:  # noqa: S310 endpoint guarded above
            raw = response.read(MAX_CLEANUP_RESPONSE_BYTES + 1)
    except CleanupRejectedError:
        raise
    except HTTPError as exc:
        raise CleanupRejectedError(f"Cleanup endpoint returned HTTP {exc.code}.") from exc
    except OSError as exc:
        raise CleanupRejectedError("Cleanup endpoint request failed.") from exc
    if len(raw) > MAX_CLEANUP_RESPONSE_BYTES:
        raise CleanupRejectedError("Cleanup endpoint response exceeded the size limit.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanupRejectedError("Cleanup endpoint returned malformed JSON.") from exc
    if not isinstance(data, dict):
        raise CleanupRejectedError("Cleanup endpoint returned an invalid response shape.")
    choices = data.get("choices", [])
    if not choices:
        raise CleanupRejectedError("Cleanup endpoint returned no choices.")
    message = choices[0].get("message", {})
    content = str(message.get("content", "")).strip()
    if not content:
        raise CleanupRejectedError("Cleanup endpoint returned empty content.")
    return content


def cleanup_is_safe(
    original: str,
    cleaned: str,
    *,
    max_word_delta_ratio: float,
) -> tuple[bool, str]:
    original_words = original.split()
    cleaned_words = cleaned.split()
    if not original_words:
        return bool(cleaned_words == []), "empty_original"
    delta = abs(len(cleaned_words) - len(original_words)) / max(1, len(original_words))
    if delta > max_word_delta_ratio:
        return False, "word_count_diverged_too_far"
    return True, "accepted"


def cleanup_diff(original: str, cleaned: str, *, accepted: bool, reason: str) -> dict[str, object]:
    return {
        "accepted": accepted,
        "reason": reason,
        "original_word_count": len(original.split()),
        "cleaned_word_count": len(cleaned.split()),
        "original_preview": original[:500],
        "cleaned_preview": cleaned[:500],
    }
