# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from local_transcript_max.presentation import DecoderWord, TrailingPunctuation
from local_transcript_max.presentation_word_model import (
    CalibratedWordPresentationProposal,
    ModelFileContract,
    ProviderAbstentionReason,
    ProviderLocality,
    ProviderRunStatus,
    RequiredWordModelProviderError,
    WordModelApprovalReceipt,
    WordModelExecutionPolicy,
    WordModelProvenance,
    WordModelProviderResponse,
    WordModelQualityMetrics,
    WordModelQualityPolicy,
    WordModelRequest,
    decoder_word_input_sha256,
    evaluate_word_model_quality,
    run_local_word_model_provider,
    serialize_word_model_execution_receipt,
    word_model_execution_receipt_sha256,
    word_model_provenance_sha256,
)


def _words(*surfaces: str) -> tuple[DecoderWord, ...]:
    return tuple(
        DecoderWord.from_text(
            index,
            surface,
            start_seconds=index * 0.5,
            end_seconds=index * 0.5 + 0.4,
            pause_after_seconds=0.1,
            speaker_id="SPEAKER_00",
            begins_turn=index == 0,
            ends_turn=index == len(surfaces) - 1,
        )
        for index, surface in enumerate(surfaces)
    )


def _provenance() -> WordModelProvenance:
    return WordModelProvenance(
        provider_id="fixture-word-model",
        provider_version="1.0",
        model_id="fixture/punctuation-case",
        model_revision="revision-abc123",
        model_files=(
            ModelFileContract("config.json", "1" * 64, 100),
            ModelFileContract("model.onnx", "2" * 64, 1000),
        ),
        model_card_sha256="3" * 64,
        license_expression="MIT",
        locality=ProviderLocality.IN_PROCESS,
        calibration_id="fixture-calibration",
        calibration_revision="calibration-v1",
        calibration_sha256="4" * 64,
    )


def _metrics(*, improved: bool) -> WordModelQualityMetrics:
    offset = 0.03 if improved else 0.0
    return WordModelQualityMetrics(
        evaluation_dataset_sha256="5" * 64,
        evaluated_words=2000,
        lexical_integrity_rate=1.0,
        punctuation_f1=0.80 + offset,
        casing_accuracy=0.85 + offset,
        proper_name_accuracy=0.75 + offset,
        acronym_accuracy=0.80 + offset,
        sentence_boundary_f1=0.78 + offset,
        expected_calibration_error=0.04,
        runtime_ms_per_1000_words=25.0,
        peak_memory_bytes=50_000_000,
    )


def _approval(provenance: WordModelProvenance) -> WordModelApprovalReceipt:
    receipt = evaluate_word_model_quality(
        provenance,
        _metrics(improved=False),
        _metrics(improved=True),
    )
    assert receipt.approved
    return receipt


def _distribution(
    selected: TrailingPunctuation = TrailingPunctuation.NONE,
    confidence: float = 0.8,
) -> tuple[tuple[TrailingPunctuation, float], ...]:
    remainder = (1.0 - confidence) / (len(TrailingPunctuation) - 1)
    return tuple(
        (punctuation, confidence if punctuation is selected else remainder)
        for punctuation in TrailingPunctuation
    )


def _proposal(
    word: DecoderWord,
    casing_surface: str,
    *,
    casing_probability: float = 0.95,
    punctuation_confidence: float = 0.8,
) -> CalibratedWordPresentationProposal:
    return CalibratedWordPresentationProposal.bind(
        word,
        casing_surface=casing_surface,
        casing_probability=casing_probability,
        capitalization_probability=0.2,
        punctuation_probabilities=_distribution(confidence=punctuation_confidence),
        sentence_break_probability=0.1,
        paragraph_break_probability=0.01,
    )


class _Provider:
    def __init__(
        self,
        provenance: WordModelProvenance,
        response: WordModelProviderResponse | object,
    ) -> None:
        self._provenance = provenance
        self.response = response
        self.request: WordModelRequest | None = None

    @property
    def provenance(self) -> WordModelProvenance:
        return self._provenance

    def propose(self, request: WordModelRequest) -> WordModelProviderResponse:
        self.request = request
        return self.response  # type: ignore[return-value]


