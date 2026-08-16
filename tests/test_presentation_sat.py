# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

import local_transcript_max.presentation_sat as sat_module
from local_transcript_max.presentation import (
    DecoderWord,
    EvidenceRole,
    PresentationProposal,
    ScorerOutput,
    ScorerProvenance,
    TrailingPunctuation,
    apply_presentation_plan,
    decode_presentation,
    lexical_text_digest,
)
from local_transcript_max.presentation_sat import (
    PRODUCTION_PROPOSAL_SOURCES,
    SAT_MODEL_ID,
    SAT_MODEL_REVISION,
    SAT_SOURCE_KEY,
    SAT_SOURCE_REGISTRATION,
    SAT_TOKENIZER_ID,
    SAT_TOKENIZER_REVISION,
    WTPSPLIT_REVISION,
    WTPSPLIT_VERSION,
    ArtifactFile,
    SaTBoundaryScorer,
    SaTMode,
    SaTScorerError,
    SaTScoreStatus,
    SaTSourceOptions,
    create_registered_proposal_source,
    deserialize_sat_score_receipt,
    registered_production_proposal_sources,
    run_registered_sat_source,
    sat_score_receipt_sha256,
    sat_score_receipt_to_dict,
    serialize_sat_score_receipt,
    validate_sat_score_receipt_payload,
)


class FakeBackend:
    def __init__(
        self,
        probabilities: object,
        *,
        after_inference: Callable[[], None] | None = None,
    ) -> None:
        self.probabilities = probabilities
        self.after_inference = after_inference
        self.calls: list[dict[str, object]] = []

    def predict_proba(
        self,
        text: str,
        *,
        stride: int,
        block_size: int,
        batch_size: int,
        weighting: str,
        remove_whitespace_before_inference: bool,
        verbose: bool,
    ) -> object:
        self.calls.append(
            {
                "text": text,
                "stride": stride,
                "block_size": block_size,
                "batch_size": batch_size,
                "weighting": weighting,
                "remove_whitespace_before_inference": (remove_whitespace_before_inference),
                "verbose": verbose,
            }
        )
        if isinstance(self.probabilities, Exception):
            raise self.probabilities
        if self.after_inference is not None:
            self.after_inference()
        return self.probabilities


def _artifact_contract(data: bytes) -> ArtifactFile:
    return ArtifactFile(size=len(data), sha256=hashlib.sha256(data).hexdigest())


def _local_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    model = tmp_path / "model"
    tokenizer = tmp_path / "tokenizer"
    model.mkdir()
    tokenizer.mkdir()
    model_files = {"config.json": b"model-config", "model_optimized.onnx": b"onnx"}
    tokenizer_files = {
        "config.json": b"tokenizer-config",
        "sentencepiece.bpe.model": b"sentencepiece",
        "tokenizer.json": b"tokenizer",
        "tokenizer_config.json": b"tokenizer-settings",
    }
    for name, data in model_files.items():
        (model / name).write_bytes(data)
    for name, data in tokenizer_files.items():
        (tokenizer / name).write_bytes(data)
    monkeypatch.setattr(
        sat_module,
        "SAT_MODEL_FILES",
        {name: _artifact_contract(data) for name, data in model_files.items()},
    )
    monkeypatch.setattr(
        sat_module,
        "SAT_TOKENIZER_FILES",
        {name: _artifact_contract(data) for name, data in tokenizer_files.items()},
    )
    return model, tokenizer


def _words() -> tuple[DecoderWord, ...]:
    return (
        DecoderWord.from_text(0, "Hello"),
        DecoderWord.from_text(1, "there"),
    )


def _probabilities(first: float = 0.8, second: float = 0.25) -> list[float]:
    probabilities = [0.0] * len("Hello there")
    probabilities[4] = first
    probabilities[10] = second
    return probabilities


