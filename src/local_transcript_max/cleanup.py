# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Optional local cleanup over immutable canonical transcript evidence."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.metadata
import json
import math
import platform
import queue
import threading
import unicodedata
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar, cast
from urllib.error import HTTPError
from urllib.parse import urlparse
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
from .presentation import (
    DECODER_VERSION,
    DecoderWord,
    EvidenceRole,
    PresentationDecision,
    PresentationPlan,
    PresentationProposal,
    ScorerOutput,
    ScorerProvenance,
    TrailingPunctuation,
    _typed_question_scope_endpoints,
    apply_presentation_plan,
    decode_presentation,
    lexical_text_digest,
    presentation_plan_sha256,
    serialize_presentation_plan,
)
from .presentation_sat import (
    SaTMode,
    SaTScorerError,
    SaTSourceOptions,
    registered_production_proposal_sources,
    run_registered_sat_source,
    sat_score_receipt_sha256,
    sat_score_receipt_to_dict,
)
from .redaction import redact_obj
from .schemas import AlignedWord, ASRSegment, LTMBaseModel, SpeakerTurn, Transcript
from .semantic_cleanup import (
    CanonicalWord,
    EditProvenance,
    LexicalCleanupPolicy,
    SemanticAbstentionReason,
    SemanticCleanupPlan,
    SemanticProposalBatch,
    SemanticReconciliationResult,
    canonical_evidence_digest,
    propose_safe_speech_artifact_edits,
    reconcile_semantic_proposal_batches,
    semantic_cleanup_plan_sha256,
    semantic_reconciliation_result_sha256,
    serialize_semantic_cleanup_plan,
    serialize_semantic_reconciliation_result,
)
from .utils import atomic_text_write

MAX_CLEANUP_RESPONSE_BYTES = 4 * 1024 * 1024
PUNCTUATION_WINDOW_WORDS = 230
PUNCTUATION_WINDOW_OVERLAP_WORDS = 30
APPLE_PUNCTUATION_WINDOW_WORDS = 80
_TRAILING_PUNCTUATION = ".,?!;:…"
_TRAILING_CLOSERS = "\"'”’)]}）】」』》"
_LEADING_PRESENTATION = "\"'“‘([{（【「『《"
APPLE_FOUNDATION_MODEL_ID = "com.apple.system-language-model.default"
SOURCE_GRAMMAR_SCORER_VERSION = "4.0"
WINDOW_AGGREGATE_SCORER_VERSION = "overlap-reconciled.v2"
_MAX_PUNCTUATION_GAP_WORDS = 34
_MIN_READABILITY_SPAN_WORDS = 4
_PREFERRED_READABILITY_SPAN_WORDS = 24
_MAX_STRONG_SENTENCE_SPAN_WORDS = 64
_HARD_MAX_STRONG_SENTENCE_SPAN_WORDS = 96
_MAX_CONSECUTIVE_ADDED_SHORT_STOPS = 2
_MAX_CLUSTERED_ADDED_STOPS = 3
_MAX_ADDED_STOP_CLUSTER_GAP_WORDS = 9
_AsyncResult = TypeVar("_AsyncResult")
_STRONG_SENTENCE_PUNCTUATION = frozenset({".", "?", "!", "…"})
CleanupTokenContext = tuple[float | None, float | None, int, str | None]


@dataclass(frozen=True, slots=True)
class _PunctuationWindowEvidence:
    """One lexically validated backend window retained for global decoding."""

    window_index: int
    start_word_index: int
    end_word_index: int
    source_tokens: tuple[str, ...]
    candidate_tokens: tuple[str, ...]
    response_path: str


@dataclass(frozen=True, slots=True)
class _PunctuationPresentationResult:
    """Internal cleanup result carrying the exact derived plan artifact."""

    text: str
    report: dict[str, object]
    plan: PresentationPlan | None
    decoder_words: tuple[DecoderWord, ...]


