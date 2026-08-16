# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Typed, lexical-conserving transcript surface planning.

This module deliberately stops at presentation decisions.  Scorers may rank
punctuation, capitalization, and sentence/paragraph boundaries for existing
words, but they cannot propose replacement text.  The decoder validates every
proposal against the bound lexical sequence before performing deterministic
whole-sequence optimization.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

_TRAILING_PRESENTATION = ".,?!;:…"
_TRAILING_CLOSERS = "\"'”’)]}）】」』》"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_EPSILON = 1e-9
_SCORE_EPSILON = 1e-12
DECODER_VERSION = "auralign.presentation.v9"
TIE_BREAK_POLICY = "lexicographic-source-preserving-v1"
MAX_PRESENTATION_WORDS = 50_000
MAX_PRESENTATION_SCORER_OUTPUTS = 16
MAX_PRESENTATION_PROPOSALS = 1_000_000
MAX_PRESENTATION_PLAN_BYTES = 64 * 1024 * 1024
MAX_SENTENCE_LENGTH_STATE = 96
SOFT_MAX_SENTENCE_WORDS = 64
MAX_SENTENCE_WORDS = MAX_SENTENCE_LENGTH_STATE
BOUNDARY_CORROBORATION_THRESHOLD = 0.70
MAX_CONSECUTIVE_ADDED_SHORT_STOPS = 2
MAX_CLUSTERED_ADDED_STOPS = 3
MAX_ADDED_STOP_CLUSTER_GAP_WORDS = 9


class TrailingPunctuation(StrEnum):
    """The complete set of punctuation surfaces a proposal may select."""

    NONE = ""
    COMMA = ","
    PERIOD = "."
    QUESTION = "?"
    EXCLAMATION = "!"
    SEMICOLON = ";"
    COLON = ":"
    ELLIPSIS = "…"


class EvidenceRole(StrEnum):
    """The only evidence dimensions a scorer proposal may influence."""

    EXACT_MARK = "exact_mark"
    BOUNDARY_ONLY = "boundary_only"
    CASE_ONLY = "case_only"
    STRUCTURAL_VETO = "structural_veto"


_EVIDENCE_ROLE_ORDER = tuple(EvidenceRole)
_EVIDENCE_ROLE_RANK = {value: index for index, value in enumerate(_EVIDENCE_ROLE_ORDER)}


_PUNCTUATION_ORDER = tuple(TrailingPunctuation)
_PUNCTUATION_RANK = {value: index for index, value in enumerate(_PUNCTUATION_ORDER)}
_STRONG_STOPS = frozenset(
    {
        TrailingPunctuation.PERIOD,
        TrailingPunctuation.QUESTION,
        TrailingPunctuation.EXCLAMATION,
        TrailingPunctuation.ELLIPSIS,
    }
)


def lexical_signature(text: str) -> str:
    """Return the case- and presentation-insensitive identity of one word."""

    if not isinstance(text, str):
        raise ValueError("lexical text must be a string")
    normalized = unicodedata.normalize("NFKC", text.strip()).casefold()
    if not normalized:
        raise ValueError("lexical text must not be empty")
    if not any(character.isalnum() for character in normalized):
        raise ValueError("lexical text must contain a lexical word")
    closer_start = len(normalized)
    while closer_start > 0 and normalized[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    body = normalized[:closer_start]
    closers = normalized[closer_start:]
    while body and body[-1] in _TRAILING_PRESENTATION:
        body = body[:-1]
    signature = f"{body}{closers}"
    if not signature:
        raise ValueError("lexical text must contain a lexical word")
    return signature


def lexical_text_digest(text: str) -> str:
    """Bind a proposal to the exact NFC source surface, not casefold alone."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("lexical text must be a non-empty string")
    normalized = unicodedata.normalize("NFC", text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DecoderWord:
    """Immutable evidence for one existing lexical word.

    ``lexical_text`` is the exact source surface. ``lexical_signature`` binds
    all scorer output to that surface while permitting only case and trailing
    presentation changes.
    """

    word_index: int
    lexical_text: str
    lexical_signature: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    pause_after_seconds: float | None = None
    speaker_id: str | None = None
    container_index: int | None = None
    begins_turn: bool = False
    ends_turn: bool = False
    incomplete_continuation: bool = False
    incomplete_contraction: bool = False
    incomplete_trailing_fragment: bool = False
    incomplete_abandoned_fragment: bool = False
    unsupported_same_speaker_short_continuation: bool = False
    contraction_repetition_exception: bool = False
    contraction_correction_exception: bool = False
    contraction_question_tail_exception: bool = False
    question_evidence: float = 0.0
    question_scope_start_index: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.word_index, bool)
            or not isinstance(self.word_index, int)
            or self.word_index < 0
        ):
            raise ValueError("word_index must be a non-negative integer")
        if not isinstance(self.lexical_signature, str):
            raise ValueError("lexical_signature must be a string")
        expected_signature = lexical_signature(self.lexical_text)
        if self.lexical_signature != expected_signature:
            raise ValueError("lexical_signature does not bind lexical_text")
        _validate_optional_timing(self.start_seconds, "start_seconds")
        _validate_optional_timing(self.end_seconds, "end_seconds")
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("word timing must provide both start_seconds and end_seconds")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("word timing end precedes start")
        _validate_optional_timing(self.pause_after_seconds, "pause_after_seconds")
        _validate_probability(self.question_evidence, "question_evidence")
        if self.question_scope_start_index is not None and (
            isinstance(self.question_scope_start_index, bool)
            or not isinstance(self.question_scope_start_index, int)
            or self.question_scope_start_index < 0
            or self.question_scope_start_index > self.word_index
        ):
            raise ValueError("question scope must start at or before the word")
        if self.question_evidence > 0.0 and self.question_scope_start_index is None:
            raise ValueError("positive question evidence requires an exact source scope")
        if self.speaker_id is not None and (
            not isinstance(self.speaker_id, str) or not self.speaker_id.strip()
        ):
            raise ValueError("speaker_id must be non-empty when supplied")
        if self.container_index is not None and (
            isinstance(self.container_index, bool)
            or not isinstance(self.container_index, int)
            or self.container_index < 0
        ):
            raise ValueError("container_index must be a non-negative integer when supplied")

    @classmethod
    def from_text(
        cls,
        word_index: int,
        lexical_text: str,
        *,
        start_seconds: float | None = None,
        end_seconds: float | None = None,
        pause_after_seconds: float | None = None,
        speaker_id: str | None = None,
        container_index: int | None = None,
        begins_turn: bool = False,
        ends_turn: bool = False,
        incomplete_continuation: bool = False,
        incomplete_contraction: bool = False,
        incomplete_trailing_fragment: bool = False,
        incomplete_abandoned_fragment: bool = False,
        unsupported_same_speaker_short_continuation: bool = False,
        contraction_repetition_exception: bool = False,
        contraction_correction_exception: bool = False,
        contraction_question_tail_exception: bool = False,
        question_evidence: float = 0.0,
        question_scope_start_index: int | None = None,
    ) -> DecoderWord:
        """Construct a word while deriving its immutable lexical binding."""

        return cls(
            word_index=word_index,
            lexical_text=lexical_text,
            lexical_signature=lexical_signature(lexical_text),
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            pause_after_seconds=pause_after_seconds,
            speaker_id=speaker_id,
            container_index=container_index,
            begins_turn=begins_turn,
            ends_turn=ends_turn,
            incomplete_continuation=incomplete_continuation,
            incomplete_contraction=incomplete_contraction,
            incomplete_trailing_fragment=incomplete_trailing_fragment,
            incomplete_abandoned_fragment=incomplete_abandoned_fragment,
            unsupported_same_speaker_short_continuation=(
                unsupported_same_speaker_short_continuation
            ),
            contraction_repetition_exception=contraction_repetition_exception,
            contraction_correction_exception=contraction_correction_exception,
            contraction_question_tail_exception=contraction_question_tail_exception,
            question_evidence=question_evidence,
            question_scope_start_index=question_scope_start_index,
        )


@dataclass(frozen=True, slots=True)
class ScorerProvenance:
    """Stable identity for one scorer and its exact configuration."""

    scorer_id: str
    scorer_version: str
    configuration_digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scorer_id, str) or not isinstance(self.scorer_version, str):
            raise ValueError("scorer provenance identifiers must be strings")
        _validate_identifier(self.scorer_id, "scorer_id")
        _validate_identifier(self.scorer_version, "scorer_version")
        if self.configuration_digest is not None:
            if not isinstance(self.configuration_digest, str) or not _DIGEST_RE.fullmatch(
                self.configuration_digest
            ):
                raise ValueError("configuration_digest must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class PresentationProposal:
    """One signature-bound presentation candidate for an existing word.

    There is intentionally no candidate-text or replacement-word member.
    """

    word_index: int
    lexical_signature: str
    lexical_text_digest: str
    trailing_punctuation: TrailingPunctuation
    punctuation_probability: float
    capitalization_probability: float
    sentence_break_probability: float
    paragraph_break_probability: float
    evidence_roles: tuple[EvidenceRole, ...] = (
        EvidenceRole.EXACT_MARK,
        EvidenceRole.CASE_ONLY,
    )
    casing_surface: str | None = None
    casing_probability: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.word_index, bool)
            or not isinstance(self.word_index, int)
            or self.word_index < 0
        ):
            raise ValueError("proposal word_index must be a non-negative integer")
        if not isinstance(self.lexical_signature, str) or not self.lexical_signature:
            raise ValueError("proposal lexical_signature must not be empty")
        if not isinstance(self.lexical_text_digest, str) or not _DIGEST_RE.fullmatch(
            self.lexical_text_digest
        ):
            raise ValueError("proposal lexical_text_digest must be a lowercase SHA-256 digest")
        if not isinstance(self.trailing_punctuation, TrailingPunctuation):
            raise ValueError("proposal trailing_punctuation is not allowlisted")
        _validate_probability(self.punctuation_probability, "punctuation_probability")
        _validate_probability(
            self.capitalization_probability,
            "capitalization_probability",
        )
        _validate_probability(
            self.sentence_break_probability,
            "sentence_break_probability",
        )
        _validate_probability(
            self.paragraph_break_probability,
            "paragraph_break_probability",
        )
        if not isinstance(self.evidence_roles, tuple) or not self.evidence_roles:
            raise ValueError("proposal evidence_roles must be a non-empty tuple")
        if any(not isinstance(role, EvidenceRole) for role in self.evidence_roles):
            raise ValueError("proposal evidence_roles contain an invalid role")
        if (
            tuple(sorted(set(self.evidence_roles), key=lambda role: _EVIDENCE_ROLE_RANK[role]))
            != self.evidence_roles
        ):
            raise ValueError("proposal evidence_roles must be unique and canonically ordered")
        if self.casing_surface is not None and EvidenceRole.CASE_ONLY not in self.evidence_roles:
            raise ValueError("casing_surface requires CASE_ONLY evidence")
        if self.casing_surface is not None:
            _validate_casing_surface(
                self.casing_surface,
                expected_signature=self.lexical_signature,
            )
            if self.casing_probability is None:
                raise ValueError("casing_surface requires casing_probability")
        elif self.casing_probability is not None:
            raise ValueError("casing_probability requires casing_surface")
        if self.casing_probability is not None:
            _validate_probability(self.casing_probability, "casing_probability")


@dataclass(frozen=True, slots=True)
class ScorerOutput:
    """One scorer's proposals, or an explicit fail-closed abstention."""

    provenance: ScorerProvenance
    proposals: tuple[PresentationProposal, ...] = ()
    abstention_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, ScorerProvenance):
            raise ValueError("scorer output provenance is invalid")
        if not isinstance(self.proposals, tuple):
            raise ValueError("scorer proposals must be an immutable tuple")
        if self.abstention_reason is None and not self.proposals:
            raise ValueError("scorer output must contain proposals or an abstention")
        if self.abstention_reason is not None:
            if not isinstance(self.abstention_reason, str):
                raise ValueError("abstention_reason must be a string")
            _validate_identifier(self.abstention_reason, "abstention_reason")
            if self.proposals:
                raise ValueError("an abstaining scorer cannot also submit proposals")
        _validate_scorer_proposals(self.proposals)

    @classmethod
    def abstain(
        cls,
        provenance: ScorerProvenance,
        reason: str,
    ) -> ScorerOutput:
        """Create a provenance-preserving abstention."""

        return cls(provenance=provenance, abstention_reason=reason)