def test_exact_local_success_emits_bound_proposals_and_deterministic_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    backend = FakeBackend(_probabilities())
    observed_paths: list[tuple[Path, Path]] = []

    def factory(model_path: Path, tokenizer_path: Path) -> FakeBackend:
        observed_paths.append((model_path, tokenizer_path))
        return backend

    scorer = SaTBoundaryScorer(model, tokenizer, backend_factory=factory)
    result = scorer.score_with_receipt(_words(), mode=SaTMode.OPTIONAL)
    repeated = scorer.score_with_receipt(_words(), mode=SaTMode.OPTIONAL)

    assert observed_paths == [(model.resolve(), tokenizer.resolve())]
    expected_call = {
        "text": "Hello there",
        "stride": 256,
        "block_size": 512,
        "batch_size": 1,
        "weighting": "uniform",
        "remove_whitespace_before_inference": False,
        "verbose": False,
    }
    assert backend.calls == [expected_call, expected_call]
    assert result == repeated
    assert result.receipt.status is SaTScoreStatus.SUCCEEDED
    assert result.receipt.reason_code is None
    assert result.receipt.abstention_visible is False
    assert result.receipt.offline_enforced is True
    assert result.receipt.network_access is False
    assert result.receipt.turn_features_used is False
    assert result.receipt.cue_boundaries_used is False
    assert result.receipt.proposal_count == len(_words()) * len(TrailingPunctuation)
    assert result.receipt.model_asset is not None
    assert result.receipt.model_asset.verification_passes == 2
    assert result.receipt.tokenizer_asset is not None
    assert result.receipt.tokenizer_asset.verification_passes == 2
    assert result.receipt.input_digest is not None
    assert result.receipt.lexical_input_digest is not None
    assert result.receipt.output_digest is not None

    by_word = {
        index: tuple(
            proposal for proposal in result.output.proposals if proposal.word_index == index
        )
        for index in range(2)
    }
    assert [item.trailing_punctuation for item in by_word[0]] == list(TrailingPunctuation)
    assert [item.punctuation_probability for item in by_word[0]] == pytest.approx(
        [1.0 / len(TrailingPunctuation)] * len(TrailingPunctuation)
    )
    assert [item.sentence_break_probability for item in by_word[0]] == [0.8] * len(
        TrailingPunctuation
    )
    assert [item.sentence_break_probability for item in by_word[1]] == [0.25] * len(
        TrailingPunctuation
    )
    assert all(item.paragraph_break_probability == 0.5 for item in result.output.proposals)
    assert all(item.capitalization_probability == 0.5 for item in result.output.proposals)
    assert len(decode_presentation(_words(), (result.output,)).decisions) == 2

    serialized = serialize_sat_score_receipt(result.receipt)
    assert serialized.endswith(b"\n")
    assert serialize_sat_score_receipt(repeated.receipt) == serialized
    assert sat_score_receipt_sha256(result.receipt) == hashlib.sha256(serialized).hexdigest()
    payload = sat_score_receipt_to_dict(result.receipt)
    assert payload["status"] == "succeeded"
    assert validate_sat_score_receipt_payload(payload) == result.receipt
    assert deserialize_sat_score_receipt(serialized) == result.receipt
    assert sat_score_receipt_sha256(deserialize_sat_score_receipt(serialized)) == (
        sat_score_receipt_sha256(result.receipt)
    )
    assert str(tmp_path).encode() not in serialized


