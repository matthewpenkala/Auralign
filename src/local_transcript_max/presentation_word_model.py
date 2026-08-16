# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fail-closed adapter contract for local word-level presentation models.

No checkpoint is selected here.  A provider must identify its exact local
model and calibration provenance, bind every proposal to immutable
``DecoderWord`` evidence, and separately earn a production-quality approval.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

from .presentation import (
    DecoderWord,
    PresentationProposal,
    ScorerOutput,
    ScorerProvenance,
    TrailingPunctuation,
    lexical_signature,
    lexical_text_digest,
)

WORD_MODEL_ADAPTER_VERSION = "auralign.presentation-word-model.v1"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,191}$")
_TRAILING_PRESENTATION = ".,?!;:…"
_TRAILING_CLOSERS = "\"'”’)]}）】」』》"


class ProviderLocality(StrEnum):
    """Runtime boundaries accepted by this offline adapter."""

    IN_PROCESS = "in_process"
    LOCAL_PROCESS = "local_process"
    LOOPBACK = "loopback"


class ProviderAbstentionReason(StrEnum):
    """Visible, allowlisted provider abstentions and adapter failures."""

    NO_SUPPORTED_EDIT = "no_supported_edit"
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS = "ambiguous"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"
    MALFORMED_OUTPUT = "malformed_output"
    INPUT_BINDING_MISMATCH = "input_binding_mismatch"
    PROVENANCE_MISMATCH = "provenance_mismatch"
    UNAPPROVED_PROVIDER = "unapproved_provider"


class ProviderRunStatus(StrEnum):
    """Terminal state recorded by one adapter invocation."""

    SUCCESS = "success"
    ABSTAINED = "abstained"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ModelFileContract:
    """One exact file in a local model contract."""

    relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise ValueError("model file relative_path must be non-empty")
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or str(path) != self.relative_path:
            raise ValueError("model file relative_path must be normalized and relative")
        _validate_digest(self.sha256, "model file sha256")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes <= 0
        ):
            raise ValueError("model file size_bytes must be a positive integer")


@dataclass(frozen=True, slots=True)
class WordModelProvenance:
    """Immutable provider, checkpoint, calibration, license, and locality identity."""

    provider_id: str
    provider_version: str
    model_id: str
    model_revision: str
    model_files: tuple[ModelFileContract, ...]
    model_card_sha256: str
    license_expression: str
    locality: ProviderLocality
    calibration_id: str
    calibration_revision: str
    calibration_sha256: str

    def __post_init__(self) -> None:
        for field_name in (
            "provider_id",
            "provider_version",
            "model_id",
            "model_revision",
            "calibration_id",
            "calibration_revision",
        ):
            _validate_identifier(getattr(self, field_name), field_name)
        if not isinstance(self.model_files, tuple) or not self.model_files:
            raise ValueError("model file contract must be a non-empty immutable tuple")
        if any(not isinstance(item, ModelFileContract) for item in self.model_files):
            raise ValueError("model file contract contains an invalid item")
        paths = tuple(item.relative_path for item in self.model_files)
        if tuple(sorted(set(paths))) != paths:
            raise ValueError("model file contract must be unique and sorted")
        _validate_digest(self.model_card_sha256, "model_card_sha256")
        _validate_digest(self.calibration_sha256, "calibration_sha256")
        if (
            not isinstance(self.license_expression, str)
            or not self.license_expression.strip()
            or len(self.license_expression) > 191
        ):
            raise ValueError("license_expression must be an explicit bounded string")
        if not isinstance(self.locality, ProviderLocality):
            raise ValueError("provider locality is not allowlisted as local")

    def scorer_provenance(self) -> ScorerProvenance:
        """Project the complete contract into the presentation scorer identity."""

        return ScorerProvenance(
            scorer_id=self.provider_id,
            scorer_version=self.provider_version,
            configuration_digest=word_model_provenance_sha256(self),
        )


@dataclass(frozen=True, slots=True)
class WordModelRequest:
    """Exact immutable input presented to a local provider."""

    words: tuple[DecoderWord, ...]
    input_digest: str
    adapter_version: str = WORD_MODEL_ADAPTER_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.words, tuple) or not self.words:
            raise ValueError("word-model request requires an immutable non-empty word tuple")
        _validate_decoder_words(self.words)
        _validate_digest(self.input_digest, "word-model input_digest")
        if self.input_digest != decoder_word_input_sha256(self.words):
            raise ValueError("word-model request digest does not bind DecoderWord input")
        if self.adapter_version != WORD_MODEL_ADAPTER_VERSION:
            raise ValueError("word-model adapter version is unsupported")

    @classmethod
    def bind(cls, words: Sequence[DecoderWord]) -> WordModelRequest:
        """Construct a request bound to every DecoderWord field."""

        bound = tuple(words)
        return cls(words=bound, input_digest=decoder_word_input_sha256(bound))