@dataclass(frozen=True, slots=True)
class ScorerAbstention:
    """An abstention retained in the final plan's provenance record."""

    provenance: ScorerProvenance
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, ScorerProvenance):
            raise ValueError("abstention provenance is invalid")
        if not isinstance(self.reason, str):
            raise ValueError("abstention reason must be a string")
        _validate_identifier(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class BoundarySupport:
    """Decision-specific evidence that made one strong boundary admissible."""

    source_punctuation: bool = False
    typed_question: bool = False
    timing_pause: bool = False
    exact_mark_provenance: tuple[ScorerProvenance, ...] = ()
    boundary_provenance: tuple[ScorerProvenance, ...] = ()
    structural_veto: bool = False

    def __post_init__(self) -> None:
        for label, values in (
            ("exact-mark", self.exact_mark_provenance),
            ("boundary", self.boundary_provenance),
        ):
            if tuple(sorted(set(values), key=_provenance_key)) != values:
                raise ValueError(f"{label} scorer provenance must be unique and sorted")

    @property
    def scorer_provenance(self) -> tuple[ScorerProvenance, ...]:
        """Return the union retained for receipt/provenance auditing."""

        return tuple(
            sorted(
                set(self.exact_mark_provenance) | set(self.boundary_provenance),
                key=_provenance_key,
            )
        )

    @property
    def supported(self) -> bool:
        independently_corroborated = any(
            exact != boundary
            for exact in self.exact_mark_provenance
            for boundary in self.boundary_provenance
        )
        return bool(
            not self.structural_veto
            and (self.source_punctuation or self.typed_question or independently_corroborated)
        )


@dataclass(frozen=True, slots=True)
class PresentationDecision:
    """The chosen presentation for one bound lexical word."""

    word_index: int
    lexical_signature: str
    trailing_punctuation: TrailingPunctuation
    capitalize: bool
    sentence_break: bool
    paragraph_break: bool
    confidence: float | None
    local_score_margin: float
    contributing_provenance: tuple[ScorerProvenance, ...]
    boundary_support: BoundarySupport
    casing_surface: str | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.word_index, bool)
            or not isinstance(self.word_index, int)
            or self.word_index < 0
        ):
            raise ValueError("decision word_index must be a non-negative integer")
        if not isinstance(self.lexical_signature, str) or not self.lexical_signature:
            raise ValueError("decision lexical_signature must not be empty")
        if not isinstance(self.trailing_punctuation, TrailingPunctuation):
            raise ValueError("decision trailing_punctuation is not allowlisted")
        if self.sentence_break != (self.trailing_punctuation in _STRONG_STOPS):
            raise ValueError("sentence_break contradicts trailing_punctuation")
        if self.paragraph_break and not self.sentence_break:
            raise ValueError("paragraph_break requires sentence_break")
        if self.confidence is not None:
            _validate_probability(self.confidence, "confidence")
        _validate_finite(self.local_score_margin, "local_score_margin")
        if not isinstance(self.contributing_provenance, tuple):
            raise ValueError("contributing provenance must be an immutable tuple")
        if (
            tuple(sorted(set(self.contributing_provenance), key=_provenance_key))
            != self.contributing_provenance
        ):
            raise ValueError("contributing provenance must be unique and sorted")
        if not isinstance(self.boundary_support, BoundarySupport):
            raise ValueError("decision boundary support is invalid")
        if self.sentence_break != self.boundary_support.supported:
            raise ValueError("strong boundary support contradicts sentence_break")
        if self.casing_surface is not None:
            _validate_casing_surface(
                self.casing_surface,
                expected_signature=self.lexical_signature,
            )
            if _is_capitalized(self.casing_surface) != self.capitalize:
                raise ValueError("casing_surface contradicts capitalize")


@dataclass(frozen=True, slots=True)
class PresentationPlan:
    """A complete deterministic presentation plan bound to one input sequence."""

    decisions: tuple[PresentationDecision, ...]
    input_digest: str
    total_score: float
    scorer_outputs: tuple[ScorerOutput, ...]
    abstentions: tuple[ScorerAbstention, ...]
    hard_constraint_application_count: int
    hard_constraint_violation_count: int
    decoder_version: str = DECODER_VERSION
    tie_break_policy: str = TIE_BREAK_POLICY

    def __post_init__(self) -> None:
        if (
            not isinstance(self.decisions, tuple)
            or not isinstance(self.scorer_outputs, tuple)
            or not isinstance(self.abstentions, tuple)
        ):
            raise ValueError("presentation plan collections must be immutable tuples")
        if not self.decisions:
            raise ValueError("presentation plan must contain decisions")
        if len(self.decisions) > MAX_PRESENTATION_WORDS:
            raise ValueError("presentation plan exceeds the maximum word count")
        if tuple(decision.word_index for decision in self.decisions) != tuple(
            range(len(self.decisions))
        ):
            raise ValueError("presentation plan decisions must have contiguous indices")
        if not _DIGEST_RE.fullmatch(self.input_digest):
            raise ValueError("input_digest must be a lowercase SHA-256 digest")
        _validate_finite(self.total_score, "total_score")
        if (
            isinstance(self.hard_constraint_application_count, bool)
            or not isinstance(self.hard_constraint_application_count, int)
            or self.hard_constraint_application_count < 0
            or self.hard_constraint_application_count > len(self.decisions)
        ):
            raise ValueError("hard_constraint_application_count must be a non-negative integer")
        if (
            isinstance(self.hard_constraint_violation_count, bool)
            or not isinstance(self.hard_constraint_violation_count, int)
            or self.hard_constraint_violation_count != 0
        ):
            raise ValueError("an accepted presentation plan cannot contain hard violations")
        if (
            tuple(sorted(self.scorer_outputs, key=lambda item: _provenance_key(item.provenance)))
            != self.scorer_outputs
            or len({output.provenance for output in self.scorer_outputs})
            != len(self.scorer_outputs)
            or any(_canonical_scorer_output(output) != output for output in self.scorer_outputs)
        ):
            raise ValueError("scorer outputs must have unique sorted provenance")
        if len(self.scorer_outputs) > MAX_PRESENTATION_SCORER_OUTPUTS:
            raise ValueError("presentation plan exceeds the maximum scorer count")
        if sum(len(output.proposals) for output in self.scorer_outputs) > (
            MAX_PRESENTATION_PROPOSALS
        ):
            raise ValueError("presentation plan exceeds the maximum proposal count")
        expected_abstentions = tuple(
            sorted(
                (
                    ScorerAbstention(output.provenance, output.abstention_reason)
                    for output in self.scorer_outputs
                    if output.abstention_reason is not None
                ),
                key=lambda item: (_provenance_key(item.provenance), item.reason),
            )
        )
        if expected_abstentions != self.abstentions:
            raise ValueError("abstentions must exactly match retained scorer outputs")
        if (
            tuple(
                sorted(
                    set(self.abstentions),
                    key=lambda item: (_provenance_key(item.provenance), item.reason),
                )
            )
            != self.abstentions
        ):
            raise ValueError("abstentions must be unique and sorted")
        if self.decoder_version != DECODER_VERSION:
            raise ValueError("unsupported presentation decoder version")
        if self.tie_break_policy != TIE_BREAK_POLICY:
            raise ValueError("unsupported presentation tie-break policy")