@pytest.mark.parametrize("mode", [SaTMode.OPTIONAL, SaTMode.REQUIRED])
def test_zero_signal_verifies_assets_but_never_loads_backend_or_contributes(
    mode: SaTMode,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    word = DecoderWord.from_text(
        0,
        "ready",
        question_evidence=0.95,
        question_scope_start_index=0,
    )
    active = ScorerOutput(
        provenance=ScorerProvenance("question-fixture", "1.0"),
        proposals=tuple(
            PresentationProposal(
                word_index=0,
                lexical_signature=word.lexical_signature,
                lexical_text_digest=lexical_text_digest(word.lexical_text),
                trailing_punctuation=punctuation,
                punctuation_probability=probability,
                capitalization_probability=0.5,
                sentence_break_probability=0.95,
                paragraph_break_probability=0.5,
            )
            for punctuation, probability in (
                (TrailingPunctuation.PERIOD, 0.1),
                (TrailingPunctuation.QUESTION, 0.9),
            )
        ),
    )
    backend_calls = 0

    def forbidden_backend(_model: Path, _tokenizer: Path) -> FakeBackend:
        nonlocal backend_calls
        backend_calls += 1
        return FakeBackend([])

    result = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=forbidden_backend,
    ).score_with_receipt((word,), mode=mode, signal_weight=0.0)
    baseline = decode_presentation((word,), (active,))
    combined = decode_presentation((word,), (active, result.output))

    assert backend_calls == 0
    assert result.receipt.status is SaTScoreStatus.SUCCEEDED
    assert result.receipt.reason_code == "signal_weight_zero"
    assert result.receipt.abstention_visible is True
    assert result.receipt.proposal_count == 0
    assert result.receipt.model_asset is not None
    assert result.receipt.model_asset.verification_passes == 2
    assert result.receipt.tokenizer_asset is not None
    assert result.receipt.tokenizer_asset.verification_passes == 2
    assert result.output.proposals == ()
    assert result.output.abstention_reason == "signal_weight_zero"
    assert (
        validate_sat_score_receipt_payload(sat_score_receipt_to_dict(result.receipt))
        == result.receipt
    )
    assert (
        deserialize_sat_score_receipt(serialize_sat_score_receipt(result.receipt)) == result.receipt
    )
    assert combined.decisions == baseline.decisions
    assert combined.total_score == baseline.total_score
    assert apply_presentation_plan((word,), combined) == apply_presentation_plan((word,), baseline)
    assert combined.abstentions[0].provenance == result.output.provenance
    assert combined.abstentions[0].reason == "signal_weight_zero"
    assert result.output.provenance not in combined.decisions[0].contributing_provenance


def test_receipt_payload_validation_rejects_tampering_and_noncanonical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    receipt = (
        SaTBoundaryScorer(
            model,
            tokenizer,
            backend_factory=lambda _model, _tokenizer: FakeBackend(_probabilities()),
        )
        .score_with_receipt(_words())
        .receipt
    )
    payload = sat_score_receipt_to_dict(receipt)

    tampered_cases: list[dict[str, object]] = []
    extra = deepcopy(payload)
    extra["receipt_sha256"] = sat_score_receipt_sha256(receipt)
    tampered_cases.append(extra)
    calibration = deepcopy(payload)
    assert isinstance(calibration["calibration"], dict)
    calibration["calibration"]["evidence_digest"] = "0" * 64
    tampered_cases.append(calibration)
    provenance = deepcopy(payload)
    assert isinstance(provenance["scorer_provenance"], dict)
    provenance["scorer_provenance"]["configuration_digest"] = "0" * 64
    tampered_cases.append(provenance)
    asset = deepcopy(payload)
    assert isinstance(asset["model_asset"], dict)
    asset["model_asset"]["manifest_digest"] = "0" * 64
    tampered_cases.append(asset)
    missing = deepcopy(payload)
    del missing["output_digest"]
    tampered_cases.append(missing)
    impossible_zero = deepcopy(payload)
    impossible_zero["proposal_count"] = 0
    tampered_cases.append(impossible_zero)

    for tampered in tampered_cases:
        with pytest.raises(ValueError):
            validate_sat_score_receipt_payload(tampered)

    noncanonical = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    with pytest.raises(ValueError, match="canonical form"):
        deserialize_sat_score_receipt(noncanonical)


def test_signal_weight_is_a_deterministic_neutral_shrink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    result = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=lambda _model, _tokenizer: FakeBackend(
            _probabilities(first=0.9, second=0.1)
        ),
    ).score_with_receipt(_words(), signal_weight=0.5)

    assert all(
        proposal.punctuation_probability == pytest.approx(1.0 / len(TrailingPunctuation))
        for proposal in result.output.proposals
    )
    assert [
        proposal.sentence_break_probability
        for proposal in result.output.proposals[: len(TrailingPunctuation)]
    ] == pytest.approx([0.7] * len(TrailingPunctuation))
    assert [
        proposal.sentence_break_probability
        for proposal in result.output.proposals[len(TrailingPunctuation) :]
    ] == pytest.approx([0.3] * len(TrailingPunctuation))
    assert result.receipt.signal_weight == 0.5


