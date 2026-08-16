# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fail-closed, traceable lexical cleanup plans.

The canonical word record is immutable.  This module can derive a cleaner word
sequence only through a small set of mechanically validated edit classes.  It
does not expose a generic replace operation and therefore cannot make an
opaque free-form rewrite authoritative.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

SEMANTIC_CLEANUP_VERSION = "auralign.semantic-cleanup.v1"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}$")
_FILLERS = frozenset({"ah", "er", "erm", "hm", "hmm", "uh", "um"})
_EMPHASIS_REPETITIONS = frozenset(
    {"absolutely", "always", "many", "much", "never", "no", "really", "so", "very", "way", "yes"}
)
_CORRECTION_MARKERS = (
    ("actually",),
    ("i", "mean"),
    ("no",),
    ("rather",),
    ("sorry",),
)
_DISPLAY_RULES: Mapping[str, Mapping[tuple[str, ...], tuple[str, ...]]] = {
    "small-cardinal-v1": {
        ("zero",): ("0",),
        ("one",): ("1",),
        ("two",): ("2",),
        ("three",): ("3",),
        ("four",): ("4",),
        ("five",): ("5",),
        ("six",): ("6",),
        ("seven",): ("7",),
        ("eight",): ("8",),
        ("nine",): ("9",),
        ("ten",): ("10",),
        ("eleven",): ("11",),
        ("twelve",): ("12",),
    }
}
_GRAMMAR_RULES: Mapping[str, Mapping[tuple[str, ...], tuple[str, ...]]] = {
    "unambiguous-contraction-spelling-v1": {
        ("cant",): ("can't",),
        ("couldnt",): ("couldn't",),
        ("didnt",): ("didn't",),
        ("doesnt",): ("doesn't",),
        ("dont",): ("don't",),
        ("im",): ("i'm",),
        ("isnt",): ("isn't",),
        ("wasnt",): ("wasn't",),
        ("werent",): ("weren't",),
        ("wont",): ("won't",),
        ("wouldnt",): ("wouldn't",),
    }
}


class LexicalEditClass(StrEnum):
    """The complete set of lexical operations understood by the clean lane."""

    FILLER_REMOVAL = "filler_removal"
    STUTTER_COLLAPSE = "stutter_collapse"
    ABANDONED_FALSE_START_REMOVAL = "abandoned_false_start_removal"
    EXPLICIT_SELF_CORRECTION = "explicit_self_correction_resolution"
    DISPLAY_NORMALIZATION = "display_normalization"
    NARROW_GRAMMATICAL_CORRECTION = "narrow_grammatical_correction"


class SemanticAbstentionReason(StrEnum):
    """Allowlisted reasons why a proposal backend emitted no lexical edit."""

    NO_SUPPORTED_EDIT = "no_supported_edit"
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS = "ambiguous"
    UNSAFE_SEMANTIC_CHANGE = "unsafe_semantic_change"
    MALFORMED_BACKEND_OUTPUT = "malformed_backend_output"
    BACKEND_UNAVAILABLE = "backend_unavailable"


class ProposalDisposition(StrEnum):
    """Deterministic reconciliation outcome for one validated proposal."""

    SELECTED = "selected"
    POLICY_DISABLED = "policy_disabled"
    BELOW_THRESHOLD = "below_threshold"
    COMMON_SAFETY_FAILED = "common_safety_failed"
    CLASS_SAFETY_FAILED = "class_safety_failed"
    CONTEXT_SAFETY_FAILED = "context_safety_failed"
    OVERLAP_CONFLICT = "overlap_conflict"


_MINIMUM_CLASS_THRESHOLDS: Mapping[LexicalEditClass, float] = {
    LexicalEditClass.FILLER_REMOVAL: 0.98,
    LexicalEditClass.STUTTER_COLLAPSE: 0.995,
    LexicalEditClass.ABANDONED_FALSE_START_REMOVAL: 0.999,
    LexicalEditClass.EXPLICIT_SELF_CORRECTION: 0.999,
    LexicalEditClass.DISPLAY_NORMALIZATION: 1.0,
    LexicalEditClass.NARROW_GRAMMATICAL_CORRECTION: 1.0,
}


@dataclass(frozen=True, slots=True)
class CanonicalWord:
    """One immutable canonical word and its audit-relevant evidence."""

    word_index: int
    surface: str
    speaker_id: str | None
    start_seconds: float | None
    end_seconds: float | None
    container_index: int | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.word_index, bool)
            or not isinstance(self.word_index, int)
            or self.word_index < 0
        ):
            raise ValueError("word_index must be a non-negative integer")
        if not isinstance(self.surface, str) or not self.surface.strip():
            raise ValueError("canonical surface must be a non-empty string")
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
        _validate_timing(self.start_seconds, "start_seconds")
        _validate_timing(self.end_seconds, "end_seconds")
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("word timing must provide both start and end")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("word timing end precedes start")


@dataclass(frozen=True, slots=True)
class EditProvenance:
    """Stable producer identity and evidence binding for a proposal."""

    producer_id: str
    producer_version: str
    evidence_digest: str

    def __post_init__(self) -> None:
        _validate_identifier(self.producer_id, "producer_id")
        _validate_identifier(self.producer_version, "producer_version")
        _validate_digest(self.evidence_digest, "evidence_digest")


@dataclass(frozen=True, slots=True)
class SemanticSafetyChecks:
    """Explicit safety assertions required before any lexical edit is eligible."""

    preserves_meaning: bool
    preserves_uncertainty: bool
    preserves_modality: bool
    preserves_negation: bool
    preserves_attribution: bool
    preserves_quantities_names_dates: bool
    preserves_tense: bool
    preserves_commitment: bool
    preserves_discourse_meaning: bool
    necessary_or_useful: bool
    unambiguous: bool
    reversible: bool
    isolated_filler: bool = False
    accidental_repetition: bool = False
    abandoned_surface_evidence: bool = False
    explicit_correction_evidence: bool = False
    deterministic_display_equivalence: bool = False
    deterministic_grammar_rule: bool = False

    def __post_init__(self) -> None:
        if any(
            not isinstance(getattr(self, field_name), bool)
            for field_name in self.__dataclass_fields__
        ):
            raise ValueError("semantic safety checks must be explicit booleans")

    def common_checks_pass(self) -> bool:
        """Return whether the edit satisfies every cross-class safety invariant."""

        return all(
            (
                self.preserves_meaning,
                self.preserves_uncertainty,
                self.preserves_modality,
                self.preserves_negation,
                self.preserves_attribution,
                self.preserves_quantities_names_dates,
                self.preserves_tense,
                self.preserves_commitment,
                self.preserves_discourse_meaning,
                self.necessary_or_useful,
                self.unambiguous,
                self.reversible,
            )
        )