@dataclass(frozen=True, slots=True)
class _WordEvidence:
    punctuation_probabilities: Mapping[TrailingPunctuation, tuple[float, ...]]
    capitalization_probabilities: tuple[float, ...]
    sentence_break_probabilities: tuple[float, ...]
    paragraph_break_probabilities: tuple[float, ...]
    scorer_proposals: tuple[tuple[ScorerProvenance, tuple[PresentationProposal, ...]], ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    punctuation: TrailingPunctuation
    capitalize: bool
    paragraph_break: bool
    local_score: float
    provenance: tuple[ScorerProvenance, ...]
    casing_surface: str | None
    boundary_support: BoundarySupport

    @property
    def boundary_supported(self) -> bool:
        return self.boundary_support.supported

    @property
    def sentence_break(self) -> bool:
        return self.punctuation in _STRONG_STOPS

    @property
    def tie_key(self) -> tuple[int, int, int, str]:
        return (
            _PUNCTUATION_RANK[self.punctuation],
            int(self.capitalize),
            int(self.paragraph_break),
            self.casing_surface or "",
        )


@dataclass(frozen=True, slots=True)
class _Path:
    score: float
    previous: _Path | None
    candidate: _Candidate | None
    tie_extension: tuple[int, tuple[int, int, int, str]]
    tie_rank: int


@dataclass(frozen=True, slots=True, order=True)
class _DecoderState:
    """Minimal global state needed by cross-boundary safety constraints."""

    sentence_length: int
    added_short_stop_streak: int
    added_stop_cluster_count: int
    words_since_added_stop: int


def decode_presentation(
    words: Sequence[DecoderWord],
    scorer_outputs: Sequence[ScorerOutput],
) -> PresentationPlan:
    """Decode one validated lexical sequence using global dynamic programming."""

    _validate_decoder_resource_envelope(words, scorer_outputs)
    bound_words = tuple(words)
    bound_outputs = tuple(
        sorted(
            (_canonical_scorer_output(output) for output in scorer_outputs),
            key=lambda item: _provenance_key(item.provenance),
        )
    )
    _validate_decoder_inputs(bound_words, bound_outputs)
    evidence = _collect_evidence(bound_words, bound_outputs)
    protected_question_scope_indices = _protected_question_scope_indices(bound_words)
    question_scope_endpoint_indices = frozenset(
        _typed_question_scope_endpoints(bound_words).values()
    )

    # State tracks both the current sentence span and correlated, same-speaker
    # bursts of model-added short stops. A complete path, rather than a local
    # winner, is retained for deterministic whole-sequence optimization.
    paths: dict[_DecoderState, _Path] = {
        _DecoderState(0, 0, 0, MAX_ADDED_STOP_CLUSTER_GAP_WORDS + 1): _Path(
            score=0.0,
            previous=None,
            candidate=None,
            tie_extension=(0, (0, 0, 0, "")),
            tie_rank=0,
        )
    }
    for position, word in enumerate(bound_words):
        previous_word = bound_words[position - 1] if position > 0 else None
        next_word = bound_words[position + 1] if position + 1 < len(bound_words) else None
        candidates = _word_candidates(word, evidence[position])
        next_paths: dict[_DecoderState, _Path] = {}
        for state, path in sorted(paths.items()):
            prior_length = state.sentence_length
            sentence_length = prior_length + 1
            prior_short_streak = (
                state.added_short_stop_streak if _same_continuity_owner(previous_word, word) else 0
            )
            prior_cluster_count = (
                state.added_stop_cluster_count if _same_continuity_owner(previous_word, word) else 0
            )
            words_since_added_stop = (
                state.words_since_added_stop
                if _same_continuity_owner(previous_word, word)
                else MAX_ADDED_STOP_CLUSTER_GAP_WORDS + 1
            )
            for candidate in candidates:
                if _reject_candidate(
                    word,
                    candidate,
                    evidence=evidence[position],
                    sentence_start=prior_length == 0,
                    sentence_length=sentence_length,
                    added_short_stop_streak=prior_short_streak,
                    added_stop_cluster_count=prior_cluster_count,
                    words_since_added_stop=words_since_added_stop,
                    question_scope_protected=(word.word_index in protected_question_scope_indices),
                    question_scope_endpoint=(word.word_index in question_scope_endpoint_indices),
                ):
                    continue
                transition_score = _transition_score(
                    word=word,
                    next_word=next_word,
                    sentence_length=sentence_length,
                    sentence_start=prior_length == 0,
                    candidate=candidate,
                )
                new_state = _next_decoder_state(
                    word,
                    candidate,
                    sentence_length=sentence_length,
                    prior_short_stop_streak=prior_short_streak,
                    prior_added_stop_cluster_count=prior_cluster_count,
                    prior_words_since_added_stop=words_since_added_stop,
                )
                proposed = _Path(
                    score=path.score + candidate.local_score + transition_score,
                    previous=path,
                    candidate=candidate,
                    tie_extension=(path.tie_rank, candidate.tie_key),
                    tie_rank=0,
                )
                current = next_paths.get(new_state)
                if current is None or _path_is_better(proposed, current):
                    next_paths[new_state] = proposed
        if not next_paths:
            raise ValueError(f"no safe presentation path at word index {word.word_index}")
        ranked = sorted(next_paths.items(), key=lambda item: item[1].tie_extension)
        paths = {state: replace(path, tie_rank=rank) for rank, (state, path) in enumerate(ranked)}

    winner_state, winner = min(
        paths.items(),
        key=lambda item: (
            -(_terminal_score(item[0].sentence_length) + item[1].score),
            item[1].tie_rank,
        ),
    )
    winner_candidates = _reconstruct_candidates(winner, len(bound_words))
    total_score = winner.score + _terminal_score(winner_state.sentence_length)
    local_score_margins = _selected_local_score_margins(
        bound_words,
        evidence,
        winner_candidates,
    )
    decisions = tuple(
        PresentationDecision(
            word_index=word.word_index,
            lexical_signature=word.lexical_signature,
            trailing_punctuation=candidate.punctuation,
            capitalize=candidate.capitalize,
            sentence_break=candidate.sentence_break,
            paragraph_break=candidate.paragraph_break,
            # Scorer probabilities are not assumed to be calibrated across
            # backends.  Expose raw score margins separately and reserve this
            # field for a future calibrated confidence contract.
            confidence=None,
            local_score_margin=local_score_margins[word.word_index],
            contributing_provenance=candidate.provenance,
            boundary_support=candidate.boundary_support,
            casing_surface=candidate.casing_surface,
        )
        for word, candidate in zip(bound_words, winner_candidates, strict=True)
    )
    abstentions = tuple(
        sorted(
            (
                ScorerAbstention(output.provenance, output.abstention_reason)
                for output in bound_outputs
                if output.abstention_reason is not None
            ),
            key=lambda item: (_provenance_key(item.provenance), item.reason),
        )
    )
    return PresentationPlan(
        decisions=decisions,
        input_digest=_input_digest(bound_words),
        total_score=total_score,
        scorer_outputs=tuple(
            sorted(bound_outputs, key=lambda item: _provenance_key(item.provenance))
        ),
        abstentions=abstentions,
        hard_constraint_application_count=sum(
            _word_has_structural_constraint(word) for word in bound_words
        ),
        hard_constraint_violation_count=0,
    )


def apply_presentation_plan(
    words: Sequence[DecoderWord],
    plan: PresentationPlan,
) -> tuple[str, ...]:
    """Apply only the plan's allowlisted presentation changes to bound words."""

    bound_words = tuple(words)
    if _input_digest(bound_words) != plan.input_digest:
        raise ValueError("presentation plan input binding mismatch")
    if len(bound_words) != len(plan.decisions):
        raise ValueError("presentation plan decision count mismatch")
    _validate_decoder_inputs(bound_words, plan.scorer_outputs)
    replay_evidence = _collect_evidence(bound_words, plan.scorer_outputs)
    surfaces: list[str] = []
    sentence_start_index = 0
    short_stop_streak = 0
    added_stop_cluster_count = 0
    words_since_added_stop = MAX_ADDED_STOP_CLUSTER_GAP_WORDS + 1
    protected_question_scope_indices = _protected_question_scope_indices(bound_words)
    question_scope_endpoint_indices = frozenset(
        _typed_question_scope_endpoints(bound_words).values()
    )
    previous_word: DecoderWord | None = None
    for position, (word, decision) in enumerate(zip(bound_words, plan.decisions, strict=True)):
        if (
            decision.word_index != word.word_index
            or decision.lexical_signature != word.lexical_signature
        ):
            raise ValueError("presentation decision input binding mismatch")
        if decision.trailing_punctuation is TrailingPunctuation.QUESTION and (
            word.word_index not in question_scope_endpoint_indices
            or word.question_scope_start_index is None
            or sentence_start_index > word.question_scope_start_index
        ):
            raise ValueError("presentation question boundary lacks an intact source scope")
        if (
            word.word_index in question_scope_endpoint_indices
            and decision.trailing_punctuation is not TrailingPunctuation.QUESTION
        ):
            raise ValueError("presentation typed question scope lacks its final question mark")
        sentence_length = word.word_index - sentence_start_index + 1
        if (
            sentence_length == 1
            and _requires_sentence_initial_capitalization(word.lexical_text)
            and not decision.capitalize
            and not _has_intentional_mixed_case(decision.casing_surface)
        ):
            raise ValueError("presentation decision violates sentence-start capitalization")
        _validate_replayed_boundary_support(word, decision, replay_evidence[position])
        _validate_replayed_contributing_provenance(
            decision,
            replay_evidence[position],
        )
        if not _mark_class_admitted(
            word,
            replay_evidence[position],
            decision.trailing_punctuation,
        ):
            raise ValueError("presentation mark class lacks exact typed support")
        if not _same_continuity_owner(previous_word, word):
            short_stop_streak = 0
            added_stop_cluster_count = 0
            words_since_added_stop = MAX_ADDED_STOP_CLUSTER_GAP_WORDS + 1
        if _decision_violates_global_constraints(
            word,
            punctuation=decision.trailing_punctuation,
            sentence_length=sentence_length,
            added_short_stop_streak=short_stop_streak,
            added_stop_cluster_count=added_stop_cluster_count,
            words_since_added_stop=words_since_added_stop,
            question_scope_protected=(word.word_index in protected_question_scope_indices),
            question_scope_endpoint=(word.word_index in question_scope_endpoint_indices),
        ):
            raise ValueError("presentation decision violates global surface constraints")
        lexical_surface = decision.casing_surface or word.lexical_text
        if decision.casing_surface is not None:
            if not _casing_preserves_exact_unicode_shape(
                decision.casing_surface,
                word.lexical_text,
            ):
                raise ValueError("presentation casing changed lexical code-point shape")
            if _is_capitalized(decision.casing_surface) != decision.capitalize:
                raise ValueError("presentation casing contradicts capitalization decision")
        surface = _replace_trailing_punctuation(
            lexical_surface,
            decision.trailing_punctuation,
        )
        if decision.capitalize and decision.casing_surface is None:
            surface = _capitalize(surface)
        if lexical_signature(surface) != word.lexical_signature:
            raise ValueError("presentation decision changed lexical identity")
        if not _casing_preserves_exact_unicode_shape(surface, word.lexical_text):
            raise ValueError("presentation decision changed lexical code-point shape")
        surfaces.append(surface)
        if decision.sentence_break:
            short_stop_streak = _next_added_short_stop_streak(
                word,
                punctuation=decision.trailing_punctuation,
                sentence_length=sentence_length,
                prior_streak=short_stop_streak,
            )
            sentence_start_index = word.word_index + 1
        added_stop_cluster_count, words_since_added_stop = _next_added_stop_cluster(
            word,
            punctuation=decision.trailing_punctuation,
            prior_count=added_stop_cluster_count,
            prior_words_since_stop=words_since_added_stop,
        )
        previous_word = word
    return tuple(surfaces)


def _validate_replayed_boundary_support(
    word: DecoderWord,
    decision: PresentationDecision,
    evidence: _WordEvidence,
) -> None:
    support = decision.boundary_support
    expected = _strong_boundary_support(word, evidence, decision.trailing_punctuation)
    if support != expected:
        raise ValueError("presentation boundary support does not match retained scorer output")
    if decision.sentence_break != support.supported:
        raise ValueError("presentation strong boundary lacks typed support")


def _validate_replayed_contributing_provenance(
    decision: PresentationDecision,
    evidence: _WordEvidence,
) -> None:
    expected = _supporting_provenance(
        evidence,
        decision.trailing_punctuation,
        decision.capitalize,
        decision.paragraph_break,
        decision.casing_surface,
    )
    if decision.contributing_provenance != expected:
        raise ValueError(
            "presentation contributing provenance does not match retained scorer output"
        )


def serialize_presentation_plan(plan: PresentationPlan) -> bytes:
    """Return canonical UTF-8 JSON suitable for a derived artifact."""

    if not isinstance(plan, PresentationPlan):
        raise ValueError("plan must be a PresentationPlan")
    if len(plan.decisions) > MAX_PRESENTATION_WORDS:
        raise ValueError("presentation plan exceeds the maximum word count")
    if len(plan.scorer_outputs) > MAX_PRESENTATION_SCORER_OUTPUTS:
        raise ValueError("presentation plan exceeds the maximum scorer count")
    if sum(len(output.proposals) for output in plan.scorer_outputs) > (MAX_PRESENTATION_PROPOSALS):
        raise ValueError("presentation plan exceeds the maximum proposal count")
    payload = {
        "schema_version": DECODER_VERSION,
        "decoder_version": plan.decoder_version,
        "tie_break_policy": plan.tie_break_policy,
        "input_digest": plan.input_digest,
        "total_score": plan.total_score,
        "hard_constraints": {
            "application_count": plan.hard_constraint_application_count,
            "violation_count": plan.hard_constraint_violation_count,
        },
        "abstentions": [
            {
                "provenance": _provenance_payload(abstention.provenance),
                "reason": abstention.reason,
            }
            for abstention in plan.abstentions
        ],
        "scorer_outputs": [_scorer_output_payload(output) for output in plan.scorer_outputs],
        "decisions": [
            {
                "word_index": decision.word_index,
                "lexical_signature": decision.lexical_signature,
                "trailing_punctuation": decision.trailing_punctuation.value,
                "capitalize": decision.capitalize,
                "sentence_break": decision.sentence_break,
                "paragraph_break": decision.paragraph_break,
                "confidence": decision.confidence,
                "local_score_margin": decision.local_score_margin,
                "casing_surface": decision.casing_surface,
                "contributing_provenance": [
                    _provenance_payload(provenance)
                    for provenance in decision.contributing_provenance
                ],
                "boundary_support": {
                    "source_punctuation": decision.boundary_support.source_punctuation,
                    "typed_question": decision.boundary_support.typed_question,
                    "timing_pause": decision.boundary_support.timing_pause,
                    "exact_mark_provenance": [
                        _provenance_payload(provenance)
                        for provenance in decision.boundary_support.exact_mark_provenance
                    ],
                    "boundary_provenance": [
                        _provenance_payload(provenance)
                        for provenance in decision.boundary_support.boundary_provenance
                    ],
                    "structural_veto": decision.boundary_support.structural_veto,
                },
            }
            for decision in plan.decisions
        ],
    }
    serialized = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if len(serialized) > MAX_PRESENTATION_PLAN_BYTES:
        raise ValueError("presentation plan exceeds the maximum byte envelope")
    return serialized


def deserialize_presentation_plan(data: bytes) -> PresentationPlan:
    """Parse canonical plan bytes into the complete typed presentation contract."""

    if len(data) > MAX_PRESENTATION_PLAN_BYTES:
        raise ValueError("presentation plan exceeds the maximum byte envelope")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("presentation plan must be valid UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    try:
        raw = json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise ValueError("presentation plan must be valid JSON") from exc
    payload = _require_object(raw, "presentation plan")
    _require_exact_keys(
        payload,
        {
            "abstentions",
            "decisions",
            "decoder_version",
            "hard_constraints",
            "input_digest",
            "scorer_outputs",
            "schema_version",
            "tie_break_policy",
            "total_score",
        },
        "presentation plan",
    )
    if _require_string(payload["schema_version"], "schema_version") != DECODER_VERSION:
        raise ValueError("presentation plan schema version is unsupported")
    _validate_serialized_resource_envelope(payload)
    hard_constraints = _require_object(payload["hard_constraints"], "hard_constraints")
    _require_exact_keys(
        hard_constraints,
        {"application_count", "violation_count"},
        "hard_constraints",
    )
    plan = PresentationPlan(
        decisions=tuple(
            _decision_from_record(item) for item in _require_list(payload["decisions"], "decisions")
        ),
        input_digest=_require_string(payload["input_digest"], "input_digest"),
        total_score=_require_number(payload["total_score"], "total_score"),
        scorer_outputs=tuple(
            _scorer_output_from_record(item)
            for item in _require_list(payload["scorer_outputs"], "scorer_outputs")
        ),
        abstentions=tuple(
            _abstention_from_record(item)
            for item in _require_list(payload["abstentions"], "abstentions")
        ),
        hard_constraint_application_count=_require_integer(
            hard_constraints["application_count"],
            "hard constraint application_count",
        ),
        hard_constraint_violation_count=_require_integer(
            hard_constraints["violation_count"],
            "hard constraint violation_count",
        ),
        decoder_version=_require_string(payload["decoder_version"], "decoder_version"),
        tie_break_policy=_require_string(payload["tie_break_policy"], "tie_break_policy"),
    )
    if serialize_presentation_plan(plan) != data:
        raise ValueError("presentation plan is not canonical JSON")
    return plan


def presentation_plan_sha256(plan: PresentationPlan) -> str:
    """Hash the exact canonical serialization of a presentation plan."""

    return hashlib.sha256(serialize_presentation_plan(plan)).hexdigest()


def _provenance_payload(provenance: ScorerProvenance) -> dict[str, str | None]:
    return {
        "scorer_id": provenance.scorer_id,
        "scorer_version": provenance.scorer_version,
        "configuration_digest": provenance.configuration_digest,
    }


def _proposal_group_payload(
    proposals: Sequence[PresentationProposal],
) -> dict[str, object]:
    if not proposals:
        raise ValueError("proposal group must not be empty")
    representative = proposals[0]
    return {
        "word_index": representative.word_index,
        "lexical_signature": representative.lexical_signature,
        "lexical_text_digest": representative.lexical_text_digest,
        "capitalization_probability": representative.capitalization_probability,
        "sentence_break_probability": representative.sentence_break_probability,
        "paragraph_break_probability": representative.paragraph_break_probability,
        "evidence_roles": [role.value for role in representative.evidence_roles],
        "casing_surface": representative.casing_surface,
        "casing_probability": representative.casing_probability,
        "punctuation_probabilities": [
            [proposal.trailing_punctuation.value, proposal.punctuation_probability]
            for proposal in proposals
        ],
    }


def _scorer_output_payload(output: ScorerOutput) -> dict[str, object]:
    proposal_groups: list[dict[str, object]] = []
    start = 0
    while start < len(output.proposals):
        word_index = output.proposals[start].word_index
        end = start + 1
        while end < len(output.proposals) and output.proposals[end].word_index == word_index:
            end += 1
        proposal_groups.append(_proposal_group_payload(output.proposals[start:end]))
        start = end
    return {
        "provenance": _provenance_payload(output.provenance),
        "proposal_groups": proposal_groups,
        "abstention_reason": output.abstention_reason,
    }


def _provenance_from_record(value: object) -> ScorerProvenance:
    payload = _require_object(value, "scorer provenance")
    _require_exact_keys(
        payload,
        {"configuration_digest", "scorer_id", "scorer_version"},
        "scorer provenance",
    )
    return ScorerProvenance(
        scorer_id=_require_string(payload["scorer_id"], "scorer_id"),
        scorer_version=_require_string(payload["scorer_version"], "scorer_version"),
        configuration_digest=_require_optional_string(
            payload["configuration_digest"],
            "configuration_digest",
        ),
    )


def _proposal_group_from_record(value: object) -> tuple[PresentationProposal, ...]:
    payload = _require_object(value, "presentation proposal group")
    _require_exact_keys(
        payload,
        {
            "capitalization_probability",
            "casing_probability",
            "casing_surface",
            "evidence_roles",
            "lexical_signature",
            "lexical_text_digest",
            "paragraph_break_probability",
            "punctuation_probabilities",
            "sentence_break_probability",
            "word_index",
        },
        "presentation proposal group",
    )
    word_index = _require_integer(payload["word_index"], "word_index")
    lexical_signature_value = _require_string(
        payload["lexical_signature"],
        "lexical_signature",
    )
    lexical_digest = _require_string(
        payload["lexical_text_digest"],
        "lexical_text_digest",
    )
    capitalization_probability = _require_number(
        payload["capitalization_probability"],
        "capitalization_probability",
    )
    sentence_break_probability = _require_number(
        payload["sentence_break_probability"],
        "sentence_break_probability",
    )
    paragraph_break_probability = _require_number(
        payload["paragraph_break_probability"],
        "paragraph_break_probability",
    )
    try:
        evidence_roles = tuple(
            EvidenceRole(_require_string(item, "evidence_role"))
            for item in _require_list(payload["evidence_roles"], "evidence_roles")
        )
    except ValueError as exc:
        raise ValueError("presentation proposal evidence role is not allowlisted") from exc
    casing_surface = _require_optional_string(payload["casing_surface"], "casing_surface")
    casing_probability = _require_optional_number(
        payload["casing_probability"],
        "casing_probability",
    )
    punctuation_records = _require_list(
        payload["punctuation_probabilities"],
        "punctuation_probabilities",
    )
    if not punctuation_records:
        raise ValueError("presentation proposal group must contain punctuation evidence")
    proposals: list[PresentationProposal] = []
    for item in punctuation_records:
        pair = _require_list(item, "punctuation probability pair")
        if len(pair) != 2:
            raise ValueError("punctuation probability pair must contain exactly two values")
        try:
            punctuation = TrailingPunctuation(_require_string(pair[0], "trailing_punctuation"))
        except ValueError as exc:
            raise ValueError("presentation proposal punctuation is not allowlisted") from exc
        proposals.append(
            PresentationProposal(
                word_index=word_index,
                lexical_signature=lexical_signature_value,
                lexical_text_digest=lexical_digest,
                trailing_punctuation=punctuation,
                punctuation_probability=_require_number(
                    pair[1],
                    "punctuation_probability",
                ),
                capitalization_probability=capitalization_probability,
                sentence_break_probability=sentence_break_probability,
                paragraph_break_probability=paragraph_break_probability,
                evidence_roles=evidence_roles,
                casing_surface=casing_surface,
                casing_probability=casing_probability,
            )
        )
    return tuple(proposals)


def _scorer_output_from_record(value: object) -> ScorerOutput:
    payload = _require_object(value, "scorer output")
    _require_exact_keys(
        payload,
        {"abstention_reason", "proposal_groups", "provenance"},
        "scorer output",
    )
    return ScorerOutput(
        provenance=_provenance_from_record(payload["provenance"]),
        proposals=tuple(
            proposal
            for item in _require_list(payload["proposal_groups"], "proposal_groups")
            for proposal in _proposal_group_from_record(item)
        ),
        abstention_reason=_require_optional_string(
            payload["abstention_reason"],
            "abstention_reason",
        ),
    )


def _decision_from_record(value: object) -> PresentationDecision:
    payload = _require_object(value, "presentation decision")
    _require_exact_keys(
        payload,
        {
            "capitalize",
            "boundary_support",
            "casing_surface",
            "confidence",
            "contributing_provenance",
            "lexical_signature",
            "local_score_margin",
            "paragraph_break",
            "sentence_break",
            "trailing_punctuation",
            "word_index",
        },
        "presentation decision",
    )
    try:
        punctuation = TrailingPunctuation(
            _require_string(payload["trailing_punctuation"], "trailing_punctuation")
        )
    except ValueError as exc:
        raise ValueError("presentation decision punctuation is not allowlisted") from exc
    return PresentationDecision(
        word_index=_require_integer(payload["word_index"], "word_index"),
        lexical_signature=_require_string(
            payload["lexical_signature"],
            "lexical_signature",
        ),
        trailing_punctuation=punctuation,
        capitalize=_require_boolean(payload["capitalize"], "capitalize"),
        sentence_break=_require_boolean(payload["sentence_break"], "sentence_break"),
        paragraph_break=_require_boolean(payload["paragraph_break"], "paragraph_break"),
        confidence=_require_optional_number(payload["confidence"], "confidence"),
        local_score_margin=_require_number(
            payload["local_score_margin"],
            "local_score_margin",
        ),
        casing_surface=_require_optional_string(payload["casing_surface"], "casing_surface"),
        contributing_provenance=tuple(
            _provenance_from_record(item)
            for item in _require_list(
                payload["contributing_provenance"],
                "contributing_provenance",
            )
        ),
        boundary_support=_boundary_support_from_record(payload["boundary_support"]),
    )


def _boundary_support_from_record(value: object) -> BoundarySupport:
    payload = _require_object(value, "boundary support")
    _require_exact_keys(
        payload,
        {
            "boundary_provenance",
            "exact_mark_provenance",
            "source_punctuation",
            "structural_veto",
            "timing_pause",
            "typed_question",
        },
        "boundary support",
    )
    return BoundarySupport(
        source_punctuation=_require_boolean(
            payload["source_punctuation"],
            "source_punctuation",
        ),
        typed_question=_require_boolean(payload["typed_question"], "typed_question"),
        timing_pause=_require_boolean(payload["timing_pause"], "timing_pause"),
        exact_mark_provenance=tuple(
            _provenance_from_record(item)
            for item in _require_list(
                payload["exact_mark_provenance"],
                "exact_mark_provenance",
            )
        ),
        boundary_provenance=tuple(
            _provenance_from_record(item)
            for item in _require_list(
                payload["boundary_provenance"],
                "boundary_provenance",
            )
        ),
        structural_veto=_require_boolean(payload["structural_veto"], "structural_veto"),
    )


def _abstention_from_record(value: object) -> ScorerAbstention:
    payload = _require_object(value, "scorer abstention")
    _require_exact_keys(payload, {"provenance", "reason"}, "scorer abstention")
    return ScorerAbstention(
        provenance=_provenance_from_record(payload["provenance"]),
        reason=_require_string(payload["reason"], "reason"),
    )


def _require_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a JSON object with string keys")
    return {str(key): item for key, item in value.items()}


def _require_exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    field: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} fields do not match the typed schema")


def _validate_serialized_resource_envelope(payload: Mapping[str, object]) -> None:
    decisions = _require_list(payload.get("decisions"), "decisions")
    if len(decisions) > MAX_PRESENTATION_WORDS:
        raise ValueError("presentation plan exceeds the maximum word count")
    outputs = _require_list(payload.get("scorer_outputs"), "scorer_outputs")
    if len(outputs) > MAX_PRESENTATION_SCORER_OUTPUTS:
        raise ValueError("presentation plan exceeds the maximum scorer count")
    proposal_count = 0
    for output_record in outputs:
        output = _require_object(output_record, "scorer output")
        groups = _require_list(output.get("proposal_groups"), "proposal_groups")
        for group_record in groups:
            group = _require_object(group_record, "presentation proposal group")
            punctuation_records = _require_list(
                group.get("punctuation_probabilities"),
                "punctuation_probabilities",
            )
            proposal_count += len(punctuation_records)
            if proposal_count > MAX_PRESENTATION_PROPOSALS:
                raise ValueError("presentation plan exceeds the maximum proposal count")


def _require_list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a JSON array")
    return list(value)


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field)