@pytest.mark.parametrize("signal_weight", [0.0, 0.75, 1.0])
def test_sat_boundary_evidence_does_not_override_question_punctuation_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal_weight: float,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    word = DecoderWord.from_text(
        0,
        "ready",
        question_evidence=0.95,
        question_scope_start_index=0,
    )
    probabilities = [0.0] * len("ready")
    probabilities[-1] = 0.99
    sat = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=lambda _model, _tokenizer: FakeBackend(probabilities),
    ).score_with_receipt((word,), signal_weight=signal_weight)
    question = ScorerOutput(
        provenance=ScorerProvenance("question-fixture", "1.0"),
        proposals=tuple(
            PresentationProposal(
                word_index=0,
                lexical_signature=word.lexical_signature,
                lexical_text_digest=lexical_text_digest(word.lexical_text),
                trailing_punctuation=punctuation,
                punctuation_probability=0.5,
                capitalization_probability=0.5,
                sentence_break_probability=0.95,
                paragraph_break_probability=0.0,
            )
            for punctuation in (
                TrailingPunctuation.PERIOD,
                TrailingPunctuation.QUESTION,
            )
        ),
    )

    baseline = decode_presentation((word,), (question,))
    combined = decode_presentation((word,), (question, sat.output))

    assert baseline.decisions[0].trailing_punctuation is TrailingPunctuation.QUESTION
    assert combined.decisions[0].trailing_punctuation is TrailingPunctuation.QUESTION


def test_turn_timing_and_question_context_are_bound_but_never_used_as_sat_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    scorer = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=lambda _model, _tokenizer: FakeBackend(_probabilities()),
    )
    lexical_only = _words()
    contextual = (
        DecoderWord.from_text(
            0,
            "Hello",
            start_seconds=2.0,
            end_seconds=2.4,
            pause_after_seconds=1.2,
            speaker_id="SPEAKER_00",
            begins_turn=True,
            ends_turn=True,
            incomplete_continuation=True,
            unsupported_same_speaker_short_continuation=True,
            question_evidence=0.9,
            question_scope_start_index=0,
        ),
        DecoderWord.from_text(
            1,
            "there",
            start_seconds=3.6,
            end_seconds=4.0,
            speaker_id="SPEAKER_01",
            begins_turn=True,
            ends_turn=True,
            question_evidence=1.0,
            question_scope_start_index=1,
        ),
    )

    plain_result = scorer.score_with_receipt(lexical_only)
    contextual_result = scorer.score_with_receipt(contextual)

    assert plain_result.output == contextual_result.output
    assert plain_result.receipt.lexical_input_digest == (
        contextual_result.receipt.lexical_input_digest
    )
    assert plain_result.receipt.input_digest != contextual_result.receipt.input_digest
    assert contextual_result.receipt.turn_features_used is False
    assert contextual_result.receipt.cue_boundaries_used is False


def test_optional_unconfigured_source_returns_visible_stable_abstention() -> None:
    options = SaTSourceOptions(mode=SaTMode.OPTIONAL, signal_weight=0.75)

    result = run_registered_sat_source(_words(), options)
    repeated = run_registered_sat_source(_words(), options)

    assert result is not None
    assert repeated is not None
    assert result == repeated
    assert result.output.abstention_reason == "assets_not_configured"
    assert result.receipt.status is SaTScoreStatus.ABSTAINED
    assert result.receipt.reason_code == "assets_not_configured"
    assert result.receipt.abstention_visible is True
    assert result.receipt.word_count == 2
    assert result.receipt.input_digest is not None
    assert result.receipt.lexical_input_digest is not None
    assert result.receipt.model_asset is None
    assert result.receipt.tokenizer_asset is None
    assert sat_score_receipt_to_dict(result.receipt)["reason_code"] == ("assets_not_configured")
    with pytest.raises(FrozenInstanceError):
        result.receipt.status = SaTScoreStatus.SUCCEEDED  # type: ignore[misc]
    with pytest.raises(ValueError, match="visibly abstain"):
        replace(result.receipt, abstention_visible=False)
    with pytest.raises(ValueError, match="cannot publish proposals"):
        replace(result.receipt, proposal_count=1)
    with pytest.raises(ValueError, match="schema version"):
        replace(result.receipt, schema_version="untrusted")