@dataclass(frozen=True, slots=True)
class LexicalEditProposal:
    """An exact-source-bound proposal for one enumerated lexical operation."""

    proposal_id: str
    edit_class: LexicalEditClass
    source_start: int
    source_end: int
    source_indices: tuple[int, ...]
    source_surfaces: tuple[str, ...]
    source_digest: str
    source_lexical_sequence: tuple[str, ...]
    result_surfaces: tuple[str, ...]
    result_lexical_sequence: tuple[str, ...]
    speaker_id: str | None
    start_seconds: float | None
    end_seconds: float | None
    provenance: EditProvenance
    semantic_safety_confidence: float
    safety_checks: SemanticSafetyChecks
    rule_id: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, "proposal_id")
        if not isinstance(self.edit_class, LexicalEditClass):
            raise ValueError("edit_class is not allowlisted")
        if self.source_end <= self.source_start:
            raise ValueError("source span must be non-empty")
        expected_indices = tuple(range(self.source_start, self.source_end))
        if self.source_indices != expected_indices:
            raise ValueError("source_indices must exactly cover the contiguous source span")
        if not self.source_surfaces or len(self.source_surfaces) != len(self.source_indices):
            raise ValueError("source surfaces must exactly cover source indices")
        if any(
            not isinstance(surface, str) or not surface.strip() for surface in self.source_surfaces
        ):
            raise ValueError("source surfaces must be non-empty strings")
        if any(
            not isinstance(surface, str) or not surface.strip() for surface in self.result_surfaces
        ):
            raise ValueError("result surfaces must be non-empty strings")
        if self.source_lexical_sequence != tuple(
            _lexical_surface(value) for value in self.source_surfaces
        ):
            raise ValueError("source lexical sequence does not bind source surfaces")
        if self.result_lexical_sequence != tuple(
            _lexical_surface(value) for value in self.result_surfaces
        ):
            raise ValueError("result lexical sequence does not bind result surfaces")
        _validate_digest(self.source_digest, "source_digest")
        _validate_timing(self.start_seconds, "start_seconds")
        _validate_timing(self.end_seconds, "end_seconds")
        if (self.start_seconds is None) != (self.end_seconds is None):
            raise ValueError("proposal timing must provide both start and end")
        if (
            self.start_seconds is not None
            and self.end_seconds is not None
            and self.end_seconds < self.start_seconds
        ):
            raise ValueError("proposal timing end precedes start")
        _validate_probability(self.semantic_safety_confidence)
        if not isinstance(self.provenance, EditProvenance):
            raise ValueError("proposal provenance is invalid")
        if not isinstance(self.safety_checks, SemanticSafetyChecks):
            raise ValueError("proposal safety checks are invalid")
        if self.rule_id is not None:
            _validate_identifier(self.rule_id, "rule_id")
        _validate_operation_shape(self)

    @classmethod
    def bind(
        cls,
        *,
        proposal_id: str,
        edit_class: LexicalEditClass,
        canonical_words: Sequence[CanonicalWord],
        source_start: int,
        source_end: int,
        result_surfaces: Sequence[str],
        provenance: EditProvenance,
        semantic_safety_confidence: float,
        safety_checks: SemanticSafetyChecks,
        rule_id: str | None = None,
    ) -> LexicalEditProposal:
        """Create a proposal whose surfaces, speaker, timing, and digest are bound."""

        word_by_index = _canonical_index(canonical_words)
        try:
            selected = tuple(word_by_index[index] for index in range(source_start, source_end))
        except KeyError as exc:
            raise ValueError("source span is not present in canonical evidence") from exc
        speakers = {word.speaker_id for word in selected}
        if len(speakers) != 1:
            raise ValueError("a lexical edit cannot cross speaker ownership")
        containers = {word.container_index for word in selected}
        if len(containers) != 1:
            raise ValueError("a lexical edit cannot cross canonical container boundaries")
        surfaces = tuple(word.surface for word in selected)
        results = tuple(result_surfaces)
        return cls(
            proposal_id=proposal_id,
            edit_class=edit_class,
            source_start=source_start,
            source_end=source_end,
            source_indices=tuple(word.word_index for word in selected),
            source_surfaces=surfaces,
            source_digest=canonical_span_digest(selected),
            source_lexical_sequence=tuple(_lexical_surface(value) for value in surfaces),
            result_surfaces=results,
            result_lexical_sequence=tuple(_lexical_surface(value) for value in results),
            speaker_id=selected[0].speaker_id,
            start_seconds=selected[0].start_seconds,
            end_seconds=selected[-1].end_seconds,
            provenance=provenance,
            semantic_safety_confidence=semantic_safety_confidence,
            safety_checks=safety_checks,
            rule_id=rule_id,
        )

    def validate_against(self, canonical_words: Sequence[CanonicalWord]) -> None:
        """Fail if any bound canonical evidence changed after proposal creation."""

        rebound = LexicalEditProposal.bind(
            proposal_id=self.proposal_id,
            edit_class=self.edit_class,
            canonical_words=canonical_words,
            source_start=self.source_start,
            source_end=self.source_end,
            result_surfaces=self.result_surfaces,
            provenance=self.provenance,
            semantic_safety_confidence=self.semantic_safety_confidence,
            safety_checks=self.safety_checks,
            rule_id=self.rule_id,
        )
        if rebound != self:
            raise ValueError("lexical proposal does not bind the current canonical evidence")


@dataclass(frozen=True, slots=True)
class SemanticProposalAbstention:
    """Source-bound receipt proving that a backend intentionally abstained."""

    abstention_id: str
    reason: SemanticAbstentionReason
    canonical_digest: str
    provenance: EditProvenance
    detail_digest: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.abstention_id, "abstention_id")
        if not isinstance(self.reason, SemanticAbstentionReason):
            raise ValueError("semantic abstention reason is not allowlisted")
        _validate_digest(self.canonical_digest, "canonical_digest")
        if not isinstance(self.provenance, EditProvenance):
            raise ValueError("semantic abstention provenance is invalid")
        if self.detail_digest is not None:
            _validate_digest(self.detail_digest, "detail_digest")


@dataclass(frozen=True, slots=True)
class SemanticProposalBatch:
    """One backend's immutable proposals or one explicit abstention.

    Backends never hand the reconciler free text. They must first construct
    validated :class:`LexicalEditProposal` values, or record why they emitted
    none. A batch cannot mix candidates with an abstention.
    """

    batch_id: str
    canonical_digest: str
    provenance: EditProvenance
    proposals: tuple[LexicalEditProposal, ...] = ()
    abstention: SemanticProposalAbstention | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.batch_id, "batch_id")
        _validate_digest(self.canonical_digest, "canonical_digest")
        if not isinstance(self.provenance, EditProvenance):
            raise ValueError("semantic proposal batch provenance is invalid")
        if not isinstance(self.proposals, tuple):
            raise ValueError("semantic proposals must be an immutable tuple")
        if bool(self.proposals) == (self.abstention is not None):
            raise ValueError("semantic batch must contain proposals or one abstention")
        if self.abstention is not None:
            if not isinstance(self.abstention, SemanticProposalAbstention):
                raise ValueError("semantic batch abstention is invalid")
            if self.abstention.canonical_digest != self.canonical_digest:
                raise ValueError("semantic abstention canonical digest does not bind batch")
            if self.abstention.provenance != self.provenance:
                raise ValueError("semantic abstention provenance does not bind batch")
        proposal_ids: set[str] = set()
        for proposal in self.proposals:
            if not isinstance(proposal, LexicalEditProposal):
                raise ValueError("semantic batch contains an invalid proposal")
            if proposal.provenance != self.provenance:
                raise ValueError("semantic proposal provenance does not bind batch")
            if proposal.proposal_id in proposal_ids:
                raise ValueError("semantic batch proposal IDs must be unique")
            proposal_ids.add(proposal.proposal_id)

    @classmethod
    def from_proposals(
        cls,
        *,
        batch_id: str,
        canonical_words: Sequence[CanonicalWord],
        provenance: EditProvenance,
        proposals: Sequence[LexicalEditProposal],
    ) -> SemanticProposalBatch:
        """Bind a non-empty typed proposal batch to canonical evidence."""

        proposal_tuple = tuple(proposals)
        if not proposal_tuple:
            raise ValueError("a proposal batch cannot be empty; emit an abstention")
        batch = cls(
            batch_id=batch_id,
            canonical_digest=canonical_evidence_digest(canonical_words),
            provenance=provenance,
            proposals=proposal_tuple,
        )
        batch.validate_against(canonical_words)
        return batch

    @classmethod
    def abstain(
        cls,
        *,
        batch_id: str,
        canonical_words: Sequence[CanonicalWord],
        provenance: EditProvenance,
        reason: SemanticAbstentionReason,
        detail_digest: str | None = None,
    ) -> SemanticProposalBatch:
        """Create an explicit canonical-bound no-proposal receipt."""

        digest = canonical_evidence_digest(canonical_words)
        abstention = SemanticProposalAbstention(
            abstention_id=f"{batch_id}.abstention",
            reason=reason,
            canonical_digest=digest,
            provenance=provenance,
            detail_digest=detail_digest,
        )
        return cls(
            batch_id=batch_id,
            canonical_digest=digest,
            provenance=provenance,
            abstention=abstention,
        )

    def validate_against(self, canonical_words: Sequence[CanonicalWord]) -> None:
        """Fail closed if the batch or any proposal targets changed evidence."""

        if self.canonical_digest != canonical_evidence_digest(canonical_words):
            raise ValueError("semantic proposal batch canonical evidence changed")
        for proposal in self.proposals:
            proposal.validate_against(canonical_words)