def _require_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _require_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _require_optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _require_number(value, field)


def _require_boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _validate_scorer_proposals(proposals: tuple[PresentationProposal, ...]) -> None:
    if not proposals:
        return
    grouped: dict[int, list[PresentationProposal]] = defaultdict(list)
    for proposal in proposals:
        if not isinstance(proposal, PresentationProposal):
            raise ValueError("scorer proposals contain an invalid item")
        grouped[proposal.word_index].append(proposal)
    indexes = sorted(grouped)
    if indexes != list(range(indexes[0], indexes[-1] + 1)):
        raise ValueError("scorer proposal indices must form one contiguous window")
    for word_index, word_proposals in grouped.items():
        punctuations = [proposal.trailing_punctuation for proposal in word_proposals]
        if len(punctuations) != len(set(punctuations)):
            raise ValueError(f"duplicate punctuation proposal at word index {word_index}")
        signatures = {proposal.lexical_signature for proposal in word_proposals}
        if len(signatures) != 1:
            raise ValueError(f"conflicting lexical bindings at word index {word_index}")
        surface_digests = {proposal.lexical_text_digest for proposal in word_proposals}
        if len(surface_digests) != 1:
            raise ValueError(f"conflicting lexical surface bindings at word index {word_index}")
        total_probability = math.fsum(
            proposal.punctuation_probability for proposal in word_proposals
        )
        if total_probability > 1.0 + _SCORE_EPSILON:
            raise ValueError(f"punctuation probabilities exceed one at word index {word_index}")
        word_level = {
            (
                proposal.capitalization_probability,
                proposal.casing_surface,
                proposal.casing_probability,
                proposal.sentence_break_probability,
                proposal.paragraph_break_probability,
                proposal.evidence_roles,
            )
            for proposal in word_proposals
        }
        if len(word_level) != 1:
            raise ValueError(f"conflicting word-level probabilities at word index {word_index}")