def test_local_provider_success_preserves_full_name_and_acronym_casing() -> None:
    words = _words("natalie", "nasa")
    provenance = _provenance()
    response = WordModelProviderResponse(
        request_digest=decoder_word_input_sha256(words),
        provenance=provenance,
        proposals=(
            _proposal(words[0], "Natalie"),
            _proposal(words[1], "NASA"),
        ),
    )
    provider = _Provider(provenance, response)

    result = run_local_word_model_provider(
        provider,
        words,
        approval=_approval(provenance),
        execution_policy=WordModelExecutionPolicy(required=True),
    )

    assert provider.request is not None
    assert provider.request.words == words
    assert result.receipt.status is ProviderRunStatus.SUCCESS
    assert result.receipt.proposal_count == 2
    assert len(result.scorer_output.proposals) == 2 * len(TrailingPunctuation)
    assert {proposal.casing_surface for proposal in result.scorer_output.proposals} == {
        "Natalie",
        "NASA",
    }
    serialized = serialize_word_model_execution_receipt(result.receipt)
    assert (
        word_model_execution_receipt_sha256(result.receipt)
        == hashlib.sha256(serialized).hexdigest()
    )
    assert serialized.endswith(b"\n")


def test_optional_provider_abstention_is_visible_to_decoder_and_receipt() -> None:
    words = _words("maybe", "later")
    provenance = _provenance()
    response = WordModelProviderResponse(
        request_digest=decoder_word_input_sha256(words),
        provenance=provenance,
        abstention_reason=ProviderAbstentionReason.AMBIGUOUS,
        detail_digest="6" * 64,
    )

    result = run_local_word_model_provider(
        _Provider(provenance, response),
        words,
        approval=_approval(provenance),
        execution_policy=WordModelExecutionPolicy(required=False),
    )

    assert result.receipt.status is ProviderRunStatus.ABSTAINED
    assert result.receipt.reason is ProviderAbstentionReason.AMBIGUOUS
    assert result.scorer_output.abstention_reason == "ambiguous"
    assert result.calibrated_proposals == ()


def test_required_provider_abstention_fails_closed_with_receipt() -> None:
    words = _words("maybe", "later")
    provenance = _provenance()
    response = WordModelProviderResponse(
        request_digest=decoder_word_input_sha256(words),
        provenance=provenance,
        abstention_reason=ProviderAbstentionReason.PROVIDER_UNAVAILABLE,
    )

    with pytest.raises(RequiredWordModelProviderError) as error:
        run_local_word_model_provider(
            _Provider(provenance, response),
            words,
            approval=_approval(provenance),
            execution_policy=WordModelExecutionPolicy(required=True),
        )

    assert error.value.receipt.reason is ProviderAbstentionReason.PROVIDER_UNAVAILABLE
    assert error.value.receipt.required


@pytest.mark.parametrize("malformation", [object(), {"opaque": "rewrite"}])
def test_malformed_provider_output_is_rejected(malformation: object) -> None:
    words = _words("natalie")
    provenance = _provenance()

    result = run_local_word_model_provider(
        _Provider(provenance, malformation),
        words,
        approval=_approval(provenance),
        execution_policy=WordModelExecutionPolicy(required=False),
    )

    assert result.receipt.status is ProviderRunStatus.REJECTED
    assert result.receipt.reason is ProviderAbstentionReason.MALFORMED_OUTPUT


