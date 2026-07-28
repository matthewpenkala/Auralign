# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Optional local transcript cleanup with strict preservation guardrails."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Literal
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
from .export.srt_export import render_srt
from .export.vtt_export import render_vtt
from .redaction import redact_obj
from .schemas import AlignedWord, ASRSegment, LTMBaseModel, SpeakerTurn, Transcript
from .utils import atomic_text_write

MAX_CLEANUP_RESPONSE_BYTES = 4 * 1024 * 1024
PUNCTUATION_WINDOW_WORDS = 230
PUNCTUATION_WINDOW_OVERLAP_WORDS = 30
_TRAILING_PUNCTUATION = ".,?!;:…"
_TRAILING_CLOSERS = "\"'”’)]}）】」』》"


class PunctuationEdit(LTMBaseModel):
    """One word-indexed presentation edit with no ability to alter word identity."""

    word_index: int
    trailing_punctuation: Literal[
        "keep",
        "none",
        ",",
        ".",
        "?",
        "!",
        ";",
        ":",
        "…",
    ] = "keep"
    capitalize: bool = False


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
    raw_text = cleanup_source_text(transcript)
    request_report: dict[str, object] = {}
    if config.cleanup.scope == "punctuation":
        cleaned_text, request_report = request_punctuation_cleanup(
            config.cleanup.endpoint,
            model=config.cleanup.model,
            text=raw_text,
        )
        if request_report["accepted_window_count"] == 0 and raw_text.split():
            reason = "no_punctuation_window_succeeded"
            diff = cleanup_diff(raw_text, raw_text, accepted=False, reason=reason)
            write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
            return {
                "enabled": True,
                "accepted": False,
                "reason": reason,
                "request": request_report,
                "diff": diff,
            }
    else:
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
        preserve_lexical_sequence=config.cleanup.scope == "punctuation",
    )
    diff = cleanup_diff(raw_text, cleaned_text, accepted=accepted, reason=reason)
    if config.cleanup.write_diff:
        write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
    if not accepted:
        write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
        return {
            "enabled": True,
            "accepted": False,
            "reason": reason,
            "request": request_report,
            "diff": diff,
        }
    if config.cleanup.scope == "punctuation":
        try:
            cleaned = project_punctuation_cleanup(transcript, cleaned_text)
        except ValueError as exc:
            reason = str(exc)
            diff = cleanup_diff(raw_text, cleaned_text, accepted=False, reason=reason)
            write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
            return {
                "enabled": True,
                "accepted": False,
                "reason": reason,
                "request": request_report,
                "diff": diff,
            }
    else:
        cleaned = transcript.model_copy(update={"cleaned_text": cleaned_text}, deep=True)
    write_json_artifact(out_dir / "transcript.cleaned.json", cleaned)
    atomic_text_write(out_dir / "transcript.cleaned.md", render_markdown(cleaned))
    if config.cleanup.scope == "punctuation":
        word_surfaces = [
            word.text
            for turn in cleaned.speaker_turns
            for word in turn.words
            if word.text.strip()
        ]
        atomic_text_write(
            out_dir / "transcript.cleaned.srt",
            render_srt(
                transcript if word_surfaces else cleaned,
                config.subtitle_export,
                word_surfaces=word_surfaces or None,
            ),
        )
        atomic_text_write(
            out_dir / "transcript.cleaned.vtt",
            render_vtt(
                transcript if word_surfaces else cleaned,
                config.subtitle_export,
                word_surfaces=word_surfaces or None,
            ),
        )
    return {
        "enabled": True,
        "accepted": True,
        "request": request_report,
        "diff": diff,
    }