def _canonical_scorer_output(output: ScorerOutput) -> ScorerOutput:
    if not isinstance(output, ScorerOutput):
        raise ValueError("decoder scorer output is invalid")
    return replace(
        output,
        proposals=tuple(
            sorted(
                output.proposals,
                key=lambda item: (
                    item.word_index,
                    _PUNCTUATION_RANK[item.trailing_punctuation],
                ),
            )
        ),
    )


def _validate_decoder_inputs(
    words: tuple[DecoderWord, ...],
    scorer_outputs: tuple[ScorerOutput, ...],
) -> None:
    _validate_decoder_resource_envelope(words, scorer_outputs)
    if not words:
        raise ValueError("decoder requires at least one word")
    if tuple(word.word_index for word in words) != tuple(range(len(words))):
        raise ValueError("decoder word indices must be contiguous and ordered from zero")
    for current, following in zip(words, words[1:], strict=False):
        # Canonical lexical order is authoritative. Conversational overlap can
        # legitimately make adjacent word starts run backward when a second
        # speaker begins before the first speaker finishes, so cross-word
        # timestamp ordering is evidence rather than an input validity rule.
        if current.ends_turn != following.begins_turn:
            raise ValueError("adjacent turn-boundary evidence is inconsistent")
        if (
            current.speaker_id is not None
            and following.speaker_id is not None
            and current.speaker_id != following.speaker_id
            and not current.ends_turn
        ):
            raise ValueError("speaker change is missing a turn boundary")
    provenances = [output.provenance for output in scorer_outputs]
    if len(provenances) != len(set(provenances)):
        raise ValueError("duplicate scorer provenance")
    for output in scorer_outputs:
        _validate_scorer_role_contract(output, words)
        for proposal in output.proposals:
            if proposal.word_index >= len(words):
                raise ValueError("proposal word_index is outside the decoder input")
            if proposal.lexical_signature != words[proposal.word_index].lexical_signature:
                raise ValueError("proposal lexical binding does not match decoder input")
            if proposal.lexical_text_digest != lexical_text_digest(
                words[proposal.word_index].lexical_text
            ):
                raise ValueError("proposal lexical surface binding does not match decoder input")
            if proposal.casing_surface is not None and not _casing_preserves_exact_unicode_shape(
                proposal.casing_surface,
                words[proposal.word_index].lexical_text,
            ):
                raise ValueError("proposal casing changed lexical code-point shape")


def _validate_scorer_role_contract(
    output: ScorerOutput,
    words: tuple[DecoderWord, ...],
) -> None:
    """Reject role confusion before any scorer dimension reaches the decoder."""

    grouped: dict[int, list[PresentationProposal]] = defaultdict(list)
    for proposal in output.proposals:
        grouped[proposal.word_index].append(proposal)
    for word_index, raw_group in grouped.items():
        proposals = tuple(raw_group)
        roles = proposals[0].evidence_roles
        distribution = _proposal_distribution(proposals)
        if EvidenceRole.EXACT_MARK not in roles and any(
            abs(probability - (1.0 / len(_PUNCTUATION_ORDER))) > _SCORE_EPSILON
            for probability in distribution.values()
        ):
            raise ValueError("non-mark evidence supplied a punctuation-class distribution")
        representative = proposals[0]
        if EvidenceRole.CASE_ONLY not in roles and (
            abs(representative.capitalization_probability - 0.5) > _SCORE_EPSILON
            or representative.casing_surface is not None
            or representative.casing_probability is not None
        ):
            raise ValueError("non-case evidence supplied a casing decision")
        if (
            EvidenceRole.STRUCTURAL_VETO in roles
            and output.provenance.scorer_id != "auralign.source-grammar-features"
        ):
            raise ValueError("only canonical source evidence may carry a structural veto")
        if output.provenance.scorer_id == "wtpsplit-sat-3l-sm" and roles != (
            EvidenceRole.BOUNDARY_ONLY,
        ):
            raise ValueError("SaT proposals must be boundary-only evidence")
        if output.provenance.scorer_id.endswith(".window-aggregate") and roles != (
            EvidenceRole.EXACT_MARK,
            EvidenceRole.CASE_ONLY,
        ):
            raise ValueError("cleanup model proposals must separate mark and case evidence")
        if output.provenance.scorer_id == "auralign.source-grammar-features":
            if EvidenceRole.CASE_ONLY not in roles:
                raise ValueError("source evidence must retain its casing dimension")
            expected_veto = _word_has_structural_constraint(words[word_index])
            if (EvidenceRole.STRUCTURAL_VETO in roles) != expected_veto:
                raise ValueError("source structural-veto role does not match decoder evidence")


def _validate_decoder_resource_envelope(
    words: Sequence[DecoderWord],
    scorer_outputs: Sequence[ScorerOutput],
) -> None:
    if len(words) > MAX_PRESENTATION_WORDS:
        raise ValueError("decoder input exceeds the maximum word count")
    if len(scorer_outputs) > MAX_PRESENTATION_SCORER_OUTPUTS:
        raise ValueError("decoder input exceeds the maximum scorer count")
    proposal_count = 0
    for output in scorer_outputs:
        if not isinstance(output, ScorerOutput):
            raise ValueError("decoder scorer output is invalid")
        proposal_count += len(output.proposals)
        if proposal_count > MAX_PRESENTATION_PROPOSALS:
            raise ValueError("decoder input exceeds the maximum proposal count")


def _collect_evidence(
    words: tuple[DecoderWord, ...],
    scorer_outputs: tuple[ScorerOutput, ...],
) -> tuple[_WordEvidence, ...]:
    by_word: list[list[tuple[ScorerProvenance, tuple[PresentationProposal, ...]]]] = [
        [] for _ in words
    ]
    for output in sorted(scorer_outputs, key=lambda item: _provenance_key(item.provenance)):
        if output.abstention_reason is not None:
            continue
        grouped: dict[int, list[PresentationProposal]] = defaultdict(list)
        for proposal in output.proposals:
            grouped[proposal.word_index].append(proposal)
        for word_index, proposals in grouped.items():
            by_word[word_index].append(
                (
                    output.provenance,
                    tuple(
                        sorted(
                            proposals,
                            key=lambda item: _PUNCTUATION_RANK[item.trailing_punctuation],
                        )
                    ),
                )
            )

    collected: list[_WordEvidence] = []
    for scorer_groups in by_word:
        punctuation_probabilities: dict[TrailingPunctuation, list[float]] = {
            punctuation: [] for punctuation in _PUNCTUATION_ORDER
        }
        capitalization: list[float] = []
        sentence_break: list[float] = []
        paragraph_break: list[float] = []
        for _provenance, group_proposals in scorer_groups:
            roles = group_proposals[0].evidence_roles
            distribution = _proposal_distribution(group_proposals)
            if EvidenceRole.EXACT_MARK in roles:
                for punctuation in _PUNCTUATION_ORDER:
                    punctuation_probabilities[punctuation].append(distribution[punctuation])
            representative = group_proposals[0]
            if EvidenceRole.CASE_ONLY in roles:
                capitalization.append(representative.capitalization_probability)
            if EvidenceRole.BOUNDARY_ONLY in roles:
                sentence_break.append(representative.sentence_break_probability)
                paragraph_break.append(representative.paragraph_break_probability)
        collected.append(
            _WordEvidence(
                punctuation_probabilities={
                    punctuation: tuple(probabilities)
                    for punctuation, probabilities in punctuation_probabilities.items()
                },
                capitalization_probabilities=tuple(capitalization),
                sentence_break_probabilities=tuple(sentence_break),
                paragraph_break_probabilities=tuple(paragraph_break),
                scorer_proposals=tuple(scorer_groups),
            )
        )
    return tuple(collected)