def test_request_and_proposal_digest_drift_fail_closed() -> None:
    words = _words("natalie")
    provenance = _provenance()
    response = WordModelProviderResponse(
        request_digest="7" * 64,
        provenance=provenance,
        proposals=(_proposal(words[0], "Natalie"),),
    )
    request_result = run_local_word_model_provider(
        _Provider(provenance, response),
        words,
        approval=_approval(provenance),
        execution_policy=WordModelExecutionPolicy(required=False),
    )
    assert request_result.receipt.reason is ProviderAbstentionReason.INPUT_BINDING_MISMATCH

    drifted = replace(words[0], lexical_text="michael", lexical_signature="michael")
    bound_response = replace(response, request_digest=decoder_word_input_sha256((drifted,)))
    proposal_result = run_local_word_model_provider(
        _Provider(provenance, bound_response),
        (drifted,),
        approval=_approval(provenance),
        execution_policy=WordModelExecutionPolicy(required=False),
    )
    assert proposal_result.receipt.reason is ProviderAbstentionReason.MALFORMED_OUTPUT


def test_unicode_casefold_expansion_is_structurally_rejected() -> None:
    word = _words("ß")[0]
    with pytest.raises(ValueError, match="code-point shape"):
        _proposal(word, "SS")


def test_low_confidence_proposal_abstains_or_fails_when_required() -> None:
    words = _words("natalie")
    provenance = _provenance()
    response = WordModelProviderResponse(
        request_digest=decoder_word_input_sha256(words),
        provenance=provenance,
        proposals=(_proposal(words[0], "Natalie", casing_probability=0.4),),
    )
    optional = run_local_word_model_provider(
        _Provider(provenance, response),
        words,
        approval=_approval(provenance),
        execution_policy=WordModelExecutionPolicy(required=False),
    )
    assert optional.receipt.status is ProviderRunStatus.ABSTAINED
    assert optional.receipt.reason is ProviderAbstentionReason.LOW_CONFIDENCE

    with pytest.raises(RequiredWordModelProviderError) as error:
        run_local_word_model_provider(
            _Provider(provenance, response),
            words,
            approval=_approval(provenance),
            execution_policy=WordModelExecutionPolicy(required=True),
        )
    assert error.value.receipt.reason is ProviderAbstentionReason.LOW_CONFIDENCE


def test_provider_cannot_run_as_approved_without_baseline_improvement() -> None:
    provenance = _provenance()
    rejected = evaluate_word_model_quality(
        provenance,
        _metrics(improved=False),
        _metrics(improved=False),
    )
    assert not rejected.approved
    assert "punctuation_f1_gain_failed" in rejected.rejection_reasons

    words = _words("natalie")
    response = WordModelProviderResponse(
        request_digest=decoder_word_input_sha256(words),
        provenance=provenance,
        proposals=(_proposal(words[0], "Natalie"),),
    )
    result = run_local_word_model_provider(
        _Provider(provenance, response),
        words,
        approval=rejected,
        execution_policy=WordModelExecutionPolicy(required=False),
    )
    assert result.receipt.reason is ProviderAbstentionReason.UNAPPROVED_PROVIDER


def test_quality_gate_rejects_dataset_drift_and_nonlexical_candidate() -> None:
    provenance = _provenance()
    baseline = _metrics(improved=False)
    candidate = replace(
        _metrics(improved=True),
        evaluation_dataset_sha256="8" * 64,
        lexical_integrity_rate=0.999,
    )

    receipt = evaluate_word_model_quality(
        provenance,
        baseline,
        candidate,
        policy=WordModelQualityPolicy(),
    )

    assert not receipt.approved
    assert receipt.rejection_reasons == (
        "evaluation_dataset_mismatch",
        "lexical_integrity_failed",
    )


def test_provenance_digest_binds_license_locality_revision_and_files() -> None:
    provenance = _provenance()
    original = word_model_provenance_sha256(provenance)
    assert (
        word_model_provenance_sha256(replace(provenance, license_expression="Apache-2.0"))
        != original
    )
    assert (
        word_model_provenance_sha256(replace(provenance, locality=ProviderLocality.LOCAL_PROCESS))
        != original
    )
    assert word_model_provenance_sha256(replace(provenance, model_revision="different")) != original
    with pytest.raises(ValueError, match="normalized and relative"):
        ModelFileContract("../escape.onnx", "9" * 64, 1)