@dataclass(frozen=True, slots=True)
class LexicalCleanupPolicy:
    """Thresholds and explicit class enablement for deterministic reconciliation."""

    thresholds: tuple[tuple[LexicalEditClass, float], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.thresholds, tuple) or any(
            not isinstance(entry, tuple) or len(entry) != 2 for entry in self.thresholds
        ):
            raise ValueError("policy thresholds must be an immutable tuple of pairs")
        classes = [edit_class for edit_class, _ in self.thresholds]
        if len(classes) != len(set(classes)):
            raise ValueError("policy contains duplicate edit classes")
        for edit_class, threshold in self.thresholds:
            if not isinstance(edit_class, LexicalEditClass):
                raise ValueError("policy edit class is not allowlisted")
            _validate_probability(threshold)
            if threshold < _MINIMUM_CLASS_THRESHOLDS[edit_class]:
                raise ValueError("policy threshold is below the fail-closed class minimum")

    @classmethod
    def safe_default(cls) -> LexicalCleanupPolicy:
        """Enable only the two safest removal classes in normal production."""

        return cls(
            thresholds=(
                (LexicalEditClass.FILLER_REMOVAL, 0.98),
                (LexicalEditClass.STUTTER_COLLAPSE, 0.995),
            )
        )

    def threshold_for(self, edit_class: LexicalEditClass) -> float | None:
        return dict(self.thresholds).get(edit_class)


@dataclass(frozen=True, slots=True)
class SourceToCleanMapping:
    """Reversible mapping from one canonical index to derived clean positions."""

    source_index: int
    source_surface: str
    clean_indices: tuple[int, ...]
    selected_proposal_id: str | None


@dataclass(frozen=True, slots=True)
class SemanticCleanupPlan:
    """A deterministic derived transcript with full canonical traceability."""

    canonical_digest: str
    policy_digest: str
    selected_edits: tuple[LexicalEditProposal, ...]
    rejected_proposal_ids: tuple[str, ...]
    clean_surfaces: tuple[str, ...]
    clean_to_source_indices: tuple[tuple[int, ...], ...]
    source_to_clean: tuple[SourceToCleanMapping, ...]
    version: str = SEMANTIC_CLEANUP_VERSION

    def __post_init__(self) -> None:
        _validate_digest(self.canonical_digest, "canonical_digest")
        _validate_digest(self.policy_digest, "policy_digest")
        if len(self.clean_surfaces) != len(self.clean_to_source_indices):
            raise ValueError("every clean surface must carry source traceability")
        if any(not indices for indices in self.clean_to_source_indices):
            raise ValueError("clean words cannot exist without canonical source indices")


@dataclass(frozen=True, slots=True)
class ProposalReconciliationReceipt:
    """Auditable disposition for one validated backend proposal."""

    proposal_id: str
    edit_class: LexicalEditClass
    source_digest: str
    provenance: EditProvenance
    semantic_safety_confidence: float
    required_threshold: float | None
    disposition: ProposalDisposition

    def __post_init__(self) -> None:
        _validate_identifier(self.proposal_id, "proposal_id")
        if not isinstance(self.edit_class, LexicalEditClass):
            raise ValueError("receipt edit class is not allowlisted")
        _validate_digest(self.source_digest, "source_digest")
        if not isinstance(self.provenance, EditProvenance):
            raise ValueError("receipt provenance is invalid")
        _validate_probability(self.semantic_safety_confidence)
        if self.required_threshold is not None:
            _validate_probability(self.required_threshold)
        if not isinstance(self.disposition, ProposalDisposition):
            raise ValueError("proposal disposition is not allowlisted")


@dataclass(frozen=True, slots=True)
class SemanticReconciliationResult:
    """A cleanup plan plus complete backend proposal and abstention receipts."""

    canonical_digest: str
    policy_digest: str
    batch_ids: tuple[str, ...]
    plan: SemanticCleanupPlan
    proposal_receipts: tuple[ProposalReconciliationReceipt, ...]
    abstention_receipts: tuple[SemanticProposalAbstention, ...]
    version: str = SEMANTIC_CLEANUP_VERSION

    def __post_init__(self) -> None:
        _validate_digest(self.canonical_digest, "canonical_digest")
        _validate_digest(self.policy_digest, "policy_digest")
        if not isinstance(self.batch_ids, tuple):
            raise ValueError("semantic result batch IDs must be an immutable tuple")
        if not isinstance(self.plan, SemanticCleanupPlan):
            raise ValueError("semantic result plan is invalid")
        if not isinstance(self.proposal_receipts, tuple):
            raise ValueError("semantic proposal receipts must be an immutable tuple")
        if not isinstance(self.abstention_receipts, tuple):
            raise ValueError("semantic abstention receipts must be an immutable tuple")
        if self.plan.canonical_digest != self.canonical_digest:
            raise ValueError("semantic result canonical digest does not bind plan")
        if self.plan.policy_digest != self.policy_digest:
            raise ValueError("semantic result policy digest does not bind plan")
        if tuple(sorted(set(self.batch_ids))) != self.batch_ids:
            raise ValueError("semantic result batch IDs must be unique and sorted")
        for batch_id in self.batch_ids:
            _validate_identifier(batch_id, "batch_id")
        if any(
            not isinstance(receipt, ProposalReconciliationReceipt)
            for receipt in self.proposal_receipts
        ):
            raise ValueError("semantic result contains an invalid proposal receipt")
        proposal_ids = tuple(receipt.proposal_id for receipt in self.proposal_receipts)
        if tuple(sorted(set(proposal_ids))) != proposal_ids:
            raise ValueError("semantic proposal receipts must be sorted")
        if any(
            not isinstance(receipt, SemanticProposalAbstention)
            for receipt in self.abstention_receipts
        ):
            raise ValueError("semantic result contains an invalid abstention receipt")
        abstention_ids = tuple(receipt.abstention_id for receipt in self.abstention_receipts)
        if tuple(sorted(set(abstention_ids))) != abstention_ids:
            raise ValueError("semantic abstention receipts must be sorted")
        if any(
            receipt.canonical_digest != self.canonical_digest
            for receipt in self.abstention_receipts
        ):
            raise ValueError("semantic abstention receipt canonical evidence changed")
        receipt_by_id = {receipt.proposal_id: receipt for receipt in self.proposal_receipts}
        selected_by_id = {proposal.proposal_id: proposal for proposal in self.plan.selected_edits}
        expected_proposal_ids = set(selected_by_id) | set(self.plan.rejected_proposal_ids)
        if set(receipt_by_id) != expected_proposal_ids:
            raise ValueError("semantic proposal receipts do not exactly cover the plan")
        for proposal_id, proposal in selected_by_id.items():
            receipt = receipt_by_id[proposal_id]
            if (
                receipt.disposition is not ProposalDisposition.SELECTED
                or receipt.edit_class is not proposal.edit_class
                or receipt.source_digest != proposal.source_digest
                or receipt.provenance != proposal.provenance
                or receipt.semantic_safety_confidence != proposal.semantic_safety_confidence
            ):
                raise ValueError("selected semantic proposal receipt does not bind its plan edit")
        if any(
            receipt_by_id[proposal_id].disposition is ProposalDisposition.SELECTED
            for proposal_id in self.plan.rejected_proposal_ids
        ):
            raise ValueError("a rejected semantic proposal cannot have selected disposition")