def test_mode_and_path_contract_rejects_unsafe_partial_configurations() -> None:
    assert run_registered_sat_source(_words(), SaTSourceOptions()) is None
    assert SaTSourceOptions(mode=SaTMode.OPTIONAL).model_dir is None

    with pytest.raises(ValueError, match="configured together"):
        SaTSourceOptions(mode=SaTMode.OPTIONAL, model_dir=Path("model"))
    with pytest.raises(ValueError, match="required mode needs exact"):
        SaTSourceOptions(mode=SaTMode.REQUIRED)
    with pytest.raises(ValueError, match="off mode must not retain"):
        SaTSourceOptions(
            mode=SaTMode.OFF,
            model_dir=Path("model"),
            tokenizer_dir=Path("tokenizer"),
        )


@pytest.mark.parametrize("signal_weight", [0.0, 1.0])
def test_optional_missing_assets_abstains_and_required_missing_assets_fails_closed(
    signal_weight: float,
    tmp_path: Path,
) -> None:
    optional = SaTSourceOptions(
        mode=SaTMode.OPTIONAL,
        model_dir=tmp_path / "missing-model",
        tokenizer_dir=tmp_path / "missing-tokenizer",
        signal_weight=signal_weight,
    )
    required = SaTSourceOptions(
        mode=SaTMode.REQUIRED,
        model_dir=tmp_path / "missing-model",
        tokenizer_dir=tmp_path / "missing-tokenizer",
        signal_weight=signal_weight,
    )

    optional_result = run_registered_sat_source(_words(), optional)

    assert optional_result is not None
    assert optional_result.receipt.status is SaTScoreStatus.ABSTAINED
    assert optional_result.receipt.reason_code == "missing_model_directory"
    assert optional_result.output.abstention_reason == "missing_model_directory"
    with pytest.raises(SaTScorerError, match="missing_model_directory") as raised:
        run_registered_sat_source(_words(), required)
    assert raised.value.receipt is not None
    assert raised.value.receipt.status is SaTScoreStatus.FAILED
    assert raised.value.receipt.mode is SaTMode.REQUIRED
    assert raised.value.receipt.reason_code == "missing_model_directory"
    assert raised.value.receipt.abstention_visible is False


def test_offline_contract_abstains_optional_and_fails_required_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    backend_calls = 0

    def factory(_model: Path, _tokenizer: Path) -> FakeBackend:
        nonlocal backend_calls
        backend_calls += 1
        return FakeBackend(_probabilities())

    optional_result = run_registered_sat_source(
        _words(),
        SaTSourceOptions(
            mode=SaTMode.OPTIONAL,
            model_dir=model,
            tokenizer_dir=tokenizer,
            offline=False,
        ),
        backend_factory=factory,
    )
    assert optional_result is not None
    assert optional_result.receipt.reason_code == "offline_required"
    assert backend_calls == 0

    with pytest.raises(SaTScorerError, match="offline_required") as raised:
        run_registered_sat_source(
            _words(),
            SaTSourceOptions(
                mode=SaTMode.REQUIRED,
                model_dir=model,
                tokenizer_dir=tokenizer,
                offline=False,
            ),
            backend_factory=factory,
        )
    assert raised.value.receipt is not None
    assert raised.value.receipt.reason_code == "offline_required"
    assert backend_calls == 0

    absent = run_registered_sat_source(
        _words(),
        SaTSourceOptions(mode=SaTMode.OPTIONAL, offline=False),
    )
    assert absent is not None
    assert absent.receipt.reason_code == "offline_required"