def _proposal_distribution(
    proposals: Sequence[PresentationProposal],
) -> dict[TrailingPunctuation, float]:
    explicit = {
        proposal.trailing_punctuation: proposal.punctuation_probability for proposal in proposals
    }
    missing = [value for value in _PUNCTUATION_ORDER if value not in explicit]
    remaining = max(0.0, 1.0 - math.fsum(explicit.values()))
    implicit = remaining / len(missing) if missing else 0.0
    return {value: explicit.get(value, implicit) for value in _PUNCTUATION_ORDER}


def _word_candidates(word: DecoderWord, evidence: _WordEvidence) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    for punctuation in _PUNCTUATION_ORDER:
        sentence_break = punctuation in _STRONG_STOPS
        paragraph_options = (False, True) if sentence_break else (False,)
        capitalization_options = (
            (False, True) if _capitalization_preserves_shape(word.lexical_text) else (False,)
        )
        for capitalize in capitalization_options:
            for paragraph_break in paragraph_options:
                casing_surface = _preferred_casing_surface(
                    word,
                    evidence,
                    capitalize=capitalize,
                )
                candidates.append(
                    _Candidate(
                        punctuation=punctuation,
                        capitalize=capitalize,
                        paragraph_break=paragraph_break,
                        local_score=_local_score(
                            word,
                            evidence,
                            punctuation,
                            capitalize,
                            paragraph_break,
                            casing_surface,
                        ),
                        provenance=_supporting_provenance(
                            evidence,
                            punctuation,
                            capitalize,
                            paragraph_break,
                            casing_surface,
                        ),
                        casing_surface=casing_surface,
                        boundary_support=_strong_boundary_support(
                            word,
                            evidence,
                            punctuation,
                        ),
                    )
                )
    return tuple(sorted(candidates, key=lambda item: item.tie_key))


def _supporting_provenance(
    evidence: _WordEvidence,
    punctuation: TrailingPunctuation,
    capitalize: bool,
    paragraph_break: bool,
    casing_surface: str | None,
) -> tuple[ScorerProvenance, ...]:
    supporters: list[ScorerProvenance] = []
    sentence_break = punctuation in _STRONG_STOPS
    for provenance, proposals in evidence.scorer_proposals:
        if _proposal_group_is_neutral(proposals):
            continue
        roles = proposals[0].evidence_roles
        distribution = _proposal_distribution(proposals)
        representative = proposals[0]
        mark_support = bool(
            EvidenceRole.EXACT_MARK in roles
            and distribution[punctuation] + _SCORE_EPSILON >= max(distribution.values())
            and distribution[punctuation] > (1.0 / len(_PUNCTUATION_ORDER)) + _SCORE_EPSILON
        )
        case_support = bool(
            EvidenceRole.CASE_ONLY in roles
            and _probability_supports(
                representative.capitalization_probability,
                capitalize,
            )
            and (
                representative.casing_surface is None
                or representative.casing_surface == casing_surface
            )
        )
        boundary_support = bool(
            EvidenceRole.BOUNDARY_ONLY in roles
            and _probability_supports(
                representative.sentence_break_probability,
                sentence_break,
            )
            and _probability_supports(
                representative.paragraph_break_probability,
                paragraph_break,
            )
        )
        if mark_support or case_support or boundary_support:
            supporters.append(provenance)
    return tuple(sorted(set(supporters), key=_provenance_key))


def _proposal_group_is_neutral(
    proposals: tuple[PresentationProposal, ...],
) -> bool:
    if len(proposals) != len(_PUNCTUATION_ORDER):
        return False
    expected = 1.0 / len(_PUNCTUATION_ORDER)
    representative = proposals[0]
    return bool(
        {proposal.trailing_punctuation for proposal in proposals} == set(_PUNCTUATION_ORDER)
        and all(
            abs(proposal.punctuation_probability - expected) <= _SCORE_EPSILON
            for proposal in proposals
        )
        and abs(representative.capitalization_probability - 0.5) <= _SCORE_EPSILON
        and abs(representative.sentence_break_probability - 0.5) <= _SCORE_EPSILON
        and abs(representative.paragraph_break_probability - 0.5) <= _SCORE_EPSILON
        and representative.casing_surface is None
        and representative.casing_probability is None
    )


def _probability_supports(probability: float, selected: bool) -> bool:
    return probability >= 0.5 if selected else probability <= 0.5


def _preferred_casing_surface(
    word: DecoderWord,
    evidence: _WordEvidence,
    *,
    capitalize: bool,
) -> str | None:
    """Choose one exact, shape-safe casing proposal deterministically."""

    candidates = {
        proposal.casing_surface
        for _provenance, proposals in evidence.scorer_proposals
        for proposal in proposals[:1]
        if EvidenceRole.CASE_ONLY in proposal.evidence_roles
        if proposal.casing_surface is not None
        and _is_capitalized(proposal.casing_surface) == capitalize
    }
    if not candidates:
        return None

    def score(surface: str) -> float:
        total = 0.0
        for _provenance, proposals in evidence.scorer_proposals:
            representative = proposals[0]
            if EvidenceRole.CASE_ONLY not in representative.evidence_roles:
                continue
            if representative.casing_surface is None:
                continue
            probability = representative.casing_probability
            if probability is None:  # guarded by PresentationProposal
                continue
            total += _log_probability(
                probability if representative.casing_surface == surface else 1.0 - probability
            )
        if surface == _case_surface(word.lexical_text):
            total += 0.35
        return total

    return min(candidates, key=lambda surface: (-score(surface), surface))


def _legacy_casing_surface(word: DecoderWord, *, capitalize: bool) -> str:
    source = _case_surface(word.lexical_text)
    return _case_surface(_capitalize(source)) if capitalize else source


def _local_score(
    word: DecoderWord,
    evidence: _WordEvidence,
    punctuation: TrailingPunctuation,
    capitalize: bool,
    paragraph_break: bool,
    casing_surface: str | None,
) -> float:
    base_scores = {
        TrailingPunctuation.NONE: 0.0,
        TrailingPunctuation.COMMA: -0.8,
        TrailingPunctuation.PERIOD: -1.25,
        TrailingPunctuation.QUESTION: -1.5,
        TrailingPunctuation.EXCLAMATION: -2.0,
        TrailingPunctuation.SEMICOLON: -1.4,
        TrailingPunctuation.COLON: -1.4,
        TrailingPunctuation.ELLIPSIS: -2.0,
    }
    score = base_scores[punctuation]
    source_punctuation = _source_punctuation(word.lexical_text)
    # An explicit ASR punctuation mark is useful source evidence.  The absence
    # of a mark is not: ASR lexical surfaces are commonly unpunctuated, so a
    # source-NONE preference would systematically suppress positive boundary
    # evidence from cleanup backends and boundary scorers.
    if source_punctuation is not TrailingPunctuation.NONE:
        score += 1.6 if punctuation == source_punctuation else -0.25
    for probability in evidence.punctuation_probabilities[punctuation]:
        score += _log_probability(probability)

    source_capitalized = _is_capitalized(word.lexical_text)
    score += 0.55 if capitalize == source_capitalized else -0.55
    for probability in evidence.capitalization_probabilities:
        score += _log_probability(probability if capitalize else 1.0 - probability)
    selected_casing = casing_surface or _legacy_casing_surface(word, capitalize=capitalize)
    source_casing = _case_surface(word.lexical_text)
    score += 0.35 if selected_casing == source_casing else -0.1
    for _provenance, proposals in evidence.scorer_proposals:
        representative = proposals[0]
        if EvidenceRole.CASE_ONLY not in representative.evidence_roles:
            continue
        if representative.casing_surface is None:
            continue
        casing_probability = representative.casing_probability
        if casing_probability is None:  # guarded by PresentationProposal
            continue
        score += _log_probability(
            casing_probability
            if representative.casing_surface == selected_casing
            else 1.0 - casing_probability
        )

    sentence_break = punctuation in _STRONG_STOPS
    for probability in evidence.sentence_break_probabilities:
        score += _log_probability(probability if sentence_break else 1.0 - probability)
    if punctuation == TrailingPunctuation.QUESTION:
        score += 3.5 * word.question_evidence
    elif sentence_break:
        score -= 1.25 * word.question_evidence
    elif word.question_evidence >= 0.8:
        score -= 0.75 * word.question_evidence

    if paragraph_break:
        score -= 1.5
    for probability in evidence.paragraph_break_probabilities:
        score += _log_probability(probability if paragraph_break else 1.0 - probability)
    return score


def _transition_score(
    *,
    word: DecoderWord,
    next_word: DecoderWord | None,
    sentence_length: int,
    sentence_start: bool,
    candidate: _Candidate,
) -> float:
    score = 0.0
    if candidate.capitalize == sentence_start:
        score += 1.15
    else:
        score -= 1.15

    pause = word.pause_after_seconds
    if pause is not None and pause >= 1.2:
        if candidate.sentence_break:
            score += 3.6
        elif candidate.punctuation == TrailingPunctuation.COMMA:
            score += 0.7
        elif candidate.punctuation == TrailingPunctuation.NONE:
            score -= 1.0
    elif pause is not None and pause >= 0.45:
        if candidate.sentence_break:
            score += 0.65
        elif candidate.punctuation == TrailingPunctuation.COMMA:
            score += 0.55

    turn_boundary = word.ends_turn or (next_word is not None and next_word.begins_turn)
    if turn_boundary:
        if candidate.punctuation == TrailingPunctuation.COMMA:
            score += 0.45
        elif candidate.sentence_break:
            score += 0.15
        elif candidate.punctuation == TrailingPunctuation.NONE:
            score -= 0.1

    # Apply one bounded span-length potential through its per-word delta.
    # Length may rank supported boundaries, but can never accumulate enough
    # pressure to manufacture an otherwise unsupported strong stop.
    score += _sentence_span_penalty(sentence_length) - _sentence_span_penalty(sentence_length - 1)
    if candidate.sentence_break:
        if sentence_length < 4:
            score -= 1.4 * (4 - sentence_length)
        elif 8 <= sentence_length <= 24:
            score += 0.55
        elif sentence_length <= 40:
            score += 0.25

    if candidate.paragraph_break:
        if pause is not None and pause >= 1.5:
            score += 0.8
        if turn_boundary:
            score += 0.35
    if next_word is None:
        score += 0.6 if candidate.sentence_break else -0.25
    return score


def _reject_candidate(
    word: DecoderWord,
    candidate: _Candidate,
    *,
    evidence: _WordEvidence,
    sentence_start: bool,
    sentence_length: int,
    added_short_stop_streak: int,
    added_stop_cluster_count: int,
    words_since_added_stop: int,
    question_scope_protected: bool,
    question_scope_endpoint: bool,
) -> bool:
    if not _mark_class_admitted(word, evidence, candidate.punctuation):
        return True
    source_punctuation = _source_punctuation(word.lexical_text)
    if (
        source_punctuation is not TrailingPunctuation.NONE
        and candidate.punctuation is not source_punctuation
        and not (
            source_punctuation in _STRONG_STOPS
            and _structural_veto_applies(word, source_punctuation)
        )
    ):
        return True
    if (
        sentence_start
        and _requires_sentence_initial_capitalization(word.lexical_text)
        and not candidate.capitalize
        and not _has_intentional_mixed_case(candidate.casing_surface)
    ):
        return True
    if _decision_violates_global_constraints(
        word,
        punctuation=candidate.punctuation,
        sentence_length=sentence_length,
        added_short_stop_streak=added_short_stop_streak,
        added_stop_cluster_count=added_stop_cluster_count,
        words_since_added_stop=words_since_added_stop,
        question_scope_protected=question_scope_protected,
        question_scope_endpoint=question_scope_endpoint,
    ):
        return True
    if question_scope_endpoint and candidate.punctuation is not TrailingPunctuation.QUESTION:
        return True
    if not question_scope_endpoint and candidate.punctuation is TrailingPunctuation.QUESTION:
        return True
    if not candidate.sentence_break:
        return False
    if candidate.punctuation is TrailingPunctuation.QUESTION and (
        word.question_scope_start_index is None
        or sentence_length < word.word_index - word.question_scope_start_index + 1
    ):
        return True
    return not candidate.boundary_supported