def reconcile_lexical_edits(
    canonical_words: Sequence[CanonicalWord],
    proposals: Sequence[LexicalEditProposal],
    *,
    policy: LexicalCleanupPolicy | None = None,
) -> SemanticCleanupPlan:
    """Select a maximum-confidence compatible set using weighted interval scheduling."""

    words = tuple(canonical_words)
    _canonical_index(words)
    active_policy = policy or LexicalCleanupPolicy.safe_default()
    proposal_ids: set[str] = set()
    eligible: list[LexicalEditProposal] = []
    for proposal in proposals:
        if proposal.proposal_id in proposal_ids:
            raise ValueError("proposal_id values must be unique")
        proposal_ids.add(proposal.proposal_id)
        proposal.validate_against(words)
        if _proposal_disposition(proposal, words, active_policy) is None:
            eligible.append(proposal)

    selected = _weighted_compatible_edits(eligible)
    if _selection_erases_container(words, selected):
        raise ValueError("lexical cleanup cannot erase a complete canonical container")
    selected_ids = {proposal.proposal_id for proposal in selected}
    rejected = tuple(sorted(proposal_ids - selected_ids))
    clean, clean_to_source, source_to_clean = _render_plan(words, selected)
    return SemanticCleanupPlan(
        canonical_digest=canonical_evidence_digest(words),
        policy_digest=_policy_digest(active_policy),
        selected_edits=selected,
        rejected_proposal_ids=rejected,
        clean_surfaces=clean,
        clean_to_source_indices=clean_to_source,
        source_to_clean=source_to_clean,
    )


def reconcile_semantic_proposal_batches(
    canonical_words: Sequence[CanonicalWord],
    batches: Sequence[SemanticProposalBatch],
    *,
    policy: LexicalCleanupPolicy | None = None,
) -> SemanticReconciliationResult:
    """Validate typed backend batches and return a plan with complete receipts."""

    words = tuple(canonical_words)
    _canonical_index(words)
    active_policy = policy or LexicalCleanupPolicy.safe_default()
    batch_ids: set[str] = set()
    proposal_ids: set[str] = set()
    proposals: list[LexicalEditProposal] = []
    abstentions: list[SemanticProposalAbstention] = []
    for batch in batches:
        if not isinstance(batch, SemanticProposalBatch):
            raise ValueError("semantic reconciliation received an invalid batch")
        if batch.batch_id in batch_ids:
            raise ValueError("semantic proposal batch IDs must be unique")
        batch_ids.add(batch.batch_id)
        batch.validate_against(words)
        if batch.abstention is not None:
            abstentions.append(batch.abstention)
        for proposal in batch.proposals:
            if proposal.proposal_id in proposal_ids:
                raise ValueError("semantic proposal IDs must be globally unique")
            proposal_ids.add(proposal.proposal_id)
            proposals.append(proposal)

    plan = reconcile_lexical_edits(words, proposals, policy=active_policy)
    selected_ids = {proposal.proposal_id for proposal in plan.selected_edits}
    receipts: list[ProposalReconciliationReceipt] = []
    for proposal in sorted(proposals, key=lambda item: item.proposal_id):
        rejection = _proposal_disposition(proposal, words, active_policy)
        if proposal.proposal_id in selected_ids:
            disposition = ProposalDisposition.SELECTED
        elif rejection is None:
            disposition = ProposalDisposition.OVERLAP_CONFLICT
        else:
            disposition = rejection
        receipts.append(
            ProposalReconciliationReceipt(
                proposal_id=proposal.proposal_id,
                edit_class=proposal.edit_class,
                source_digest=proposal.source_digest,
                provenance=proposal.provenance,
                semantic_safety_confidence=proposal.semantic_safety_confidence,
                required_threshold=active_policy.threshold_for(proposal.edit_class),
                disposition=disposition,
            )
        )
    return SemanticReconciliationResult(
        canonical_digest=canonical_evidence_digest(words),
        policy_digest=lexical_cleanup_policy_sha256(active_policy),
        batch_ids=tuple(sorted(batch_ids)),
        plan=plan,
        proposal_receipts=tuple(receipts),
        abstention_receipts=tuple(sorted(abstentions, key=lambda item: item.abstention_id)),
    )


def propose_safe_speech_artifact_edits(
    canonical_words: Sequence[CanonicalWord],
) -> tuple[LexicalEditProposal, ...]:
    """Generate only mechanically strong filler and stutter candidates.

    This deterministic scorer intentionally does not propose false-start,
    self-correction, display-normalization, or grammar edits. Those classes
    require explicit evidence from a separately audited scorer and remain
    disabled by the safe default policy.
    """

    words = tuple(canonical_words)
    _canonical_index(words)
    proposals: list[LexicalEditProposal] = []
    common = {
        "preserves_meaning": True,
        "preserves_uncertainty": True,
        "preserves_modality": True,
        "preserves_negation": True,
        "preserves_attribution": True,
        "preserves_quantities_names_dates": True,
        "preserves_tense": True,
        "preserves_commitment": True,
        "preserves_discourse_meaning": True,
        "necessary_or_useful": True,
        "unambiguous": True,
        "reversible": True,
    }
    lexical = tuple(_lexical_surface(word.surface) for word in words)
    for position, word in enumerate(words):
        if lexical[position] not in _FILLERS:
            continue
        previous_core = lexical[position - 1] if position > 0 else None
        following_core = lexical[position + 1] if position + 1 < len(words) else None
        if (
            following_core is None
            or previous_core in _FILLERS
            or following_core in _FILLERS
            or _has_terminal_punctuation(word.surface)
        ):
            continue
        proposals.append(
            LexicalEditProposal.bind(
                proposal_id=f"deterministic.filler.{word.word_index}",
                edit_class=LexicalEditClass.FILLER_REMOVAL,
                canonical_words=words,
                source_start=word.word_index,
                source_end=word.word_index + 1,
                result_surfaces=(),
                provenance=EditProvenance(
                    "auralign.safe-speech-artifacts",
                    "1.0",
                    canonical_span_digest((word,)),
                ),
                semantic_safety_confidence=0.99,
                safety_checks=SemanticSafetyChecks(
                    **common,
                    isolated_filler=True,
                ),
            )
        )

    position = 0
    while position + 1 < len(words):
        start = position
        core = lexical[start]
        end = start + 1
        while end < len(words) and lexical[end] == core:
            if (
                words[end].speaker_id != words[start].speaker_id
                or words[end].container_index != words[start].container_index
            ):
                break
            end += 1
        if end - start >= 2 and core not in _EMPHASIS_REPETITIONS:
            span = words[start:end]
            abandoned = any(_has_abandonment_marker(word.surface) for word in span[:-1])
            if abandoned:
                proposals.append(
                    LexicalEditProposal.bind(
                        proposal_id=f"deterministic.stutter.{span[0].word_index}.{span[-1].word_index}",
                        edit_class=LexicalEditClass.STUTTER_COLLAPSE,
                        canonical_words=words,
                        source_start=span[0].word_index,
                        source_end=span[-1].word_index + 1,
                        result_surfaces=(span[-1].surface,),
                        provenance=EditProvenance(
                            "auralign.safe-speech-artifacts",
                            "1.0",
                            canonical_span_digest(span),
                        ),
                        semantic_safety_confidence=0.999,
                        safety_checks=SemanticSafetyChecks(
                            **common,
                            accidental_repetition=True,
                        ),
                    )
                )
        position = max(end, position + 1)
    return tuple(proposals)


def canonical_evidence_digest(words: Sequence[CanonicalWord]) -> str:
    """Hash the complete ordered canonical evidence used by this module."""

    records = [_word_record(word) for word in words]
    return _json_digest({"words": records})


def canonical_span_digest(words: Sequence[CanonicalWord]) -> str:
    """Hash one exact ordered source span, including speaker and timing."""

    if not words:
        raise ValueError("canonical source span cannot be empty")
    return _json_digest({"words": [_word_record(word) for word in words]})