def test_invalid_unconfigured_input_is_a_visible_optional_abstention() -> None:
    invalid = (DecoderWord.from_text(1, "Hello"),)

    result = run_registered_sat_source(
        invalid,
        SaTSourceOptions(mode=SaTMode.OPTIONAL),
    )

    assert result is not None
    assert result.output.abstention_reason == "invalid_word_sequence"
    assert result.receipt.reason_code == "invalid_word_sequence"
    assert result.receipt.word_count == 0
    assert result.receipt.input_digest is None


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("tamper", "invalid_model_artifacts"),
        ("extra", "invalid_tokenizer_artifacts"),
        ("hardlink", "invalid_model_artifacts"),
    ],
)
def test_asset_contract_rejects_tampering_extras_and_hardlinks_before_backend(
    mutation: str,
    expected_reason: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    if mutation == "tamper":
        (model / "model_optimized.onnx").write_bytes(b"tampered")
    elif mutation == "extra":
        (tokenizer / "unexpected.json").write_text("{}", encoding="utf-8")
    else:
        os.link(model / "config.json", tmp_path / "model-config-hardlink")
    backend_calls = 0

    def factory(_model: Path, _tokenizer: Path) -> FakeBackend:
        nonlocal backend_calls
        backend_calls += 1
        return FakeBackend(_probabilities())

    result = SaTBoundaryScorer(model, tokenizer, backend_factory=factory).score_with_receipt(
        _words()
    )

    assert result.output.abstention_reason == expected_reason
    assert result.receipt.reason_code == expected_reason
    assert backend_calls == 0


def test_asset_mutation_during_inference_discards_all_proposals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)

    def mutate_model() -> None:
        (model / "config.json").write_bytes(b"changed-after-inference")

    result = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=lambda _model, _tokenizer: FakeBackend(
            _probabilities(),
            after_inference=mutate_model,
        ),
    ).score_with_receipt(_words())

    assert result.receipt.status is SaTScoreStatus.ABSTAINED
    assert result.receipt.reason_code == "invalid_model_artifacts"
    assert result.receipt.proposal_count == 0
    assert result.output.proposals == ()


def test_symlinked_asset_root_is_rejected_before_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    linked_model = tmp_path / "linked-model"
    linked_model.symlink_to(model, target_is_directory=True)

    result = SaTBoundaryScorer(
        linked_model,
        tokenizer,
        backend_factory=lambda _model, _tokenizer: pytest.fail(
            "backend must not load for a linked asset root"
        ),
    ).score_with_receipt(_words())

    assert result.receipt.reason_code == "missing_model_directory"


@pytest.mark.parametrize(
    "probabilities",
    [
        [0.0],
        [0.0] * 10 + [float("nan")],
        [0.0] * 10 + [1.1],
        [False] * 11,
        "not-probabilities",
        {"unsafe": 1.0},
    ],
)
def test_malformed_backend_output_abstains_without_leaking_details(
    probabilities: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    result = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=lambda _model, _tokenizer: FakeBackend(probabilities),
    ).score_with_receipt(_words())

    assert result.output.abstention_reason == "malformed_probabilities"
    assert result.receipt.reason_code == "malformed_probabilities"
    assert result.receipt.proposal_count == 0


def test_backend_failures_are_safe_and_specific_adapter_codes_survive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)

    def unavailable(_model: Path, _tokenizer: Path) -> FakeBackend:
        raise ModuleNotFoundError("private import detail")

    def wrong_version(_model: Path, _tokenizer: Path) -> FakeBackend:
        raise SaTScorerError("backend_version_mismatch")

    unavailable_result = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=unavailable,
    ).score_with_receipt(_words())
    wrong_version_result = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=wrong_version,
    ).score_with_receipt(_words())
    inference_result = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=lambda _model, _tokenizer: FakeBackend(
            RuntimeError(f"private path: {tmp_path}")
        ),
    ).score_with_receipt(_words())

    assert unavailable_result.receipt.reason_code == "backend_unavailable"
    assert wrong_version_result.receipt.reason_code == "backend_version_mismatch"
    assert inference_result.receipt.reason_code == "inference_failed"
    assert str(tmp_path).encode() not in serialize_sat_score_receipt(inference_result.receipt)


def test_default_backend_constructor_is_exact_local_cpu_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    sentinel = FakeBackend([])

    class FakeModule:
        @staticmethod
        def SaT(*args: object, **kwargs: object) -> FakeBackend:
            calls.append((args, kwargs))
            return sentinel

    monkeypatch.setattr(sat_module.importlib.metadata, "version", lambda _name: "2.2.1")
    monkeypatch.setattr(sat_module.importlib, "import_module", lambda _name: FakeModule)

    result = sat_module._load_local_backend(Path("/model"), Path("/tokenizer"))

    assert result is sentinel
    assert calls == [
        (
            ("/model",),
            {
                "tokenizer_name_or_path": "/tokenizer",
                "from_pretrained_kwargs": {"local_files_only": True},
                "ort_providers": ["CPUExecutionProvider"],
                "hub_prefix": None,
            },
        )
    ]