def _decision_violates_global_constraints(
    word: DecoderWord,
    *,
    punctuation: TrailingPunctuation,
    sentence_length: int,
    added_short_stop_streak: int,
    added_stop_cluster_count: int,
    words_since_added_stop: int,
    question_scope_protected: bool,
    question_scope_endpoint: bool,
) -> bool:
    """Reject unsafe sequence shapes independently of scorer confidence."""

    if sentence_length > MAX_SENTENCE_WORDS:
        return True
    sentence_break = punctuation in _STRONG_STOPS
    if (
        punctuation
        in {
            TrailingPunctuation.COMMA,
            TrailingPunctuation.SEMICOLON,
            TrailingPunctuation.COLON,
        }
        and _positive_incomplete_contraction(word)
        and not _weak_contraction_exception(word)
        and _source_punctuation(word.lexical_text) is not punctuation
    ):
        return True
    if sentence_break and question_scope_protected:
        return True
    if not sentence_break:
        return False
    if _structural_veto_applies(word, punctuation):
        return True
    if word.incomplete_continuation or word.unsupported_same_speaker_short_continuation:
        return True
    if word.incomplete_trailing_fragment:
        return True
    if word.incomplete_abandoned_fragment and _is_added_strong_stop(
        word,
        punctuation=punctuation,
    ):
        return True
    if word.incomplete_contraction and (
        (
            sentence_length == 1
            and not _source_authored_complete_negative_contraction(word, punctuation)
        )
        or (
            punctuation is TrailingPunctuation.QUESTION
            and _source_punctuation(word.lexical_text) is not TrailingPunctuation.QUESTION
            and not _complete_negative_contraction_question_supported(
                word,
                sentence_length=sentence_length,
            )
        )
    ):
        return True
    if question_scope_endpoint:
        return False
    return bool(
        (
            _is_added_short_strong_stop(
                word,
                punctuation=punctuation,
                sentence_length=sentence_length,
            )
            and added_short_stop_streak >= MAX_CONSECUTIVE_ADDED_SHORT_STOPS
        )
        or (
            _is_added_strong_stop(word, punctuation=punctuation)
            and added_stop_cluster_count >= MAX_CLUSTERED_ADDED_STOPS
            and words_since_added_stop < MAX_ADDED_STOP_CLUSTER_GAP_WORDS
        )
    )


def _word_has_structural_constraint(word: DecoderWord) -> bool:
    return bool(
        word.incomplete_continuation
        or word.incomplete_contraction
        or word.incomplete_trailing_fragment
        or word.incomplete_abandoned_fragment
        or word.unsupported_same_speaker_short_continuation
    )


def _positive_incomplete_contraction(word: DecoderWord) -> bool:
    normalized = word.lexical_signature.replace("’", "'")
    return bool(word.incomplete_contraction and not normalized.endswith("n't"))


def _weak_contraction_exception(word: DecoderWord) -> bool:
    return bool(
        word.contraction_repetition_exception
        or word.contraction_correction_exception
        or word.contraction_question_tail_exception
    )


def _structural_veto_applies(
    word: DecoderWord,
    punctuation: TrailingPunctuation,
) -> bool:
    if punctuation not in _STRONG_STOPS:
        return False
    if word.incomplete_continuation or word.unsupported_same_speaker_short_continuation:
        return True
    if word.incomplete_trailing_fragment:
        return True
    if word.incomplete_abandoned_fragment and _is_added_strong_stop(
        word,
        punctuation=punctuation,
    ):
        return True
    if _positive_incomplete_contraction(word):
        return True
    return False


def _complete_negative_contraction_question_supported(
    word: DecoderWord,
    *,
    sentence_length: int,
) -> bool:
    core = _case_surface(word.lexical_text).casefold().replace("’", "'")
    scope_start = word.question_scope_start_index
    return bool(
        core.endswith("n't")
        and word.question_evidence >= 0.8
        and scope_start is not None
        and sentence_length >= word.word_index - scope_start + 1
    )


def _protected_question_scope_indices(
    words: Sequence[DecoderWord],
) -> frozenset[int]:
    """Protect each typed question scope until its selected or abstained edge."""

    latest_by_scope: dict[int, int] = {}
    for word in words:
        scope_start = word.question_scope_start_index
        if word.question_evidence < 0.8 or scope_start is None:
            continue
        latest_by_scope[scope_start] = word.word_index
    endpoints = _typed_question_scope_endpoints(words)
    return frozenset(
        word_index
        for scope_start, latest_index in latest_by_scope.items()
        for word_index in range(scope_start, endpoints.get(scope_start, latest_index))
    )


def _typed_question_scope_endpoints(
    words: Sequence[DecoderWord],
) -> dict[int, int]:
    """Select one final eligible endpoint per typed high-confidence scope.

    Structurally unsafe sites abstain locally. The latest eligible site owns the
    scope, so a later typed continuation migrates the endpoint while an unsafe
    question-like token in an immediate answer or self-correction cannot erase
    an earlier complete direct question. A scope with no eligible site abstains.
    """

    endpoints: dict[int, int] = {}
    for word in words:
        scope_start = word.question_scope_start_index
        if word.question_evidence < 0.8 or scope_start is None:
            continue
        if word.word_index - scope_start + 1 > MAX_SENTENCE_WORDS:
            continue
        if (
            word.incomplete_continuation
            or word.unsupported_same_speaker_short_continuation
            or word.incomplete_trailing_fragment
            or word.incomplete_abandoned_fragment
        ):
            continue
        if word.incomplete_contraction:
            core = _case_surface(word.lexical_text).casefold().replace("’", "'")
            if not (core.endswith("n't") and word.word_index > scope_start):
                continue
        endpoints[scope_start] = word.word_index
    return endpoints


def _source_authored_complete_negative_contraction(
    word: DecoderWord,
    punctuation: TrailingPunctuation,
) -> bool:
    """Preserve explicit negative-contraction replies from canonical evidence."""

    normalized = word.lexical_signature.replace("’", "'")
    return bool(
        normalized.endswith("n't")
        and punctuation in _STRONG_STOPS
        and _source_punctuation(word.lexical_text) is punctuation
    )


def _is_added_strong_stop(
    word: DecoderWord,
    *,
    punctuation: TrailingPunctuation,
) -> bool:
    return bool(
        punctuation in _STRONG_STOPS and _source_punctuation(word.lexical_text) not in _STRONG_STOPS
    )


def _is_added_short_strong_stop(
    word: DecoderWord,
    *,
    punctuation: TrailingPunctuation,
    sentence_length: int,
) -> bool:
    return bool(
        _is_added_strong_stop(
            word,
            punctuation=punctuation,
        )
        and sentence_length <= 2
    )


def _next_added_short_stop_streak(
    word: DecoderWord,
    *,
    punctuation: TrailingPunctuation,
    sentence_length: int,
    prior_streak: int,
) -> int:
    if punctuation not in _STRONG_STOPS:
        return prior_streak
    if _is_added_short_strong_stop(
        word,
        punctuation=punctuation,
        sentence_length=sentence_length,
    ):
        return prior_streak + 1
    return 0


def _next_decoder_state(
    word: DecoderWord,
    candidate: _Candidate,
    *,
    sentence_length: int,
    prior_short_stop_streak: int,
    prior_added_stop_cluster_count: int,
    prior_words_since_added_stop: int,
) -> _DecoderState:
    cluster_count, words_since_stop = _next_added_stop_cluster(
        word,
        punctuation=candidate.punctuation,
        prior_count=prior_added_stop_cluster_count,
        prior_words_since_stop=prior_words_since_added_stop,
    )
    return _DecoderState(
        sentence_length=(
            0 if candidate.sentence_break else min(sentence_length, MAX_SENTENCE_LENGTH_STATE)
        ),
        added_short_stop_streak=_next_added_short_stop_streak(
            word,
            punctuation=candidate.punctuation,
            sentence_length=sentence_length,
            prior_streak=prior_short_stop_streak,
        ),
        added_stop_cluster_count=cluster_count,
        words_since_added_stop=words_since_stop,
    )


def _next_added_stop_cluster(
    word: DecoderWord,
    *,
    punctuation: TrailingPunctuation,
    prior_count: int,
    prior_words_since_stop: int,
) -> tuple[int, int]:
    if punctuation in _STRONG_STOPS and not _is_added_strong_stop(
        word,
        punctuation=punctuation,
    ):
        return 0, MAX_ADDED_STOP_CLUSTER_GAP_WORDS + 1
    if _is_added_strong_stop(word, punctuation=punctuation):
        count = prior_count + 1 if prior_words_since_stop < MAX_ADDED_STOP_CLUSTER_GAP_WORDS else 1
        return count, 0
    distance = min(
        prior_words_since_stop + 1,
        MAX_ADDED_STOP_CLUSTER_GAP_WORDS + 1,
    )
    return (0 if distance > MAX_ADDED_STOP_CLUSTER_GAP_WORDS else prior_count), distance


def _same_continuity_owner(left: DecoderWord | None, right: DecoderWord) -> bool:
    if left is None:
        return False
    if left.speaker_id is not None or right.speaker_id is not None:
        return bool(
            left.speaker_id is not None
            and right.speaker_id is not None
            and left.speaker_id == right.speaker_id
        )
    return bool(
        left.container_index is not None
        and right.container_index is not None
        and left.container_index == right.container_index
    )


def _path_is_better(proposed: _Path, current: _Path) -> bool:
    if proposed.score > current.score + _SCORE_EPSILON:
        return True
    if abs(proposed.score - current.score) <= _SCORE_EPSILON:
        return proposed.tie_extension < current.tie_extension
    return False


def _reconstruct_candidates(path: _Path, expected_count: int) -> tuple[_Candidate, ...]:
    reversed_candidates: list[_Candidate] = []
    current: _Path | None = path
    while current is not None and current.candidate is not None:
        reversed_candidates.append(current.candidate)
        current = current.previous
    candidates = tuple(reversed(reversed_candidates))
    if len(candidates) != expected_count:
        raise ValueError("presentation decoder produced an incomplete path")
    return candidates