_INCOMPLETE_SENTENCE_END_WORDS = frozenset(
    {
        "a",
        "an",
        "another",
        "and",
        "any",
        "are",
        "as",
        "at",
        "also",
        "because",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "fully",
        "had",
        "has",
        "have",
        "he",
        "her",
        "hers",
        "him",
        "his",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "like",
        "may",
        "maybe",
        "might",
        "more",
        "mostly",
        "my",
        "of",
        "on",
        "or",
        "our",
        "only",
        "potentially",
        "probably",
        "really",
        "she",
        "should",
        "so",
        "some",
        "still",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "very",
        "was",
        "we",
        "were",
        "when",
        "where",
        "which",
        "while",
        "who",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)
_INCOMPLETE_CONTRACTIONS = frozenset(
    {
        "can't",
        "couldn't",
        "didn't",
        "doesn't",
        "don't",
        "hadn't",
        "hasn't",
        "haven't",
        "he'll",
        "he's",
        "here's",
        "i'll",
        "i'm",
        "i've",
        "isn't",
        "it'll",
        "it's",
        "she'll",
        "she's",
        "shouldn't",
        "that'll",
        "that's",
        "there's",
        "they'll",
        "they're",
        "they've",
        "wasn't",
        "we'll",
        "we're",
        "we've",
        "weren't",
        "what's",
        "who's",
        "won't",
        "wouldn't",
        "you'll",
        "you're",
        "you've",
    }
)
_STRICT_SYNTACTIC_CONTINUATIONS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "because",
        "but",
        "by",
        "for",
        "from",
        "if",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "while",
        "with",
    }
)
_STRICT_AUXILIARY_CONTRACTIONS = frozenset(
    {
        "he'll",
        "i'll",
        "it'll",
        "she'll",
        "that'll",
        "they'll",
        "we'll",
        "you'll",
    }
)
_ABANDONED_FRAGMENT_HEADS = frozenset(
    {
        "am",
        "are",
        "can",
        "could",
        "did",
        "do",
        "does",
        "had",
        "has",
        "have",
        "he",
        "i",
        "is",
        "it",
        "may",
        "might",
        "she",
        "should",
        "they",
        "was",
        "we",
        "were",
        "will",
        "would",
        "you",
    }
)
_DISCOURSE_COMMA_WORDS = frozenset(
    {
        "actually",
        "basically",
        "honestly",
        "like",
        "no",
        "okay",
        "so",
        "uh",
        "um",
        "well",
        "yeah",
        "yes",
    }
)
_EXPLICIT_CORRECTION_MARKERS = frozenset({"actually", "correction", "no", "rather"})
_QUESTION_LEADS = frozenset(
    {
        "are",
        "can",
        "could",
        "did",
        "do",
        "does",
        "how",
        "is",
        "should",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "would",
    }
)
_WH_QUESTION_LEADS = frozenset({"how", "what", "when", "where", "which", "who", "why"})
_QUESTION_SUBJECT_PRONOUNS = frozenset(
    {"he", "i", "it", "she", "that", "these", "they", "this", "those", "we", "you"}
)
_QUESTION_SUBJECT_DETERMINERS = frozenset(
    {"a", "an", "her", "his", "my", "our", "the", "their", "your"}
)
_CONTRACTED_QUESTION_LEADS_WITH_RESPONSE_EVIDENCE = frozenset({"how"})
_DECLARATIVE_WH_PRONOUNS = frozenset(
    {"he", "i", "it", "she", "that", "they", "we", "what", "who", "you"}
)
_PROPER_NOUN_LIST_CONNECTORS = frozenset({"and", "maybe", "or", "potentially"})
_CLAUSE_CONNECTORS = frozenset({"and", "because", "but", "or", "so", "that", "which", "who"})
_FIRST_PERSON_RESTARTS = frozenset(
    {"believe", "don't", "dont", "guess", "know", "mean", "remember", "think"}
)
_STANDARD_ENGLISH_CASING = {
    "christmas": "Christmas",
    "friday": "Friday",
    "january": "January",
    "july": "July",
    "june": "June",
    "monday": "Monday",
    "saturday": "Saturday",
    "sunday": "Sunday",
    "thursday": "Thursday",
    "tuesday": "Tuesday",
    "wednesday": "Wednesday",
}
_STANDARD_ENGLISH_CASING.update(
    {
        month.casefold(): month
        for month in (
            "February",
            "March",
            "April",
            "May",
            "August",
            "September",
            "October",
            "November",
            "December",
        )
    }
)


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
        if config.cleanup.required_complete:
            raise CleanupRejectedError("Required cleanup cannot run while cleanup mode is off.")
        return {"enabled": False, "reason": "cleanup disabled"}
    validate_cleanup_policy(
        mode=config.cleanup.mode,
        endpoint=config.cleanup.endpoint,
        allow_remote_cleanup=config.cleanup.allow_remote_cleanup,
        offline=config.offline,
    )
    raw_text = cleanup_source_text(transcript)
    request_report = cleanup_backend_report(config)
    presentation_result: _PunctuationPresentationResult | None = None
    semantic_plan: SemanticCleanupPlan | None = None
    semantic_result: SemanticReconciliationResult | None = None
    if config.cleanup.scope == "punctuation":
        semantic_result = _semantic_cleanup_result(
            transcript,
            raw_text.split(),
            enabled=(config.cleanup.quality_profile == "semantically-faithful-clean-v1"),
            backend_mode=config.cleanup.mode,
        )
        semantic_plan = semantic_result.plan
        semantic_text = " ".join(semantic_plan.clean_surfaces)
        presentation_result = _request_punctuation_presentation(
            config.cleanup.endpoint,
            model=config.cleanup.model,
            text=semantic_text,
            mode=config.cleanup.mode,
            proper_nouns=config.cleanup.proper_nouns,
            required_complete=config.cleanup.required_complete,
            transcript=transcript,
            source_word_indices=semantic_plan.clean_to_source_indices,
            sat_options=SaTSourceOptions(
                mode=SaTMode(config.cleanup.presentation_scorers.sat.mode),
                model_dir=config.cleanup.presentation_scorers.sat.model_dir,
                tokenizer_dir=config.cleanup.presentation_scorers.sat.tokenizer_dir,
                signal_weight=config.cleanup.presentation_scorers.sat.signal_weight,
                # SaT is an independently offline-only proposal source even
                # when the wider pipeline is not running in strict-offline
                # mode. Its adapter has no acquisition or network execution
                # path and verifies local assets before and after inference.
                offline=True,
            ),
        )
        cleaned_text = presentation_result.text
        request_report.update(presentation_result.report)
        request_report.update(
            {
                "quality_profile": config.cleanup.quality_profile,
                "semantic_cleanup_version": semantic_plan.version,
                "semantic_cleanup_plan_sha256": semantic_cleanup_plan_sha256(semantic_plan),
                "canonical_semantic_evidence_digest": semantic_plan.canonical_digest,
                "semantic_edit_count": len(semantic_plan.selected_edits),
                "semantic_rejected_proposal_count": len(semantic_plan.rejected_proposal_ids),
                "semantic_clean_word_count": len(semantic_plan.clean_surfaces),
                "semantic_reconciliation_sha256": (
                    semantic_reconciliation_result_sha256(semantic_result)
                ),
                "semantic_proposal_receipt_count": len(semantic_result.proposal_receipts),
                "semantic_abstention_receipt_count": len(semantic_result.abstention_receipts),
            }
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
        cleaned_text = request_cleanup_backend(
            mode=config.cleanup.mode,
            endpoint=config.cleanup.endpoint,
            model=config.cleanup.model,
            scope=config.cleanup.scope,
            text=raw_text,
            proper_nouns=config.cleanup.proper_nouns,
        )
    if config.cleanup.scope == "punctuation" and semantic_plan is not None:
        if config.cleanup.quality_profile == "strict-verbatim-v1":
            accepted, reason = cleanup_is_safe(
                raw_text,
                cleaned_text,
                max_word_delta_ratio=0.0,
                preserve_lexical_sequence=True,
            )
        else:
            accepted = True
            reason = "enumerated_semantic_cleanup_plan_accepted"
        diff = cleanup_diff(raw_text, cleaned_text, accepted=accepted, reason=reason)
        diff.update(
            {
                "canonical_evidence_preserved": True,
                "selected_semantic_edit_count": len(semantic_plan.selected_edits),
                "semantic_cleanup_plan_sha256": semantic_cleanup_plan_sha256(semantic_plan),
            }
        )
    else:
        accepted, reason = cleanup_is_safe(
            raw_text,
            cleaned_text,
            max_word_delta_ratio=config.cleanup.max_word_delta_ratio,
            preserve_lexical_sequence=False,
        )
        diff = cleanup_diff(raw_text, cleaned_text, accepted=accepted, reason=reason)
    if config.cleanup.write_diff:
        write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
    if not accepted:
        write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
        if config.cleanup.required_complete:
            raise CleanupRejectedError(f"Required cleanup rejected output: {reason}.")
        return {
            "enabled": True,
            "accepted": False,
            "reason": reason,
            "request": request_report,
            "diff": diff,
        }
    if config.cleanup.scope == "punctuation":
        try:
            if semantic_plan is None:
                raise ValueError("semantic_cleanup_plan_missing")
            cleaned = _project_semantic_presentation_cleanup(
                transcript,
                semantic_plan,
                cleaned_text.split(),
            )
        except ValueError as exc:
            reason = str(exc)
            diff = cleanup_diff(raw_text, cleaned_text, accepted=False, reason=reason)
            write_json_artifact(out_dir / "cleanup_diff.json", redact_obj(diff))
            if config.cleanup.required_complete:
                raise CleanupRejectedError(
                    f"Required cleanup projection failed: {reason}."
                ) from exc
            return {
                "enabled": True,
                "accepted": False,
                "reason": reason,
                "request": request_report,
                "diff": diff,
            }
    else:
        cleaned = transcript.model_copy(update={"cleaned_text": cleaned_text}, deep=True)
    if presentation_result is not None and presentation_result.plan is not None:
        plan_bytes = serialize_presentation_plan(presentation_result.plan)
        atomic_text_write(
            out_dir / "presentation_plan.json",
            plan_bytes.decode("utf-8"),
        )
    if semantic_plan is not None:
        atomic_text_write(
            out_dir / "semantic_cleanup_plan.json",
            serialize_semantic_cleanup_plan(semantic_plan).decode("utf-8") + "\n",
        )
    if semantic_result is not None:
        atomic_text_write(
            out_dir / "semantic_cleanup_receipt.json",
            serialize_semantic_reconciliation_result(semantic_result).decode("utf-8") + "\n",
        )
    write_json_artifact(out_dir / "transcript.cleaned.json", cleaned)
    atomic_text_write(out_dir / "transcript.cleaned.md", render_markdown(cleaned))
    if config.cleanup.scope == "punctuation":
        word_surfaces = [word.text for turn in cleaned.speaker_turns for word in turn.words]
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


def cleanup_backend_report(config: PipelineConfig) -> dict[str, object]:
    """Return privacy-safe backend identity and locality evidence."""

    if config.cleanup.mode == "apple-foundation":
        try:
            sdk_version: str | None = importlib.metadata.version("apple-fm-sdk")
        except importlib.metadata.PackageNotFoundError:
            sdk_version = None
        return {
            "backend": "apple-foundation",
            "locality": "on-device-system-model",
            "model": APPLE_FOUNDATION_MODEL_ID,
            "response_contract": "guided-verbatim-punctuation.v1",
            "sdk": "apple-fm-sdk",
            "sdk_version": sdk_version,
            "os": platform.platform(),
            "proper_noun_count": len(config.cleanup.proper_nouns),
        }
    return {
        "backend": "local-llm",
        "locality": (
            "loopback"
            if (urlparse(config.cleanup.endpoint).hostname or "")
            in {
                "127.0.0.1",
                "localhost",
                "::1",
            }
            else "explicit-remote"
        ),
        "model": config.cleanup.model,
        "proper_noun_count": len(config.cleanup.proper_nouns),
    }


def _cleanup_instructions(scope: str, proper_nouns: Sequence[str]) -> str:
    instructions = (
        "You are a conservative verbatim-transcript punctuation restorer. "
        "Restore natural sentence punctuation and capitalization only. "
        "Preserve every spoken word, filler, repetition, contraction, and proper "
        "name in exactly the same order. Never add, delete, replace, combine, split, "
        "or reorder words. Attach punctuation to existing words. Use commas "
        "sparingly and grammatically. Never place sentence-ending punctuation "
        "after a conjunction, preposition, determiner, pronoun, auxiliary verb, "
        "or filler unless it truly ends a complete spoken sentence. Never insert "
        "a comma or sentence-ending mark after a contracted auxiliary when the "
        "next word completes its phrase. Do not treat the end of the supplied "
        "window as a sentence boundary. Never insert punctuation between adjacent "
        "repetitions of the same word. Return only the corrected transcript."
        if scope == "punctuation"
        else (
            "Improve punctuation, casing, paragraphing, and readability. Preserve "
            "timestamps, speaker labels, facts, and meaning; do not summarize."
        )
    )
    if proper_nouns:
        glossary = json.dumps(list(proper_nouns), ensure_ascii=False)
        instructions += (
            " Known proper-noun spellings are supplied in this JSON array: "
            f"{glossary}. Apply those spellings only to matching words already "
            "present in the transcript; never insert a glossary term or change "
            "the lexical word sequence."
        )
    return instructions


def request_local_cleanup(
    endpoint: str,
    *,
    model: str | None,
    scope: str,
    text: str,
    proper_nouns: Sequence[str] = (),
) -> str:
    instructions = _cleanup_instructions(scope, proper_nouns)
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


def request_apple_foundation_cleanup(
    *,
    scope: str,
    text: str,
    proper_nouns: Sequence[str] = (),
    diagnostics: dict[str, int] | None = None,
) -> str:
    """Invoke Apple's on-device system model through its optional Python SDK."""

    try:
        fm = importlib.import_module("apple_fm_sdk")
    except (ImportError, OSError) as exc:
        raise CleanupRejectedError(
            "Apple Foundation Models cleanup requires the optional apple-fm-sdk dependency."
        ) from exc
    try:
        system_model = fm.SystemLanguageModel()
        available, unavailable_reason = system_model.is_available()
    except Exception as exc:
        raise CleanupRejectedError(
            "Apple Foundation Models availability could not be determined."
        ) from exc
    if not available:
        reason = getattr(unavailable_reason, "name", None) or "unknown"
        raise CleanupRejectedError(
            f"Apple Foundation Models is unavailable on this device ({reason})."
        )

    async def respond() -> str:
        options = fm.GenerationOptions(
            sampling=fm.SamplingMode.greedy(),
            temperature=0.0,
            maximum_response_tokens=1024,
        )
        if scope == "punctuation":
            session = fm.LanguageModelSession(
                instructions=_cleanup_instructions(scope, proper_nouns),
                model=system_model,
            )
            try:
                response = await session.respond(
                    f"Punctuate this exact verbatim transcript now:\n\n{text}",
                    json_schema=_apple_verbatim_punctuation_schema(),
                    options=options,
                )
            except Exception as exc:
                safety_error_types = tuple(
                    error_type
                    for error_type in (
                        getattr(fm, "RefusalError", None),
                        getattr(fm, "GuardrailViolationError", None),
                    )
                    if isinstance(error_type, type)
                )
                if not safety_error_types or not isinstance(exc, safety_error_types):
                    raise
                _increment_cleanup_diagnostic(
                    diagnostics,
                    "structured_edit_safety_recovery_count",
                )
                return await _request_apple_structured_punctuation_edits(
                    fm=fm,
                    system_model=system_model,
                    text=text,
                    options=options,
                )
            corrected = _parse_apple_verbatim_response(response)
            accepted, _reason = cleanup_is_safe(
                text,
                corrected,
                max_word_delta_ratio=0.0,
                preserve_lexical_sequence=True,
            )
            if not accepted:
                _increment_cleanup_diagnostic(
                    diagnostics,
                    "structured_edit_lexical_recovery_count",
                )
                return await _request_apple_structured_punctuation_edits(
                    fm=fm,
                    system_model=system_model,
                    text=text,
                    options=options,
                )
            return corrected
        session = fm.LanguageModelSession(
            instructions=_cleanup_instructions(scope, proper_nouns),
            model=system_model,
        )
        response = await session.respond(
            f"Scope: {scope}\n\n{text}",
            options=options,
        )
        if not isinstance(response, str):
            raise CleanupRejectedError(
                "Apple Foundation Models returned an invalid response shape."
            )
        return response

    try:
        raw_content = _run_async_from_sync(respond())
    except CleanupRejectedError:
        raise
    except Exception as exc:
        raise CleanupRejectedError("Apple Foundation Models cleanup failed.") from exc
    if len(raw_content.encode("utf-8")) > MAX_CLEANUP_RESPONSE_BYTES:
        raise CleanupRejectedError("Apple Foundation Models response exceeded the size limit.")
    content = raw_content.strip()
    if not content:
        raise CleanupRejectedError("Apple Foundation Models returned empty content.")
    return content


def _apple_verbatim_punctuation_schema() -> dict[str, object]:
    """Constrain Apple output to one bounded transcript string without wrappers."""

    return {
        "additionalProperties": False,
        "properties": {
            "corrected_transcript": {
                "description": (
                    "All and only the supplied spoken words, unchanged and in order, "
                    "with natural punctuation and capitalization restored."
                ),
                "type": "string",
            }
        },
        "required": ["corrected_transcript"],
        "title": "VerbatimPunctuation",
        "type": "object",
        # Foundation Models' JSON-schema bridge requires deterministic property order.
        "x-order": ["corrected_transcript"],
    }


def _parse_apple_verbatim_response(response: object) -> str:
    try:
        payload = response.value()  # type: ignore[attr-defined]
    except (AttributeError, TypeError, ValueError) as exc:
        raise CleanupRejectedError(
            "Apple Foundation Models returned malformed guided content."
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"corrected_transcript"}:
        raise CleanupRejectedError("Apple Foundation Models returned malformed guided content.")
    corrected = payload.get("corrected_transcript")
    if not isinstance(corrected, str):
        raise CleanupRejectedError("Apple Foundation Models returned malformed guided content.")
    return corrected


async def _request_apple_structured_punctuation_edits(
    *,
    fm: Any,
    system_model: Any,
    text: str,
    options: Any,
) -> str:
    """Recover one refused/drifting window without asking the model to emit words."""

    source_tokens = text.split()
    if not source_tokens:
        return ""
    maximum_edit_window = 32
    edit_overlap = 8
    if len(source_tokens) > maximum_edit_window:
        stride = maximum_edit_window - edit_overlap
        margin = edit_overlap // 2
        restored_tokens: list[str] = []
        start = 0
        while start < len(source_tokens):
            end = min(len(source_tokens), start + maximum_edit_window)
            chunk = await _request_apple_structured_punctuation_edits(
                fm=fm,
                system_model=system_model,
                text=" ".join(source_tokens[start:end]),
                options=options,
            )
            candidate_tokens = chunk.split()
            if len(candidate_tokens) != end - start:
                raise CleanupRejectedError("Apple structured-edit window ownership was incomplete.")
            eligible_start = 0 if start == 0 else margin
            eligible_end = (
                len(candidate_tokens)
                if end == len(source_tokens)
                else len(candidate_tokens) - margin
            )
            restored_tokens.extend(candidate_tokens[eligible_start:eligible_end])
            if end == len(source_tokens):
                break
            start += stride
        if len(restored_tokens) != len(source_tokens):
            raise CleanupRejectedError("Apple structured-edit window ownership was incomplete.")
        return " ".join(restored_tokens)
    session = fm.LanguageModelSession(
        instructions=(
            "Restore natural punctuation and capitalization for indexed verbatim "
            "transcript words. Output only presentation edits referencing existing "
            "indices. Never output, add, remove, replace, combine, split, or reorder "
            "lexical words. Use commas sparingly. Never place sentence-ending "
            "punctuation after a conjunction, preposition, determiner, pronoun, "
            "auxiliary verb, or filler unless it truly ends a complete spoken "
            "sentence. Never place a comma or sentence-ending mark after a "
            "contracted auxiliary when the next word completes its phrase. Do not "
            "treat the end of this bounded word window as a sentence boundary. "
            "Never insert punctuation between adjacent repetitions of the same word."
        ),
        model=system_model,
    )
    response = await session.respond(
        "Indexed words:\n"
        + "\n".join(f"{index}\t{token}" for index, token in enumerate(source_tokens)),
        json_schema=_apple_structured_edit_schema(len(source_tokens)),
        options=options,
    )
    try:
        payload = response.value()
    except (AttributeError, TypeError, ValueError) as exc:
        raise CleanupRejectedError(
            "Apple Foundation Models returned malformed structured edits."
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"edits"}:
        raise CleanupRejectedError("Apple Foundation Models returned malformed structured edits.")
    raw_edits = payload.get("edits")
    if not isinstance(raw_edits, list) or len(raw_edits) > len(source_tokens):
        raise CleanupRejectedError("Apple Foundation Models returned malformed structured edits.")
    try:
        edits = [PunctuationEdit.model_validate(item, strict=True) for item in raw_edits]
        repaired = _apply_punctuation_edits_to_tokens(source_tokens, edits)
    except ValueError as exc:
        raise CleanupRejectedError(
            "Apple Foundation Models returned unsafe structured edits."
        ) from exc
    accepted, reason = cleanup_is_safe(
        text,
        " ".join(repaired),
        max_word_delta_ratio=0.0,
        preserve_lexical_sequence=True,
    )
    if not accepted:
        raise CleanupRejectedError(f"Apple structured edits failed lexical validation: {reason}.")
    return " ".join(repaired)


def _apple_structured_edit_schema(word_count: int) -> dict[str, object]:
    return {
        "$defs": {
            "Edit": {
                "additionalProperties": False,
                "properties": {
                    "word_index": {
                        "description": "Index of an existing supplied word.",
                        "maximum": word_count - 1,
                        "minimum": 0,
                        "type": "integer",
                    },
                    "trailing_punctuation": {
                        "description": "Trailing punctuation for that word.",
                        "enum": ["keep", "none", ",", ".", "?", "!", ";", ":", "…"],
                        "type": "string",
                    },
                    "capitalize": {
                        "description": "Whether to capitalize that existing word.",
                        "type": "boolean",
                    },
                },
                "required": ["word_index", "trailing_punctuation", "capitalize"],
                "title": "Edit",
                "type": "object",
                "x-order": ["word_index", "trailing_punctuation", "capitalize"],
            }
        },
        "additionalProperties": False,
        "properties": {
            "edits": {
                "description": "Only presentation edits; never lexical words.",
                "items": {"$ref": "#/$defs/Edit"},
                "maxItems": word_count,
                "type": "array",
            }
        },
        "required": ["edits"],
        "title": "PunctuationEdits",
        "type": "object",
        "x-order": ["edits"],
    }


def _increment_cleanup_diagnostic(
    diagnostics: dict[str, int] | None,
    key: str,
) -> None:
    if diagnostics is not None:
        diagnostics[key] = diagnostics.get(key, 0) + 1


def _run_async_from_sync(awaitable: Coroutine[Any, Any, _AsyncResult]) -> _AsyncResult:
    """Run one coroutine safely whether or not this thread owns an event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    results: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            results.put((True, asyncio.run(awaitable)))
        except BaseException as exc:  # propagate the worker failure to this thread
            results.put((False, exc))

    worker = threading.Thread(target=runner, name="auralign-apple-cleanup", daemon=True)
    worker.start()
    worker.join()
    succeeded, value = results.get_nowait()
    if not succeeded:
        if isinstance(value, BaseException):
            raise value
        raise RuntimeError("Apple cleanup worker returned an invalid failure.")
    return cast(_AsyncResult, value)


def request_cleanup_backend(
    *,
    mode: str,
    endpoint: str,
    model: str | None,
    scope: str,
    text: str,
    proper_nouns: Sequence[str] = (),
    diagnostics: dict[str, int] | None = None,
) -> str:
    """Dispatch cleanup to an explicitly selected local-capable backend."""

    if mode == "apple-foundation":
        return request_apple_foundation_cleanup(
            scope=scope,
            text=text,
            proper_nouns=proper_nouns,
            diagnostics=diagnostics,
        )
    if mode == "local-llm":
        if not proper_nouns:
            return request_local_cleanup(
                endpoint,
                model=model,
                scope=scope,
                text=text,
            )
        return request_local_cleanup(
            endpoint,
            model=model,
            scope=scope,
            text=text,
            proper_nouns=proper_nouns,
        )
    raise CleanupRejectedError(f"Unsupported cleanup mode '{mode}'.")


def request_punctuation_cleanup(
    endpoint: str,
    *,
    model: str | None,
    text: str,
    mode: str = "local-llm",
    proper_nouns: Sequence[str] = (),
    required_complete: bool = False,
) -> tuple[str, dict[str, object]]:
    """Restore text through the production global presentation decoder.

    This compatibility entry point has no timing or speaker evidence. The full
    pipeline calls the same implementation with the canonical transcript so
    pause and turn evidence participate in the global optimization.
    """

    result = _request_punctuation_presentation(
        endpoint,
        model=model,
        text=text,
        mode=mode,
        proper_nouns=proper_nouns,
        required_complete=required_complete,
        transcript=None,
    )
    return result.text, result.report


def _request_punctuation_presentation(
    endpoint: str,
    *,
    model: str | None,
    text: str,
    mode: str,
    proper_nouns: Sequence[str],
    required_complete: bool,
    transcript: Transcript | None,
    source_word_indices: Sequence[tuple[int, ...]] | None = None,
    sat_options: SaTSourceOptions | None = None,
) -> _PunctuationPresentationResult:
    """Collect every valid window proposal and decode one bound global plan."""

    source_tokens = text.split()
    window_size = (
        APPLE_PUNCTUATION_WINDOW_WORDS if mode == "apple-foundation" else PUNCTUATION_WINDOW_WORDS
    )
    if not source_tokens:
        return _PunctuationPresentationResult(
            text="",
            report={
                "schema_version": "punctuation_windows.v2",
                "window_size_words": window_size,
                "overlap_words": PUNCTUATION_WINDOW_OVERLAP_WORDS,
                "window_count": 0,
                "accepted_window_count": 0,
                "fallback_window_count": 0,
                "fallback_reasons": {},
                "structured_edit_safety_recovery_count": 0,
                "structured_edit_lexical_recovery_count": 0,
                "decoder_version": DECODER_VERSION,
                "presentation_plan_sha256": None,
                "proposal_source_registry": _proposal_source_registry_report(),
                "proposal_source_receipts": [],
                "sat_receipt": {
                    "status": "not-run",
                    "reason_code": "empty_input",
                },
                "word_model_receipt": _disabled_word_model_receipt(),
                "surface_quality": {
                    "schema_version": "global_presentation_quality.v7",
                    "lexical_sequence_preserved": True,
                    "suspicious_fragment_addition_count": 0,
                    "contraction_punctuation_violation_count": 0,
                    "added_short_strong_stop_count": 0,
                    "added_short_strong_stop_burst_count": 0,
                    "max_added_short_strong_stop_streak": 0,
                    "added_strong_stop_burst_count": 0,
                    "max_added_strong_stop_cluster": 0,
                    "long_unpunctuated_run_count": 0,
                    "long_sentence_span_count": 0,
                    "overlong_sentence_span_count": 0,
                    "unresolved_overlong_sentence_span_count": 0,
                    "long_sentence_span_receipt_sha256": _sentence_span_receipt_sha256(()),
                    "overlong_sentence_span_receipt_sha256": _sentence_span_receipt_sha256(()),
                    "max_sentence_span_words": 0,
                    "incomplete_trailing_fragment_count": 0,
                    "incomplete_abandoned_fragment_stop_violation_count": 0,
                    "typed_supported_added_strong_stop_count": 0,
                    "unsupported_added_strong_stop_count": 0,
                    "typed_question_scope_without_question_mark_count": 0,
                },
            },
            plan=None,
            decoder_words=(),
        )

    windows, report = _collect_punctuation_window_evidence(
        endpoint,
        model=model,
        source_tokens=source_tokens,
        mode=mode,
        proper_nouns=proper_nouns,
        required_complete=required_complete,
        window_size=window_size,
    )
    decoder_words = _build_presentation_decoder_words(
        transcript,
        source_tokens,
        source_word_indices=source_word_indices,
    )
    scorer_outputs = [
        _source_and_grammar_scorer(
            decoder_words,
            source_tokens,
            proper_nouns=proper_nouns,
        )
    ]
    scorer_outputs.append(
        _aggregate_window_presentation_scorer(
            windows,
            decoder_words,
            mode=mode,
            model=(APPLE_FOUNDATION_MODEL_ID if mode == "apple-foundation" else model),
            window_size=window_size,
        )
    )
    sat_result = None
    try:
        sat_result = run_registered_sat_source(
            decoder_words,
            sat_options or SaTSourceOptions(),
        )
    except SaTScorerError as exc:
        reason = exc.receipt.reason_code if exc.receipt is not None else exc.reason
        raise CleanupRejectedError(
            f"Required SaT presentation scoring failed closed ({reason})."
        ) from exc
    sat_contributes_to_decoder = sat_result is not None and sat_result.receipt.signal_weight > 0.0
    if sat_contributes_to_decoder and sat_result is not None:
        scorer_outputs.append(sat_result.output)
    try:
        plan = decode_presentation(decoder_words, scorer_outputs)
        cleaned_tokens = list(apply_presentation_plan(decoder_words, plan))
    except ValueError as exc:
        raise CleanupRejectedError(f"Global presentation decoding failed closed: {exc}.") from exc

    accepted, reason = cleanup_is_safe(
        " ".join(source_tokens),
        " ".join(cleaned_tokens),
        max_word_delta_ratio=0.0,
        preserve_lexical_sequence=True,
    )
    if not accepted:
        raise CleanupRejectedError(
            f"Global presentation plan failed lexical conservation: {reason}."
        )
    quality_transcript = _presentation_context_transcript(
        transcript,
        source_word_indices,
        source_tokens,
    )
    surface_quality = _decoded_surface_quality(
        quality_transcript,
        source_tokens,
        cleaned_tokens,
        plan,
        proper_nouns=proper_nouns,
    )
    if required_complete:
        blocking_quality_fields = {
            key: surface_quality.get(key)
            for key in (
                "suspicious_fragment_addition_count",
                "contraction_punctuation_violation_count",
                "added_short_strong_stop_burst_count",
                "added_strong_stop_burst_count",
                "unsupported_added_strong_stop_count",
                "incomplete_abandoned_fragment_stop_violation_count",
                "lowercase_after_strong_stop_count",
                "typed_question_scope_without_question_mark_count",
            )
            if surface_quality.get(key) != 0
        }
        if blocking_quality_fields:
            raise CleanupRejectedError(
                "Required cleanup failed surface-quality validation: "
                + ", ".join(sorted(blocking_quality_fields))
                + "."
            )
    proposal_source_receipts: list[dict[str, object]] = []
    backend_evidenced_indices = sorted(
        {
            index
            for window in windows
            if window.response_path != "source_fallback"
            for index in range(window.start_word_index, window.end_word_index)
        }
    )
    backend_coverage_digest = hashlib.sha256(
        (
            json.dumps(
                backend_evidenced_indices,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    for output in scorer_outputs:
        source_receipt = _scorer_output_receipt(output, plan.input_digest)
        if output.provenance.scorer_id == f"{mode}.window-aggregate":
            source_receipt.update(
                {
                    "backend_mode": mode,
                    "backend_model": (
                        APPLE_FOUNDATION_MODEL_ID if mode == "apple-foundation" else model
                    ),
                    "window_size_words": window_size,
                    "window_count": len(windows),
                    "window_evidence_digest": _window_evidence_digest(windows),
                    "evidenced_word_count": len(backend_evidenced_indices),
                    "neutral_gap_word_count": len(decoder_words) - len(backend_evidenced_indices),
                    "evidenced_word_indices_sha256": backend_coverage_digest,
                }
            )
        if sat_result is not None and output.provenance == sat_result.output.provenance:
            source_receipt["output_digest"] = sat_result.receipt.output_digest
            source_receipt["source_receipt_sha256"] = sat_score_receipt_sha256(sat_result.receipt)
        source_receipt["receipt_sha256"] = _scorer_receipt_sha256(source_receipt)
        proposal_source_receipts.append(source_receipt)
    # A configured zero-weight SaT source still publishes its exact execution
    # receipt, but it is deliberately absent from decoder inputs so the plan,
    # score, abstentions, and contributing provenance are identical to SaT-off.
    if sat_result is not None and not sat_contributes_to_decoder:
        source_receipt = _scorer_output_receipt(
            sat_result.output,
            plan.input_digest,
        )
        source_receipt["output_digest"] = sat_result.receipt.output_digest
        source_receipt["source_receipt_sha256"] = sat_score_receipt_sha256(sat_result.receipt)
        source_receipt["receipt_sha256"] = _scorer_receipt_sha256(source_receipt)
        proposal_source_receipts.append(source_receipt)

    report.update(
        {
            "decoder_version": plan.decoder_version,
            "decoder_tie_break_policy": plan.tie_break_policy,
            "decoder_escalation_policy": plan.escalation_policy,
            "presentation_plan_sha256": presentation_plan_sha256(plan),
            "presentation_plan_word_count": len(plan.decisions),
            "presentation_input_digest": plan.input_digest,
            "hard_constraint_application_count": (plan.hard_constraint_application_count),
            "hard_constraint_violation_count": plan.hard_constraint_violation_count,
            "scorer_count": len(scorer_outputs),
            "scorer_abstention_count": len(plan.abstentions),
            "proposal_source_registry": _proposal_source_registry_report(),
            "proposal_source_receipts": proposal_source_receipts,
            "sat_receipt": (
                {
                    **sat_score_receipt_to_dict(sat_result.receipt),
                    "receipt_sha256": sat_score_receipt_sha256(sat_result.receipt),
                }
                if sat_result is not None
                else {"status": "off", "reason_code": "disabled"}
            ),
            "word_model_receipt": _disabled_word_model_receipt(),
            "overlap_disagreement_count": _overlap_disagreement_count(windows),
            "selected_negative_local_margin_count": sum(
                decision.local_score_margin < 0 for decision in plan.decisions
            ),
            "surface_quality": surface_quality,
        }
    )
    return _PunctuationPresentationResult(
        text=" ".join(cleaned_tokens),
        report=report,
        plan=plan,
        decoder_words=decoder_words,
    )


def _presentation_context_transcript(
    transcript: Transcript | None,
    source_word_indices: Sequence[tuple[int, ...]] | None,
    source_tokens: Sequence[str],
) -> Transcript | None:
    if transcript is None or source_word_indices is None:
        return transcript
    if len(source_word_indices) != len(source_tokens):
        raise CleanupRejectedError("Presentation context mapping count changed.")
    canonical_words: list[AlignedWord] = []
    source_container_indexes: list[int] = []
    containers: Sequence[SpeakerTurn | ASRSegment] = (
        transcript.speaker_turns if transcript.speaker_turns else transcript.segments
    )
    for container_index, container in enumerate(containers):
        timed_words = [word for word in container.words if word.text.strip()]
        if timed_words:
            canonical_words.extend(timed_words)
            source_container_indexes.extend(container_index for _ in timed_words)
            continue
        text_tokens = container.text.split()
        canonical_words.extend(
            AlignedWord(
                text=token,
                start=None,
                end=None,
                speaker=(container.speaker if isinstance(container, SpeakerTurn) else None),
            )
            for token in text_tokens
        )
        source_container_indexes.extend(container_index for _ in text_tokens)
    if len(canonical_words) != len(source_container_indexes):
        raise CleanupRejectedError("Presentation context canonical ownership was invalid.")
    container_words: list[list[AlignedWord]] = [[] for _ in containers]
    for clean_index, mapped_indices in enumerate(source_word_indices):
        target = mapped_indices[-1]
        if target < 0 or target >= len(canonical_words):
            raise CleanupRejectedError("Presentation context source index was invalid.")
        container_words[source_container_indexes[target]].append(
            canonical_words[target].model_copy(
                update={"text": source_tokens[clean_index]},
                deep=True,
            )
        )
    if transcript.speaker_turns:
        turns = [
            turn.model_copy(
                update={
                    "words": container_words[index],
                    "text": " ".join(word.text for word in container_words[index]),
                },
                deep=True,
            )
            for index, turn in enumerate(transcript.speaker_turns)
        ]
        return transcript.model_copy(update={"speaker_turns": turns}, deep=True)
    segments = [
        segment.model_copy(
            update={
                "words": container_words[index],
                "text": " ".join(word.text for word in container_words[index]),
            },
            deep=True,
        )
        for index, segment in enumerate(transcript.segments)
    ]
    return transcript.model_copy(update={"segments": segments}, deep=True)


def _collect_punctuation_window_evidence(
    endpoint: str,
    *,
    model: str | None,
    source_tokens: Sequence[str],
    mode: str,
    proper_nouns: Sequence[str],
    required_complete: bool,
    window_size: int,
) -> tuple[list[_PunctuationWindowEvidence], dict[str, object]]:
    """Call the selected backend and retain valid overlap alternatives."""

    stride = window_size - PUNCTUATION_WINDOW_OVERLAP_WORDS
    window_count = 0
    accepted_window_count = 0
    fallback_reasons: dict[str, int] = {}
    backend_diagnostics: dict[str, int] = {}
    windows: list[_PunctuationWindowEvidence] = []
    start = 0
    while start < len(source_tokens):
        end = min(len(source_tokens), start + window_size)
        window_source = tuple(source_tokens[start:end])
        reason: str | None = None
        before_safety = backend_diagnostics.get("structured_edit_safety_recovery_count", 0)
        before_lexical = backend_diagnostics.get("structured_edit_lexical_recovery_count", 0)
        try:
            candidate = request_cleanup_backend(
                mode=mode,
                endpoint=endpoint,
                model=model,
                scope="punctuation",
                text=" ".join(window_source),
                proper_nouns=proper_nouns,
                diagnostics=backend_diagnostics,
            )
        except CleanupRejectedError:
            if required_complete:
                raise
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
                if required_complete:
                    raise CleanupRejectedError(
                        f"Required cleanup changed the lexical sequence in window {window_count}."
                    )
                candidate = " ".join(window_source)
                reason = "lexical_validation_failed"
            else:
                accepted_window_count += 1
        if reason is not None:
            fallback_reasons[reason] = fallback_reasons.get(reason, 0) + 1
        candidate_tokens = tuple(candidate.split())
        response_path = "source_fallback"
        if reason is None:
            after_safety = backend_diagnostics.get("structured_edit_safety_recovery_count", 0)
            after_lexical = backend_diagnostics.get("structured_edit_lexical_recovery_count", 0)
            if after_lexical > before_lexical:
                response_path = "same_model_structured_lexical_recovery"
            elif after_safety > before_safety:
                response_path = "same_model_structured_safety_recovery"
            else:
                response_path = "guided_verbatim"
        windows.append(
            _PunctuationWindowEvidence(
                window_index=window_count,
                start_word_index=start,
                end_word_index=end,
                source_tokens=window_source,
                candidate_tokens=candidate_tokens,
                response_path=response_path,
            )
        )
        window_count += 1
        if end == len(source_tokens):
            break
        start += stride
    covered = {
        index
        for window in windows
        for index in range(window.start_word_index, window.end_word_index)
    }
    if covered != set(range(len(source_tokens))):
        raise CleanupRejectedError("Punctuation proposal coverage was incomplete.")
    report: dict[str, object] = {
        "schema_version": "punctuation_windows.v2",
        "window_size_words": window_size,
        "overlap_words": PUNCTUATION_WINDOW_OVERLAP_WORDS,
        "window_count": window_count,
        "accepted_window_count": accepted_window_count,
        "fallback_window_count": window_count - accepted_window_count,
        "fallback_reasons": fallback_reasons,
        "structured_edit_safety_recovery_count": backend_diagnostics.get(
            "structured_edit_safety_recovery_count", 0
        ),
        "structured_edit_lexical_recovery_count": backend_diagnostics.get(
            "structured_edit_lexical_recovery_count", 0
        ),
    }
    return windows, report


def _semantic_cleanup_result(
    transcript: Transcript,
    source_tokens: Sequence[str],
    *,
    enabled: bool,
    backend_mode: str,
) -> SemanticReconciliationResult:
    contexts, _turn_end_indexes = _cleanup_token_contexts(transcript)
    if len(contexts) != len(source_tokens):
        raise CleanupRejectedError(
            "Semantic cleanup canonical context did not cover every source word."
        )
    canonical_words = tuple(
        CanonicalWord(
            word_index=index,
            surface=surface,
            speaker_id=contexts[index][3],
            start_seconds=contexts[index][0],
            end_seconds=contexts[index][1],
            container_index=contexts[index][2],
        )
        for index, surface in enumerate(source_tokens)
    )
    proposals = propose_safe_speech_artifact_edits(canonical_words) if enabled else ()
    policy = LexicalCleanupPolicy.safe_default() if enabled else LexicalCleanupPolicy(())
    batches: list[SemanticProposalBatch] = []
    for index, proposal in enumerate(proposals):
        batches.append(
            SemanticProposalBatch.from_proposals(
                batch_id=f"deterministic.safe-speech-artifacts.{index}",
                canonical_words=canonical_words,
                provenance=proposal.provenance,
                proposals=(proposal,),
            )
        )
    canonical_digest = canonical_evidence_digest(canonical_words)
    if not batches:
        deterministic_provenance = EditProvenance(
            "auralign.safe-speech-artifacts",
            "1.0",
            canonical_digest,
        )
        batches.append(
            SemanticProposalBatch.abstain(
                batch_id="deterministic.safe-speech-artifacts",
                canonical_words=canonical_words,
                provenance=deterministic_provenance,
                reason=SemanticAbstentionReason.NO_SUPPORTED_EDIT,
            )
        )

    # Apple Foundation Models and the localhost LLM currently have an exact
    # presentation-only contract. Record that semantic abstention explicitly
    # instead of silently treating full-text generation as lexical authority.
    backend_contract = json.dumps(
        {
            "backend_mode": backend_mode,
            "contract": "indexed-presentation-proposals-only",
            "semantic_free_text_authority": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    backend_provenance = EditProvenance(
        f"auralign.{backend_mode}.semantic-proposals",
        "1.0",
        canonical_digest,
    )
    batches.append(
        SemanticProposalBatch.abstain(
            batch_id=f"backend.{backend_mode}.semantic-proposals",
            canonical_words=canonical_words,
            provenance=backend_provenance,
            reason=SemanticAbstentionReason.NO_SUPPORTED_EDIT,
            detail_digest=hashlib.sha256(backend_contract).hexdigest(),
        )
    )
    try:
        return reconcile_semantic_proposal_batches(
            canonical_words,
            batches,
            policy=policy,
        )
    except ValueError as exc:
        raise CleanupRejectedError(f"Semantic cleanup planning failed closed: {exc}.") from exc


def _project_semantic_presentation_cleanup(
    transcript: Transcript,
    semantic_plan: SemanticCleanupPlan,
    presentation_tokens: Sequence[str],
) -> Transcript:
    if len(presentation_tokens) != len(semantic_plan.clean_surfaces):
        raise ValueError("semantic_presentation_word_count_mismatch")
    canonical_word_count = len(semantic_plan.source_to_clean)
    source_surfaces = ["" for _ in range(canonical_word_count)]
    assigned_clean_indices: set[int] = set()
    for clean_index, source_indices in enumerate(semantic_plan.clean_to_source_indices):
        if not source_indices:
            raise ValueError("semantic_clean_word_without_source_mapping")
        if clean_index in assigned_clean_indices:
            raise ValueError("semantic_clean_word_was_projected_twice")
        assigned_clean_indices.add(clean_index)
        target_index = source_indices[-1]
        if target_index < 0 or target_index >= canonical_word_count:
            raise ValueError("semantic_clean_word_source_index_out_of_bounds")
        if source_surfaces[target_index]:
            raise ValueError("semantic_projection_target_collision")
        source_surfaces[target_index] = presentation_tokens[clean_index]
    if assigned_clean_indices != set(range(len(presentation_tokens))):
        raise ValueError("semantic_projection_did_not_consume_all_clean_words")

    updates: dict[str, object] = {}
    if transcript.speaker_turns:
        cursor = 0
        turns: list[SpeakerTurn] = []
        for turn in transcript.speaker_turns:
            word_count = sum(bool(word.text.strip()) for word in turn.words) or len(
                turn.text.split()
            )
            tokens = source_surfaces[cursor : cursor + word_count]
            cursor += word_count
            repaired_words = _repair_words(turn.words, tokens) if turn.words else []
            turns.append(
                turn.model_copy(
                    update={
                        "text": " ".join(token for token in tokens if token.strip()),
                        "words": repaired_words,
                    },
                    deep=True,
                )
            )
        if cursor != canonical_word_count:
            raise ValueError("semantic_turn_projection_did_not_consume_canonical_words")
        updates["speaker_turns"] = turns
    if transcript.segments:
        cursor = 0
        segments: list[ASRSegment] = []
        for segment in transcript.segments:
            word_count = sum(bool(word.text.strip()) for word in segment.words) or len(
                segment.text.split()
            )
            tokens = source_surfaces[cursor : cursor + word_count]
            cursor += word_count
            segments.append(
                segment.model_copy(
                    update={
                        "text": " ".join(token for token in tokens if token.strip()),
                        "words": _repair_words(segment.words, tokens) if segment.words else [],
                    },
                    deep=True,
                )
            )
        if cursor != canonical_word_count:
            raise ValueError("semantic_segment_projection_did_not_consume_canonical_words")
        updates["segments"] = segments
    if transcript.words:
        if sum(bool(word.text.strip()) for word in transcript.words) != canonical_word_count:
            raise ValueError("semantic_top_level_projection_count_mismatch")
        updates["words"] = _repair_words(transcript.words, source_surfaces)
    cleaned = transcript.model_copy(update=updates, deep=True)
    presentation_text = (
        "\n".join(turn.text for turn in cleaned.speaker_turns if turn.text.strip())
        if cleaned.speaker_turns
        else "\n".join(segment.text for segment in cleaned.segments if segment.text.strip())
    )
    return cleaned.model_copy(update={"cleaned_text": presentation_text}, deep=True)


def _build_presentation_decoder_words(
    transcript: Transcript | None,
    source_tokens: Sequence[str],
    *,
    source_word_indices: Sequence[tuple[int, ...]] | None = None,
) -> tuple[DecoderWord, ...]:
    if transcript is None:
        contexts: list[CleanupTokenContext] = [(None, None, 0, None) for _ in source_tokens]
        turn_end_indexes = {len(source_tokens) - 1}
    else:
        canonical_contexts, _canonical_turn_end_indexes = _cleanup_token_contexts(transcript)
        if source_word_indices is None:
            source_word_indices = tuple((index,) for index in range(len(source_tokens)))
        if len(source_word_indices) != len(source_tokens):
            raise CleanupRejectedError(
                "Semantic source mapping did not cover every clean lexical word."
            )
        contexts = []
        for mapped_indices in source_word_indices:
            if not mapped_indices:
                raise CleanupRejectedError(
                    "A clean lexical word lacked canonical source traceability."
                )
            if any(index < 0 or index >= len(canonical_contexts) for index in mapped_indices):
                raise CleanupRejectedError(
                    "A clean lexical word referenced an invalid canonical index."
                )
            selected = [canonical_contexts[index] for index in mapped_indices]
            containers = {context[2] for context in selected}
            speakers = {context[3] for context in selected}
            if len(containers) != 1 or len(speakers) != 1:
                raise CleanupRejectedError(
                    "A clean lexical word crossed canonical ownership boundaries."
                )
            contexts.append(
                (
                    selected[0][0],
                    selected[-1][1],
                    selected[0][2],
                    selected[0][3],
                )
            )
        turn_end_indexes = {
            index
            for index in range(len(contexts))
            if index == len(contexts) - 1 or contexts[index + 1][2] != contexts[index][2]
        }

    question_evidence: list[float] = []
    question_scope_starts: list[int | None] = []
    clause_length = 0
    for index, token in enumerate(source_tokens):
        clause_length += 1
        syntactic_question_scope_start = _question_clause_start(
            source_tokens,
            index=index,
            clause_length=clause_length,
            contexts=contexts,
        )
        question_scope_start = syntactic_question_scope_start
        contracted_question_supported = bool(
            question_scope_start is None
            and _question_boundary_supported(
                index=index,
                source_tokens=source_tokens,
                contexts=contexts,
                turn_end_indexes=turn_end_indexes,
                clause_length=clause_length,
            )
        )
        if contracted_question_supported:
            question_scope_start = _contracted_question_scope_start(
                source_tokens,
                index=index,
                clause_length=clause_length,
                contexts=contexts,
            )
        elif question_scope_start is None and _question_boundary_supported(
            index=index,
            source_tokens=source_tokens,
            contexts=contexts,
            turn_end_indexes=turn_end_indexes,
            clause_length=clause_length,
        ):
            question_scope_start = _contracted_question_scope_start(
                source_tokens,
                index=index,
                clause_length=clause_length,
                contexts=contexts,
            )
        clause_supported = syntactic_question_scope_start is not None or bool(
            contracted_question_supported and question_scope_start is not None
        )
        punctuation = _split_presentation(token)[2]
        question_pause = _following_pause(index, contexts)
        structurally_incomplete = _structural_incomplete_continuation(
            index,
            source_tokens,
            contexts,
        )
        if "?" in punctuation:
            evidence = 1.0
            if question_scope_start is None:
                question_scope_start = index
        elif structurally_incomplete:
            # Keep the interrogative scope alive across this boundary without
            # forcing its mark onto a word that necessarily continues.
            evidence = 0.0
        elif not clause_supported:
            evidence = 0.0
        elif index == len(source_tokens) - 1 or _speaker_changes_after(
            index,
            contexts,
            turn_end_indexes,
        ):
            evidence = 0.95
        elif index in turn_end_indexes:
            evidence = 0.85
        elif question_pause is not None and question_pause >= 0.75:
            evidence = 0.82
        else:
            evidence = 0.0
        question_evidence.append(evidence)
        question_scope_starts.append(question_scope_start)
        if not structurally_incomplete and any(
            mark in _STRONG_SENTENCE_PUNCTUATION for mark in _split_presentation(token)[2]
        ):
            clause_length = 0

    words: list[DecoderWord] = []
    for index, token in enumerate(source_tokens):
        start, end, container_index, speaker = contexts[index]
        pause: float | None = None
        if index + 1 < len(contexts):
            following_start = contexts[index + 1][0]
            if end is not None and following_start is not None:
                pause = max(0.0, following_start - end)
        begins_turn = index == 0 or contexts[index - 1][2] != container_index
        ends_turn = index in turn_end_indexes
        words.append(
            DecoderWord.from_text(
                index,
                token,
                start_seconds=start,
                end_seconds=end,
                pause_after_seconds=pause,
                speaker_id=speaker,
                container_index=container_index,
                begins_turn=begins_turn,
                ends_turn=ends_turn,
                # Only narrow, timing-bound syntactic continuations become
                # structural constraints. Broad function-word and turn rules
                # remain review evidence and never become universal laws.
                incomplete_continuation=_structural_incomplete_continuation(
                    index,
                    source_tokens,
                    contexts,
                ),
                incomplete_contraction=(
                    _normalized_core(token).replace("’", "'") in _INCOMPLETE_CONTRACTIONS
                ),
                incomplete_trailing_fragment=(
                    index == len(source_tokens) - 1
                    and _is_explicitly_incomplete_trailing_fragment(_normalized_core(token))
                ),
                incomplete_abandoned_fragment=_isolated_abandoned_fragment_boundary(
                    index,
                    source_tokens,
                    contexts,
                ),
                unsupported_same_speaker_short_continuation=False,
                contraction_repetition_exception=_contraction_repeats_soon(
                    index,
                    source_tokens,
                    contexts,
                ),
                contraction_correction_exception=_contraction_has_typed_correction(
                    index,
                    source_tokens,
                    contexts,
                ),
                contraction_question_tail_exception=bool(
                    question_evidence[index] >= 0.8 and question_scope_starts[index] is not None
                ),
                question_evidence=question_evidence[index],
                question_scope_start_index=question_scope_starts[index],
            )
        )
    return tuple(words)


def _source_and_grammar_scorer(
    words: Sequence[DecoderWord],
    source_tokens: Sequence[str],
    *,
    proper_nouns: Sequence[str],
) -> ScorerOutput:
    proper_parts = {
        unicodedata.normalize("NFKC", part).casefold()
        for name in proper_nouns
        for part in name.split()
    }
    proposals: list[PresentationProposal] = []
    readability_comma_indices = _readability_comma_indices(words, source_tokens)
    source_sentence_start = True
    for word, token in zip(words, source_tokens, strict=True):
        punctuation = _token_trailing_punctuation(token)
        source_has_mark = punctuation is not TrailingPunctuation.NONE
        probability = 0.68 if source_has_mark else (1.0 / len(TrailingPunctuation))
        sentence_break_probability = (
            0.9
            if punctuation
            in {
                TrailingPunctuation.PERIOD,
                TrailingPunctuation.QUESTION,
                TrailingPunctuation.EXCLAMATION,
                TrailingPunctuation.ELLIPSIS,
            }
            else (0.08 if source_has_mark else 0.5)
        )
        if word.incomplete_continuation:
            punctuation = TrailingPunctuation.NONE
            probability = 0.96
            sentence_break_probability = 0.02
        elif word.question_evidence >= 0.8:
            punctuation = TrailingPunctuation.QUESTION
            probability = 0.82
            sentence_break_probability = 0.9
        else:
            configured_mark = _configured_name_boundary_punctuation(
                source_tokens,
                word.word_index,
                proper_nouns,
            )
            if configured_mark is not None:
                punctuation = configured_mark
                probability = 0.80
                sentence_break_probability = (
                    0.9
                    if configured_mark
                    in {
                        TrailingPunctuation.PERIOD,
                        TrailingPunctuation.QUESTION,
                        TrailingPunctuation.EXCLAMATION,
                        TrailingPunctuation.ELLIPSIS,
                    }
                    else 0.08
                )
            elif word.word_index in readability_comma_indices:
                punctuation = TrailingPunctuation.COMMA
                probability = 0.985
                sentence_break_probability = 0.03

        folded = unicodedata.normalize("NFKC", _split_presentation(token)[1]).casefold()
        configured_name = any(folded in {part, f"{part}'s", f"{part}’s"} for part in proper_parts)
        must_capitalize = (
            source_sentence_start
            or folded == "i"
            or folded.startswith(("i'", "i’"))
            or configured_name
            or folded in _STANDARD_ENGLISH_CASING
        )
        casing_surface = _configured_casing_surface(token, proper_nouns)
        capitalization_probability = (
            (0.9999 if _token_is_capitalized(casing_surface) else 0.0001)
            if casing_surface is not None
            else (0.9999 if must_capitalize else (0.72 if _token_is_capitalized(token) else 0.18))
        )
        proposals.append(
            PresentationProposal(
                word_index=word.word_index,
                lexical_signature=word.lexical_signature,
                lexical_text_digest=lexical_text_digest(word.lexical_text),
                trailing_punctuation=punctuation,
                punctuation_probability=probability,
                capitalization_probability=capitalization_probability,
                sentence_break_probability=sentence_break_probability,
                paragraph_break_probability=0.02,
                evidence_roles=_source_proposal_roles(word),
                casing_surface=casing_surface,
                casing_probability=0.9999 if casing_surface is not None else None,
            )
        )
        source_sentence_start = punctuation in {
            TrailingPunctuation.PERIOD,
            TrailingPunctuation.QUESTION,
            TrailingPunctuation.EXCLAMATION,
            TrailingPunctuation.ELLIPSIS,
        }
    configuration_digest = source_grammar_configuration_digest(
        proper_nouns=proper_nouns,
        word_count=len(words),
    )
    return ScorerOutput(
        provenance=ScorerProvenance(
            "auralign.source-grammar-features",
            SOURCE_GRAMMAR_SCORER_VERSION,
            configuration_digest,
        ),
        proposals=tuple(proposals),
    )


def _source_proposal_roles(
    word: DecoderWord,
) -> tuple[EvidenceRole, ...]:
    # This scorer always makes an explicit mark-class observation, including
    # the meaningful NONE class. Boundary admission remains a separate role:
    # a source-grammar mark cannot manufacture its own sentence boundary.
    roles: list[EvidenceRole] = [EvidenceRole.EXACT_MARK, EvidenceRole.CASE_ONLY]
    if _decoder_word_has_structural_constraint(word):
        roles.append(EvidenceRole.STRUCTURAL_VETO)
    return tuple(roles)


def _decoder_word_has_structural_constraint(word: DecoderWord) -> bool:
    return bool(
        word.incomplete_continuation
        or word.incomplete_contraction
        or word.incomplete_trailing_fragment
        or word.incomplete_abandoned_fragment
        or word.unsupported_same_speaker_short_continuation
    )


def _readability_comma_indices(
    words: Sequence[DecoderWord],
    source_tokens: Sequence[str],
) -> frozenset[int]:
    """Propose sparse, source-bound commas that cap punctuation-free spans.

    The scorer never manufactures a strong sentence boundary. It selects the
    minimum number of weak boundaries needed to keep a source span below the
    required-quality limit, and only at a discourse, clause, or timing-backed
    position. If no safe path exists, it abstains for that span and lets the
    required cleanup gate fail closed.
    """

    if len(words) != len(source_tokens):
        raise ValueError("readability scorer word coverage is incomplete")
    selected: set[int] = set()
    run_start = 0
    for index, (word, token) in enumerate(zip(words, source_tokens, strict=True)):
        if _split_presentation(token)[2] or word.ends_turn:
            selected.update(
                _readability_commas_for_span(
                    words,
                    source_tokens,
                    start=run_start,
                    end=index,
                )
            )
            run_start = index + 1
    if run_start < len(words):
        selected.update(
            _readability_commas_for_span(
                words,
                source_tokens,
                start=run_start,
                end=len(words) - 1,
            )
        )
    return frozenset(selected)


def _readability_commas_for_span(
    words: Sequence[DecoderWord],
    source_tokens: Sequence[str],
    *,
    start: int,
    end: int,
) -> tuple[int, ...]:
    if end - start + 1 < _MAX_PUNCTUATION_GAP_WORDS + 1:
        return ()
    candidates = [
        index
        for index in range(start, end)
        if _safe_readability_comma_boundary(words, source_tokens, index)
    ]
    nodes = [start - 1, *candidates, end]
    # node -> (selected indices, geometry penalty, accumulated evidence)
    paths: dict[int, tuple[tuple[int, ...], float, float]] = {start - 1: ((), 0.0, 0.0)}
    for node in nodes[1:]:
        best: tuple[tuple[int, ...], float, float] | None = None
        for previous, (marks, geometry, evidence) in paths.items():
            span_length = node - previous
            terminal = node == end
            if span_length > _MAX_PUNCTUATION_GAP_WORDS or (
                not terminal and span_length < _MIN_READABILITY_SPAN_WORDS
            ):
                continue
            proposed_marks = marks if terminal else (*marks, node)
            proposed = (
                proposed_marks,
                geometry + abs(span_length - _PREFERRED_READABILITY_SPAN_WORDS),
                evidence
                + (0.0 if terminal else _readability_boundary_evidence(words, source_tokens, node)),
            )
            if best is None or _readability_path_key(proposed) < _readability_path_key(best):
                best = proposed
        if best is not None:
            paths[node] = best
    result = paths.get(end)
    return () if result is None else result[0]


def _safe_readability_comma_boundary(
    words: Sequence[DecoderWord],
    source_tokens: Sequence[str],
    index: int,
) -> bool:
    word = words[index]
    current = _normalized_core(source_tokens[index]).replace("’", "'")
    following = _normalized_core(source_tokens[index + 1]).replace("’", "'")
    if current == following or word.incomplete_continuation:
        return False
    if current in _DISCOURSE_COMMA_WORDS:
        return True
    if _is_incomplete_sentence_end(current):
        return False
    return bool(
        following in _CLAUSE_CONNECTORS
        or (word.pause_after_seconds is not None and word.pause_after_seconds >= 0.30)
    )


def _readability_boundary_evidence(
    words: Sequence[DecoderWord],
    source_tokens: Sequence[str],
    index: int,
) -> float:
    current = _normalized_core(source_tokens[index]).replace("’", "'")
    following = _normalized_core(source_tokens[index + 1]).replace("’", "'")
    pause = words[index].pause_after_seconds or 0.0
    return (
        min(1.5, pause)
        + (0.6 if current in _DISCOURSE_COMMA_WORDS else 0.0)
        + (0.4 if following in _CLAUSE_CONNECTORS else 0.0)
    )


def _readability_path_key(
    value: tuple[tuple[int, ...], float, float],
) -> tuple[int, float, float, tuple[int, ...]]:
    marks, geometry, evidence = value
    return (len(marks), geometry, -evidence, marks)


def source_grammar_configuration_digest(
    *,
    proper_nouns: Sequence[str],
    word_count: int,
) -> str:
    """Bind the deterministic source scorer to its exact production inputs."""

    if isinstance(word_count, bool) or not isinstance(word_count, int) or word_count < 0:
        raise ValueError("source scorer word_count must be a non-negative integer")
    return hashlib.sha256(
        json.dumps(
            {
                "scorer": f"source-grammar-features.v{SOURCE_GRAMMAR_SCORER_VERSION}",
                "max_punctuation_gap_words": _MAX_PUNCTUATION_GAP_WORDS,
                "minimum_readability_span_words": _MIN_READABILITY_SPAN_WORDS,
                "preferred_readability_span_words": _PREFERRED_READABILITY_SPAN_WORDS,
                "evidence_role_contract": "exact-mark-case-structural-veto.v1",
                "source_stop_continuation": "typed-relational-structural-evidence-only.v1",
                "weak_contraction_exceptions": {
                    "exact_repetition_lookahead_words": 1,
                    "correction_markers": sorted(_EXPLICIT_CORRECTION_MARKERS),
                    "correction_pause_max_seconds": 0.75,
                    "typed_question_tail_threshold": 0.8,
                },
                "proper_nouns": list(proper_nouns),
                "word_count": word_count,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def window_aggregate_configuration_digest(
    *,
    mode: str,
    model: str | None,
    window_size: int,
    window_count: int,
    evidence_digest: str,
) -> str:
    """Bind the backend aggregate scorer to exact window execution evidence."""

    if (
        isinstance(window_size, bool)
        or not isinstance(window_size, int)
        or window_size <= 0
        or isinstance(window_count, bool)
        or not isinstance(window_count, int)
        or window_count < 0
        or len(evidence_digest) != 64
        or any(character not in "0123456789abcdef" for character in evidence_digest)
    ):
        raise ValueError("aggregate scorer configuration evidence is invalid")
    return hashlib.sha256(
        json.dumps(
            {
                "mode": mode,
                "model": model,
                "evidence_role_contract": "exact-mark-case.v1",
                "window_size": window_size,
                "window_count": window_count,
                "evidence_digest": evidence_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _aggregate_window_presentation_scorer(
    windows: Sequence[_PunctuationWindowEvidence],
    words: Sequence[DecoderWord],
    *,
    mode: str,
    model: str | None,
    window_size: int,
) -> ScorerOutput:
    evidence_digest = _window_evidence_digest(windows)
    provenance = ScorerProvenance(
        scorer_id=f"{mode}.window-aggregate",
        scorer_version=WINDOW_AGGREGATE_SCORER_VERSION,
        configuration_digest=window_aggregate_configuration_digest(
            mode=mode,
            model=model,
            window_size=window_size,
            window_count=len(windows),
            evidence_digest=evidence_digest,
        ),
    )
    accepted_windows = [window for window in windows if window.response_path != "source_fallback"]
    if not accepted_windows:
        return ScorerOutput.abstain(provenance, "backend_window_fallback")

    # Overlapping windows are correlated samples from one backend, not
    # independent scorers. Reconcile them into one distribution per word so
    # overlap can expose disagreement without multiplying model authority.
    per_word: dict[
        int,
        list[
            tuple[
                float,
                dict[TrailingPunctuation, float],
                float,
                str,
            ]
        ],
    ] = {}
    punctuation_order = tuple(TrailingPunctuation)
    for window in accepted_windows:
        length = len(window.candidate_tokens)
        for local_index, candidate in enumerate(window.candidate_tokens):
            global_index = window.start_word_index + local_index
            center_distance = abs(local_index - ((length - 1) / 2.0))
            center_weight = 1.0 - (center_distance / max(1.0, length / 2.0))
            vote_weight = 0.5 + (0.5 * max(0.0, center_weight))
            selected_probability = 0.72 + (0.22 * max(0.0, center_weight))
            selected = _token_trailing_punctuation(candidate)
            other_probability = (1.0 - selected_probability) / (len(punctuation_order) - 1)
            distribution = {
                punctuation: (
                    selected_probability if punctuation is selected else other_probability
                )
                for punctuation in punctuation_order
            }
            per_word.setdefault(global_index, []).append(
                (
                    vote_weight,
                    distribution,
                    0.92 if _token_is_capitalized(candidate) else 0.08,
                    _replace_trailing_punctuation(candidate, "none"),
                )
            )

    proposals: list[PresentationProposal] = []
    for global_index in range(len(words)):
        word = words[global_index]
        entries = per_word.get(global_index, [])
        if entries:
            total_weight = math.fsum(entry[0] for entry in entries)
            distribution = {
                punctuation: math.fsum(
                    weight * probabilities[punctuation]
                    for weight, probabilities, _capitalization, _casing in entries
                )
                / total_weight
                for punctuation in punctuation_order
            }
            normalization = math.fsum(distribution.values())
            distribution = {
                punctuation: probability / normalization
                for punctuation, probability in distribution.items()
            }
            capitalization_probability = (
                math.fsum(
                    weight * capitalization
                    for weight, _probabilities, capitalization, _casing in entries
                )
                / total_weight
            )
            casing_weights: dict[str, float] = {}
            for weight, _probabilities, _capitalization, casing in entries:
                casing_weights[casing] = casing_weights.get(casing, 0.0) + weight
            casing_surface = min(
                casing_weights,
                key=lambda surface: (-casing_weights[surface], surface),
            )
            casing_share = casing_weights[casing_surface] / total_weight
            casing_probability = 0.5 + (0.42 * casing_share)
        else:
            distribution = {
                punctuation: 1.0 / len(punctuation_order) for punctuation in punctuation_order
            }
            capitalization_probability = 0.5
            casing_surface = None
            casing_probability = None
        sentence_break_probability = math.fsum(
            distribution[punctuation]
            for punctuation in (
                TrailingPunctuation.PERIOD,
                TrailingPunctuation.QUESTION,
                TrailingPunctuation.EXCLAMATION,
                TrailingPunctuation.ELLIPSIS,
            )
        )
        proposals.extend(
            PresentationProposal(
                word_index=global_index,
                lexical_signature=word.lexical_signature,
                lexical_text_digest=lexical_text_digest(word.lexical_text),
                trailing_punctuation=punctuation,
                punctuation_probability=probability,
                capitalization_probability=capitalization_probability,
                # Derive the boundary channel from the one reconciled class
                # distribution rather than counting overlapping windows again.
                sentence_break_probability=sentence_break_probability,
                paragraph_break_probability=0.5,
                evidence_roles=(EvidenceRole.EXACT_MARK, EvidenceRole.CASE_ONLY),
                casing_surface=casing_surface,
                casing_probability=casing_probability,
            )
            for punctuation, probability in distribution.items()
        )
    return ScorerOutput(provenance=provenance, proposals=tuple(proposals))


def _window_evidence_digest(windows: Sequence[_PunctuationWindowEvidence]) -> str:
    return hashlib.sha256(
        json.dumps(
            [
                {
                    "window_index": window.window_index,
                    "start": window.start_word_index,
                    "end": window.end_word_index,
                    "response_path": window.response_path,
                    "source_sha256": hashlib.sha256(
                        " ".join(window.source_tokens).encode("utf-8")
                    ).hexdigest(),
                    "candidate_sha256": hashlib.sha256(
                        " ".join(window.candidate_tokens).encode("utf-8")
                    ).hexdigest(),
                }
                for window in windows
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _token_trailing_punctuation(token: str) -> TrailingPunctuation:
    punctuation = _split_presentation(token)[2]
    if not punctuation:
        return TrailingPunctuation.NONE
    if punctuation == "..." or punctuation == "…":
        return TrailingPunctuation.ELLIPSIS
    if "?" in punctuation:
        return TrailingPunctuation.QUESTION
    if "!" in punctuation:
        return TrailingPunctuation.EXCLAMATION
    try:
        return TrailingPunctuation(punctuation[-1])
    except ValueError:
        return TrailingPunctuation.NONE


def _token_is_capitalized(token: str) -> bool:
    for character in _split_presentation(token)[1]:
        if character.isalpha():
            return character.isupper()
    return False


def _configured_casing_surface(
    token: str,
    proper_nouns: Sequence[str],
) -> str | None:
    """Return an exact case-only surface for trusted configured spellings."""

    prefix, core, _punctuation, closers = _split_presentation(token)
    folded = unicodedata.normalize("NFKC", core).casefold()
    desired: str | None = _STANDARD_ENGLISH_CASING.get(folded)
    if folded == "i":
        desired = "I"
    elif folded.startswith(("i'", "i’")):
        desired = f"I{core[1:]}"
    for proper_noun in proper_nouns:
        for part in proper_noun.split():
            normalized = unicodedata.normalize("NFKC", part).casefold()
            if folded == normalized:
                desired = part
                break
            for possessive in ("'s", "’s"):
                if folded == f"{normalized}{possessive}":
                    desired = f"{part}{core[len(part) :]}"
                    break
            if desired is not None:
                break
        if desired is not None:
            break
    if desired is None:
        return None
    return f"{prefix}{desired}{closers}"


def _configured_name_boundary_punctuation(
    tokens: Sequence[str],
    index: int,
    proper_nouns: Sequence[str],
) -> TrailingPunctuation | None:
    single_word_names = [name for name in proper_nouns if len(name.split()) == 1]
    if len(single_word_names) < 3 or not any(
        _proper_noun_core_matches(_split_presentation(tokens[index])[1], name)
        for name in single_word_names
    ):
        return None
    following_name: int | None = None
    for candidate_index in range(index + 1, min(len(tokens), index + 4)):
        intervening = [_normalized_core(token) for token in tokens[index + 1 : candidate_index]]
        if not all(word in _PROPER_NOUN_LIST_CONNECTORS for word in intervening):
            break
        if any(
            _proper_noun_core_matches(
                _split_presentation(tokens[candidate_index])[1],
                name,
            )
            for name in single_word_names
        ):
            following_name = candidate_index
            break
    if following_name is not None:
        return TrailingPunctuation.COMMA
    if (
        index + 2 < len(tokens)
        and _normalized_core(tokens[index + 1]) == "i"
        and _normalized_core(tokens[index + 2]) in _FIRST_PERSON_RESTARTS
    ):
        return TrailingPunctuation.PERIOD
    return None


def _overlap_disagreement_count(
    windows: Sequence[_PunctuationWindowEvidence],
) -> int:
    by_index: dict[int, set[tuple[TrailingPunctuation, bool]]] = {}
    for window in windows:
        if window.response_path == "source_fallback":
            continue
        for local_index, token in enumerate(window.candidate_tokens):
            global_index = window.start_word_index + local_index
            by_index.setdefault(global_index, set()).add(
                (_token_trailing_punctuation(token), _token_is_capitalized(token))
            )
    return sum(len(proposals) > 1 for proposals in by_index.values())


def _scorer_output_receipt(
    output: ScorerOutput,
    input_digest: str,
) -> dict[str, object]:
    output_digest = scorer_output_digest(output)
    return {
        "schema_version": "auralign.presentation.scorer-receipt.v1",
        "status": "abstained" if output.abstention_reason is not None else "succeeded",
        "abstention_reason": output.abstention_reason,
        "scorer_id": output.provenance.scorer_id,
        "scorer_version": output.provenance.scorer_version,
        "configuration_digest": output.provenance.configuration_digest,
        "presentation_input_digest": input_digest,
        "proposal_count": len(output.proposals),
        "output_digest": output_digest,
    }


def scorer_output_digest(output: ScorerOutput) -> str:
    """Hash one typed scorer output independently of its outer receipt."""

    proposal_records = [
        {
            "word_index": proposal.word_index,
            "lexical_signature": proposal.lexical_signature,
            "lexical_text_digest": proposal.lexical_text_digest,
            "trailing_punctuation": proposal.trailing_punctuation.value,
            "punctuation_probability": proposal.punctuation_probability,
            "capitalization_probability": proposal.capitalization_probability,
            "casing_surface_digest": (
                hashlib.sha256(proposal.casing_surface.encode("utf-8")).hexdigest()
                if proposal.casing_surface is not None
                else None
            ),
            "casing_probability": proposal.casing_probability,
            "sentence_break_probability": proposal.sentence_break_probability,
            "paragraph_break_probability": proposal.paragraph_break_probability,
            "evidence_roles": [role.value for role in proposal.evidence_roles],
            "source_mark_veto": proposal.source_mark_veto,
        }
        for proposal in output.proposals
    ]
    return hashlib.sha256(
        json.dumps(
            proposal_records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _scorer_receipt_sha256(receipt: dict[str, object]) -> str:
    if "receipt_sha256" in receipt:
        raise ValueError("scorer receipt cannot hash an existing receipt digest")
    return hashlib.sha256(
        (
            json.dumps(
                receipt,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def _proposal_source_registry_report() -> list[dict[str, object]]:
    registrations: list[dict[str, object]] = [
        {
            "key": "source-grammar-features",
            "kind": "deterministic-local",
            "production": True,
            "standalone_included": True,
        },
        {
            "key": "cleanup-backend-indexed-windows",
            "kind": "apple-or-openai-compatible-local-proposal-source",
            "production": True,
            "authoritative_text": False,
        },
    ]
    registrations.extend(
        {
            "key": registration.key,
            "kind": "exact-local-model-proposal-source",
            "scorer_id": registration.scorer_id,
            "model_id": registration.model_id,
            "model_revision": registration.model_revision,
            "tokenizer_id": registration.tokenizer_id,
            "tokenizer_revision": registration.tokenizer_revision,
            "runtime_extra": registration.runtime_extra,
            "local_only": registration.local_only,
            "offline_required": registration.offline_required,
            "production": registration.production,
            "standalone_included": registration.standalone_included,
        }
        for registration in registered_production_proposal_sources()
    )
    registrations.append(
        {
            "key": "word-token-classifier",
            "kind": "typed-provider-adapter",
            "production": True,
            "checkpoint_status": "no-approved-checkpoint",
            "enabled": False,
        }
    )
    return registrations


def _disabled_word_model_receipt() -> dict[str, object]:
    """Record why the typed word-model adapter did not execute.

    The adapter is production code, but execution is intentionally impossible
    until one exact checkpoint has a provenance-bound approval receipt showing
    that it materially improves the conversational baseline.  Keeping this as
    a receipt, rather than silently omitting the source, prevents a configured
    MAX run from implying that a token classifier contributed evidence.
    """

    return {
        "schema_version": "auralign.presentation-word-model.routing.v1",
        "status": "off",
        "backend": "none",
        "reason_code": "no_approved_checkpoint",
        "authoritative_text": False,
        "network_access": False,
    }


def _decoded_surface_quality(
    transcript: Transcript | None,
    source_tokens: Sequence[str],
    cleaned_tokens: Sequence[str],
    plan: PresentationPlan,
    *,
    proper_nouns: Sequence[str] = (),
) -> dict[str, object]:
    if transcript is None:
        diagnostics: dict[str, object] = {
            "same_speaker_short_break_addition_count": 0,
            "contraction_punctuation_violation_count": 0,
            "added_short_strong_stop_count": 0,
            "added_short_strong_stop_burst_count": 0,
            "max_added_short_strong_stop_streak": 0,
            "added_strong_stop_burst_count": 0,
            "max_added_strong_stop_cluster": 0,
            "long_low_punctuation_turn_count": 0,
            "long_unpunctuated_run_count": 0,
            "max_unpunctuated_run_words": 0,
            "long_sentence_span_count": 0,
            "overlong_sentence_span_count": 0,
            "unresolved_overlong_sentence_span_count": 0,
            "long_sentence_span_receipt_sha256": _sentence_span_receipt_sha256(()),
            "overlong_sentence_span_receipt_sha256": _sentence_span_receipt_sha256(()),
            "max_sentence_span_words": 0,
            "incomplete_trailing_fragment_count": 0,
            "incomplete_abandoned_fragment_stop_violation_count": 0,
            "question_leading_turn_without_question_mark_count": 0,
            "typed_question_scope_without_question_mark_count": 0,
            "quality_warnings": [],
        }
        suspicious = 0
    else:
        contexts, turn_end_indexes = _cleanup_token_contexts(transcript)
        suspicious = _suspicious_added_sentence_breaks(
            source_tokens,
            cleaned_tokens,
            contexts=contexts,
            turn_end_indexes=turn_end_indexes,
            decisions=plan.decisions,
        )
        diagnostics = _surface_quality_diagnostics(
            transcript,
            source_tokens,
            cleaned_tokens,
            contexts=contexts,
            turn_end_indexes=turn_end_indexes,
        )
    raw_warnings = diagnostics.get("quality_warnings", [])
    warnings = list(raw_warnings) if isinstance(raw_warnings, list) else []
    if suspicious:
        warnings.append("static_fragment_risk_requires_editorial_review")
    locally_adverse_added_strong_stops = 0
    typed_supported_added_strong_stops = 0
    unsupported_added_strong_stops = 0
    lowercase_after_strong_stops = 0
    del proper_nouns
    for index, (source, _cleaned, decision) in enumerate(
        zip(source_tokens, cleaned_tokens, plan.decisions, strict=True)
    ):
        source_has_strong = any(
            mark in _STRONG_SENTENCE_PUNCTUATION for mark in _split_presentation(source)[2]
        )
        added_strong = decision.sentence_break and not source_has_strong
        if added_strong:
            if decision.boundary_support.supported:
                typed_supported_added_strong_stops += 1
            else:
                unsupported_added_strong_stops += 1
        if decision.sentence_break and not source_has_strong and decision.local_score_margin < -4.0:
            locally_adverse_added_strong_stops += 1
        if decision.sentence_break and index + 1 < len(cleaned_tokens):
            following_core = _split_presentation(cleaned_tokens[index + 1])[1]
            letters = [character for character in following_core if character.isalpha()]
            if (
                letters
                and letters[0].islower()
                and not any(character.isupper() for character in letters[1:])
            ):
                lowercase_after_strong_stops += 1
    if locally_adverse_added_strong_stops:
        warnings.append("locally_adverse_added_strong_stops_require_review")
    if lowercase_after_strong_stops:
        warnings.append("lowercase_after_strong_stops_require_review")
    return {
        "schema_version": "global_presentation_quality.v7",
        "lexical_sequence_preserved": True,
        "suspicious_fragment_addition_count": suspicious,
        "hard_constraint_violation_count": plan.hard_constraint_violation_count,
        "unsupported_low_margin_high_impact_change_count": sum(
            decision.sentence_break and decision.local_score_margin < -1.0
            for decision in plan.decisions
        ),
        "locally_adverse_margin_threshold": -4.0,
        "locally_adverse_added_strong_stop_count": (locally_adverse_added_strong_stops),
        "typed_supported_added_strong_stop_count": typed_supported_added_strong_stops,
        "unsupported_added_strong_stop_count": unsupported_added_strong_stops,
        "lowercase_after_strong_stop_count": lowercase_after_strong_stops,
        **diagnostics,
        "quality_warnings": warnings,
    }


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
            if not _same_lexical_word(original_word, cleaned_word):
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
    repaired_tokens = _apply_punctuation_edits_to_tokens(source_tokens, edits)
    repaired_tokens = _apply_safe_capitalization(transcript, repaired_tokens)
    return project_punctuation_cleanup(transcript, " ".join(repaired_tokens))


def _apply_punctuation_edits_to_tokens(
    source_tokens: list[str],
    edits: list[PunctuationEdit],
) -> list[str]:
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
        if not _same_lexical_word(token, source_tokens[edit.word_index]):
            raise ValueError("punctuation_edit_changed_lexical_word")
        repaired_tokens[edit.word_index] = token
    return repaired_tokens


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


def _same_lexical_word(left: str, right: str) -> bool:
    if _lexical_signature(left) != _lexical_signature(right):
        return False
    left_core = unicodedata.normalize("NFC", _split_presentation(left)[1])
    right_core = unicodedata.normalize("NFC", _split_presentation(right)[1])
    return len(left_core) == len(right_core)


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


def _conservative_punctuation_surfaces(
    transcript: Transcript,
    source_tokens: list[str],
    candidate_tokens: list[str],
) -> tuple[list[str], dict[str, object]]:
    """Conserve lexical text while rejecting locally implausible model marks.

    Foundation Models can satisfy exact lexical validation yet over-segment a
    transcript into one-word sentences. This compatibility layer treats source
    punctuation and model output as fallible presentation evidence, accepts
    only locally supported changes, and intentionally discards model casing so
    deterministic sentence, pronoun, and configured proper-noun casing can be
    applied next.
    """

    accepted, reason = cleanup_is_safe(
        " ".join(source_tokens),
        " ".join(candidate_tokens),
        max_word_delta_ratio=0.0,
        preserve_lexical_sequence=True,
    )
    if not accepted:
        raise CleanupRejectedError(
            f"Punctuation surface quality input failed lexical validation: {reason}."
        )
    if len(source_tokens) != len(candidate_tokens):
        raise CleanupRejectedError("Punctuation surface quality input count changed.")

    contexts, turn_end_indexes = _cleanup_token_contexts(transcript)
    if len(contexts) != len(source_tokens):
        raise CleanupRejectedError("Punctuation surface timing context was incomplete.")

    repaired: list[str] = []
    clause_length = 0
    accepted_additions = 0
    rejected_additions = 0
    source_marks_observed = 0
    source_marks_repaired = 0
    deterministic_boundaries = 0
    discarded_model_casing = 0
    for index, (source, candidate) in enumerate(zip(source_tokens, candidate_tokens, strict=True)):
        source_prefix, source_core, source_punctuation, source_closers = _split_presentation(source)
        _, candidate_core, candidate_punctuation, _ = _split_presentation(candidate)
        if source_core != candidate_core:
            discarded_model_casing += 1
        clause_length += 1
        punctuation = source_punctuation
        if source_punctuation:
            source_marks_observed += 1
            punctuation = _repair_source_punctuation(
                index=index,
                punctuation=source_punctuation,
                source_tokens=source_tokens,
                contexts=contexts,
                turn_end_indexes=turn_end_indexes,
                clause_length=clause_length,
            )
            if punctuation != source_punctuation:
                source_marks_repaired += 1
        elif candidate_punctuation:
            if _model_punctuation_is_supported(
                index=index,
                punctuation=candidate_punctuation,
                source_tokens=source_tokens,
                contexts=contexts,
                turn_end_indexes=turn_end_indexes,
                clause_length=clause_length,
            ):
                punctuation = candidate_punctuation
                accepted_additions += 1
            else:
                rejected_additions += 1

        deterministic_mark = None
        if not punctuation:
            deterministic_mark = _deterministic_boundary_mark(
                index=index,
                source_tokens=source_tokens,
                contexts=contexts,
                turn_end_indexes=turn_end_indexes,
                clause_length=clause_length,
            )
        if deterministic_mark is not None:
            punctuation = deterministic_mark
            deterministic_boundaries += 1

        repaired.append(f"{source_prefix}{source_core}{punctuation}{source_closers}")
        if any(mark in _STRONG_SENTENCE_PUNCTUATION for mark in punctuation):
            clause_length = 0

    suspicious_fragments = _suspicious_added_sentence_breaks(
        source_tokens,
        repaired,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )
    if suspicious_fragments:
        raise CleanupRejectedError(
            "Punctuation surface quality validation found unsupported sentence fragments."
        )
    diagnostics = _surface_quality_diagnostics(
        transcript,
        source_tokens,
        repaired,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )
    return repaired, {
        "schema_version": "conservative_punctuation_surfaces.v2",
        "source_punctuation_observed_count": source_marks_observed,
        "source_punctuation_unchanged_count": (source_marks_observed - source_marks_repaired),
        "source_punctuation_repaired_count": source_marks_repaired,
        "accepted_model_punctuation_addition_count": accepted_additions,
        "rejected_model_punctuation_addition_count": rejected_additions,
        "deterministic_boundary_punctuation_count": deterministic_boundaries,
        "discarded_model_casing_count": discarded_model_casing,
        "suspicious_fragment_addition_count": 0,
        "lexical_sequence_preserved": True,
        **diagnostics,
    }


def _cleanup_token_contexts(
    transcript: Transcript,
) -> tuple[list[CleanupTokenContext], set[int]]:
    contexts: list[CleanupTokenContext] = []
    turn_end_indexes: set[int] = set()
    containers: Sequence[SpeakerTurn | ASRSegment] = (
        transcript.speaker_turns if transcript.speaker_turns else transcript.segments
    )
    for container_index, container in enumerate(containers):
        context_start = len(contexts)
        speaker = container.speaker if isinstance(container, SpeakerTurn) else None
        nonempty_words = [word for word in container.words if word.text.strip()]
        if nonempty_words:
            contexts.extend(
                (word.start, word.end, container_index, speaker) for word in nonempty_words
            )
        else:
            token_count = len(container.text.split())
            contexts.extend((None, None, container_index, speaker) for _ in range(token_count))
        if len(contexts) > context_start:
            turn_end_indexes.add(len(contexts) - 1)
    return contexts, turn_end_indexes


def _following_pause(
    index: int,
    contexts: Sequence[CleanupTokenContext],
) -> float | None:
    if index + 1 >= len(contexts):
        return None
    _, current_end, _, _ = contexts[index]
    next_start, _, _, _ = contexts[index + 1]
    if current_end is None or next_start is None:
        return None
    return max(0.0, next_start - current_end)


def _normalized_core(token: str) -> str:
    return unicodedata.normalize("NFKC", _split_presentation(token)[1]).casefold()


def _is_incomplete_sentence_end(core: str) -> bool:
    normalized = unicodedata.normalize("NFKC", core).casefold().replace("’", "'")
    return normalized in _INCOMPLETE_SENTENCE_END_WORDS or normalized in (_INCOMPLETE_CONTRACTIONS)


def _is_explicitly_incomplete_trailing_fragment(core: str) -> bool:
    """Return only terminal surfaces that cannot complete a clause alone.

    The broader ``_is_incomplete_sentence_end`` heuristic is useful as soft
    scoring evidence, but pronouns and complete negative contractions in that
    set can legitimately end a sentence. A hard terminal constraint must be
    limited to syntax that necessarily selects following material.
    """

    normalized = unicodedata.normalize("NFKC", core).casefold().replace("’", "'")
    return normalized in _STRICT_SYNTACTIC_CONTINUATIONS or normalized in (
        _STRICT_AUXILIARY_CONTRACTIONS
    )


def _incomplete_contraction_blocks_added_question(
    index: int,
    source_tokens: Sequence[str],
    *,
    question_scope_start_index: int | None,
) -> bool:
    """Return whether a question at this contraction would be model-added."""

    current = _normalized_core(source_tokens[index]).replace("’", "'")
    if current not in _INCOMPLETE_CONTRACTIONS:
        return False
    if "?" in _split_presentation(source_tokens[index])[2]:
        return False
    return not (
        current.endswith("n't")
        and question_scope_start_index is not None
        and index > question_scope_start_index
    )


def _structural_incomplete_continuation(
    index: int,
    source_tokens: Sequence[str],
    contexts: Sequence[CleanupTokenContext],
) -> bool:
    """Return high-confidence, local syntactic continuation evidence only."""

    if index + 1 >= len(source_tokens) or index + 1 >= len(contexts):
        return False
    same_container = contexts[index][2] == contexts[index + 1][2]
    same_speaker_adjacent_turn = bool(
        not same_container
        and contexts[index][3] is not None
        and contexts[index][3] == contexts[index + 1][3]
    )
    if not same_container and not same_speaker_adjacent_turn:
        return False
    pause = _following_pause(index, contexts)
    if pause is None or pause >= 0.35:
        return False
    current = _normalized_core(source_tokens[index]).replace("’", "'")
    following = _normalized_core(source_tokens[index + 1]).replace("’", "'")
    if not following or current == following:
        return False
    return current in _STRICT_SYNTACTIC_CONTINUATIONS or current in (_STRICT_AUXILIARY_CONTRACTIONS)


def _same_owner_at(
    left_index: int,
    right_index: int,
    contexts: Sequence[CleanupTokenContext],
) -> bool:
    left_container = contexts[left_index][2]
    right_container = contexts[right_index][2]
    if left_container == right_container:
        return True
    left_speaker = contexts[left_index][3]
    right_speaker = contexts[right_index][3]
    return bool(
        left_speaker is not None and right_speaker is not None and left_speaker == right_speaker
    )


def _contraction_repeats_soon(
    index: int,
    source_tokens: Sequence[str],
    contexts: Sequence[CleanupTokenContext],
) -> bool:
    current = _normalized_core(source_tokens[index]).replace("’", "'")
    if current not in _INCOMPLETE_CONTRACTIONS or current.endswith("n't"):
        return False
    following_index = index + 1
    return bool(
        following_index < len(source_tokens)
        and following_index < len(contexts)
        and _same_owner_at(index, following_index, contexts)
        and _normalized_core(source_tokens[following_index]).replace("’", "'") == current
    )


def _contraction_has_typed_correction(
    index: int,
    source_tokens: Sequence[str],
    contexts: Sequence[CleanupTokenContext],
) -> bool:
    if index + 1 >= len(source_tokens) or index + 1 >= len(contexts):
        return False
    current = _normalized_core(source_tokens[index]).replace("’", "'")
    if (
        current not in _INCOMPLETE_CONTRACTIONS
        or current.endswith("n't")
        or not _same_owner_at(index, index + 1, contexts)
    ):
        return False
    pause = _following_pause(index, contexts)
    if pause is None or pause >= 0.75:
        return False
    following = _normalized_core(source_tokens[index + 1]).replace("’", "'")
    return following in _EXPLICIT_CORRECTION_MARKERS


def _isolated_abandoned_fragment_boundary(
    index: int,
    source_tokens: Sequence[str],
    contexts: Sequence[CleanupTokenContext],
) -> bool:
    """Identify a local pronoun/aux false start before a discourse restart."""

    if index + 2 >= len(source_tokens) or index + 2 >= len(contexts):
        return False
    container = contexts[index][2]
    if contexts[index + 1][2] != container or contexts[index + 2][2] != container:
        return False
    pause = _following_pause(index, contexts)
    if pause is None or pause >= 0.35:
        return False
    current = _normalized_core(source_tokens[index]).replace("’", "'")
    discourse_restart = _normalized_core(source_tokens[index + 1]).replace("’", "'")
    restarted_head = _normalized_core(source_tokens[index + 2]).replace("’", "'")
    return bool(
        current in _ABANDONED_FRAGMENT_HEADS
        and discourse_restart in _DISCOURSE_COMMA_WORDS
        and restarted_head in _ABANDONED_FRAGMENT_HEADS
    )


def _question_clause_supported(
    source_tokens: Sequence[str],
    *,
    index: int,
    clause_length: int,
    contexts: Sequence[CleanupTokenContext],
) -> bool:
    return (
        _question_clause_start(
            source_tokens,
            index=index,
            clause_length=clause_length,
            contexts=contexts,
        )
        is not None
    )


def _question_clause_start(
    source_tokens: Sequence[str],
    *,
    index: int,
    clause_length: int,
    contexts: Sequence[CleanupTokenContext],
) -> int | None:
    """Return the exact leading-word index that licenses a question boundary."""

    clause_start = max(0, index - clause_length + 1)
    context_start = index
    while context_start > clause_start and contexts[context_start - 1][2] == contexts[index][2]:
        context_start -= 1
    # A short, same-speaker turn transition after a strict syntactic
    # continuation does not reset the linguistic clause. Carry question scope
    # across that diarization boundary so the mark can move to the true end.
    while context_start > clause_start and _structural_incomplete_continuation(
        context_start - 1,
        source_tokens,
        contexts,
    ):
        prior_container = contexts[context_start - 1][2]
        context_start -= 1
        while context_start > clause_start and contexts[context_start - 1][2] == prior_container:
            context_start -= 1
    clause_start = max(clause_start, context_start)
    leading_indexes = list(range(clause_start, min(index + 1, clause_start + 8)))
    leading = [
        _normalized_core(source_tokens[position]).replace("’", "'") for position in leading_indexes
    ]
    while len(leading) > 1 and leading[0] in _DISCOURSE_COMMA_WORDS:
        leading.pop(0)
        leading_indexes.pop(0)
    if not leading:
        return None
    first = leading[0]
    base = first.split("'", 1)[0]
    if first not in _QUESTION_LEADS and base not in _QUESTION_LEADS:
        return None
    if "'" in first:
        # Contracted WH/auxiliary forms can lead either an interrogative or a
        # declarative pseudo-cleft/discourse construction of unbounded length.
        # Syntax alone therefore abstains; source or backend punctuation and
        # boundary/prosody evidence may still support a question.
        return None
    if base in _WH_QUESTION_LEADS and len(leading) > 1:
        return leading_indexes[0] if leading[1] not in _DECLARATIVE_WH_PRONOUNS else None
    if base not in _WH_QUESTION_LEADS:
        if len(leading) < 2 or leading[1] not in (
            _QUESTION_SUBJECT_PRONOUNS | _QUESTION_SUBJECT_DETERMINERS
        ):
            return None
        if leading[1] in _QUESTION_SUBJECT_DETERMINERS and len(leading) < 3:
            return None
    return leading_indexes[0]


def _same_speaker_short_continuation(
    index: int,
    contexts: Sequence[CleanupTokenContext],
    turn_end_indexes: set[int],
    *,
    threshold_seconds: float = 2.0,
) -> bool:
    if index not in turn_end_indexes or index + 1 >= len(contexts):
        return False
    _, _, current_container, current_speaker = contexts[index]
    _, _, next_container, next_speaker = contexts[index + 1]
    if (
        current_container == next_container
        or current_speaker is None
        or next_speaker is None
        or current_speaker != next_speaker
    ):
        return False
    pause = _following_pause(index, contexts)
    return pause is not None and pause < threshold_seconds


def _speaker_changes_after(
    index: int,
    contexts: Sequence[CleanupTokenContext],
    turn_end_indexes: set[int],
) -> bool:
    if index not in turn_end_indexes or index + 1 >= len(contexts):
        return False
    _, _, current_container, current_speaker = contexts[index]
    _, _, next_container, next_speaker = contexts[index + 1]
    return bool(
        current_container != next_container
        and current_speaker is not None
        and next_speaker is not None
        and current_speaker != next_speaker
    )


def _question_boundary_supported(
    *,
    index: int,
    source_tokens: Sequence[str],
    contexts: Sequence[CleanupTokenContext],
    turn_end_indexes: set[int],
    clause_length: int,
) -> bool:
    if clause_length < 2:
        return False
    question_scope_start = _question_clause_start(
        source_tokens,
        index=index,
        clause_length=clause_length,
        contexts=contexts,
    )
    clause_supported = question_scope_start is not None
    contracted_response_candidate = _contracted_question_response_candidate(
        source_tokens,
        index=index,
        clause_length=clause_length,
        contexts=contexts,
    )
    if not clause_supported and not contracted_response_candidate:
        return False
    if _structural_incomplete_continuation(index, source_tokens, contexts):
        return False
    if _incomplete_contraction_blocks_added_question(
        index,
        source_tokens,
        question_scope_start_index=question_scope_start,
    ):
        return False
    if index == len(source_tokens) - 1 and _is_explicitly_incomplete_trailing_fragment(
        _normalized_core(source_tokens[index])
    ):
        return False
    if index == len(source_tokens) - 1:
        return True
    pause = _following_pause(index, contexts)
    if pause is not None and pause >= 0.75:
        return True
    if _speaker_changes_after(index, contexts, turn_end_indexes):
        return True
    # A diarization turn is not a sentence, but a syntactically interrogative
    # clause ending at a turn is positive boundary evidence even when the same
    # speaker resumes shortly afterward.
    return index in turn_end_indexes


def _contracted_question_response_candidate(
    source_tokens: Sequence[str],
    *,
    index: int,
    clause_length: int,
    contexts: Sequence[CleanupTokenContext],
) -> bool:
    return (
        _contracted_question_scope_start(
            source_tokens,
            index=index,
            clause_length=clause_length,
            contexts=contexts,
        )
        is not None
    )


def _contracted_question_scope_start(
    source_tokens: Sequence[str],
    *,
    index: int,
    clause_length: int,
    contexts: Sequence[CleanupTokenContext],
) -> int | None:
    clause_start = max(0, index - clause_length + 1)
    while clause_start < index and contexts[clause_start][2] != contexts[index][2]:
        clause_start += 1
    if clause_start > index:
        return None
    first = _normalized_core(source_tokens[clause_start]).replace("’", "'")
    if "'" not in first:
        return None
    if first.split("'", 1)[0] not in _CONTRACTED_QUESTION_LEADS_WITH_RESPONSE_EVIDENCE:
        return None
    return clause_start


def _repair_source_punctuation(
    *,
    index: int,
    punctuation: str,
    source_tokens: Sequence[str],
    contexts: Sequence[CleanupTokenContext],
    turn_end_indexes: set[int],
    clause_length: int,
) -> str:
    if punctuation != "?" and _question_boundary_supported(
        index=index,
        source_tokens=source_tokens,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
        clause_length=clause_length,
    ):
        return "?"
    current = _normalized_core(source_tokens[index]).replace("’", "'")
    if (
        punctuation in _STRONG_SENTENCE_PUNCTUATION
        and current in _INCOMPLETE_CONTRACTIONS
        and index + 1 < len(contexts)
    ):
        same_container = contexts[index][2] == contexts[index + 1][2]
        pause = _following_pause(index, contexts)
        if _same_speaker_short_continuation(
            index,
            contexts,
            turn_end_indexes,
        ) or (same_container and (pause is None or pause < 0.75)):
            return ""
    return punctuation


def _model_punctuation_is_supported(
    *,
    index: int,
    punctuation: str,
    source_tokens: Sequence[str],
    contexts: Sequence[CleanupTokenContext],
    turn_end_indexes: set[int],
    clause_length: int,
) -> bool:
    if punctuation not in {",", ".", "?", "!", ";", ":", "…"}:
        return False
    current = _normalized_core(source_tokens[index])
    next_core = (
        _normalized_core(source_tokens[index + 1]) if index + 1 < len(source_tokens) else None
    )
    if next_core == current:
        return False
    pause = _following_pause(index, contexts)
    if punctuation == ",":
        if _question_boundary_supported(
            index=index,
            source_tokens=source_tokens,
            contexts=contexts,
            turn_end_indexes=turn_end_indexes,
            clause_length=clause_length,
        ):
            return False
        if _is_incomplete_sentence_end(current) and current not in (_DISCOURSE_COMMA_WORDS):
            return False
        return bool(
            current in _DISCOURSE_COMMA_WORDS
            or next_core in _CLAUSE_CONNECTORS
            or (not _is_incomplete_sentence_end(current) and pause is not None and pause >= 0.30)
        )
    if punctuation in {";", ":"}:
        return bool(
            not _is_incomplete_sentence_end(current)
            and pause is not None
            and pause >= 0.35
            and clause_length >= 4
        )
    if _is_incomplete_sentence_end(current) or clause_length < 3:
        return False
    question_boundary = _question_boundary_supported(
        index=index,
        source_tokens=source_tokens,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
        clause_length=clause_length,
    )
    if question_boundary:
        return punctuation == "?"
    if punctuation == "?":
        return False
    if _same_speaker_short_continuation(index, contexts, turn_end_indexes):
        return False
    boundary_supported = index == len(source_tokens) - 1 or (pause is not None and pause >= 0.45)
    if not boundary_supported:
        return False
    return True


def _deterministic_boundary_mark(
    *,
    index: int,
    source_tokens: Sequence[str],
    contexts: Sequence[CleanupTokenContext],
    turn_end_indexes: set[int],
    clause_length: int,
) -> str | None:
    current = _normalized_core(source_tokens[index])
    if index not in turn_end_indexes and clause_length == 1 and current in _DISCOURSE_COMMA_WORDS:
        return ","
    if _is_incomplete_sentence_end(current) or clause_length < 3:
        return None
    if index + 1 < len(source_tokens) and _normalized_core(source_tokens[index + 1]) == current:
        return None
    question = _question_boundary_supported(
        index=index,
        source_tokens=source_tokens,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
        clause_length=clause_length,
    )
    if index == len(source_tokens) - 1:
        return "?" if question else "."
    if question:
        return "?"
    if _same_speaker_short_continuation(index, contexts, turn_end_indexes):
        return None
    pause = _following_pause(index, contexts)
    if pause is not None and pause >= 0.75 and clause_length >= 4:
        return ","
    return None


def _suspicious_added_sentence_breaks(
    source_tokens: Sequence[str],
    repaired_tokens: Sequence[str],
    *,
    contexts: Sequence[CleanupTokenContext],
    turn_end_indexes: set[int],
    decisions: Sequence[PresentationDecision] | None = None,
) -> int:
    if decisions is not None and len(decisions) != len(source_tokens):
        raise ValueError("presentation decision coverage does not match source tokens")
    suspicious = 0
    clause_length = 0
    for index, (source, repaired) in enumerate(zip(source_tokens, repaired_tokens, strict=True)):
        clause_length += 1
        source_punctuation = _split_presentation(source)[2]
        repaired_punctuation = _split_presentation(repaired)[2]
        added_strong = not any(
            mark in _STRONG_SENTENCE_PUNCTUATION for mark in source_punctuation
        ) and any(mark in _STRONG_SENTENCE_PUNCTUATION for mark in repaired_punctuation)
        typed_question = bool(
            decisions is not None
            and decisions[index].trailing_punctuation is TrailingPunctuation.QUESTION
            and decisions[index].boundary_support.typed_question
        )
        if added_strong and (
            _structural_incomplete_continuation(
                index,
                source_tokens,
                contexts,
            )
            or (
                "?" in repaired_punctuation
                and not typed_question
                and not _question_boundary_supported(
                    index=index,
                    source_tokens=source_tokens,
                    contexts=contexts,
                    turn_end_indexes=turn_end_indexes,
                    clause_length=clause_length,
                )
            )
        ):
            suspicious += 1
        if any(mark in _STRONG_SENTENCE_PUNCTUATION for mark in repaired_punctuation):
            clause_length = 0
    return suspicious


def _clause_length_at(tokens: Sequence[str], index: int) -> int:
    length = 1
    cursor = index - 1
    while cursor >= 0:
        punctuation = _split_presentation(tokens[cursor])[2]
        if any(mark in _STRONG_SENTENCE_PUNCTUATION for mark in punctuation):
            break
        length += 1
        cursor -= 1
    return length


def _sentence_span_receipt_sha256(
    spans: Sequence[tuple[int, int, int, bool, bool]],
) -> str:
    """Bind private sentence-span placement without publishing transcript text."""

    return hashlib.sha256(
        json.dumps(
            [
                {
                    "start_word_index": start,
                    "end_word_index": end,
                    "word_count": length,
                    "over_soft_limit": over_soft,
                    "over_hard_limit": over_hard,
                }
                for start, end, length, over_soft, over_hard in spans
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _surface_quality_diagnostics(
    transcript: Transcript,
    source_tokens: Sequence[str],
    repaired_tokens: Sequence[str],
    *,
    contexts: Sequence[CleanupTokenContext],
    turn_end_indexes: set[int],
) -> dict[str, object]:
    same_speaker_short_breaks = 0
    contraction_punctuation_violations = 0
    incomplete_abandoned_fragment_stop_violations = 0
    added_short_strong_stops = 0
    added_short_strong_stop_bursts = 0
    max_added_short_strong_stop_streak = 0
    short_stop_streak = 0
    prior_short_stop_owner: tuple[str, object] | None = None
    added_strong_stop_bursts = 0
    added_strong_stop_cluster = 0
    max_added_strong_stop_cluster = 0
    prior_added_strong_stop_index: int | None = None
    prior_added_strong_stop_owner: tuple[str, object] | None = None
    sentence_length = 0
    sentence_start_index = 0
    sentence_owners: set[tuple[str, object]] = set()
    long_sentence_span_records: list[tuple[int, int, int, bool, bool]] = []
    long_sentence_spans = 0
    overlong_sentence_spans = 0
    max_sentence_span_words = 0
    for index, (source, repaired) in enumerate(zip(source_tokens, repaired_tokens, strict=True)):
        sentence_length += 1
        speaker = contexts[index][3]
        sentence_owners.add(
            ("speaker", speaker) if speaker is not None else ("container", contexts[index][2])
        )
        source_punctuation = _split_presentation(source)[2]
        repaired_punctuation = _split_presentation(repaired)[2]
        has_strong = any(mark in _STRONG_SENTENCE_PUNCTUATION for mark in repaired_punctuation)
        added_strong = (
            not any(mark in _STRONG_SENTENCE_PUNCTUATION for mark in source_punctuation)
            and has_strong
        )
        if (
            added_strong
            and _same_speaker_short_continuation(
                index,
                contexts,
                turn_end_indexes,
            )
            and not (
                "?" in repaired_punctuation
                and _question_boundary_supported(
                    index=index,
                    source_tokens=source_tokens,
                    contexts=contexts,
                    turn_end_indexes=turn_end_indexes,
                    clause_length=_clause_length_at(repaired_tokens, index),
                )
            )
        ):
            same_speaker_short_breaks += 1
        current = _normalized_core(repaired).replace("’", "'")
        source_authored_complete_negative = bool(
            current.endswith("n't")
            and source_punctuation == repaired_punctuation
            and any(mark in _STRONG_SENTENCE_PUNCTUATION for mark in source_punctuation)
        )
        contraction_violation = bool(
            current in _INCOMPLETE_CONTRACTIONS
            and has_strong
            and not source_authored_complete_negative
            and (sentence_length == 1 or (added_strong and "?" in repaired_punctuation))
        )
        if current in _STRICT_AUXILIARY_CONTRACTIONS and index + 1 < len(repaired_tokens):
            next_core = _normalized_core(repaired_tokens[index + 1])
            repeats_soon = current in {
                _normalized_core(token).replace("’", "'")
                for token in repaired_tokens[index + 1 : index + 4]
            }
            pause = _following_pause(index, contexts)
            same_container = contexts[index][2] == contexts[index + 1][2]
            has_weak = any(mark in {",", ";", ":"} for mark in repaired_punctuation)
            close_continuation = same_container and pause is not None and pause < 0.35
            if (has_strong and close_continuation and not repeats_soon) or (
                has_weak
                and close_continuation
                and next_core not in _DISCOURSE_COMMA_WORDS
                and not repeats_soon
            ):
                contraction_violation = True
        if contraction_violation:
            contraction_punctuation_violations += 1
        if added_strong and _isolated_abandoned_fragment_boundary(
            index,
            source_tokens,
            contexts,
        ):
            incomplete_abandoned_fragment_stop_violations += 1
        if has_strong:
            max_sentence_span_words = max(max_sentence_span_words, sentence_length)
            over_soft_limit = sentence_length > _MAX_STRONG_SENTENCE_SPAN_WORDS
            over_hard_limit = sentence_length > _HARD_MAX_STRONG_SENTENCE_SPAN_WORDS
            if over_soft_limit:
                long_sentence_spans += 1
                long_sentence_span_records.append(
                    (
                        sentence_start_index,
                        index,
                        sentence_length,
                        True,
                        over_hard_limit,
                    )
                )
            if over_hard_limit:
                overlong_sentence_spans += 1
            short_added = bool(added_strong and sentence_length <= 2 and len(sentence_owners) == 1)
            if short_added:
                added_short_strong_stops += 1
                owner = next(iter(sentence_owners))
                short_stop_streak = short_stop_streak + 1 if owner == prior_short_stop_owner else 1
                prior_short_stop_owner = owner
                max_added_short_strong_stop_streak = max(
                    max_added_short_strong_stop_streak,
                    short_stop_streak,
                )
                if short_stop_streak == _MAX_CONSECUTIVE_ADDED_SHORT_STOPS + 1:
                    added_short_strong_stop_bursts += 1
            else:
                short_stop_streak = 0
                prior_short_stop_owner = None
            if added_strong and len(sentence_owners) == 1:
                owner = next(iter(sentence_owners))
                clustered = bool(
                    owner == prior_added_strong_stop_owner
                    and prior_added_strong_stop_index is not None
                    and index - prior_added_strong_stop_index <= _MAX_ADDED_STOP_CLUSTER_GAP_WORDS
                )
                added_strong_stop_cluster = added_strong_stop_cluster + 1 if clustered else 1
                prior_added_strong_stop_index = index
                prior_added_strong_stop_owner = owner
                max_added_strong_stop_cluster = max(
                    max_added_strong_stop_cluster,
                    added_strong_stop_cluster,
                )
                if added_strong_stop_cluster == _MAX_CLUSTERED_ADDED_STOPS + 1:
                    added_strong_stop_bursts += 1
            else:
                added_strong_stop_cluster = 0
                prior_added_strong_stop_index = None
                prior_added_strong_stop_owner = None
            sentence_length = 0
            sentence_start_index = index + 1
            sentence_owners.clear()

    if sentence_length:
        max_sentence_span_words = max(max_sentence_span_words, sentence_length)
        over_soft_limit = sentence_length > _MAX_STRONG_SENTENCE_SPAN_WORDS
        over_hard_limit = sentence_length > _HARD_MAX_STRONG_SENTENCE_SPAN_WORDS
        if over_soft_limit:
            long_sentence_spans += 1
            long_sentence_span_records.append(
                (
                    sentence_start_index,
                    len(repaired_tokens) - 1,
                    sentence_length,
                    True,
                    over_hard_limit,
                )
            )
        if over_hard_limit:
            overlong_sentence_spans += 1
    incomplete_trailing_fragments = int(
        bool(source_tokens)
        and bool(repaired_tokens)
        and _is_explicitly_incomplete_trailing_fragment(_normalized_core(source_tokens[-1]))
        and not any(
            mark in _STRONG_SENTENCE_PUNCTUATION
            for mark in _split_presentation(repaired_tokens[-1])[2]
        )
    )

    long_low_punctuation_turns = 0
    long_unpunctuated_runs = 0
    max_unpunctuated_run_words = 0
    decoder_words = _build_presentation_decoder_words(transcript, source_tokens)
    typed_question_endpoints = _typed_question_scope_endpoints(decoder_words)
    typed_question_scopes_without_mark = sum(
        (
            "?" not in _split_presentation(repaired_tokens[endpoint])[2]
            or sum(
                "?" in _split_presentation(repaired_tokens[index])[2]
                for index in range(scope_start, endpoint + 1)
            )
            != 1
        )
        for scope_start, endpoint in typed_question_endpoints.items()
    )
    cursor = 0
    containers: Sequence[SpeakerTurn | ASRSegment] = (
        transcript.speaker_turns if transcript.speaker_turns else transcript.segments
    )
    for container in containers:
        source_length = len(
            _speaker_turn_source_text(container).split()
            if isinstance(container, SpeakerTurn)
            else _segment_source_text(container).split()
        )
        turn_tokens = repaired_tokens[cursor : cursor + source_length]
        cursor += source_length
        if source_length >= 35:
            punctuation_tokens = sum(bool(_split_presentation(token)[2]) for token in turn_tokens)
            if punctuation_tokens / source_length <= 0.06:
                long_low_punctuation_turns += 1
        run_length = 0
        for token in turn_tokens:
            run_length += 1
            max_unpunctuated_run_words = max(max_unpunctuated_run_words, run_length)
            if _split_presentation(token)[2]:
                if run_length >= 35:
                    long_unpunctuated_runs += 1
                run_length = 0
        if run_length >= 35:
            long_unpunctuated_runs += 1
    warnings: list[str] = []
    if long_low_punctuation_turns:
        warnings.append("long_low_punctuation_turns_require_editorial_review")
    if typed_question_scopes_without_mark:
        warnings.append("typed_question_scopes_without_question_marks_require_review")
    if long_unpunctuated_runs:
        warnings.append("long_unpunctuated_runs_require_editorial_review")
    if added_short_strong_stop_bursts:
        warnings.append("added_short_strong_stop_bursts_require_editorial_review")
    if added_strong_stop_bursts:
        warnings.append("added_strong_stop_bursts_require_editorial_review")
    if long_sentence_spans:
        warnings.append("long_sentence_spans_require_editorial_review")
    if overlong_sentence_spans:
        warnings.append("unresolved_overlong_sentence_spans_need_editorial_review")
    if incomplete_trailing_fragments:
        warnings.append("incomplete_trailing_fragment_preserved")
    if incomplete_abandoned_fragment_stop_violations:
        warnings.append("incomplete_abandoned_fragment_stops_require_review")
    return {
        "same_speaker_short_break_addition_count": same_speaker_short_breaks,
        "contraction_punctuation_violation_count": contraction_punctuation_violations,
        "incomplete_abandoned_fragment_stop_violation_count": (
            incomplete_abandoned_fragment_stop_violations
        ),
        "added_short_strong_stop_count": added_short_strong_stops,
        "added_short_strong_stop_burst_count": added_short_strong_stop_bursts,
        "max_added_short_strong_stop_streak": max_added_short_strong_stop_streak,
        "added_strong_stop_burst_count": added_strong_stop_bursts,
        "max_added_strong_stop_cluster": max_added_strong_stop_cluster,
        "long_low_punctuation_turn_count": long_low_punctuation_turns,
        "long_unpunctuated_run_count": long_unpunctuated_runs,
        "max_unpunctuated_run_words": max_unpunctuated_run_words,
        "long_sentence_span_count": long_sentence_spans,
        "overlong_sentence_span_count": overlong_sentence_spans,
        "unresolved_overlong_sentence_span_count": overlong_sentence_spans,
        "long_sentence_span_receipt_sha256": _sentence_span_receipt_sha256(
            long_sentence_span_records
        ),
        "overlong_sentence_span_receipt_sha256": _sentence_span_receipt_sha256(
            tuple(record for record in long_sentence_span_records if record[4])
        ),
        "max_sentence_span_words": max_sentence_span_words,
        "incomplete_trailing_fragment_count": incomplete_trailing_fragments,
        "question_leading_turn_without_question_mark_count": (typed_question_scopes_without_mark),
        "typed_question_scope_without_question_mark_count": (typed_question_scopes_without_mark),
        "quality_warnings": warnings,
    }


def _apply_proper_noun_list_punctuation(
    tokens: list[str],
    proper_nouns: Sequence[str],
) -> tuple[list[str], int]:
    """Punctuate compact configured-name lists without changing any word."""

    single_word_names = [name for name in proper_nouns if len(name.split()) == 1]
    if len(single_word_names) < 3:
        return list(tokens), 0
    positions = [
        index
        for index, token in enumerate(tokens)
        if any(
            _proper_noun_core_matches(_split_presentation(token)[1], name)
            for name in single_word_names
        )
    ]
    groups: list[list[int]] = []
    current: list[int] = []
    for position in positions:
        if not current:
            current = [position]
            continue
        intervening = [_normalized_core(token) for token in tokens[current[-1] + 1 : position]]
        if len(intervening) <= 2 and all(
            word in _PROPER_NOUN_LIST_CONNECTORS for word in intervening
        ):
            current.append(position)
            continue
        if len(current) >= 3:
            groups.append(current)
        current = [position]
    if len(current) >= 3:
        groups.append(current)

    repaired = list(tokens)
    change_count = 0
    for group in groups:
        for position in group[:-1]:
            if not _split_presentation(repaired[position])[2]:
                repaired[position] = _replace_trailing_punctuation(repaired[position], ",")
                change_count += 1
        last = group[-1]
        if (
            not _split_presentation(repaired[last])[2]
            and last + 2 < len(repaired)
            and _normalized_core(repaired[last + 1]) == "i"
            and _normalized_core(repaired[last + 2]) in _FIRST_PERSON_RESTARTS
        ):
            repaired[last] = _replace_trailing_punctuation(repaired[last], ".")
            change_count += 1
    return repaired, change_count


def _apply_safe_capitalization(
    transcript: Transcript,
    tokens: list[str],
) -> list[str]:
    del transcript
    repaired = list(tokens)
    previous_ends_sentence = True
    for index, token in enumerate(repaired):
        normalized = unicodedata.normalize("NFKC", token).casefold()
        is_i_pronoun = _lexical_signature(token) == "i" or normalized.startswith(("i'", "i’"))
        if previous_ends_sentence or is_i_pronoun:
            repaired[index] = _capitalize_token(token)
        previous_ends_sentence = _ends_sentence(repaired[index])
    return repaired


def _apply_corpus_consistent_casing(
    transcript: Transcript,
    tokens: list[str],
    proper_nouns: Sequence[str],
) -> tuple[list[str], int]:
    """Lower anomalous internal title casing when the same word proves its norm."""

    lower_counts: dict[str, int] = {}
    title_counts: dict[str, int] = {}
    for token in cleanup_source_text(transcript).split():
        core = _split_presentation(token)[1]
        folded = unicodedata.normalize("NFKC", core).casefold()
        if core == core.lower():
            lower_counts[folded] = lower_counts.get(folded, 0) + 1
        elif len(core) > 1 and core[:1].isupper() and core[1:] == core[1:].lower():
            title_counts[folded] = title_counts.get(folded, 0) + 1

    proper_cores = {
        unicodedata.normalize("NFKC", part).casefold()
        for name in proper_nouns
        for part in name.split()
    }
    repaired = list(tokens)
    change_count = 0
    for index, token in enumerate(repaired):
        prefix, core, punctuation, closers = _split_presentation(token)
        folded = unicodedata.normalize("NFKC", core).casefold()
        sentence_start = index == 0 or _ends_sentence(repaired[index - 1])
        if (
            sentence_start
            or folded == "i"
            or folded.startswith(("i'", "i’"))
            or folded in proper_cores
            or folded in _STANDARD_ENGLISH_CASING
            or len(core) <= 1
            or not core[:1].isupper()
            or core[1:] != core[1:].lower()
            or lower_counts.get(folded, 0) < 1
            or title_counts.get(folded, 0) < 1
        ):
            continue
        candidate = f"{prefix}{core[:1].lower()}{core[1:]}{punctuation}{closers}"
        if not _same_lexical_word(candidate, token):
            raise ValueError("corpus_casing_changed_lexical_word")
        repaired[index] = candidate
        change_count += 1
    return repaired, change_count


def _apply_standard_english_casing(
    transcript: Transcript,
    tokens: list[str],
) -> tuple[list[str], int]:
    language = (transcript.language or "").casefold().replace("_", "-").split("-", 1)[0]
    if language not in {"", "en"}:
        return list(tokens), 0
    repaired = list(tokens)
    change_count = 0
    for index, token in enumerate(repaired):
        prefix, core, punctuation, closers = _split_presentation(token)
        folded = unicodedata.normalize("NFKC", core).casefold()
        desired = _STANDARD_ENGLISH_CASING.get(folded)
        if desired is None or core == desired:
            continue
        candidate = f"{prefix}{desired}{punctuation}{closers}"
        if not _same_lexical_word(candidate, token):
            raise ValueError("standard_english_casing_changed_lexical_word")
        repaired[index] = candidate
        change_count += 1
    return repaired, change_count


def _apply_proper_noun_casing(
    tokens: list[str],
    proper_nouns: Sequence[str],
) -> list[str]:
    """Apply exact configured casing without changing any lexical characters."""

    repaired = list(tokens)
    for proper_noun in proper_nouns:
        desired_parts = proper_noun.split()
        if not desired_parts:
            continue
        width = len(desired_parts)
        for start in range(0, len(repaired) - width + 1):
            parsed = [_split_presentation(repaired[start + offset]) for offset in range(width)]
            if any(
                not _proper_noun_core_matches(core, desired)
                for (_, core, _, _), desired in zip(parsed, desired_parts, strict=True)
            ):
                continue
            for offset, (presentation, desired) in enumerate(
                zip(parsed, desired_parts, strict=True)
            ):
                prefix, core, punctuation, closers = presentation
                possessive = core[len(desired) :] if len(core) > len(desired) else ""
                candidate = f"{prefix}{desired}{possessive}{punctuation}{closers}"
                source = repaired[start + offset]
                if not _same_lexical_word(candidate, source):
                    raise ValueError("proper_noun_casing_changed_lexical_word")
                repaired[start + offset] = candidate
    return repaired


def _proper_noun_core_matches(core: str, desired: str) -> bool:
    normalized_core = unicodedata.normalize("NFKC", core).casefold()
    normalized_desired = unicodedata.normalize("NFKC", desired).casefold()
    return normalized_core in {
        normalized_desired,
        f"{normalized_desired}'s",
        f"{normalized_desired}’s",
    }


def _split_presentation(token: str) -> tuple[str, str, str, str]:
    prefix_end = 0
    while prefix_end < len(token) and token[prefix_end] in _LEADING_PRESENTATION:
        prefix_end += 1
    closer_start = len(token)
    while closer_start > prefix_end and token[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    punctuation_start = closer_start
    while punctuation_start > prefix_end and token[punctuation_start - 1] in _TRAILING_PUNCTUATION:
        punctuation_start -= 1
    return (
        token[:prefix_end],
        token[prefix_end:punctuation_start],
        token[punctuation_start:closer_start],
        token[closer_start:],
    )


def _ends_sentence(token: str) -> bool:
    return token.rstrip(_TRAILING_CLOSERS).endswith((".", "?", "!", "…"))