def test_backend_version_mismatch_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sat_module.importlib.metadata, "version", lambda _name: "2.2.0")

    with pytest.raises(SaTScorerError, match="backend_version_mismatch"):
        sat_module._load_local_backend(Path("/model"), Path("/tokenizer"))


def test_registry_is_executable_immutable_and_excluded_from_standalone(
    tmp_path: Path,
) -> None:
    assert registered_production_proposal_sources() == (SAT_SOURCE_REGISTRATION,)
    assert PRODUCTION_PROPOSAL_SOURCES[SAT_SOURCE_KEY].production is True
    assert PRODUCTION_PROPOSAL_SOURCES[SAT_SOURCE_KEY].local_only is True
    assert PRODUCTION_PROPOSAL_SOURCES[SAT_SOURCE_KEY].offline_required is True
    assert PRODUCTION_PROPOSAL_SOURCES[SAT_SOURCE_KEY].standalone_included is False
    assert SAT_SOURCE_REGISTRATION.runtime_extra == "presentation-sat"
    with pytest.raises(TypeError):
        PRODUCTION_PROPOSAL_SOURCES["unsafe"] = SAT_SOURCE_REGISTRATION  # type: ignore[index]
    with pytest.raises(KeyError, match="unknown production presentation source"):
        create_registered_proposal_source(
            "unknown",
            tmp_path / "model",
            tmp_path / "tokenizer",
        )
    assert isinstance(
        create_registered_proposal_source(
            SAT_SOURCE_KEY,
            tmp_path / "model",
            tmp_path / "tokenizer",
            backend_factory=lambda _model, _tokenizer: FakeBackend([]),
        ),
        SaTBoundaryScorer,
    )


def test_pins_and_proposal_surface_have_no_lexical_mutation_operation() -> None:
    assert WTPSPLIT_VERSION == "2.2.1"
    assert WTPSPLIT_REVISION == "e48922c0ad240c7d2cb1ef8afbc0cd413372bc83"
    assert (SAT_MODEL_ID, SAT_MODEL_REVISION) == (
        "segment-any-text/sat-3l-sm",
        "137da054051ad9f1eac42025f758db4ac9f22535",
    )
    assert (SAT_TOKENIZER_ID, SAT_TOKENIZER_REVISION) == (
        "facebookAI/xlm-roberta-base",
        "e73636d4f797dec63c3081bb6ed5c7b0bb3f2089",
    )
    proposal_fields = {field.name for field in fields(PresentationProposal)}
    assert proposal_fields == {
        "word_index",
        "lexical_signature",
        "lexical_text_digest",
        "trailing_punctuation",
        "punctuation_probability",
        "capitalization_probability",
        "sentence_break_probability",
        "paragraph_break_probability",
        "evidence_roles",
        "casing_surface",
        "casing_probability",
    }
    assert not proposal_fields & {
        "candidate_text",
        "replacement_text",
        "lexical_text",
        "word",
    }


def test_sat_emits_boundary_only_evidence_and_never_selects_a_mark_class(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, tokenizer = _local_artifacts(tmp_path, monkeypatch)
    scorer = SaTBoundaryScorer(
        model,
        tokenizer,
        backend_factory=lambda _model, _tokenizer: FakeBackend(_probabilities()),
    )
    words = _words()

    result = scorer.score(words)

    assert all(
        proposal.evidence_roles == (EvidenceRole.BOUNDARY_ONLY,) for proposal in result.proposals
    )
    assert all(
        proposal.punctuation_probability == 1.0 / len(TrailingPunctuation)
        for proposal in result.proposals
    )


def test_backend_is_lazy_and_provenance_is_available_without_assets() -> None:
    calls = 0

    def factory(_model: Path, _tokenizer: Path) -> FakeBackend:
        nonlocal calls
        calls += 1
        return FakeBackend([])

    scorer = SaTBoundaryScorer(
        Path("missing-model"),
        Path("missing-tokenizer"),
        backend_factory=factory,
    )

    assert calls == 0
    assert scorer.provenance.scorer_id == "wtpsplit-sat-3l-sm"
    assert scorer.provenance.scorer_version == WTPSPLIT_VERSION
    assert scorer.provenance.configuration_digest is not None
    assert len(scorer.provenance.configuration_digest) == 64