@dataclass(frozen=True, slots=True)
class CalibratedWordPresentationProposal:
    """One complete calibrated punctuation and casing distribution."""

    word_index: int
    lexical_signature: str
    lexical_text_digest: str
    casing_surface: str
    casing_probability: float
    capitalization_probability: float
    punctuation_probabilities: tuple[tuple[TrailingPunctuation, float], ...]
    sentence_break_probability: float
    paragraph_break_probability: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.word_index, bool)
            or not isinstance(self.word_index, int)
            or self.word_index < 0
        ):
            raise ValueError("word-model proposal word_index must be non-negative")
        if not isinstance(self.lexical_signature, str) or not self.lexical_signature:
            raise ValueError("word-model proposal lexical_signature must be non-empty")
        _validate_digest(self.lexical_text_digest, "proposal lexical_text_digest")
        if not isinstance(self.casing_surface, str) or not self.casing_surface.strip():
            raise ValueError("proposal casing_surface must be non-empty")
        for field_name in (
            "casing_probability",
            "capitalization_probability",
            "sentence_break_probability",
            "paragraph_break_probability",
        ):
            _validate_probability(getattr(self, field_name), field_name)
        if not isinstance(self.punctuation_probabilities, tuple):
            raise ValueError("punctuation probabilities must be an immutable tuple")
        if any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], TrailingPunctuation)
            for item in self.punctuation_probabilities
        ):
            raise ValueError("punctuation distribution contains an invalid class entry")
        punctuation = tuple(item[0] for item in self.punctuation_probabilities)
        if punctuation != tuple(TrailingPunctuation):
            raise ValueError("punctuation distribution must cover every class in canonical order")
        probabilities = tuple(item[1] for item in self.punctuation_probabilities)
        for probability in probabilities:
            _validate_probability(probability, "punctuation probability")
        if not math.isclose(sum(probabilities), 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("punctuation probabilities must sum to one")

    @classmethod
    def bind(
        cls,
        word: DecoderWord,
        *,
        casing_surface: str,
        casing_probability: float,
        capitalization_probability: float,
        punctuation_probabilities: Sequence[tuple[TrailingPunctuation, float]],
        sentence_break_probability: float,
        paragraph_break_probability: float,
    ) -> CalibratedWordPresentationProposal:
        """Create a proposal whose exact casing surface binds its source word."""

        _validate_casing_surface(word.lexical_text, casing_surface)
        return cls(
            word_index=word.word_index,
            lexical_signature=word.lexical_signature,
            lexical_text_digest=lexical_text_digest(word.lexical_text),
            casing_surface=casing_surface,
            casing_probability=casing_probability,
            capitalization_probability=capitalization_probability,
            punctuation_probabilities=tuple(punctuation_probabilities),
            sentence_break_probability=sentence_break_probability,
            paragraph_break_probability=paragraph_break_probability,
        )

    def validate_against(self, word: DecoderWord) -> None:
        """Fail if the proposal drifts from its exact DecoderWord evidence."""

        if (
            self.word_index != word.word_index
            or self.lexical_signature != word.lexical_signature
            or self.lexical_text_digest != lexical_text_digest(word.lexical_text)
        ):
            raise ValueError("word-model proposal input binding mismatch")
        _validate_casing_surface(word.lexical_text, self.casing_surface)

    def presentation_proposals(self) -> tuple[PresentationProposal, ...]:
        """Project the calibrated distribution into decoder scorer proposals."""

        return tuple(
            PresentationProposal(
                word_index=self.word_index,
                lexical_signature=self.lexical_signature,
                lexical_text_digest=self.lexical_text_digest,
                trailing_punctuation=punctuation,
                punctuation_probability=probability,
                capitalization_probability=self.capitalization_probability,
                casing_surface=self.casing_surface,
                casing_probability=self.casing_probability,
                sentence_break_probability=self.sentence_break_probability,
                paragraph_break_probability=self.paragraph_break_probability,
            )
            for punctuation, probability in self.punctuation_probabilities
        )


@dataclass(frozen=True, slots=True)
class WordModelProviderResponse:
    """One exact response: complete proposals or a visible abstention."""

    request_digest: str
    provenance: WordModelProvenance
    proposals: tuple[CalibratedWordPresentationProposal, ...] = ()
    abstention_reason: ProviderAbstentionReason | None = None
    detail_digest: str | None = None

    def __post_init__(self) -> None:
        _validate_digest(self.request_digest, "provider response request_digest")
        if not isinstance(self.provenance, WordModelProvenance):
            raise ValueError("provider response provenance is invalid")
        if not isinstance(self.proposals, tuple):
            raise ValueError("provider proposals must be an immutable tuple")
        if bool(self.proposals) == (self.abstention_reason is not None):
            raise ValueError("provider response must contain proposals or one abstention")
        if self.abstention_reason is not None and not isinstance(
            self.abstention_reason, ProviderAbstentionReason
        ):
            raise ValueError("provider abstention reason is not allowlisted")
        if self.detail_digest is not None:
            _validate_digest(self.detail_digest, "provider response detail_digest")
            if self.abstention_reason is None:
                raise ValueError("provider detail_digest is reserved for abstention evidence")


@runtime_checkable
class LocalPresentationWordProvider(Protocol):
    """Synchronous local provider interface; network acquisition is absent."""

    @property
    def provenance(self) -> WordModelProvenance:
        """Return immutable model/runtime provenance before inference."""

    def propose(self, request: WordModelRequest) -> WordModelProviderResponse:
        """Return complete calibrated proposals or an explicit abstention."""


@dataclass(frozen=True, slots=True)
class WordModelQualityMetrics:
    """Reviewed baseline or candidate measurements on one bound evaluation set."""

    evaluation_dataset_sha256: str
    evaluated_words: int
    lexical_integrity_rate: float
    punctuation_f1: float
    casing_accuracy: float
    proper_name_accuracy: float
    acronym_accuracy: float
    sentence_boundary_f1: float
    expected_calibration_error: float
    runtime_ms_per_1000_words: float
    peak_memory_bytes: int

    def __post_init__(self) -> None:
        _validate_digest(self.evaluation_dataset_sha256, "evaluation_dataset_sha256")
        if (
            isinstance(self.evaluated_words, bool)
            or not isinstance(self.evaluated_words, int)
            or self.evaluated_words <= 0
        ):
            raise ValueError("evaluated_words must be a positive integer")
        for field_name in (
            "lexical_integrity_rate",
            "punctuation_f1",
            "casing_accuracy",
            "proper_name_accuracy",
            "acronym_accuracy",
            "sentence_boundary_f1",
            "expected_calibration_error",
        ):
            _validate_probability(getattr(self, field_name), field_name)
        _validate_nonnegative_finite(self.runtime_ms_per_1000_words, "runtime_ms_per_1000_words")
        if (
            isinstance(self.peak_memory_bytes, bool)
            or not isinstance(self.peak_memory_bytes, int)
            or self.peak_memory_bytes < 0
        ):
            raise ValueError("peak_memory_bytes must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class WordModelQualityPolicy:
    """Production approval thresholds relative to an established baseline."""

    minimum_evaluated_words: int = 1000
    minimum_punctuation_f1_gain: float = 0.01
    minimum_casing_accuracy_gain: float = 0.005
    minimum_proper_name_accuracy_gain: float = 0.01
    minimum_acronym_accuracy_gain: float = 0.0
    minimum_sentence_boundary_f1_gain: float = 0.0
    maximum_expected_calibration_error: float = 0.08
    maximum_runtime_ms_per_1000_words: float = 5000.0
    maximum_peak_memory_bytes: int = 4 * 1024**3

    def __post_init__(self) -> None:
        if (
            isinstance(self.minimum_evaluated_words, bool)
            or not isinstance(self.minimum_evaluated_words, int)
            or self.minimum_evaluated_words <= 0
        ):
            raise ValueError("minimum_evaluated_words must be positive")
        for field_name in (
            "minimum_punctuation_f1_gain",
            "minimum_casing_accuracy_gain",
            "minimum_proper_name_accuracy_gain",
            "minimum_acronym_accuracy_gain",
            "minimum_sentence_boundary_f1_gain",
            "maximum_expected_calibration_error",
        ):
            _validate_probability(getattr(self, field_name), field_name)
        _validate_nonnegative_finite(
            self.maximum_runtime_ms_per_1000_words,
            "maximum_runtime_ms_per_1000_words",
        )
        if (
            isinstance(self.maximum_peak_memory_bytes, bool)
            or not isinstance(self.maximum_peak_memory_bytes, int)
            or self.maximum_peak_memory_bytes <= 0
        ):
            raise ValueError("maximum_peak_memory_bytes must be positive")
        if not any(
            gain > 0.0
            for gain in (
                self.minimum_punctuation_f1_gain,
                self.minimum_casing_accuracy_gain,
                self.minimum_proper_name_accuracy_gain,
                self.minimum_acronym_accuracy_gain,
                self.minimum_sentence_boundary_f1_gain,
            )
        ):
            raise ValueError("quality policy must require improvement over baseline")


@dataclass(frozen=True, slots=True)
class WordModelApprovalReceipt:
    """Deterministic evidence that a specific provider passed or failed quality gates."""

    provenance_sha256: str
    baseline_metrics_sha256: str
    candidate_metrics_sha256: str
    policy_sha256: str
    approved: bool
    rejection_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "provenance_sha256",
            "baseline_metrics_sha256",
            "candidate_metrics_sha256",
            "policy_sha256",
        ):
            _validate_digest(getattr(self, field_name), field_name)
        if not isinstance(self.approved, bool):
            raise ValueError("approval status must be boolean")
        if not isinstance(self.rejection_reasons, tuple):
            raise ValueError("approval rejection reasons must be immutable")
        if tuple(sorted(set(self.rejection_reasons))) != self.rejection_reasons:
            raise ValueError("approval rejection reasons must be unique and sorted")
        for reason in self.rejection_reasons:
            _validate_identifier(reason, "approval rejection reason")
        if self.approved == bool(self.rejection_reasons):
            raise ValueError("approved receipt and rejection reasons contradict")


@dataclass(frozen=True, slots=True)
class WordModelExecutionPolicy:
    """Invocation-specific required/optional and proposal confidence gates."""

    required: bool
    minimum_casing_probability: float = 0.5
    minimum_max_punctuation_probability: float = 0.35

    def __post_init__(self) -> None:
        if not isinstance(self.required, bool):
            raise ValueError("provider required flag must be boolean")
        _validate_probability(self.minimum_casing_probability, "minimum_casing_probability")
        _validate_probability(
            self.minimum_max_punctuation_probability,
            "minimum_max_punctuation_probability",
        )


@dataclass(frozen=True, slots=True)
class WordModelExecutionReceipt:
    """Canonical receipt for every provider invocation, including abstentions."""

    request_digest: str
    provenance_sha256: str
    approval_receipt_sha256: str
    required: bool
    status: ProviderRunStatus
    reason: ProviderAbstentionReason | None
    proposal_digest: str | None
    proposal_count: int
    detail_digest: str | None = None
    adapter_version: str = WORD_MODEL_ADAPTER_VERSION

    def __post_init__(self) -> None:
        _validate_digest(self.request_digest, "execution request_digest")
        _validate_digest(self.provenance_sha256, "execution provenance_sha256")
        _validate_digest(self.approval_receipt_sha256, "approval_receipt_sha256")
        if not isinstance(self.required, bool):
            raise ValueError("execution required flag must be boolean")
        if not isinstance(self.status, ProviderRunStatus):
            raise ValueError("execution status is not allowlisted")
        if self.reason is not None and not isinstance(self.reason, ProviderAbstentionReason):
            raise ValueError("execution reason is not allowlisted")
        if self.proposal_digest is not None:
            _validate_digest(self.proposal_digest, "execution proposal_digest")
        if (
            isinstance(self.proposal_count, bool)
            or not isinstance(self.proposal_count, int)
            or self.proposal_count < 0
        ):
            raise ValueError("execution proposal_count must be non-negative")
        if self.detail_digest is not None:
            _validate_digest(self.detail_digest, "execution detail_digest")
        if self.status is ProviderRunStatus.SUCCESS:
            if self.reason is not None or self.proposal_digest is None or self.proposal_count == 0:
                raise ValueError("successful execution receipt is incomplete")
        elif self.reason is None or self.proposal_digest is not None or self.proposal_count != 0:
            raise ValueError("non-success execution receipt is inconsistent")
        if self.adapter_version != WORD_MODEL_ADAPTER_VERSION:
            raise ValueError("execution adapter version is unsupported")


@dataclass(frozen=True, slots=True)
class WordModelRunResult:
    """Validated decoder output and its exact execution receipt."""

    scorer_output: ScorerOutput
    calibrated_proposals: tuple[CalibratedWordPresentationProposal, ...]
    receipt: WordModelExecutionReceipt


class RequiredWordModelProviderError(RuntimeError):
    """Required provider failed closed; the receipt remains inspectable."""

    def __init__(self, receipt: WordModelExecutionReceipt) -> None:
        super().__init__(f"required word-model provider failed: {receipt.reason}")
        self.receipt = receipt


class _LowConfidenceProposalError(ValueError):
    """Internal distinction between safe abstention and malformed output."""


def evaluate_word_model_quality(
    provenance: WordModelProvenance,
    baseline: WordModelQualityMetrics,
    candidate: WordModelQualityMetrics,
    *,
    policy: WordModelQualityPolicy | None = None,
) -> WordModelApprovalReceipt:
    """Approve only a checkpoint that improves a bound baseline and passes every gate."""

    active = policy or WordModelQualityPolicy()
    reasons: list[str] = []
    if baseline.evaluation_dataset_sha256 != candidate.evaluation_dataset_sha256:
        reasons.append("evaluation_dataset_mismatch")
    if baseline.evaluated_words != candidate.evaluated_words:
        reasons.append("evaluation_word_count_mismatch")
    if candidate.evaluated_words < active.minimum_evaluated_words:
        reasons.append("evaluation_too_small")
    if candidate.lexical_integrity_rate != 1.0:
        reasons.append("lexical_integrity_failed")
    gains = (
        ("punctuation_f1", active.minimum_punctuation_f1_gain),
        ("casing_accuracy", active.minimum_casing_accuracy_gain),
        ("proper_name_accuracy", active.minimum_proper_name_accuracy_gain),
        ("acronym_accuracy", active.minimum_acronym_accuracy_gain),
        ("sentence_boundary_f1", active.minimum_sentence_boundary_f1_gain),
    )
    for metric, minimum_gain in gains:
        if getattr(candidate, metric) + 1e-12 < getattr(baseline, metric) + minimum_gain:
            reasons.append(f"{metric}_gain_failed")
    if candidate.expected_calibration_error > active.maximum_expected_calibration_error:
        reasons.append("calibration_error_failed")
    if candidate.runtime_ms_per_1000_words > active.maximum_runtime_ms_per_1000_words:
        reasons.append("runtime_failed")
    if candidate.peak_memory_bytes > active.maximum_peak_memory_bytes:
        reasons.append("memory_failed")
    return WordModelApprovalReceipt(
        provenance_sha256=word_model_provenance_sha256(provenance),
        baseline_metrics_sha256=word_model_quality_metrics_sha256(baseline),
        candidate_metrics_sha256=word_model_quality_metrics_sha256(candidate),
        policy_sha256=word_model_quality_policy_sha256(active),
        approved=not reasons,
        rejection_reasons=tuple(sorted(reasons)),
    )


def run_local_word_model_provider(
    provider: LocalPresentationWordProvider,
    words: Sequence[DecoderWord],
    *,
    approval: WordModelApprovalReceipt,
    execution_policy: WordModelExecutionPolicy,
) -> WordModelRunResult:
    """Execute and validate one already-approved local provider.

    Optional failures become visible decoder abstentions. Required failures
    raise :class:`RequiredWordModelProviderError` carrying the same canonical
    receipt.
    """

    request = WordModelRequest.bind(words)
    try:
        provenance = provider.provenance
    except Exception as exc:
        return _provider_failure(
            request,
            _unknown_provenance_digest(),
            approval,
            execution_policy,
            ProviderAbstentionReason.PROVIDER_UNAVAILABLE,
            _exception_digest(exc),
            ScorerProvenance("unavailable-word-model", "unknown"),
        )
    provenance_digest = word_model_provenance_sha256(provenance)
    if not approval.approved or approval.provenance_sha256 != provenance_digest:
        return _provider_failure(
            request,
            provenance_digest,
            approval,
            execution_policy,
            ProviderAbstentionReason.UNAPPROVED_PROVIDER,
            None,
            provenance.scorer_provenance(),
        )
    try:
        response = provider.propose(request)
    except Exception as exc:
        return _provider_failure(
            request,
            provenance_digest,
            approval,
            execution_policy,
            ProviderAbstentionReason.PROVIDER_ERROR,
            _exception_digest(exc),
            provenance.scorer_provenance(),
        )
    if not isinstance(response, WordModelProviderResponse):
        return _provider_failure(
            request,
            provenance_digest,
            approval,
            execution_policy,
            ProviderAbstentionReason.MALFORMED_OUTPUT,
            None,
            provenance.scorer_provenance(),
        )
    if response.provenance != provenance:
        return _provider_failure(
            request,
            provenance_digest,
            approval,
            execution_policy,
            ProviderAbstentionReason.PROVENANCE_MISMATCH,
            None,
            provenance.scorer_provenance(),
        )
    if response.request_digest != request.input_digest:
        return _provider_failure(
            request,
            provenance_digest,
            approval,
            execution_policy,
            ProviderAbstentionReason.INPUT_BINDING_MISMATCH,
            None,
            provenance.scorer_provenance(),
        )
    if response.abstention_reason is not None:
        return _provider_failure(
            request,
            provenance_digest,
            approval,
            execution_policy,
            response.abstention_reason,
            response.detail_digest,
            provenance.scorer_provenance(),
            rejected=False,
        )
    try:
        _validate_provider_proposals(
            request.words,
            response.proposals,
            execution_policy,
        )
        decoder_proposals = tuple(
            proposal
            for word_proposal in response.proposals
            for proposal in word_proposal.presentation_proposals()
        )
        scorer_output = ScorerOutput(
            provenance=provenance.scorer_provenance(),
            proposals=decoder_proposals,
        )
    except _LowConfidenceProposalError as exc:
        return _provider_failure(
            request,
            provenance_digest,
            approval,
            execution_policy,
            ProviderAbstentionReason.LOW_CONFIDENCE,
            _exception_digest(exc),
            provenance.scorer_provenance(),
            rejected=False,
        )
    except (TypeError, ValueError) as exc:
        return _provider_failure(
            request,
            provenance_digest,
            approval,
            execution_policy,
            ProviderAbstentionReason.MALFORMED_OUTPUT,
            _exception_digest(exc),
            provenance.scorer_provenance(),
        )
    proposal_digest = _proposal_digest(response.proposals)
    receipt = WordModelExecutionReceipt(
        request_digest=request.input_digest,
        provenance_sha256=provenance_digest,
        approval_receipt_sha256=word_model_approval_receipt_sha256(approval),
        required=execution_policy.required,
        status=ProviderRunStatus.SUCCESS,
        reason=None,
        proposal_digest=proposal_digest,
        proposal_count=len(response.proposals),
    )
    return WordModelRunResult(scorer_output, response.proposals, receipt)


def decoder_word_input_sha256(words: Sequence[DecoderWord]) -> str:
    """Hash every DecoderWord field consumed or exposed to a provider."""

    bound = tuple(words)
    _validate_decoder_words(bound)
    return _json_sha256({"words": [_decoder_word_record(word) for word in bound]})


def word_model_provenance_sha256(provenance: WordModelProvenance) -> str:
    return _json_sha256(_provenance_record(provenance))


def word_model_quality_metrics_sha256(metrics: WordModelQualityMetrics) -> str:
    return _json_sha256(_quality_metrics_record(metrics))


def word_model_quality_policy_sha256(policy: WordModelQualityPolicy) -> str:
    return _json_sha256(_quality_policy_record(policy))


def serialize_word_model_approval_receipt(receipt: WordModelApprovalReceipt) -> bytes:
    return _canonical_json_bytes(_approval_record(receipt))


def word_model_approval_receipt_sha256(receipt: WordModelApprovalReceipt) -> str:
    return hashlib.sha256(serialize_word_model_approval_receipt(receipt)).hexdigest()


def serialize_word_model_execution_receipt(receipt: WordModelExecutionReceipt) -> bytes:
    """Return canonical JSON for a success, rejection, or visible abstention."""

    return _canonical_json_bytes(
        {
            "adapter_version": receipt.adapter_version,
            "approval_receipt_sha256": receipt.approval_receipt_sha256,
            "detail_digest": receipt.detail_digest,
            "proposal_count": receipt.proposal_count,
            "proposal_digest": receipt.proposal_digest,
            "provenance_sha256": receipt.provenance_sha256,
            "reason": receipt.reason.value if receipt.reason is not None else None,
            "request_digest": receipt.request_digest,
            "required": receipt.required,
            "status": receipt.status.value,
        }
    )


def word_model_execution_receipt_sha256(receipt: WordModelExecutionReceipt) -> str:
    return hashlib.sha256(serialize_word_model_execution_receipt(receipt)).hexdigest()


def _provider_failure(
    request: WordModelRequest,
    provenance_digest: str,
    approval: WordModelApprovalReceipt,
    policy: WordModelExecutionPolicy,
    reason: ProviderAbstentionReason,
    detail_digest: str | None,
    scorer_provenance: ScorerProvenance,
    *,
    rejected: bool = True,
) -> WordModelRunResult:
    receipt = WordModelExecutionReceipt(
        request_digest=request.input_digest,
        provenance_sha256=provenance_digest,
        approval_receipt_sha256=word_model_approval_receipt_sha256(approval),
        required=policy.required,
        status=ProviderRunStatus.REJECTED if rejected else ProviderRunStatus.ABSTAINED,
        reason=reason,
        proposal_digest=None,
        proposal_count=0,
        detail_digest=detail_digest,
    )
    if policy.required:
        raise RequiredWordModelProviderError(receipt)
    return WordModelRunResult(
        scorer_output=ScorerOutput.abstain(scorer_provenance, reason.value),
        calibrated_proposals=(),
        receipt=receipt,
    )


def _validate_provider_proposals(
    words: tuple[DecoderWord, ...],
    proposals: tuple[CalibratedWordPresentationProposal, ...],
    policy: WordModelExecutionPolicy,
) -> None:
    if not isinstance(proposals, tuple) or len(proposals) != len(words):
        raise ValueError("provider must return exactly one calibrated proposal per input word")
    if tuple(proposal.word_index for proposal in proposals) != tuple(
        word.word_index for word in words
    ):
        raise ValueError("provider proposal indices do not exactly cover the request")
    for word, proposal in zip(words, proposals, strict=True):
        if not isinstance(proposal, CalibratedWordPresentationProposal):
            raise ValueError("provider returned an invalid calibrated proposal")
        proposal.validate_against(word)
        if proposal.casing_probability < policy.minimum_casing_probability:
            raise _LowConfidenceProposalError(
                "provider casing proposal is below the configured threshold"
            )
        if (
            max(probability for _, probability in proposal.punctuation_probabilities)
            < policy.minimum_max_punctuation_probability
        ):
            raise _LowConfidenceProposalError(
                "provider punctuation proposal is below the configured threshold"
            )


def _validate_decoder_words(words: tuple[DecoderWord, ...]) -> None:
    if not words:
        raise ValueError("DecoderWord input must not be empty")
    if any(not isinstance(word, DecoderWord) for word in words):
        raise ValueError("word-model input contains an invalid DecoderWord")
    if tuple(word.word_index for word in words) != tuple(range(len(words))):
        raise ValueError("word-model DecoderWord indices must be contiguous from zero")


def _validate_casing_surface(source: str, proposed: str) -> None:
    if not isinstance(proposed, str) or not proposed.strip():
        raise ValueError("casing surface must be a non-empty string")
    source_core, source_suffix = _nfc_lexical_core_and_suffix(source)
    proposed_core, proposed_suffix = _nfc_lexical_core_and_suffix(proposed)
    if source_suffix != proposed_suffix:
        raise ValueError("casing surface changed non-casing presentation suffix")
    if len(source_core) != len(proposed_core):
        raise ValueError("casing surface changed NFC lexical-core code-point shape")
    if any(
        source_character.casefold() != proposed_character.casefold()
        for source_character, proposed_character in zip(source_core, proposed_core, strict=True)
    ):
        raise ValueError("casing surface changed lexical identity")
    if lexical_signature(source) != lexical_signature(proposed):
        raise ValueError("casing surface changed lexical signature")


def _nfc_lexical_core_and_suffix(text: str) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFC", text)
    closer_start = len(normalized)
    while closer_start > 0 and normalized[closer_start - 1] in _TRAILING_CLOSERS:
        closer_start -= 1
    body = normalized[:closer_start]
    closers = normalized[closer_start:]
    punctuation_start = len(body)
    while punctuation_start > 0 and body[punctuation_start - 1] in _TRAILING_PRESENTATION:
        punctuation_start -= 1
    return body[:punctuation_start], f"{body[punctuation_start:]}{closers}"


def _decoder_word_record(word: DecoderWord) -> dict[str, object]:
    return {
        "begins_turn": word.begins_turn,
        "end_seconds": word.end_seconds,
        "ends_turn": word.ends_turn,
        "incomplete_continuation": word.incomplete_continuation,
        "lexical_signature": word.lexical_signature,
        "lexical_text": unicodedata.normalize("NFC", word.lexical_text),
        "pause_after_seconds": word.pause_after_seconds,
        "question_evidence": word.question_evidence,
        "speaker_id": word.speaker_id,
        "start_seconds": word.start_seconds,
        "unsupported_same_speaker_short_continuation": (
            word.unsupported_same_speaker_short_continuation
        ),
        "word_index": word.word_index,
    }


def _provenance_record(provenance: WordModelProvenance) -> dict[str, object]:
    return {
        "calibration_id": provenance.calibration_id,
        "calibration_revision": provenance.calibration_revision,
        "calibration_sha256": provenance.calibration_sha256,
        "license_expression": provenance.license_expression,
        "locality": provenance.locality.value,
        "model_card_sha256": provenance.model_card_sha256,
        "model_files": [
            {
                "relative_path": item.relative_path,
                "sha256": item.sha256,
                "size_bytes": item.size_bytes,
            }
            for item in provenance.model_files
        ],
        "model_id": provenance.model_id,
        "model_revision": provenance.model_revision,
        "provider_id": provenance.provider_id,
        "provider_version": provenance.provider_version,
    }


def _proposal_record(proposal: CalibratedWordPresentationProposal) -> dict[str, object]:
    return {
        "capitalization_probability": proposal.capitalization_probability,
        "casing_probability": proposal.casing_probability,
        "casing_surface": unicodedata.normalize("NFC", proposal.casing_surface),
        "lexical_signature": proposal.lexical_signature,
        "lexical_text_digest": proposal.lexical_text_digest,
        "paragraph_break_probability": proposal.paragraph_break_probability,
        "punctuation_probabilities": [
            [punctuation.value, probability]
            for punctuation, probability in proposal.punctuation_probabilities
        ],
        "sentence_break_probability": proposal.sentence_break_probability,
        "word_index": proposal.word_index,
    }


def _quality_metrics_record(metrics: WordModelQualityMetrics) -> dict[str, object]:
    return {
        "acronym_accuracy": metrics.acronym_accuracy,
        "casing_accuracy": metrics.casing_accuracy,
        "evaluated_words": metrics.evaluated_words,
        "evaluation_dataset_sha256": metrics.evaluation_dataset_sha256,
        "expected_calibration_error": metrics.expected_calibration_error,
        "lexical_integrity_rate": metrics.lexical_integrity_rate,
        "peak_memory_bytes": metrics.peak_memory_bytes,
        "proper_name_accuracy": metrics.proper_name_accuracy,
        "punctuation_f1": metrics.punctuation_f1,
        "runtime_ms_per_1000_words": metrics.runtime_ms_per_1000_words,
        "sentence_boundary_f1": metrics.sentence_boundary_f1,
    }


def _quality_policy_record(policy: WordModelQualityPolicy) -> dict[str, object]:
    return {
        "maximum_expected_calibration_error": policy.maximum_expected_calibration_error,
        "maximum_peak_memory_bytes": policy.maximum_peak_memory_bytes,
        "maximum_runtime_ms_per_1000_words": policy.maximum_runtime_ms_per_1000_words,
        "minimum_acronym_accuracy_gain": policy.minimum_acronym_accuracy_gain,
        "minimum_casing_accuracy_gain": policy.minimum_casing_accuracy_gain,
        "minimum_evaluated_words": policy.minimum_evaluated_words,
        "minimum_proper_name_accuracy_gain": policy.minimum_proper_name_accuracy_gain,
        "minimum_punctuation_f1_gain": policy.minimum_punctuation_f1_gain,
        "minimum_sentence_boundary_f1_gain": (policy.minimum_sentence_boundary_f1_gain),
    }


def _approval_record(receipt: WordModelApprovalReceipt) -> dict[str, object]:
    return {
        "approved": receipt.approved,
        "baseline_metrics_sha256": receipt.baseline_metrics_sha256,
        "candidate_metrics_sha256": receipt.candidate_metrics_sha256,
        "policy_sha256": receipt.policy_sha256,
        "provenance_sha256": receipt.provenance_sha256,
        "rejection_reasons": list(receipt.rejection_reasons),
    }


def _proposal_digest(proposals: Sequence[CalibratedWordPresentationProposal]) -> str:
    return _json_sha256({"proposals": [_proposal_record(proposal) for proposal in proposals]})


def _exception_digest(exc: Exception) -> str:
    return hashlib.sha256(
        f"{type(exc).__module__}.{type(exc).__qualname__}:{exc}".encode()
    ).hexdigest()


def _unknown_provenance_digest() -> str:
    return hashlib.sha256(b"unavailable-word-model-provenance").hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _json_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _validate_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a stable identifier")


def _validate_digest(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _validate_probability(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"{field_name} must be a probability")


def _validate_nonnegative_finite(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise ValueError(f"{field_name} must be a finite non-negative number")