def _selected_local_score_margins(
    words: tuple[DecoderWord, ...],
    evidence: tuple[_WordEvidence, ...],
    selected: tuple[_Candidate, ...],
) -> tuple[float, ...]:
    """Return transparent local margins without claiming calibration.

    A negative margin is meaningful: global sequence optimization selected a
    locally weaker action because it improved the complete path.
    """

    margins: list[float] = []
    protected_question_scope_indices = _protected_question_scope_indices(words)
    question_scope_endpoint_indices = frozenset(_typed_question_scope_endpoints(words).values())
    state = _DecoderState(0, 0, 0, MAX_ADDED_STOP_CLUSTER_GAP_WORDS + 1)
    for position, (word, winner) in enumerate(zip(words, selected, strict=True)):
        previous_word = words[position - 1] if position > 0 else None
        next_word = words[position + 1] if position + 1 < len(words) else None
        prior_length = state.sentence_length
        sentence_length = prior_length + 1
        prior_short_streak = (
            state.added_short_stop_streak if _same_continuity_owner(previous_word, word) else 0
        )
        prior_cluster_count = (
            state.added_stop_cluster_count if _same_continuity_owner(previous_word, word) else 0
        )
        words_since_added_stop = (
            state.words_since_added_stop
            if _same_continuity_owner(previous_word, word)
            else MAX_ADDED_STOP_CLUSTER_GAP_WORDS + 1
        )
        alternatives = [
            candidate
            for candidate in _word_candidates(word, evidence[position])
            if candidate.tie_key != winner.tie_key
            and not _reject_candidate(
                word,
                candidate,
                evidence=evidence[position],
                sentence_start=prior_length == 0,
                sentence_length=sentence_length,
                added_short_stop_streak=prior_short_streak,
                added_stop_cluster_count=prior_cluster_count,
                words_since_added_stop=words_since_added_stop,
                question_scope_protected=(word.word_index in protected_question_scope_indices),
                question_scope_endpoint=(word.word_index in question_scope_endpoint_indices),
            )
        ]
        if not alternatives:
            margin = 0.0
        else:
            winner_score = _candidate_step_score(
                word,
                next_word,
                sentence_length,
                prior_length == 0,
                winner,
            )
            margin = winner_score - max(
                _candidate_step_score(
                    word,
                    next_word,
                    sentence_length,
                    prior_length == 0,
                    candidate,
                )
                for candidate in alternatives
            )
        margins.append(margin)
        state = _next_decoder_state(
            word,
            winner,
            sentence_length=sentence_length,
            prior_short_stop_streak=prior_short_streak,
            prior_added_stop_cluster_count=prior_cluster_count,
            prior_words_since_added_stop=words_since_added_stop,
        )
    return tuple(margins)


def _candidate_step_score(
    word: DecoderWord,
    next_word: DecoderWord | None,
    sentence_length: int,
    sentence_start: bool,
    candidate: _Candidate,
) -> float:
    return candidate.local_score + _transition_score(
        word=word,
        next_word=next_word,
        sentence_length=sentence_length,
        sentence_start=sentence_start,
        candidate=candidate,
    )


def _terminal_score(trailing_sentence_length: int) -> float:
    del trailing_sentence_length
    return 0.0


def _sentence_span_penalty(length: int) -> float:
    """Return a bounded whole-span prior represented by transition deltas."""

    if length <= 32:
        return 0.0
    if length <= SOFT_MAX_SENTENCE_WORDS:
        return -min(1.5, 0.05 * (length - 32))
    return -1.5 - min(2.0, 0.10 * (length - SOFT_MAX_SENTENCE_WORDS))


def _strong_boundary_support(
    word: DecoderWord,
    evidence: _WordEvidence,
    punctuation: TrailingPunctuation,
) -> BoundarySupport:
    """Return every typed local signal admitting this exact strong stop."""

    if punctuation not in _STRONG_STOPS:
        return BoundarySupport()
    exact_mark_supporters: list[ScorerProvenance] = []
    boundary_supporters: list[ScorerProvenance] = []
    for provenance, proposals in evidence.scorer_proposals:
        roles = proposals[0].evidence_roles
        distribution = _proposal_distribution(proposals)
        selected = distribution[punctuation]
        representative = proposals[0]
        if (
            EvidenceRole.EXACT_MARK in roles
            and selected + _SCORE_EPSILON >= max(distribution.values())
            and selected > (1.0 / len(_PUNCTUATION_ORDER)) + _SCORE_EPSILON
        ):
            exact_mark_supporters.append(provenance)
        if (
            EvidenceRole.BOUNDARY_ONLY in roles
            and representative.sentence_break_probability
            >= BOUNDARY_CORROBORATION_THRESHOLD - _SCORE_EPSILON
        ):
            boundary_supporters.append(provenance)
    return BoundarySupport(
        source_punctuation=_source_punctuation(word.lexical_text) == punctuation,
        typed_question=(
            punctuation is TrailingPunctuation.QUESTION and word.question_evidence >= 0.8
        ),
        timing_pause=(word.pause_after_seconds is not None and word.pause_after_seconds >= 1.2),
        exact_mark_provenance=tuple(sorted(set(exact_mark_supporters), key=_provenance_key)),
        boundary_provenance=tuple(sorted(set(boundary_supporters), key=_provenance_key)),
        structural_veto=_structural_veto_applies(word, punctuation),
    )


def _mark_class_admitted(
    word: DecoderWord,
    evidence: _WordEvidence,
    punctuation: TrailingPunctuation,
) -> bool:
    """Require typed mark-class authority independently from boundary evidence."""

    if punctuation is TrailingPunctuation.NONE:
        return True
    if _source_punctuation(word.lexical_text) is punctuation:
        return True
    if (
        punctuation is TrailingPunctuation.QUESTION
        and word.question_evidence >= 0.8
        and word.question_scope_start_index is not None
    ):
        return True
    if punctuation is TrailingPunctuation.COMMA and _weak_contraction_exception(word):
        return True
    for _provenance, proposals in evidence.scorer_proposals:
        if EvidenceRole.EXACT_MARK not in proposals[0].evidence_roles:
            continue
        distribution = _proposal_distribution(proposals)
        selected = distribution[punctuation]
        if (
            selected + _SCORE_EPSILON >= max(distribution.values())
            and selected > (1.0 / len(_PUNCTUATION_ORDER)) + _SCORE_EPSILON
        ):
            return True
    return False


def _requires_sentence_initial_capitalization(text: str) -> bool:
    """Return whether ordinary lowercase is invalid at a sentence start."""

    if not _capitalization_preserves_shape(text):
        return False
    letters = [character for character in _case_surface(text) if character.isalpha()]
    if not letters or letters[0].isupper():
        return False
    # Preserve intentional mixed-case brands and identifiers such as macOS or
    # iPhone rather than manufacturing an invalid casing variant.
    return not any(character.isupper() for character in letters[1:])


def _has_intentional_mixed_case(text: str | None) -> bool:
    if text is None:
        return False
    letters = [character for character in _case_surface(text) if character.isalpha()]
    return bool(
        len(letters) > 1
        and letters[0].islower()
        and any(character.isupper() for character in letters[1:])
    )


def _input_digest(words: tuple[DecoderWord, ...]) -> str:
    payload = [
        {
            "word_index": word.word_index,
            "lexical_text": word.lexical_text,
            "lexical_signature": word.lexical_signature,
            "start_seconds": word.start_seconds,
            "end_seconds": word.end_seconds,
            "pause_after_seconds": word.pause_after_seconds,
            "speaker_id": word.speaker_id,
            "container_index": word.container_index,
            "begins_turn": word.begins_turn,
            "ends_turn": word.ends_turn,
            "incomplete_continuation": word.incomplete_continuation,
            "incomplete_contraction": word.incomplete_contraction,
            "incomplete_trailing_fragment": word.incomplete_trailing_fragment,
            "incomplete_abandoned_fragment": word.incomplete_abandoned_fragment,
            "unsupported_same_speaker_short_continuation": (
                word.unsupported_same_speaker_short_continuation
            ),
            "contraction_repetition_exception": word.contraction_repetition_exception,
            "contraction_correction_exception": word.contraction_correction_exception,
            "contraction_question_tail_exception": word.contraction_question_tail_exception,
            "question_evidence": word.question_evidence,
            "question_scope_start_index": word.question_scope_start_index,
        }
        for word in words
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_punctuation(text: str) -> TrailingPunctuation:
    stripped = text.strip()
    closer_start = len(stripped)
    while closer_start > 0 and stripped[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    body = stripped[:closer_start]
    if not body:
        return TrailingPunctuation.NONE
    value = body[-1]
    try:
        return TrailingPunctuation(value)
    except ValueError:
        return TrailingPunctuation.NONE


def _case_surface(text: str) -> str:
    """Return a word surface with trailing punctuation removed."""

    return _replace_trailing_punctuation(text, TrailingPunctuation.NONE)


def _validate_casing_surface(text: str, *, expected_signature: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("casing_surface must be a non-empty string")
    if _source_punctuation(text) is not TrailingPunctuation.NONE:
        raise ValueError("casing_surface must not contain trailing punctuation")
    if lexical_signature(text) != expected_signature:
        raise ValueError("casing_surface changed lexical identity")


def _replace_trailing_punctuation(
    text: str,
    punctuation: TrailingPunctuation,
) -> str:
    closer_start = len(text)
    while closer_start > 0 and text[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    body = text[:closer_start]
    closers = text[closer_start:]
    while body and body[-1] in _TRAILING_PRESENTATION:
        body = body[:-1]
    return f"{body}{punctuation.value}{closers}"


def _capitalize(text: str) -> str:
    characters = list(text)
    for index, character in enumerate(characters):
        if not character.isalpha():
            continue
        if any(later.isupper() for later in characters[index + 1 :] if later.isalpha()):
            return text
        characters[index] = character.upper()
        capitalized = "".join(characters)
        if not _casing_preserves_exact_unicode_shape(capitalized, text):
            raise ValueError("capitalization changed lexical code-point shape")
        return capitalized
    return text


def _capitalization_preserves_shape(text: str) -> bool:
    try:
        _capitalize(text)
    except ValueError:
        return False
    return True


def _exact_lexical_scaffold(text: str) -> str:
    stripped = text.strip()
    closer_start = len(stripped)
    while closer_start > 0 and stripped[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    body = stripped[:closer_start]
    while body and body[-1] in _TRAILING_PRESENTATION:
        body = body[:-1]
    return f"{body}{stripped[closer_start:]}"


def _casing_preserves_exact_unicode_shape(candidate: str, source: str) -> bool:
    """Allow case changes without accepting normalization or compatibility drift."""

    candidate_scaffold = _exact_lexical_scaffold(candidate)
    source_scaffold = _exact_lexical_scaffold(source)
    return len(candidate_scaffold) == len(source_scaffold) and all(
        candidate_character.casefold() == source_character.casefold()
        for candidate_character, source_character in zip(
            candidate_scaffold,
            source_scaffold,
            strict=True,
        )
    )


def _is_capitalized(text: str) -> bool:
    return next((character.isupper() for character in text if character.isalpha()), False)


def _log_probability(probability: float) -> float:
    return math.log(max(_EPSILON, min(1.0 - _EPSILON, probability)))


def _mean(values: Sequence[float], *, default: float) -> float:
    return math.fsum(values) / len(values) if values else default


def _provenance_key(provenance: ScorerProvenance) -> tuple[str, str, str]:
    return (
        provenance.scorer_id,
        provenance.scorer_version,
        provenance.configuration_digest or "",
    )


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label} must be a stable identifier")


def _validate_probability(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label} must be a finite probability")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{label} must be between zero and one")


def _validate_optional_timing(value: float | None, label: str) -> None:
    if value is not None:
        _validate_nonnegative_finite(value, label)


def _validate_nonnegative_finite(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label} must be a finite non-negative number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite non-negative number")


def _validate_finite(value: float, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label} must be finite")
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