def serialize_semantic_cleanup_plan(plan: SemanticCleanupPlan) -> bytes:
    """Return canonical JSON bytes suitable for a manifest artifact."""

    payload = {
        "canonical_digest": plan.canonical_digest,
        "clean_surfaces": list(plan.clean_surfaces),
        "clean_to_source_indices": [list(indices) for indices in plan.clean_to_source_indices],
        "policy_digest": plan.policy_digest,
        "rejected_proposal_ids": list(plan.rejected_proposal_ids),
        "selected_edits": [_proposal_record(proposal) for proposal in plan.selected_edits],
        "source_to_clean": [
            {
                "clean_indices": list(mapping.clean_indices),
                "selected_proposal_id": mapping.selected_proposal_id,
                "source_index": mapping.source_index,
                "source_surface": mapping.source_surface,
            }
            for mapping in plan.source_to_clean
        ],
        "version": plan.version,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def semantic_cleanup_plan_sha256(plan: SemanticCleanupPlan) -> str:
    """Return the SHA-256 of the canonical serialized plan."""

    return hashlib.sha256(serialize_semantic_cleanup_plan(plan)).hexdigest()


def serialize_semantic_reconciliation_result(
    result: SemanticReconciliationResult,
) -> bytes:
    """Serialize a plan and all proposal/abstention receipts as canonical JSON."""

    plan_payload: object = json.loads(serialize_semantic_cleanup_plan(result.plan))
    payload = {
        "abstention_receipts": [
            {
                "abstention_id": receipt.abstention_id,
                "canonical_digest": receipt.canonical_digest,
                "detail_digest": receipt.detail_digest,
                "provenance": _provenance_record(receipt.provenance),
                "reason": receipt.reason.value,
            }
            for receipt in result.abstention_receipts
        ],
        "batch_ids": list(result.batch_ids),
        "canonical_digest": result.canonical_digest,
        "plan": plan_payload,
        "policy_digest": result.policy_digest,
        "proposal_receipts": [
            {
                "disposition": receipt.disposition.value,
                "edit_class": receipt.edit_class.value,
                "proposal_id": receipt.proposal_id,
                "provenance": _provenance_record(receipt.provenance),
                "required_threshold": receipt.required_threshold,
                "semantic_safety_confidence": receipt.semantic_safety_confidence,
                "source_digest": receipt.source_digest,
            }
            for receipt in result.proposal_receipts
        ],
        "version": result.version,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def semantic_reconciliation_result_sha256(result: SemanticReconciliationResult) -> str:
    """Return the digest of a canonical semantic reconciliation receipt."""

    return hashlib.sha256(serialize_semantic_reconciliation_result(result)).hexdigest()


def deserialize_semantic_reconciliation_result(
    data: bytes,
) -> SemanticReconciliationResult:
    """Parse canonical reconciliation bytes into the complete typed receipt."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("semantic reconciliation receipt must be valid UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    try:
        raw: Any = json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise ValueError("semantic reconciliation receipt must be valid JSON") from exc
    payload = _require_object(raw, "semantic reconciliation receipt")
    _require_exact_keys(
        payload,
        {
            "abstention_receipts",
            "batch_ids",
            "canonical_digest",
            "plan",
            "policy_digest",
            "proposal_receipts",
            "version",
        },
        "semantic reconciliation receipt",
    )
    plan_payload = _require_object(payload["plan"], "semantic reconciliation plan")
    plan_bytes = json.dumps(
        plan_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    result = SemanticReconciliationResult(
        canonical_digest=_require_string(payload["canonical_digest"], "canonical_digest"),
        policy_digest=_require_string(payload["policy_digest"], "policy_digest"),
        batch_ids=_require_string_tuple(payload["batch_ids"], "batch_ids"),
        plan=deserialize_semantic_cleanup_plan(plan_bytes),
        proposal_receipts=tuple(
            _proposal_receipt_from_record(item)
            for item in _require_list(payload["proposal_receipts"], "proposal_receipts")
        ),
        abstention_receipts=tuple(
            _abstention_from_record(item)
            for item in _require_list(payload["abstention_receipts"], "abstention_receipts")
        ),
        version=_require_string(payload["version"], "version"),
    )
    if result.version != SEMANTIC_CLEANUP_VERSION:
        raise ValueError("semantic reconciliation receipt version is unsupported")
    if serialize_semantic_reconciliation_result(result) != data:
        raise ValueError("semantic reconciliation receipt is not canonical JSON")
    return result


def lexical_cleanup_policy_sha256(policy: LexicalCleanupPolicy) -> str:
    """Return the canonical digest used to bind one lexical-cleanup policy."""

    if not isinstance(policy, LexicalCleanupPolicy):
        raise ValueError("policy must be a LexicalCleanupPolicy")
    return _policy_digest(policy)


def deserialize_semantic_cleanup_plan(data: bytes) -> SemanticCleanupPlan:
    """Parse one canonical plan artifact into the validated typed contract."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("semantic cleanup plan must be valid UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    try:
        raw: Any = json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise ValueError("semantic cleanup plan must be valid JSON") from exc
    payload = _require_object(raw, "semantic cleanup plan")
    _require_exact_keys(
        payload,
        {
            "canonical_digest",
            "clean_surfaces",
            "clean_to_source_indices",
            "policy_digest",
            "rejected_proposal_ids",
            "selected_edits",
            "source_to_clean",
            "version",
        },
        "semantic cleanup plan",
    )
    selected = tuple(
        _proposal_from_record(item)
        for item in _require_list(payload["selected_edits"], "selected_edits")
    )
    mappings = tuple(
        _mapping_from_record(item)
        for item in _require_list(payload["source_to_clean"], "source_to_clean")
    )
    plan = SemanticCleanupPlan(
        canonical_digest=_require_string(payload["canonical_digest"], "canonical_digest"),
        policy_digest=_require_string(payload["policy_digest"], "policy_digest"),
        selected_edits=selected,
        rejected_proposal_ids=_require_string_tuple(
            payload["rejected_proposal_ids"],
            "rejected_proposal_ids",
        ),
        clean_surfaces=_require_string_tuple(
            payload["clean_surfaces"],
            "clean_surfaces",
        ),
        clean_to_source_indices=tuple(
            _require_integer_tuple(item, "clean_to_source_indices item")
            for item in _require_list(
                payload["clean_to_source_indices"],
                "clean_to_source_indices",
            )
        ),
        source_to_clean=mappings,
        version=_require_string(payload["version"], "version"),
    )
    if plan.version != SEMANTIC_CLEANUP_VERSION:
        raise ValueError("semantic cleanup plan version is unsupported")
    if serialize_semantic_cleanup_plan(plan) != data:
        raise ValueError("semantic cleanup plan is not canonical JSON")
    return plan


def validate_semantic_cleanup_plan(
    plan: SemanticCleanupPlan,
    canonical_words: Sequence[CanonicalWord],
    *,
    policy: LexicalCleanupPolicy,
) -> None:
    """Revalidate a serialized plan against canonical evidence and policy."""

    words = tuple(canonical_words)
    _canonical_index(words)
    if plan.canonical_digest != canonical_evidence_digest(words):
        raise ValueError("semantic cleanup plan canonical evidence digest changed")
    if plan.policy_digest != lexical_cleanup_policy_sha256(policy):
        raise ValueError("semantic cleanup plan policy digest changed")
    selected_ids = tuple(proposal.proposal_id for proposal in plan.selected_edits)
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("semantic cleanup plan selected proposal IDs are not unique")
    if tuple(sorted(set(plan.rejected_proposal_ids))) != plan.rejected_proposal_ids:
        raise ValueError("semantic cleanup plan rejected proposal IDs are not canonical")
    for proposal_id in plan.rejected_proposal_ids:
        _validate_identifier(proposal_id, "rejected_proposal_id")
    if set(selected_ids) & set(plan.rejected_proposal_ids):
        raise ValueError("semantic cleanup proposal cannot be selected and rejected")
    if policy == LexicalCleanupPolicy.safe_default():
        expected = reconcile_lexical_edits(
            words,
            propose_safe_speech_artifact_edits(words),
            policy=policy,
        )
        if expected != plan:
            raise ValueError(
                "semantic cleanup plan does not match deterministic production proposals"
            )
        return
    if not policy.thresholds:
        expected = reconcile_lexical_edits(words, (), policy=policy)
        if expected != plan:
            raise ValueError("strict semantic cleanup plan contains an unapproved edit")
        return
    reconciled = reconcile_lexical_edits(
        words,
        plan.selected_edits,
        policy=policy,
    )
    if reconciled.selected_edits != plan.selected_edits:
        raise ValueError("semantic cleanup selected edits are unsafe or incompatible")
    if reconciled.rejected_proposal_ids:
        raise ValueError("semantic cleanup selected edits did not all survive reconciliation")
    if (
        reconciled.clean_surfaces != plan.clean_surfaces
        or reconciled.clean_to_source_indices != plan.clean_to_source_indices
        or reconciled.source_to_clean != plan.source_to_clean
    ):
        raise ValueError("semantic cleanup mappings do not match selected edits")


def _proposal_from_record(value: object) -> LexicalEditProposal:
    payload = _require_object(value, "selected edit")
    _require_exact_keys(
        payload,
        {
            "edit_class",
            "end_seconds",
            "proposal_id",
            "provenance",
            "result_lexical_sequence",
            "result_surfaces",
            "rule_id",
            "safety_checks",
            "semantic_safety_confidence",
            "source_digest",
            "source_end",
            "source_indices",
            "source_lexical_sequence",
            "source_start",
            "source_surfaces",
            "speaker_id",
            "start_seconds",
        },
        "selected edit",
    )
    provenance_payload = _require_object(payload["provenance"], "edit provenance")
    _require_exact_keys(
        provenance_payload,
        {"evidence_digest", "producer_id", "producer_version"},
        "edit provenance",
    )
    safety_payload = _require_object(payload["safety_checks"], "semantic safety checks")
    safety_fields = {
        "preserves_meaning",
        "preserves_uncertainty",
        "preserves_modality",
        "preserves_negation",
        "preserves_attribution",
        "preserves_quantities_names_dates",
        "preserves_tense",
        "preserves_commitment",
        "preserves_discourse_meaning",
        "necessary_or_useful",
        "unambiguous",
        "reversible",
        "isolated_filler",
        "accidental_repetition",
        "abandoned_surface_evidence",
        "explicit_correction_evidence",
        "deterministic_display_equivalence",
        "deterministic_grammar_rule",
    }
    _require_exact_keys(safety_payload, safety_fields, "semantic safety checks")
    try:
        edit_class = LexicalEditClass(_require_string(payload["edit_class"], "edit_class"))
    except ValueError as exc:
        raise ValueError("semantic cleanup edit class is not allowlisted") from exc
    return LexicalEditProposal(
        proposal_id=_require_string(payload["proposal_id"], "proposal_id"),
        edit_class=edit_class,
        source_start=_require_integer(payload["source_start"], "source_start"),
        source_end=_require_integer(payload["source_end"], "source_end"),
        source_indices=_require_integer_tuple(payload["source_indices"], "source_indices"),
        source_surfaces=_require_string_tuple(payload["source_surfaces"], "source_surfaces"),
        source_digest=_require_string(payload["source_digest"], "source_digest"),
        source_lexical_sequence=_require_string_tuple(
            payload["source_lexical_sequence"],
            "source_lexical_sequence",
        ),
        result_surfaces=_require_string_tuple(payload["result_surfaces"], "result_surfaces"),
        result_lexical_sequence=_require_string_tuple(
            payload["result_lexical_sequence"],
            "result_lexical_sequence",
        ),
        speaker_id=_require_optional_string(payload["speaker_id"], "speaker_id"),
        start_seconds=_require_optional_number(payload["start_seconds"], "start_seconds"),
        end_seconds=_require_optional_number(payload["end_seconds"], "end_seconds"),
        provenance=EditProvenance(
            producer_id=_require_string(
                provenance_payload["producer_id"],
                "producer_id",
            ),
            producer_version=_require_string(
                provenance_payload["producer_version"],
                "producer_version",
            ),
            evidence_digest=_require_string(
                provenance_payload["evidence_digest"],
                "evidence_digest",
            ),
        ),
        semantic_safety_confidence=_require_number(
            payload["semantic_safety_confidence"],
            "semantic_safety_confidence",
        ),
        safety_checks=SemanticSafetyChecks(
            preserves_meaning=_require_boolean(
                safety_payload["preserves_meaning"], "preserves_meaning"
            ),
            preserves_uncertainty=_require_boolean(
                safety_payload["preserves_uncertainty"], "preserves_uncertainty"
            ),
            preserves_modality=_require_boolean(
                safety_payload["preserves_modality"], "preserves_modality"
            ),
            preserves_negation=_require_boolean(
                safety_payload["preserves_negation"], "preserves_negation"
            ),
            preserves_attribution=_require_boolean(
                safety_payload["preserves_attribution"], "preserves_attribution"
            ),
            preserves_quantities_names_dates=_require_boolean(
                safety_payload["preserves_quantities_names_dates"],
                "preserves_quantities_names_dates",
            ),
            preserves_tense=_require_boolean(safety_payload["preserves_tense"], "preserves_tense"),
            preserves_commitment=_require_boolean(
                safety_payload["preserves_commitment"], "preserves_commitment"
            ),
            preserves_discourse_meaning=_require_boolean(
                safety_payload["preserves_discourse_meaning"],
                "preserves_discourse_meaning",
            ),
            necessary_or_useful=_require_boolean(
                safety_payload["necessary_or_useful"], "necessary_or_useful"
            ),
            unambiguous=_require_boolean(safety_payload["unambiguous"], "unambiguous"),
            reversible=_require_boolean(safety_payload["reversible"], "reversible"),
            isolated_filler=_require_boolean(safety_payload["isolated_filler"], "isolated_filler"),
            accidental_repetition=_require_boolean(
                safety_payload["accidental_repetition"], "accidental_repetition"
            ),
            abandoned_surface_evidence=_require_boolean(
                safety_payload["abandoned_surface_evidence"],
                "abandoned_surface_evidence",
            ),
            explicit_correction_evidence=_require_boolean(
                safety_payload["explicit_correction_evidence"],
                "explicit_correction_evidence",
            ),
            deterministic_display_equivalence=_require_boolean(
                safety_payload["deterministic_display_equivalence"],
                "deterministic_display_equivalence",
            ),
            deterministic_grammar_rule=_require_boolean(
                safety_payload["deterministic_grammar_rule"],
                "deterministic_grammar_rule",
            ),
        ),
        rule_id=_require_optional_string(payload["rule_id"], "rule_id"),
    )


def _mapping_from_record(value: object) -> SourceToCleanMapping:
    payload = _require_object(value, "source-to-clean mapping")
    _require_exact_keys(
        payload,
        {"clean_indices", "selected_proposal_id", "source_index", "source_surface"},
        "source-to-clean mapping",
    )
    return SourceToCleanMapping(
        source_index=_require_integer(payload["source_index"], "source_index"),
        source_surface=_require_string(payload["source_surface"], "source_surface"),
        clean_indices=_require_integer_tuple(payload["clean_indices"], "clean_indices"),
        selected_proposal_id=_require_optional_string(
            payload["selected_proposal_id"],
            "selected_proposal_id",
        ),
    )


def _provenance_from_record(value: object) -> EditProvenance:
    payload = _require_object(value, "edit provenance")
    _require_exact_keys(
        payload,
        {"evidence_digest", "producer_id", "producer_version"},
        "edit provenance",
    )
    return EditProvenance(
        producer_id=_require_string(payload["producer_id"], "producer_id"),
        producer_version=_require_string(payload["producer_version"], "producer_version"),
        evidence_digest=_require_string(payload["evidence_digest"], "evidence_digest"),
    )


def _proposal_receipt_from_record(value: object) -> ProposalReconciliationReceipt:
    payload = _require_object(value, "semantic proposal receipt")
    _require_exact_keys(
        payload,
        {
            "disposition",
            "edit_class",
            "proposal_id",
            "provenance",
            "required_threshold",
            "semantic_safety_confidence",
            "source_digest",
        },
        "semantic proposal receipt",
    )
    try:
        edit_class = LexicalEditClass(_require_string(payload["edit_class"], "edit_class"))
        disposition = ProposalDisposition(_require_string(payload["disposition"], "disposition"))
    except ValueError as exc:
        raise ValueError("semantic proposal receipt enum is not allowlisted") from exc
    return ProposalReconciliationReceipt(
        proposal_id=_require_string(payload["proposal_id"], "proposal_id"),
        edit_class=edit_class,
        source_digest=_require_string(payload["source_digest"], "source_digest"),
        provenance=_provenance_from_record(payload["provenance"]),
        semantic_safety_confidence=_require_number(
            payload["semantic_safety_confidence"],
            "semantic_safety_confidence",
        ),
        required_threshold=_require_optional_number(
            payload["required_threshold"], "required_threshold"
        ),
        disposition=disposition,
    )


def _abstention_from_record(value: object) -> SemanticProposalAbstention:
    payload = _require_object(value, "semantic abstention receipt")
    _require_exact_keys(
        payload,
        {
            "abstention_id",
            "canonical_digest",
            "detail_digest",
            "provenance",
            "reason",
        },
        "semantic abstention receipt",
    )
    try:
        reason = SemanticAbstentionReason(_require_string(payload["reason"], "reason"))
    except ValueError as exc:
        raise ValueError("semantic abstention reason is not allowlisted") from exc
    return SemanticProposalAbstention(
        abstention_id=_require_string(payload["abstention_id"], "abstention_id"),
        reason=reason,
        canonical_digest=_require_string(payload["canonical_digest"], "canonical_digest"),
        provenance=_provenance_from_record(payload["provenance"]),
        detail_digest=_require_optional_string(payload["detail_digest"], "detail_digest"),
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


def _require_string_tuple(value: object, field: str) -> tuple[str, ...]:
    return tuple(_require_string(item, field) for item in _require_list(value, field))


def _require_integer_tuple(value: object, field: str) -> tuple[int, ...]:
    return tuple(_require_integer(item, field) for item in _require_list(value, field))


def _weighted_compatible_edits(
    proposals: Sequence[LexicalEditProposal],
) -> tuple[LexicalEditProposal, ...]:
    ordered = sorted(
        proposals,
        key=lambda item: (item.source_end, item.source_start, item.proposal_id),
    )
    previous: list[int] = []
    for index, proposal in enumerate(ordered):
        compatible = -1
        for candidate in range(index - 1, -1, -1):
            if ordered[candidate].source_end <= proposal.source_start:
                compatible = candidate
                break
        previous.append(compatible)
    states: list[tuple[float, tuple[str, ...], tuple[int, ...]]] = [(0.0, (), ())]
    for index, proposal in enumerate(ordered):
        excluded = states[index]
        base = states[previous[index] + 1]
        included = (
            base[0] + proposal.semantic_safety_confidence,
            base[1] + (proposal.proposal_id,),
            base[2] + (index,),
        )
        if included[0] > excluded[0] + 1e-12:
            chosen = included
        elif excluded[0] > included[0] + 1e-12:
            chosen = excluded
        else:
            chosen = included if included[1] < excluded[1] else excluded
        states.append(chosen)
    return tuple(
        sorted((ordered[index] for index in states[-1][2]), key=lambda item: item.source_start)
    )


def _render_plan(
    words: tuple[CanonicalWord, ...],
    selected: tuple[LexicalEditProposal, ...],
) -> tuple[tuple[str, ...], tuple[tuple[int, ...], ...], tuple[SourceToCleanMapping, ...]]:
    edits = {proposal.source_start: proposal for proposal in selected}
    selected_by_source: dict[int, str] = {}
    clean_surfaces: list[str] = []
    clean_sources: list[tuple[int, ...]] = []
    source_clean: dict[int, list[int]] = {word.word_index: [] for word in words}
    cursor = words[0].word_index if words else 0
    end = words[-1].word_index + 1 if words else 0
    word_by_index = {word.word_index: word for word in words}
    while cursor < end:
        edit = edits.get(cursor)
        if edit is None:
            word = word_by_index[cursor]
            clean_index = len(clean_surfaces)
            clean_surfaces.append(word.surface)
            clean_sources.append((cursor,))
            source_clean[cursor].append(clean_index)
            cursor += 1
            continue
        for source_index in edit.source_indices:
            selected_by_source[source_index] = edit.proposal_id
        source_mapping = _result_source_mapping(edit)
        for surface, source_indices in zip(edit.result_surfaces, source_mapping, strict=True):
            clean_index = len(clean_surfaces)
            clean_surfaces.append(surface)
            clean_sources.append(source_indices)
            for source_index in source_indices:
                source_clean[source_index].append(clean_index)
        cursor = edit.source_end
    reverse = tuple(
        SourceToCleanMapping(
            source_index=word.word_index,
            source_surface=word.surface,
            clean_indices=tuple(source_clean[word.word_index]),
            selected_proposal_id=selected_by_source.get(word.word_index),
        )
        for word in words
    )
    return tuple(clean_surfaces), tuple(clean_sources), reverse


def _result_source_mapping(proposal: LexicalEditProposal) -> tuple[tuple[int, ...], ...]:
    if not proposal.result_surfaces:
        return ()
    if proposal.edit_class is LexicalEditClass.STUTTER_COLLAPSE:
        return ((proposal.source_indices[-1],),)
    if proposal.edit_class is LexicalEditClass.EXPLICIT_SELF_CORRECTION:
        offset = len(proposal.source_indices) - len(proposal.result_surfaces)
        return tuple((index,) for index in proposal.source_indices[offset:])
    if len(proposal.result_surfaces) == len(proposal.source_indices):
        return tuple((index,) for index in proposal.source_indices)
    return (proposal.source_indices,) * len(proposal.result_surfaces)


def _validate_operation_shape(proposal: LexicalEditProposal) -> None:
    source = proposal.source_lexical_sequence
    result = proposal.result_lexical_sequence
    if proposal.edit_class is LexicalEditClass.FILLER_REMOVAL:
        if len(source) != 1 or source[0] not in _FILLERS or result:
            raise ValueError("filler removal must delete one allowlisted non-semantic filler")
    elif proposal.edit_class is LexicalEditClass.STUTTER_COLLAPSE:
        if len(source) < 2 or len(result) != 1 or len(set(source)) != 1 or result[0] != source[-1]:
            raise ValueError("stutter collapse must retain one exact repeated source word")
        if proposal.result_surfaces != (proposal.source_surfaces[-1],):
            raise ValueError("stutter collapse must retain the exact final source surface")
        if source[0] in _EMPHASIS_REPETITIONS:
            raise ValueError("meaning-bearing emphasis repetition cannot be collapsed")
    elif proposal.edit_class is LexicalEditClass.ABANDONED_FALSE_START_REMOVAL:
        if result:
            raise ValueError("false-start removal may only delete the bound abandoned span")
    elif proposal.edit_class is LexicalEditClass.EXPLICIT_SELF_CORRECTION:
        if not result or len(result) >= len(source) or source[-len(result) :] != result:
            raise ValueError("self-correction result must be an exact canonical suffix")
        if proposal.result_surfaces != proposal.source_surfaces[-len(result) :]:
            raise ValueError("self-correction must retain exact canonical suffix surfaces")
        prefix = source[: -len(result)]
        if not any(_contains_sequence(prefix, marker) for marker in _CORRECTION_MARKERS):
            raise ValueError("self-correction source must contain an explicit correction marker")
    elif proposal.edit_class is LexicalEditClass.DISPLAY_NORMALIZATION:
        rules = _DISPLAY_RULES.get(proposal.rule_id or "")
        if rules is None or rules.get(source) != result:
            raise ValueError("display normalization must match an allowlisted deterministic rule")
        if proposal.result_surfaces != rules[source]:
            raise ValueError("display normalization must use the exact allowlisted surfaces")
    elif proposal.edit_class is LexicalEditClass.NARROW_GRAMMATICAL_CORRECTION:
        rules = _GRAMMAR_RULES.get(proposal.rule_id or "")
        if rules is None or rules.get(source) != result:
            raise ValueError("grammatical correction must match an allowlisted deterministic rule")
        if proposal.result_surfaces != rules[source]:
            raise ValueError("grammatical correction must use the exact allowlisted surfaces")


def _class_checks_pass(proposal: LexicalEditProposal) -> bool:
    checks = proposal.safety_checks
    return {
        LexicalEditClass.FILLER_REMOVAL: checks.isolated_filler,
        LexicalEditClass.STUTTER_COLLAPSE: checks.accidental_repetition,
        LexicalEditClass.ABANDONED_FALSE_START_REMOVAL: checks.abandoned_surface_evidence,
        LexicalEditClass.EXPLICIT_SELF_CORRECTION: checks.explicit_correction_evidence,
        LexicalEditClass.DISPLAY_NORMALIZATION: checks.deterministic_display_equivalence,
        LexicalEditClass.NARROW_GRAMMATICAL_CORRECTION: checks.deterministic_grammar_rule,
    }[proposal.edit_class]


def _proposal_disposition(
    proposal: LexicalEditProposal,
    words: tuple[CanonicalWord, ...],
    policy: LexicalCleanupPolicy,
) -> ProposalDisposition | None:
    """Return a deterministic rejection reason, or ``None`` when eligible."""

    threshold = policy.threshold_for(proposal.edit_class)
    if threshold is None:
        return ProposalDisposition.POLICY_DISABLED
    if proposal.semantic_safety_confidence < threshold:
        return ProposalDisposition.BELOW_THRESHOLD
    if not proposal.safety_checks.common_checks_pass():
        return ProposalDisposition.COMMON_SAFETY_FAILED
    if not _class_checks_pass(proposal):
        return ProposalDisposition.CLASS_SAFETY_FAILED
    if not _context_checks_pass(proposal, words):
        return ProposalDisposition.CONTEXT_SAFETY_FAILED
    return None


def _context_checks_pass(
    proposal: LexicalEditProposal,
    words: tuple[CanonicalWord, ...],
) -> bool:
    by_index = {word.word_index: word for word in words}
    before = by_index.get(proposal.source_start - 1)
    after = by_index.get(proposal.source_end)
    if proposal.edit_class is LexicalEditClass.FILLER_REMOVAL:
        # A standalone response such as "Um." can communicate hesitation. A
        # leading filler may still be isolated when a same-speaker lexical
        # continuation follows; an internal filler additionally requires any
        # preceding neighbor to belong to that speaker.
        return (
            after is not None
            and after.speaker_id == proposal.speaker_id
            and after.container_index == by_index[proposal.source_start].container_index
            and (before is None or before.speaker_id == proposal.speaker_id)
            and (
                before is None
                or before.container_index == by_index[proposal.source_start].container_index
            )
            and _lexical_surface(after.surface) not in {"huh", "hum"}
            and not _has_terminal_punctuation(proposal.source_surfaces[0])
        )
    if proposal.edit_class is LexicalEditClass.STUTTER_COLLAPSE:
        # A punctuation-separated repeat is more likely rhetorical/emphatic.
        return not any(
            _has_terminal_punctuation(surface) for surface in proposal.source_surfaces[:-1]
        )
    if proposal.edit_class is LexicalEditClass.ABANDONED_FALSE_START_REMOVAL:
        return _has_abandonment_marker(proposal.source_surfaces[-1])
    return True


def _canonical_index(words: Sequence[CanonicalWord]) -> dict[int, CanonicalWord]:
    result: dict[int, CanonicalWord] = {}
    previous: int | None = None
    for word in words:
        if not isinstance(word, CanonicalWord):
            raise ValueError("canonical evidence contains an invalid word")
        if previous is not None and word.word_index != previous + 1:
            raise ValueError("canonical word indices must be ordered and contiguous")
        if word.word_index in result:
            raise ValueError("canonical word indices must be unique")
        result[word.word_index] = word
        previous = word.word_index
    return result


def _word_record(word: CanonicalWord) -> dict[str, object]:
    return {
        "container_index": word.container_index,
        "end_seconds": word.end_seconds,
        "speaker_id": word.speaker_id,
        "start_seconds": word.start_seconds,
        "surface": unicodedata.normalize("NFC", word.surface),
        "word_index": word.word_index,
    }


def _selection_erases_container(
    words: tuple[CanonicalWord, ...],
    selected: tuple[LexicalEditProposal, ...],
) -> bool:
    container_words: dict[int, set[int]] = {}
    for word in words:
        if word.container_index is not None:
            container_words.setdefault(word.container_index, set()).add(word.word_index)
    if not container_words:
        return False
    retained = {word.word_index for word in words}
    for proposal in selected:
        retained.difference_update(proposal.source_indices)
        for source_indices in _result_source_mapping(proposal):
            retained.update(source_indices)
    return any(not indices.intersection(retained) for indices in container_words.values())


def _proposal_record(proposal: LexicalEditProposal) -> dict[str, object]:
    return {
        "edit_class": proposal.edit_class.value,
        "end_seconds": proposal.end_seconds,
        "proposal_id": proposal.proposal_id,
        "provenance": _provenance_record(proposal.provenance),
        "result_lexical_sequence": list(proposal.result_lexical_sequence),
        "result_surfaces": list(proposal.result_surfaces),
        "rule_id": proposal.rule_id,
        "safety_checks": {
            field: getattr(proposal.safety_checks, field)
            for field in proposal.safety_checks.__dataclass_fields__
        },
        "semantic_safety_confidence": proposal.semantic_safety_confidence,
        "source_digest": proposal.source_digest,
        "source_end": proposal.source_end,
        "source_indices": list(proposal.source_indices),
        "source_lexical_sequence": list(proposal.source_lexical_sequence),
        "source_start": proposal.source_start,
        "source_surfaces": list(proposal.source_surfaces),
        "speaker_id": proposal.speaker_id,
        "start_seconds": proposal.start_seconds,
    }


def _provenance_record(provenance: EditProvenance) -> dict[str, str]:
    return {
        "evidence_digest": provenance.evidence_digest,
        "producer_id": provenance.producer_id,
        "producer_version": provenance.producer_version,
    }


def _policy_digest(policy: LexicalCleanupPolicy) -> str:
    return _json_digest(
        {
            "thresholds": [
                [edit_class.value, threshold]
                for edit_class, threshold in sorted(
                    policy.thresholds, key=lambda item: item[0].value
                )
            ]
        }
    )


def _json_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _lexical_surface(surface: str) -> str:
    normalized = unicodedata.normalize("NFKC", surface).casefold().strip()
    normalized = normalized.strip(".,?!;:\"'“”‘’()[]{}")
    normalized = normalized.rstrip("-–—…")
    if not normalized or not any(character.isalnum() for character in normalized):
        raise ValueError("surface has no lexical content")
    return normalized


def _has_terminal_punctuation(surface: str) -> bool:
    return surface.rstrip().endswith((".", "?", "!", "…", ";", ":"))


def _has_abandonment_marker(surface: str) -> bool:
    return surface.rstrip().endswith(("-", "–", "—", "…"))


def _contains_sequence(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    return any(
        haystack[index : index + len(needle)] == needle
        for index in range(len(haystack) - len(needle) + 1)
    )


def _validate_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field} must be a stable identifier")


def _validate_digest(value: str, field: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _validate_probability(value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError("semantic_safety_confidence must be between zero and one")


def _validate_timing(value: float | None, field: str) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0.0
    ):
        raise ValueError(f"{field} must be a finite non-negative number")