def request_local_cleanup(endpoint: str, *, model: str | None, scope: str, text: str) -> str:
    instructions = (
        "You are a conservative verbatim-transcript punctuation restorer. "
        "Restore natural sentence punctuation and capitalization only. "
        "Preserve every spoken word, filler, repetition, contraction, and proper "
        "name in exactly the same order. Never add, delete, replace, combine, split, "
        "or reorder words. Attach punctuation to existing words. Use commas "
        "sparingly and grammatically. Return only the corrected transcript."
        if scope == "punctuation"
        else (
            "Improve punctuation, casing, paragraphing, and readability. Preserve "
            "timestamps, speaker labels, facts, and meaning; do not summarize."
        )
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": instructions,
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
    if not isinstance(choices, list) or not choices:
        raise CleanupRejectedError("Cleanup endpoint returned no choices.")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise CleanupRejectedError("Cleanup endpoint returned an invalid choice.")
    message = first_choice.get("message", {})
    if not isinstance(message, dict):
        raise CleanupRejectedError("Cleanup endpoint returned an invalid message.")
    raw_content = message.get("content", "")
    if not isinstance(raw_content, str):
        raise CleanupRejectedError("Cleanup endpoint returned invalid content.")
    content = raw_content.strip()
    if not content:
        raise CleanupRejectedError("Cleanup endpoint returned empty content.")
    return content


def request_punctuation_cleanup(
    endpoint: str,
    *,
    model: str | None,
    text: str,
) -> tuple[str, dict[str, object]]:
    """Restore bounded windows and retain only center-owned, validated tokens."""

    source_tokens = text.split()
    if not source_tokens:
        return "", {
            "schema_version": "punctuation_windows.v1",
            "window_size_words": PUNCTUATION_WINDOW_WORDS,
            "overlap_words": PUNCTUATION_WINDOW_OVERLAP_WORDS,
            "window_count": 0,
            "accepted_window_count": 0,
            "fallback_window_count": 0,
            "fallback_reasons": {},
        }
    stride = PUNCTUATION_WINDOW_WORDS - PUNCTUATION_WINDOW_OVERLAP_WORDS
    margin = PUNCTUATION_WINDOW_OVERLAP_WORDS // 2
    restored_tokens: list[str] = []
    window_count = 0
    accepted_window_count = 0
    fallback_reasons: dict[str, int] = {}
    start = 0
    while start < len(source_tokens):
        end = min(len(source_tokens), start + PUNCTUATION_WINDOW_WORDS)
        window_source = source_tokens[start:end]
        reason: str | None = None
        try:
            candidate = request_local_cleanup(
                endpoint,
                model=model,
                scope="punctuation",
                text=" ".join(window_source),
            )
        except CleanupRejectedError:
            candidate = " ".join(window_source)
            reason = "request_failed"
        if reason is None:
            accepted, _ = cleanup_is_safe(
                " ".join(window_source),
                candidate,
                max_word_delta_ratio=0.0,
                preserve_lexical_sequence=True,
            )
            if not accepted:
                candidate = " ".join(window_source)
                reason = "lexical_validation_failed"
            else:
                accepted_window_count += 1
        if reason is not None:
            fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
        candidate_tokens = candidate.split()
        eligible_start = 0 if start == 0 else margin
        eligible_end = len(candidate_tokens) if end == len(source_tokens) else (
            len(candidate_tokens) - margin
        )
        restored_tokens.extend(candidate_tokens[eligible_start:eligible_end])
        window_count += 1
        start += stride
    if len(restored_tokens) != len(source_tokens):
        raise CleanupRejectedError("Punctuation window ownership was incomplete.")
    report: dict[str, object] = {
        "schema_version": "punctuation_windows.v1",
        "window_size_words": PUNCTUATION_WINDOW_WORDS,
        "overlap_words": PUNCTUATION_WINDOW_OVERLAP_WORDS,
        "window_count": window_count,
        "accepted_window_count": accepted_window_count,
        "fallback_window_count": window_count - accepted_window_count,
        "fallback_reasons": fallback_reasons,
    }
    return " ".join(restored_tokens), report


def cleanup_is_safe(
    original: str,
    cleaned: str,
    *,
    max_word_delta_ratio: float,
    preserve_lexical_sequence: bool = False,
) -> tuple[bool, str]:
    original_words = original.split()
    cleaned_words = cleaned.split()
    if not original_words:
        return bool(cleaned_words == []), "empty_original"
    delta = abs(len(cleaned_words) - len(original_words)) / max(1, len(original_words))
    if delta > max_word_delta_ratio:
        return False, "word_count_diverged_too_far"
    if preserve_lexical_sequence:
        if len(original_words) != len(cleaned_words):
            return False, "lexical_word_count_changed"
        for index, (original_word, cleaned_word) in enumerate(
            zip(original_words, cleaned_words, strict=True)
        ):
            if _lexical_signature(original_word) != _lexical_signature(cleaned_word):
                return False, f"lexical_word_changed_at_index_{index}"
    return True, "accepted"


def cleanup_diff(original: str, cleaned: str, *, accepted: bool, reason: str) -> dict[str, object]:
    return {
        "accepted": accepted,
        "reason": reason,
        "original_word_count": len(original.split()),
        "cleaned_word_count": len(cleaned.split()),
    }


def cleanup_source_text(transcript: Transcript) -> str:
    """Return projection-safe text from the most derived timed containers."""

    if transcript.speaker_turns:
        return "\n".join(_speaker_turn_source_text(turn) for turn in transcript.speaker_turns)
    return "\n".join(_segment_source_text(segment) for segment in transcript.segments)


def project_punctuation_cleanup(
    transcript: Transcript,
    cleaned_text: str,
) -> Transcript:
    """Project punctuation/casing onto timed words without changing lexical content."""

    source_text = cleanup_source_text(transcript)
    accepted, reason = cleanup_is_safe(
        source_text,
        cleaned_text,
        max_word_delta_ratio=0.0,
        preserve_lexical_sequence=True,
    )
    if not accepted:
        raise ValueError(reason)
    cleaned_tokens = cleaned_text.split()
    updates: dict[str, object] = {}
    if transcript.speaker_turns:
        cursor = 0
        repaired_turns: list[SpeakerTurn] = []
        for turn in transcript.speaker_turns:
            source_tokens = _speaker_turn_source_text(turn).split()
            repaired_tokens = cleaned_tokens[cursor : cursor + len(source_tokens)]
            cursor += len(source_tokens)
            repaired_turns.append(_repair_speaker_turn(turn, repaired_tokens))
        if cursor != len(cleaned_tokens):
            raise ValueError("speaker_turn_projection_did_not_consume_all_words")
        updates["speaker_turns"] = repaired_turns
    if transcript.segments:
        segment_source_text = "\n".join(
            _segment_source_text(segment) for segment in transcript.segments
        )
        _require_projection_source_match(
            "segment",
            segment_source_text,
            cleaned_text,
        )
        cursor = 0
        repaired_segments: list[ASRSegment] = []
        for segment in transcript.segments:
            source_tokens = _segment_source_text(segment).split()
            repaired_tokens = cleaned_tokens[cursor : cursor + len(source_tokens)]
            cursor += len(source_tokens)
            repaired_segments.append(_repair_segment(segment, repaired_tokens))
        if cursor != len(cleaned_tokens):
            raise ValueError("segment_projection_did_not_consume_all_words")
        updates["segments"] = repaired_segments
    if transcript.words:
        top_level_source_text = " ".join(
            word.text.strip() for word in transcript.words if word.text.strip()
        )
        _require_projection_source_match(
            "top_level_word",
            top_level_source_text,
            cleaned_text,
        )
        updates["words"] = _repair_words(transcript.words, cleaned_tokens)
    cleaned = transcript.model_copy(update=updates, deep=True)
    presentation_text = (
        "\n".join(turn.text for turn in cleaned.speaker_turns)
        if cleaned.speaker_turns
        else "\n".join(segment.text for segment in cleaned.segments)
    )
    return cleaned.model_copy(update={"cleaned_text": presentation_text}, deep=True)


def project_punctuation_edits(
    transcript: Transcript,
    edits: list[PunctuationEdit],
) -> Transcript:
    """Apply constrained word-index edits and then reuse lexical projection."""

    source_tokens = cleanup_source_text(transcript).split()
    repaired_tokens = list(source_tokens)
    seen_indexes: set[int] = set()
    for edit in sorted(edits, key=lambda item: item.word_index):
        if edit.word_index in seen_indexes:
            raise ValueError("duplicate_punctuation_edit_index")
        seen_indexes.add(edit.word_index)
        if edit.word_index < 0 or edit.word_index >= len(repaired_tokens):
            raise ValueError("punctuation_edit_index_out_of_range")
        token = repaired_tokens[edit.word_index]
        if edit.trailing_punctuation != "keep":
            token = _replace_trailing_punctuation(
                token,
                edit.trailing_punctuation,
            )
        if edit.capitalize:
            token = _capitalize_token(token)
        if _lexical_signature(token) != _lexical_signature(
            source_tokens[edit.word_index]
        ):
            raise ValueError("punctuation_edit_changed_lexical_word")
        repaired_tokens[edit.word_index] = token
    repaired_tokens = _apply_safe_capitalization(transcript, repaired_tokens)
    return project_punctuation_cleanup(transcript, " ".join(repaired_tokens))


def _speaker_turn_source_text(turn: SpeakerTurn) -> str:
    tokens = [word.text.strip() for word in turn.words if word.text.strip()]
    return " ".join(tokens) if tokens else " ".join(turn.text.split())


def _segment_source_text(segment: ASRSegment) -> str:
    tokens = [word.text.strip() for word in segment.words if word.text.strip()]
    return " ".join(tokens) if tokens else " ".join(segment.text.split())


def _repair_speaker_turn(
    turn: SpeakerTurn,
    repaired_tokens: list[str],
) -> SpeakerTurn:
    return turn.model_copy(
        update={
            "text": " ".join(repaired_tokens),
            "words": _repair_words(turn.words, repaired_tokens),
        },
        deep=True,
    )


def _repair_segment(
    segment: ASRSegment,
    repaired_tokens: list[str],
) -> ASRSegment:
    return segment.model_copy(
        update={
            "text": " ".join(repaired_tokens),
            "words": _repair_words(segment.words, repaired_tokens),
        },
        deep=True,
    )


def _repair_words(
    words: list[AlignedWord],
    repaired_tokens: list[str],
) -> list[AlignedWord]:
    nonempty_word_count = sum(bool(word.text.strip()) for word in words)
    if not words:
        return []
    if nonempty_word_count != len(repaired_tokens):
        raise ValueError("timed_word_projection_count_mismatch")
    repaired: list[AlignedWord] = []
    token_index = 0
    for word in words:
        if not word.text.strip():
            repaired.append(word.model_copy(deep=True))
            continue
        repaired.append(
            word.model_copy(
                update={"text": repaired_tokens[token_index]},
                deep=True,
            )
        )
        token_index += 1
    return repaired


def _lexical_signature(token: str) -> str:
    normalized = unicodedata.normalize("NFKC", token).casefold()
    if not any(character.isalnum() for character in normalized):
        return normalized
    closer_start = len(normalized)
    while closer_start > 0 and normalized[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    body = normalized[:closer_start]
    closers = normalized[closer_start:]
    while body and body[-1] in _TRAILING_PUNCTUATION:
        body = body[:-1]
    return f"{body}{closers}"


def _require_projection_source_match(
    label: str,
    source_text: str,
    cleaned_text: str,
) -> None:
    accepted, _ = cleanup_is_safe(
        source_text,
        cleaned_text,
        max_word_delta_ratio=0.0,
        preserve_lexical_sequence=True,
    )
    if not accepted:
        raise ValueError(f"{label}_lexical_sequence_mismatch")


def _replace_trailing_punctuation(token: str, punctuation: str) -> str:
    closer_start = len(token)
    while closer_start > 0 and token[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    body = token[:closer_start]
    closers = token[closer_start:]
    while body and body[-1] in _TRAILING_PUNCTUATION:
        body = body[:-1]
    suffix = "" if punctuation == "none" else punctuation
    return f"{body}{suffix}{closers}"


def _capitalize_token(token: str) -> str:
    characters = list(token)
    for index, character in enumerate(characters):
        if not character.isalpha():
            continue
        if any(later.isupper() for later in characters[index + 1 :] if later.isalpha()):
            return token
        characters[index] = character.upper()
        return "".join(characters)
    return token


def _apply_safe_capitalization(
    transcript: Transcript,
    tokens: list[str],
) -> list[str]:
    repaired = list(tokens)
    turn_lengths = (
        [len(_speaker_turn_source_text(turn).split()) for turn in transcript.speaker_turns]
        if transcript.speaker_turns
        else [len(_segment_source_text(segment).split()) for segment in transcript.segments]
    )
    cursor = 0
    previous_ends_sentence = True
    for turn_length in turn_lengths:
        for index in range(cursor, cursor + turn_length):
            token = repaired[index]
            normalized = unicodedata.normalize("NFKC", token).casefold()
            is_i_pronoun = _lexical_signature(token) == "i" or normalized.startswith(
                ("i'", "i’")
            )
            if previous_ends_sentence or is_i_pronoun:
                repaired[index] = _capitalize_token(token)
            previous_ends_sentence = _ends_sentence(repaired[index])
        cursor += turn_length
    return repaired


def _ends_sentence(token: str) -> bool:
    return token.rstrip(_TRAILING_CLOSERS).endswith((".", "?", "!", "…"))
