# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, fields, replace

import pytest

import local_transcript_max.presentation as presentation_module
from local_transcript_max.presentation import (
    BoundarySupport,
    DecoderWord,
    EvidenceRole,
    IndexedEditorialAdjudicationEvidence,
    IndexedExactMarkEvidence,
    PresentationDecision,
    PresentationPlan,
    PresentationProposal,
    RetainedEvidenceDisposition,
    RetainedScorerEvidence,
    ScorerOutput,
    ScorerProvenance,
    TrailingPunctuation,
    apply_presentation_plan,
    build_indexed_editorial_adjudication_scorer_output,
    build_indexed_exact_mark_scorer_output,
    compose_retained_presentation,
    decode_presentation,
    deserialize_presentation_plan,
    lexical_signature,
    lexical_text_digest,
    presentation_input_digest,
    presentation_plan_sha256,
    scorer_output_sha256,
    serialize_presentation_plan,
)

_FORGED_SCORER = ScorerProvenance("forged-boundary", "1")
_FORGED_BOUNDARY_SCORER = ScorerProvenance("forged-corroboration", "1")


def _forged_support(*, typed_question: bool = False) -> BoundarySupport:
    return BoundarySupport(
        typed_question=typed_question,
        exact_mark_provenance=(_FORGED_SCORER,),
        boundary_provenance=(_FORGED_BOUNDARY_SCORER,),
    )


def _scorer_support(
    exact: ScorerOutput,
    boundary: ScorerOutput | None = None,
    typed_question: bool = False,
) -> BoundarySupport:
    return BoundarySupport(
        typed_question=typed_question,
        exact_mark_provenance=(exact.provenance,),
        boundary_provenance=(
            (boundary.provenance,) if boundary is not None else (_FORGED_BOUNDARY_SCORER,)
        ),
    )


def _contributing(*outputs: ScorerOutput) -> tuple[ScorerProvenance, ...]:
    return tuple(
        sorted(
            {output.provenance for output in outputs},
            key=lambda item: (item.scorer_id, item.scorer_version),
        )
    )


def _word(
    index: int,
    text: str,
    *,
    pause: float = 0.0,
    speaker: str | None = "SPEAKER_00",
    container: int | None = 0,
    begins_turn: bool = False,
    ends_turn: bool = False,
    incomplete: bool = False,
    incomplete_contraction: bool = False,
    incomplete_trailing: bool = False,
    incomplete_abandoned_fragment: bool = False,
    unsupported_short_continuation: bool = False,
    question: float = 0.0,
    question_scope_start: int | None = None,
    contraction_repetition: bool = False,
    contraction_correction: bool = False,
    contraction_question_tail: bool = False,
) -> DecoderWord:
    return DecoderWord.from_text(
        index,
        text,
        start_seconds=float(index),
        end_seconds=float(index) + 0.4,
        pause_after_seconds=pause,
        speaker_id=speaker,
        container_index=container,
        begins_turn=begins_turn,
        ends_turn=ends_turn,
        incomplete_continuation=incomplete,
        incomplete_contraction=incomplete_contraction,
        incomplete_trailing_fragment=incomplete_trailing,
        incomplete_abandoned_fragment=incomplete_abandoned_fragment,
        unsupported_same_speaker_short_continuation=unsupported_short_continuation,
        contraction_repetition_exception=contraction_repetition,
        contraction_correction_exception=contraction_correction,
        contraction_question_tail_exception=contraction_question_tail,
        question_evidence=question,
        question_scope_start_index=question_scope_start,
    )


def _proposal(
    word: DecoderWord,
    punctuation: TrailingPunctuation,
    punctuation_probability: float,
    *,
    capitalization: float = 0.1,
    sentence_break: float = 0.1,
    paragraph_break: float = 0.01,
    casing_surface: str | None = None,
    casing_probability: float | None = None,
    roles: tuple[EvidenceRole, ...] = (
        EvidenceRole.EXACT_MARK,
        EvidenceRole.CASE_ONLY,
    ),
    source_mark_veto: bool = False,
) -> PresentationProposal:
    return PresentationProposal(
        word_index=word.word_index,
        lexical_signature=word.lexical_signature,
        lexical_text_digest=lexical_text_digest(word.lexical_text),
        trailing_punctuation=punctuation,
        punctuation_probability=punctuation_probability,
        capitalization_probability=capitalization,
        sentence_break_probability=sentence_break,
        paragraph_break_probability=paragraph_break,
        evidence_roles=roles,
        source_mark_veto=source_mark_veto,
        casing_surface=casing_surface,
        casing_probability=casing_probability,
    )


def _output(
    scorer_id: str,
    *proposals: PresentationProposal,
) -> ScorerOutput:
    return ScorerOutput(
        provenance=ScorerProvenance(scorer_id, "1.0"),
        proposals=tuple(proposals),
    )


def _boundary_output(
    scorer_id: str,
    words: tuple[DecoderWord, ...],
    *,
    probability: float = 0.95,
) -> ScorerOutput:
    return _output(
        scorer_id,
        *(
            _proposal(
                word,
                TrailingPunctuation.NONE,
                1.0 / len(TrailingPunctuation),
                capitalization=0.5,
                sentence_break=probability,
                paragraph_break=0.5,
                roles=(EvidenceRole.BOUNDARY_ONLY,),
            )
            for word in words
        ),
    )


def _corroborated(
    exact: ScorerOutput,
    *words: DecoderWord,
    probability: float = 0.95,
) -> tuple[ScorerOutput, ScorerOutput]:
    return (
        exact,
        _boundary_output(
            f"{exact.provenance.scorer_id}-boundary",
            tuple(words),
            probability=probability,
        ),
    )


def _indexed_mark_evidence(
    word_index: int,
    selected: TrailingPunctuation | None,
) -> IndexedExactMarkEvidence:
    selected_probability = 0.9
    other_probability = (1.0 - selected_probability) / (len(TrailingPunctuation) - 1)
    return IndexedExactMarkEvidence(
        word_index=word_index,
        punctuation_probabilities=tuple(
            (
                punctuation,
                (
                    1.0 / len(TrailingPunctuation)
                    if selected is None
                    else selected_probability
                    if punctuation is selected
                    else other_probability
                ),
            )
            for punctuation in TrailingPunctuation
        ),
    )


def _editorial_evidence(
    word_index: int,
    selected: TrailingPunctuation | None,
    *,
    sentence_break: float = 0.5,
    source_mark_veto: bool = False,
) -> IndexedEditorialAdjudicationEvidence:
    selected_probability = 0.99
    other_probability = (1.0 - selected_probability) / (len(TrailingPunctuation) - 1)
    return IndexedEditorialAdjudicationEvidence(
        word_index=word_index,
        punctuation_probabilities=tuple(
            (
                punctuation,
                (
                    1.0 / len(TrailingPunctuation)
                    if selected is None
                    else selected_probability
                    if punctuation is selected
                    else other_probability
                ),
            )
            for punctuation in TrailingPunctuation
        ),
        sentence_break_probability=sentence_break,
        source_mark_veto=source_mark_veto,
    )


def _retained(
    words: tuple[DecoderWord, ...],
    output: ScorerOutput,
    *,
    receipt_character: str = "a",
    disposition: RetainedEvidenceDisposition = RetainedEvidenceDisposition.ADMIT,
) -> RetainedScorerEvidence:
    return RetainedScorerEvidence(
        output=output,
        presentation_input_digest=presentation_input_digest(words),
        output_sha256=scorer_output_sha256(output),
        receipt_sha256=receipt_character * 64,
        disposition=disposition,
        abstention_reason=(
            None if disposition is RetainedEvidenceDisposition.ADMIT else "quality_gate_failed"
        ),
    )


def test_turn_boundary_is_evidence_but_not_a_sentence_boundary() -> None:
    words = (
        _word(0, "right", speaker="SPEAKER_00", begins_turn=True, ends_turn=True),
        _word(1, "yeah", speaker="SPEAKER_01", begins_turn=True, ends_turn=True),
        _word(2, "exactly", speaker="SPEAKER_00", begins_turn=True, ends_turn=True),
    )

    plan = decode_presentation(words, ())

    assert all(not decision.sentence_break for decision in plan.decisions[:-1])
    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.NONE
    assert apply_presentation_plan(words, plan)[0] == "Right"


@pytest.mark.parametrize("with_boundary", (False, True))
def test_boundary_only_and_timing_evidence_never_select_any_mark_class(
    with_boundary: bool,
) -> None:
    word = _word(0, "ready", pause=2.0)
    boundary = _boundary_output("boundary-only", (word,), probability=0.99)

    plan = decode_presentation((word,), (boundary,) if with_boundary else ())

    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.NONE
    assert not plan.decisions[0].sentence_break


@pytest.mark.parametrize(
    "punctuation",
    tuple(value for value in TrailingPunctuation if value is not TrailingPunctuation.NONE),
)
def test_every_source_authored_mark_class_is_preserved(
    punctuation: TrailingPunctuation,
) -> None:
    word = _word(
        0,
        f"ready{punctuation.value}",
        question=0.95 if punctuation is TrailingPunctuation.QUESTION else 0.0,
        question_scope_start=0 if punctuation is TrailingPunctuation.QUESTION else None,
    )

    plan = decode_presentation((word,), ())

    assert plan.decisions[0].trailing_punctuation is punctuation


@pytest.mark.parametrize(
    "punctuation",
    (
        TrailingPunctuation.COMMA,
        TrailingPunctuation.SEMICOLON,
        TrailingPunctuation.COLON,
    ),
)
def test_exact_mark_evidence_can_select_supported_weak_mark(
    punctuation: TrailingPunctuation,
) -> None:
    word = _word(0, "ready")
    exact = _output(
        "exact-weak-mark",
        _proposal(word, punctuation, 0.999),
    )

    plan = decode_presentation((word,), (exact,))

    assert plan.decisions[0].trailing_punctuation is punctuation


def test_replay_rejects_boundary_only_forged_weak_mark() -> None:
    word = _word(0, "ready", pause=2.0)
    boundary = _boundary_output("boundary-only", (word,), probability=0.99)
    plan = decode_presentation((word,), (boundary,))
    forged = replace(
        plan,
        decisions=(
            replace(
                plan.decisions[0],
                trailing_punctuation=TrailingPunctuation.COMMA,
                contributing_provenance=(),
            ),
        ),
    )

    with pytest.raises(ValueError, match="mark class lacks exact typed support"):
        apply_presentation_plan((word,), forged)


def test_model_strong_mark_requires_distinct_boundary_evidence_at_pinned_threshold() -> None:
    word = _word(0, "ready")
    exact = _output(
        "exact-mark-model",
        _proposal(word, TrailingPunctuation.PERIOD, 0.999, sentence_break=0.999),
    )

    below = decode_presentation(
        (word,),
        (exact, _boundary_output("sat-below", (word,), probability=0.699999)),
    )
    admitted = decode_presentation(
        (word,),
        (exact, _boundary_output("sat-admitted", (word,), probability=0.70)),
    )

    assert not below.decisions[0].sentence_break
    assert admitted.decisions[0].trailing_punctuation is TrailingPunctuation.PERIOD
    assert admitted.decisions[0].boundary_support.supported


def test_one_scorer_cannot_self_corroborate_exact_mark_and_boundary_roles() -> None:
    word = _word(0, "ready")
    self_corroborating = _output(
        "self-corroborating",
        _proposal(
            word,
            TrailingPunctuation.PERIOD,
            0.999,
            sentence_break=0.999,
            roles=(
                EvidenceRole.EXACT_MARK,
                EvidenceRole.BOUNDARY_ONLY,
                EvidenceRole.CASE_ONLY,
            ),
        ),
    )

    plan = decode_presentation((word,), (self_corroborating,))

    assert not plan.decisions[0].sentence_break


def test_structural_veto_demotes_source_authored_strong_stop() -> None:
    words = (
        _word(0, "and.", incomplete=True),
        _word(1, "continued"),
    )

    plan = decode_presentation(words, ())

    assert plan.decisions[0].trailing_punctuation is not TrailingPunctuation.PERIOD
    assert not plan.decisions[0].sentence_break


@pytest.mark.parametrize(
    "punctuation",
    (
        TrailingPunctuation.COMMA,
        TrailingPunctuation.SEMICOLON,
        TrailingPunctuation.COLON,
    ),
)
def test_positive_incomplete_contraction_rejects_unsupported_weak_mark(
    punctuation: TrailingPunctuation,
) -> None:
    words = (_word(0, "I'm", incomplete_contraction=True), _word(1, "ready"))
    scorer = _output(
        "weak-mark-model",
        _proposal(words[0], punctuation, 0.999),
    )

    plan = decode_presentation(words, (scorer,))

    assert plan.decisions[0].trailing_punctuation is not punctuation


@pytest.mark.parametrize(
    "exception",
    ("repetition", "correction", "question-tail"),
)
def test_typed_contraction_exception_preserves_defensible_comma(exception: str) -> None:
    word = _word(
        0,
        "I'm",
        incomplete_contraction=True,
        contraction_repetition=exception == "repetition",
        contraction_correction=exception == "correction",
        contraction_question_tail=exception == "question-tail",
    )
    words = (word, _word(1, "ready"))
    scorer = _output(
        "weak-mark-model",
        _proposal(word, TrailingPunctuation.COMMA, 0.999),
    )

    plan = decode_presentation(words, (scorer,))

    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.COMMA


def test_length_pressure_never_promotes_uncorroborated_exact_mark() -> None:
    words = tuple(_word(index, f"word{index}") for index in range(97))
    exact = _output(
        "length-exact",
        _proposal(words[63], TrailingPunctuation.PERIOD, 1.0, sentence_break=1.0),
    )

    uncorroborated = decode_presentation(words, (exact,))

    assert not uncorroborated.decisions[63].sentence_break
    assert not any(decision.sentence_break for decision in uncorroborated.decisions)

    admitted = decode_presentation(
        words,
        (exact, _boundary_output("length-boundary", (words[63],), probability=0.70)),
    )
    assert admitted.decisions[63].sentence_break


def test_hard_sentence_limit_saturates_without_manufacturing_punctuation() -> None:
    words = tuple(_word(index, f"word{index}") for index in range(97))

    plan = decode_presentation(words, ())

    assert len(plan.decisions) == 97
    assert not any(decision.sentence_break for decision in plan.decisions)
    rendered = apply_presentation_plan(words, plan)
    assert len(rendered) == 97
    assert all(not any(mark in token for mark in ".?!…") for token in rendered)


@pytest.mark.parametrize(
    "source_mark",
    (
        TrailingPunctuation.COMMA,
        TrailingPunctuation.SEMICOLON,
        TrailingPunctuation.COLON,
    ),
)
def test_typed_question_endpoint_may_replace_only_source_weak_mark(
    source_mark: TrailingPunctuation,
) -> None:
    words = (
        _word(0, "can"),
        _word(
            1,
            f"you{source_mark.value}",
            question=0.95,
            question_scope_start=0,
        ),
    )

    plan = decode_presentation(words, ())
    restored = deserialize_presentation_plan(serialize_presentation_plan(plan))

    assert plan.decisions[1].trailing_punctuation is TrailingPunctuation.QUESTION
    assert plan.decisions[1].boundary_support.source_weak_question_override
    assert apply_presentation_plan(words, restored)[1].endswith("?")


def test_source_weak_mark_outside_typed_question_endpoint_remains_unchanged() -> None:
    words = (_word(0, "ready,"), _word(1, "today"))
    exact = _output(
        "question-model",
        _proposal(words[0], TrailingPunctuation.QUESTION, 0.999, sentence_break=0.999),
    )
    boundary = _boundary_output("boundary-model", (words[0],), probability=0.95)

    plan = decode_presentation(words, (exact, boundary))

    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.COMMA


@pytest.mark.parametrize(
    "source_text,expected",
    (("ready.", "."), ("ready!", "!"), ("ready…", "…")),
)
def test_typed_question_scope_abstains_before_conflicting_source_strong_mark(
    source_text: str,
    expected: str,
) -> None:
    words = (
        _word(0, "can"),
        _word(1, source_text, question=0.95, question_scope_start=0),
    )

    plan = decode_presentation(words, ())

    assert plan.decisions[1].trailing_punctuation.value == expected
    assert not plan.decisions[1].boundary_support.source_weak_question_override


def test_replay_rejects_forged_source_weak_mark_replacement_outside_endpoint() -> None:
    words = (_word(0, "ready,"),)
    exact = _output(
        "period-model",
        _proposal(words[0], TrailingPunctuation.PERIOD, 0.999, sentence_break=0.999),
    )
    boundary = _boundary_output("boundary-model", (words[0],), probability=0.95)
    plan = decode_presentation(words, (exact, boundary))
    forged_decision = replace(
        plan.decisions[0],
        trailing_punctuation=TrailingPunctuation.PERIOD,
        sentence_break=True,
        boundary_support=_scorer_support(exact, boundary),
        contributing_provenance=_contributing(exact, boundary),
    )
    forged = replace(plan, decisions=(forged_decision,))

    with pytest.raises(ValueError, match="source punctuation policy"):
        apply_presentation_plan(words, forged)


def test_incomplete_contraction_hard_rejects_strong_stop() -> None:
    words = (
        _word(0, "I'm", incomplete=True),
        _word(1, "probably"),
        _word(2, "going"),
        _word(3, "tomorrow"),
    )
    scorer = _output(
        "local-model",
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            0.99,
            capitalization=0.99,
            sentence_break=0.99,
        ),
    )

    plan = decode_presentation(words, (scorer,))

    assert plan.decisions[0].trailing_punctuation not in {
        TrailingPunctuation.PERIOD,
        TrailingPunctuation.QUESTION,
        TrailingPunctuation.EXCLAMATION,
        TrailingPunctuation.ELLIPSIS,
    }
    assert lexical_signature(apply_presentation_plan(words, plan)[0]) == "i'm"


def test_unsupported_same_speaker_short_continuation_rejects_stop() -> None:
    words = (
        _word(0, "and"),
        _word(1, "potentially", unsupported_short_continuation=True),
        _word(2, "later"),
        _word(3, "today"),
    )
    scorer = _output(
        "weak-boundary",
        _proposal(
            words[1],
            TrailingPunctuation.PERIOD,
            0.7,
            sentence_break=0.7,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[1]))

    assert not plan.decisions[1].sentence_break
    assert plan.hard_constraint_application_count == 1
    assert plan.hard_constraint_violation_count == 0


def test_plan_replay_enforces_structural_and_sentence_start_constraints() -> None:
    words = (
        _word(0, "we"),
        _word(1, "agree"),
        _word(2, "and", incomplete=True),
        _word(3, "then"),
    )
    scorer = _output(
        "structural-forgery",
        _proposal(
            words[2],
            TrailingPunctuation.PERIOD,
            0.99,
            sentence_break=0.99,
        ),
    )
    boundary = _boundary_output("structural-boundary", (words[2],))
    plan = decode_presentation(words, (scorer, boundary))
    structural_forgery = replace(
        plan,
        decisions=(
            *plan.decisions[:2],
            replace(
                plan.decisions[2],
                trailing_punctuation=TrailingPunctuation.PERIOD,
                sentence_break=True,
                contributing_provenance=_contributing(scorer, boundary),
                boundary_support=_scorer_support(scorer, boundary),
            ),
            replace(plan.decisions[3], capitalize=True),
        ),
    )
    capitalization_forgery = replace(
        plan,
        decisions=(replace(plan.decisions[0], capitalize=False), *plan.decisions[1:]),
    )

    with pytest.raises(ValueError, match="boundary support does not match"):
        apply_presentation_plan(words, structural_forgery)
    with pytest.raises(ValueError, match="sentence-start capitalization"):
        apply_presentation_plan(words, capitalization_forgery)


@pytest.mark.parametrize("tamper", ("wrong-index", "wrong-punctuation"))
def test_replay_rejects_boundary_support_without_exact_scorer_membership(
    tamper: str,
) -> None:
    words = (_word(0, "we"), _word(1, "agree"))
    scorer = _output(
        "sat-local",
        _proposal(
            words[1],
            TrailingPunctuation.PERIOD,
            0.99,
            sentence_break=0.99,
        ),
    )
    boundary = _boundary_output("sat-local-boundary", (words[1],))
    plan = decode_presentation(words, (scorer, boundary))

    assert plan.decisions[1].boundary_support.exact_mark_provenance == (scorer.provenance,)
    assert plan.decisions[1].boundary_support.boundary_provenance == (boundary.provenance,)
    tampered_proposal = (
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            0.99,
            sentence_break=0.99,
        )
        if tamper == "wrong-index"
        else _proposal(
            words[1],
            TrailingPunctuation.QUESTION,
            0.99,
            sentence_break=0.99,
        )
    )
    tampered = replace(
        plan,
        scorer_outputs=tuple(
            sorted(
                (replace(scorer, proposals=(tampered_proposal,)), boundary),
                key=lambda item: item.provenance.scorer_id,
            )
        ),
    )

    with pytest.raises(ValueError, match="retained scorer output"):
        apply_presentation_plan(words, tampered)


def test_replay_rejects_abstained_sat_as_boundary_support() -> None:
    words = (_word(0, "Can't."),)
    sat = ScorerOutput.abstain(
        ScorerProvenance("sat-local", "1.0"),
        "zero_signal_weight",
    )
    plan = decode_presentation(words, (sat,))
    forged = replace(
        plan,
        decisions=(
            replace(
                plan.decisions[0],
                trailing_punctuation=TrailingPunctuation.PERIOD,
                sentence_break=True,
                boundary_support=BoundarySupport(
                    source_punctuation=True,
                    boundary_provenance=(sat.provenance,),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="retained scorer output"):
        apply_presentation_plan(words, forged)


@pytest.mark.parametrize("tamper", ("erase", "inject", "wrong-support"))
def test_replay_requires_exact_contributing_provenance(tamper: str) -> None:
    words = (_word(0, "ready"),)
    supporter = _output(
        "alpha-supporter",
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            0.99,
            capitalization=0.99,
            sentence_break=0.99,
        ),
    )
    abstained = ScorerOutput.abstain(
        ScorerProvenance("zeta-abstained", "1.0"),
        "no_evidence",
    )
    plan = decode_presentation(words, (supporter, abstained))

    assert plan.decisions[0].contributing_provenance == (supporter.provenance,)
    forged_provenance = {
        "erase": (),
        "inject": (supporter.provenance, abstained.provenance),
        "wrong-support": (abstained.provenance,),
    }[tamper]
    forged = replace(
        plan,
        decisions=(
            replace(
                plan.decisions[0],
                contributing_provenance=forged_provenance,
            ),
        ),
    )

    with pytest.raises(ValueError, match="contributing provenance"):
        apply_presentation_plan(words, forged)


@pytest.mark.parametrize("resource", ("words", "scorers", "proposals"))
def test_decoder_fails_closed_at_explicit_resource_envelope(
    resource: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    words = (_word(0, "one"), _word(1, "two"))
    outputs = (
        _output("alpha", _proposal(words[0], TrailingPunctuation.NONE, 0.6)),
        _output("beta", _proposal(words[1], TrailingPunctuation.NONE, 0.6)),
    )
    if resource == "words":
        monkeypatch.setattr(presentation_module, "MAX_PRESENTATION_WORDS", 1)
        expected = "maximum word count"
    elif resource == "scorers":
        monkeypatch.setattr(presentation_module, "MAX_PRESENTATION_SCORER_OUTPUTS", 1)
        expected = "maximum scorer count"
    else:
        monkeypatch.setattr(presentation_module, "MAX_PRESENTATION_PROPOSALS", 1)
        expected = "maximum proposal count"

    with pytest.raises(ValueError, match=expected):
        decode_presentation(words, outputs)


def test_presentation_plan_byte_envelope_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = decode_presentation((_word(0, "ready"),), ())
    monkeypatch.setattr(presentation_module, "MAX_PRESENTATION_PLAN_BYTES", 1)

    with pytest.raises(ValueError, match="maximum byte envelope"):
        serialize_presentation_plan(plan)
    with pytest.raises(ValueError, match="maximum byte envelope"):
        deserialize_presentation_plan(b"{}\n")


def test_deserializer_rejects_proposal_count_before_plan_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    words = (_word(0, "ready"),)
    plan = decode_presentation(
        words,
        (_output("source", _proposal(words[0], TrailingPunctuation.NONE, 0.9)),),
    )
    serialized = serialize_presentation_plan(plan)
    monkeypatch.setattr(presentation_module, "MAX_PRESENTATION_PROPOSALS", 0)

    with pytest.raises(ValueError, match="maximum proposal count"):
        deserialize_presentation_plan(serialized)


def test_compact_scorer_evidence_keeps_long_recording_plan_bounded() -> None:
    word_count = 1_000
    words = tuple(_word(index, f"token{index}") for index in range(word_count))
    source = _output(
        "source",
        *(
            _proposal(
                word,
                (
                    TrailingPunctuation.PERIOD
                    if (word.word_index + 1) % 64 == 0 or word.word_index == word_count - 1
                    else TrailingPunctuation.NONE
                ),
                0.99,
                sentence_break=(
                    0.99
                    if (word.word_index + 1) % 64 == 0 or word.word_index == word_count - 1
                    else 0.01
                ),
            )
            for word in words
        ),
    )

    def neutral_ensemble(scorer_id: str) -> ScorerOutput:
        return _output(
            scorer_id,
            *(
                _proposal(
                    word,
                    punctuation,
                    1.0 / len(TrailingPunctuation),
                    capitalization=0.5,
                    sentence_break=0.5,
                    paragraph_break=0.5,
                )
                for word in words
                for punctuation in TrailingPunctuation
            ),
        )

    plan = decode_presentation(
        words,
        (
            source,
            neutral_ensemble("aggregate"),
            _boundary_output("sat", words, probability=0.95),
        ),
    )
    serialized = serialize_presentation_plan(plan)

    assert sum(len(output.proposals) for output in plan.scorer_outputs) == 10_000
    # Ten times this representative evidence volume remains comfortably below
    # the fixed 64 MiB reader, with margin for wider decimal word indices.
    assert len(serialized) * 10 < 48 * 1024 * 1024
    assert deserialize_presentation_plan(serialized) == plan


def test_global_length_prior_selects_supported_mid_sequence_stop() -> None:
    words = tuple(
        _word(index, f"word{index}", pause=1.25 if index == 11 else 0.0) for index in range(25)
    )
    scorer = _output(
        "boundary-model",
        _proposal(
            words[11],
            TrailingPunctuation.NONE,
            0.48,
            sentence_break=0.52,
        ),
        _proposal(
            words[11],
            TrailingPunctuation.PERIOD,
            0.52,
            sentence_break=0.52,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[11], probability=0.70))

    assert plan.decisions[11].trailing_punctuation is TrailingPunctuation.PERIOD
    assert plan.decisions[12].capitalize


def test_maximum_sentence_span_saturates_without_a_supported_stop() -> None:
    words = tuple(_word(index, f"word{index}") for index in range(120))

    plan = decode_presentation(words, ())

    assert len(plan.decisions) == 120
    assert not any(decision.sentence_break for decision in plan.decisions)


def test_64_word_incomplete_trailing_fragment_remains_unpunctuated() -> None:
    words = tuple(
        _word(
            index,
            "because" if index == 63 else f"word{index}",
            incomplete_trailing=index == 63,
        )
        for index in range(64)
    )
    scorer = _output(
        "unsafe-terminal-model",
        _proposal(
            words[-1],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[-1]))

    assert not plan.decisions[-1].sentence_break
    assert apply_presentation_plan(words, plan)[-1] == "because"


def test_65_word_incomplete_trailing_fragment_crosses_only_the_soft_limit() -> None:
    words = tuple(
        _word(
            index,
            "because" if index == 64 else f"word{index}",
            incomplete_trailing=index == 64,
        )
        for index in range(65)
    )

    plan = decode_presentation(words, ())

    assert not plan.decisions[-1].sentence_break
    assert apply_presentation_plan(words, plan)[-1] == "because"


def test_supported_stop_on_word_65_is_legal_after_the_soft_limit() -> None:
    words = tuple(_word(index, f"word{index}") for index in range(65))
    scorer = _output(
        "late-boundary-model",
        _proposal(
            words[-1],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[-1]))

    assert plan.decisions[-1].sentence_break


def test_comma_punctuated_65_to_96_span_reaches_later_supported_stop() -> None:
    words = tuple(
        _word(index, f"word{index}{',' if index in {31, 63} else ''}") for index in range(80)
    )
    scorer = _output(
        "late-boundary-model",
        _proposal(
            words[-1],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[-1]))

    assert plan.decisions[-1].sentence_break
    assert apply_presentation_plan(words, plan)[31].endswith(",")


def test_uncorroborated_exact_stop_on_word_97_does_not_gain_length_authority() -> None:
    words = tuple(_word(index, f"word{index}") for index in range(97))
    scorer = _output(
        "too-late-boundary-model",
        _proposal(
            words[-1],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )

    plan = decode_presentation(words, (scorer,))

    assert not plan.decisions[-1].sentence_break


def test_supported_stop_on_word_64_allows_incomplete_trailing_continuation() -> None:
    words = tuple(
        _word(
            index,
            "because" if index == 69 else f"word{index}",
            incomplete_trailing=index == 69,
        )
        for index in range(70)
    )
    scorer = _output(
        "safe-boundary-model",
        _proposal(
            words[63],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[63]))

    assert plan.decisions[63].sentence_break
    assert not plan.decisions[-1].sentence_break
    assert apply_presentation_plan(words, plan)[-1] == "because"


def test_isolated_incomplete_contraction_stop_is_rejected_without_timing() -> None:
    words = (
        _word(0, "well."),
        _word(1, "I'm", pause=2.0, incomplete_contraction=True),
        _word(2, "still"),
        _word(3, "working"),
    )
    scorer = _output(
        "local-model",
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
        _proposal(
            words[1],
            TrailingPunctuation.PERIOD,
            0.999,
            sentence_break=0.999,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[0]))

    assert not plan.decisions[1].sentence_break


def test_added_stop_cannot_sentenceize_an_abandoned_pronoun_fragment() -> None:
    words = (
        _word(0, "before"),
        _word(1, "I", incomplete_abandoned_fragment=True),
        _word(2, "So"),
        _word(3, "I"),
        _word(4, "continued"),
    )
    scorer = _output(
        "fragmenting-model",
        _proposal(
            words[1],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )
    boundary = _boundary_output("fragmenting-boundary", (words[1],))

    plan = decode_presentation(words, (scorer, boundary))

    assert not plan.decisions[1].sentence_break
    forged = replace(
        plan,
        decisions=(
            plan.decisions[0],
            replace(
                plan.decisions[1],
                trailing_punctuation=TrailingPunctuation.PERIOD,
                sentence_break=True,
                contributing_provenance=_contributing(scorer, boundary),
                boundary_support=_scorer_support(scorer, boundary),
            ),
            replace(plan.decisions[2], capitalize=True),
            *plan.decisions[3:],
        ),
    )
    with pytest.raises(ValueError, match="boundary support does not match"):
        apply_presentation_plan(words, forged)


def test_source_authored_pronoun_reply_bypasses_abandoned_fragment_guard() -> None:
    words = (_word(0, "I.", incomplete_abandoned_fragment=True),)
    scorer = _output(
        "source-confirmation",
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )

    plan = decode_presentation(words, (scorer,))

    assert plan.decisions[0].sentence_break
    assert apply_presentation_plan(words, plan) == ("I.",)


def test_supported_complete_short_sentence_is_not_an_abandoned_fragment() -> None:
    words = (_word(0, "Done"), _word(1, "Next"))
    scorer = _output(
        "short-sentence-model",
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[0]))

    assert plan.decisions[0].sentence_break


def test_source_authored_negative_contraction_reply_is_preserved() -> None:
    word = _word(0, "Can't.", incomplete_contraction=True)
    scorer = _output(
        "source-confirmation",
        _proposal(
            word,
            TrailingPunctuation.PERIOD,
            1.0,
            sentence_break=1.0,
        ),
    )

    plan = decode_presentation((word,), (scorer,))

    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.PERIOD
    assert apply_presentation_plan((word,), plan) == ("Can't.",)


def test_added_short_stop_burst_is_globally_constrained() -> None:
    words = tuple(
        _word(index, token) for index, token in enumerate("one two three four five".split())
    )
    scorer = _output(
        "fragmenting-model",
        *(
            _proposal(
                word,
                TrailingPunctuation.PERIOD,
                0.999,
                sentence_break=0.999,
            )
            for word in words[:-1]
        ),
    )

    plan = decode_presentation(words, (scorer,))

    streak = maximum = 0
    sentence_length = 0
    for word, decision in zip(words, plan.decisions, strict=True):
        sentence_length += 1
        if decision.sentence_break:
            source_strong = word.lexical_text.endswith((".", "?", "!", "…"))
            streak = streak + 1 if sentence_length <= 2 and not source_strong else 0
            maximum = max(maximum, streak)
            sentence_length = 0
    assert maximum <= 2


def test_unknown_speaker_burst_uses_exact_container_continuity() -> None:
    words = tuple(
        _word(index, token, speaker=None, container=0)
        for index, token in enumerate("one two three four five".split())
    )
    scorer = _output(
        "fragmenting-model",
        *(
            _proposal(
                word,
                TrailingPunctuation.PERIOD,
                0.999,
                sentence_break=0.999,
            )
            for word in words[:-1]
        ),
    )

    plan = decode_presentation(words, (scorer,))

    assert sum(decision.sentence_break for decision in plan.decisions[:-1]) <= 2


def test_distinct_unknown_containers_do_not_share_burst_state() -> None:
    words = tuple(
        _word(index, token, speaker=None, container=index)
        for index, token in enumerate("yes no okay thanks".split())
    )
    scorer = _output(
        "short-turn-model",
        *(
            _proposal(
                word,
                TrailingPunctuation.PERIOD,
                1.0,
                sentence_break=1.0,
            )
            for word in words
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, *words))

    assert all(decision.sentence_break for decision in plan.decisions)


def test_source_authored_short_sentence_sequence_is_preserved() -> None:
    words = tuple(
        _word(index, token) for index, token in enumerate(("Yes.", "No.", "Okay.", "Thanks."))
    )
    scorer = _output(
        "source-confirmation",
        *(
            _proposal(
                word,
                TrailingPunctuation.PERIOD,
                1.0,
                sentence_break=1.0,
            )
            for word in words
        ),
    )

    plan = decode_presentation(words, (scorer,))

    assert all(decision.sentence_break for decision in plan.decisions)
    assert all(surface.endswith(".") for surface in apply_presentation_plan(words, plan))


def test_dense_added_medium_length_stop_cluster_is_globally_constrained() -> None:
    words = tuple(_word(index, f"word{index}") for index in range(20))
    boundary_indexes = (3, 7, 11, 15)
    scorers = tuple(
        _output(
            f"dense-boundary-{index}",
            _proposal(
                words[index],
                TrailingPunctuation.PERIOD,
                0.999,
                sentence_break=0.999,
            ),
        )
        for index in boundary_indexes
    )

    plan = decode_presentation(
        words,
        (
            *scorers,
            _boundary_output("long-span-sat", words, probability=0.95),
        ),
    )

    assert sum(plan.decisions[index].sentence_break for index in boundary_indexes) <= 3


def test_exact_plan_reapplication_rejects_forged_added_short_stop_burst() -> None:
    words = tuple(_word(index, token) for index, token in enumerate("one two three four".split()))
    scorer = _output(
        "burst-forgery",
        *(
            _proposal(
                word,
                TrailingPunctuation.PERIOD,
                0.99,
                capitalization=0.99,
                sentence_break=0.99,
            )
            for word in words
        ),
    )
    boundary = _boundary_output("burst-boundary", words)
    plan = decode_presentation(words, (scorer, boundary))
    forged = replace(
        plan,
        decisions=tuple(
            replace(
                decision,
                trailing_punctuation=TrailingPunctuation.PERIOD,
                sentence_break=True,
                capitalize=True,
                contributing_provenance=_contributing(scorer, boundary),
                boundary_support=_scorer_support(scorer, boundary),
            )
            for decision in plan.decisions
        ),
    )

    with pytest.raises(ValueError, match="global surface constraints"):
        apply_presentation_plan(words, forged)


def test_added_question_after_incomplete_contraction_retains_safe_abstention() -> None:
    text = ("although", "the", "result", "looks", "good", "I", "think", "it's")
    words = tuple(
        _word(
            index,
            token,
            incomplete_contraction=index == len(text) - 1,
            question=0.95 if index == len(text) - 1 else 0.0,
            question_scope_start=0 if index == len(text) - 1 else None,
        )
        for index, token in enumerate(text)
    )
    scorer = _output(
        "question-model",
        _proposal(
            words[-1],
            TrailingPunctuation.QUESTION,
            0.999,
            sentence_break=0.999,
        ),
    )
    plan = decode_presentation(words, (scorer,))

    assert plan.decisions[-1].trailing_punctuation is not TrailingPunctuation.QUESTION
    assert plan.decisions[-1].sentence_break is False


def test_duplicate_high_question_sites_select_latest_eligible_endpoint() -> None:
    text = ("Can", "we", "check", "this", "because", "I", "think", "it's")
    words = tuple(
        _word(
            index,
            token,
            incomplete_contraction=index == len(text) - 1,
            question=0.82 if index == 3 else (0.85 if index == len(text) - 1 else 0.0),
            question_scope_start=0 if index in {3, len(text) - 1} else None,
        )
        for index, token in enumerate(text)
    )
    scorers = (
        _output(
            "early-question-model",
            _proposal(
                words[3],
                TrailingPunctuation.QUESTION,
                0.95,
                sentence_break=0.95,
            ),
        ),
        _output(
            "late-question-model",
            _proposal(
                words[-1],
                TrailingPunctuation.QUESTION,
                0.95,
                sentence_break=0.95,
            ),
        ),
    )

    plan = decode_presentation(words, scorers)

    assert [
        decision.word_index
        for decision in plan.decisions
        if decision.trailing_punctuation is TrailingPunctuation.QUESTION
    ] == [3]
    forged = replace(
        plan,
        decisions=tuple(
            replace(
                decision,
                trailing_punctuation=TrailingPunctuation.QUESTION,
                sentence_break=True,
                contributing_provenance=(scorers[1].provenance,),
                boundary_support=_scorer_support(
                    scorers[1],
                    typed_question=True,
                ),
            )
            if decision.word_index == len(words) - 1
            else decision
            for decision in plan.decisions
        ),
    )
    with pytest.raises(ValueError, match="question boundary lacks an intact source scope"):
        apply_presentation_plan(words, forged)


def test_duplicate_eligible_question_sites_require_only_final_endpoint() -> None:
    words = tuple(
        _word(
            index,
            token,
            question=0.82 if index == 3 else (0.9 if index == 5 else 0.0),
            question_scope_start=0 if index in {3, 5} else None,
        )
        for index, token in enumerate(("Can", "we", "review", "this", "once", "more"))
    )
    scorers = (
        _output(
            "early-question",
            _proposal(
                words[3],
                TrailingPunctuation.QUESTION,
                0.99,
                sentence_break=0.99,
            ),
        ),
        _output(
            "final-question",
            _proposal(
                words[5],
                TrailingPunctuation.QUESTION,
                0.99,
                sentence_break=0.99,
            ),
        ),
    )

    plan = decode_presentation(words, scorers)

    assert [
        decision.word_index
        for decision in plan.decisions
        if decision.trailing_punctuation is TrailingPunctuation.QUESTION
    ] == [5]
    missing_final = replace(
        plan,
        decisions=(
            *plan.decisions[:5],
            replace(
                plan.decisions[5],
                trailing_punctuation=TrailingPunctuation.NONE,
                sentence_break=False,
                boundary_support=BoundarySupport(),
            ),
        ),
    )
    with pytest.raises(ValueError, match="lacks its final question mark"):
        apply_presentation_plan(words, missing_final)


def test_complete_negative_contraction_question_is_supported() -> None:
    words = (
        _word(0, "Who"),
        _word(
            1,
            "isn't",
            incomplete_contraction=True,
            question=0.95,
            question_scope_start=0,
        ),
    )
    scorer = _output(
        "question-model",
        _proposal(
            words[-1],
            TrailingPunctuation.QUESTION,
            0.95,
            sentence_break=0.95,
        ),
    )
    plan = decode_presentation(words, (scorer,))

    assert plan.decisions[-1].trailing_punctuation is TrailingPunctuation.QUESTION
    assert apply_presentation_plan(words, plan)[-1] == "isn't?"


def test_sentence_break_requires_safe_following_capitalization() -> None:
    words = (_word(0, "hello"), _word(1, "world"))
    boundary = _output(
        "boundary-model",
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            0.99,
            capitalization=0.01,
            sentence_break=0.99,
        ),
    )
    lowercase = _output(
        "lowercase-model",
        _proposal(
            words[1],
            TrailingPunctuation.NONE,
            0.99,
            capitalization=0.001,
            sentence_break=0.01,
        ),
    )

    plan = decode_presentation(words, (boundary, lowercase))

    if plan.decisions[0].sentence_break:
        assert plan.decisions[1].capitalize
        assert apply_presentation_plan(words, plan)[1] == "World"
    else:
        assert apply_presentation_plan(words, plan)[0] == "Hello"


def test_question_evidence_disambiguates_equal_strong_stop_proposals() -> None:
    words = (
        _word(0, "are"),
        _word(1, "you"),
        _word(2, "ready", question=0.95, question_scope_start=0),
    )
    scorer = _output(
        "question-model",
        _proposal(
            words[2],
            TrailingPunctuation.PERIOD,
            0.5,
            sentence_break=0.95,
        ),
        _proposal(
            words[2],
            TrailingPunctuation.QUESTION,
            0.5,
            sentence_break=0.95,
        ),
    )
    plan = decode_presentation(words, (scorer,))

    assert plan.decisions[2].trailing_punctuation is TrailingPunctuation.QUESTION
    assert apply_presentation_plan(words, plan)[2].endswith("?")


def test_question_leading_token_alone_does_not_force_interrogative() -> None:
    words = tuple(
        _word(index, text) for index, text in enumerate(("what", "I", "mean", "is", "this"))
    )

    plan = decode_presentation(words, ())

    assert all(
        decision.trailing_punctuation is not TrailingPunctuation.QUESTION
        for decision in plan.decisions
    )


def test_question_scope_cannot_be_severed_by_an_earlier_selected_stop() -> None:
    words = (
        _word(0, "can"),
        _word(1, "you"),
        _word(2, "explain"),
        _word(3, "this"),
        _word(4, "clearly", question=0.95, question_scope_start=0),
    )
    scorer = _output(
        "question-model",
        _proposal(
            words[3],
            TrailingPunctuation.PERIOD,
            0.99,
            sentence_break=0.99,
        ),
        _proposal(
            words[4],
            TrailingPunctuation.QUESTION,
            0.99,
            sentence_break=0.99,
        ),
    )
    boundary = _boundary_output("question-boundary", words[3:])

    plan = decode_presentation(words, (scorer, boundary))

    assert not (
        plan.decisions[3].sentence_break
        and plan.decisions[4].trailing_punctuation is TrailingPunctuation.QUESTION
    )
    assert apply_presentation_plan(words, plan)

    unsafe = replace(
        plan,
        decisions=(
            *plan.decisions[:3],
            replace(
                plan.decisions[3],
                trailing_punctuation=TrailingPunctuation.PERIOD,
                sentence_break=True,
                contributing_provenance=_contributing(scorer, boundary),
                boundary_support=_scorer_support(scorer, boundary),
            ),
            replace(
                plan.decisions[4],
                trailing_punctuation=TrailingPunctuation.QUESTION,
                sentence_break=True,
                contributing_provenance=_contributing(scorer, boundary),
                boundary_support=_scorer_support(
                    scorer,
                    boundary,
                    typed_question=True,
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="global surface constraints"):
        apply_presentation_plan(words, unsafe)


def test_question_candidate_without_typed_source_scope_is_rejected() -> None:
    words = (
        _word(0, "this"),
        _word(1, "remains"),
        _word(2, "declarative"),
    )
    scorer = _output(
        "question-model",
        _proposal(
            words[-1],
            TrailingPunctuation.QUESTION,
            0.999,
            sentence_break=0.999,
        ),
    )

    plan = decode_presentation(words, (scorer,))

    assert plan.decisions[-1].trailing_punctuation is not TrailingPunctuation.QUESTION
    unsafe = replace(
        plan,
        decisions=(
            *plan.decisions[:-1],
            replace(
                plan.decisions[-1],
                trailing_punctuation=TrailingPunctuation.QUESTION,
                sentence_break=True,
                boundary_support=_forged_support(),
            ),
        ),
    )
    with pytest.raises(ValueError, match="lacks an intact source scope"):
        apply_presentation_plan(words, unsafe)


def test_positive_question_evidence_requires_an_exact_source_scope() -> None:
    with pytest.raises(ValueError, match="requires an exact source scope"):
        DecoderWord.from_text(0, "ready", question_evidence=0.95)


def test_long_question_scope_abstains_beyond_the_sentence_state_limit() -> None:
    words = tuple(
        _word(
            index,
            "can" if index == 0 else ("now" if index == 99 else f"word{index}"),
            question=0.95 if index == 99 else 0.0,
            question_scope_start=0 if index == 99 else None,
        )
        for index in range(100)
    )
    scorer = _output(
        "question-model",
        _proposal(
            words[-1],
            TrailingPunctuation.QUESTION,
            0.999,
            sentence_break=0.999,
        ),
    )

    plan = decode_presentation(words, (scorer,))

    assert not any(decision.sentence_break for decision in plan.decisions)


def test_question_scope_over_state_cap_abstains_without_an_earlier_stop() -> None:
    words = tuple(
        _word(
            index,
            "can" if index == 0 else ("now" if index == 109 else f"word{index}"),
            question=0.95 if index == 109 else 0.0,
            question_scope_start=0 if index == 109 else None,
        )
        for index in range(110)
    )
    scorers = (
        _output(
            "mid-sentence-boundary",
            _proposal(
                words[63],
                TrailingPunctuation.PERIOD,
                1.0,
                sentence_break=1.0,
            ),
        ),
        _output(
            "question-model",
            _proposal(
                words[-1],
                TrailingPunctuation.QUESTION,
                1.0,
                sentence_break=1.0,
            ),
        ),
    )

    plan = decode_presentation(words, scorers)

    assert not any(decision.sentence_break for decision in plan.decisions)


def test_typed_plan_round_trip_rejects_opaque_or_noncanonical_authority() -> None:
    words = (_word(0, "hello"), _word(1, "there"))
    plan = decode_presentation(words, ())
    serialized = serialize_presentation_plan(plan)

    assert deserialize_presentation_plan(serialized) == plan
    with pytest.raises(ValueError, match="typed schema"):
        deserialize_presentation_plan(
            b'{"decisions":[{"opaque":"rewrite-authority"}],"input_digest":"' + b"a" * 64 + b'"}\n'
        )
    with pytest.raises(ValueError, match="canonical JSON"):
        deserialize_presentation_plan(b" " + serialized)


def test_plan_schema_bump_rejects_stale_decoder_contract() -> None:
    payload = json.loads(serialize_presentation_plan(decode_presentation((_word(0, "ready"),), ())))
    payload["decoder_version"] = "auralign.presentation.v8"
    serialized = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()

    with pytest.raises(ValueError, match="unsupported presentation decoder version"):
        deserialize_presentation_plan(serialized)


@pytest.mark.parametrize("mutation", ("missing", "stale"))
def test_plan_rejects_missing_or_stale_escalation_policy(mutation: str) -> None:
    payload = json.loads(serialize_presentation_plan(decode_presentation((_word(0, "ready"),), ())))
    if mutation == "missing":
        del payload["escalation_policy"]
    else:
        payload["escalation_policy"] = "obsolete-hard-fail.v0"
    serialized = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()

    with pytest.raises(ValueError):
        deserialize_presentation_plan(serialized)


def test_replay_rejects_sat_role_tamper_even_after_canonical_reserialization() -> None:
    word = _word(0, "ready")
    exact = _output(
        "exact-model",
        _proposal(word, TrailingPunctuation.PERIOD, 0.999, sentence_break=0.999),
    )
    sat = _boundary_output("wtpsplit-sat-3l-sm", (word,), probability=0.70)
    payload = json.loads(serialize_presentation_plan(decode_presentation((word,), (exact, sat))))
    sat_payload = next(
        output
        for output in payload["scorer_outputs"]
        if output["provenance"]["scorer_id"] == "wtpsplit-sat-3l-sm"
    )
    sat_payload["proposal_groups"][0]["evidence_roles"] = ["exact_mark"]
    serialized = (
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()
    tampered = deserialize_presentation_plan(serialized)

    with pytest.raises(ValueError, match="SaT proposals must be boundary-only"):
        apply_presentation_plan((word,), tampered)


def test_missing_pause_is_unknown_and_does_not_create_a_hard_continuation() -> None:
    words = (
        DecoderWord.from_text(0, "well", speaker_id="SPEAKER_00"),
        DecoderWord.from_text(1, "okay", speaker_id="SPEAKER_00"),
    )
    scorer = _output(
        "strong-boundary",
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            0.99,
            sentence_break=0.99,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[0]))

    assert words[0].pause_after_seconds is None
    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.PERIOD
    assert plan.hard_constraint_application_count == 0
    assert plan.hard_constraint_violation_count == 0


def test_overlapping_speakers_may_have_reversed_adjacent_word_starts() -> None:
    words = (
        DecoderWord.from_text(
            0,
            "still",
            start_seconds=1.0,
            end_seconds=1.8,
            speaker_id="SPEAKER_00",
            begins_turn=True,
            ends_turn=True,
        ),
        DecoderWord.from_text(
            1,
            "yes",
            start_seconds=0.9,
            end_seconds=1.2,
            speaker_id="SPEAKER_01",
            begins_turn=True,
            ends_turn=True,
        ),
    )

    plan = decode_presentation(words, ())

    assert len(plan.decisions) == 2
    assert apply_presentation_plan(words, plan)[0] == "Still"


def test_abstention_is_retained_but_does_not_contribute_to_decisions() -> None:
    words = (_word(0, "hello"), _word(1, "there"))
    abstaining_provenance = ScorerProvenance("uncertain-model", "2.0")
    active_provenance = ScorerProvenance("rule-model", "1.0")
    outputs = (
        ScorerOutput.abstain(abstaining_provenance, "insufficient_evidence"),
        ScorerOutput(
            provenance=active_provenance,
            proposals=(
                _proposal(
                    words[1],
                    TrailingPunctuation.PERIOD,
                    0.99,
                    sentence_break=0.99,
                ),
            ),
        ),
    )

    plan = decode_presentation(words, outputs)

    assert plan.abstentions[0].provenance == abstaining_provenance
    assert plan.abstentions[0].reason == "insufficient_evidence"
    assert active_provenance in plan.decisions[1].contributing_provenance
    assert abstaining_provenance not in plan.decisions[1].contributing_provenance


def test_retained_hybrid_composer_binds_receipts_and_abstains_rejected_scorers() -> None:
    words = (
        _word(0, "we", begins_turn=True),
        _word(1, "are"),
        _word(2, "ready", pause=1.4, ends_turn=True),
    )
    original_words = tuple(words)
    source = _output(
        "auralign.source-grammar-features",
        _proposal(words[0], TrailingPunctuation.NONE, 0.95),
    )
    apple = _output(
        "apple-foundation.window-aggregate",
        _proposal(
            words[2],
            TrailingPunctuation.PERIOD,
            0.99,
            sentence_break=0.99,
        ),
    )
    sat = _boundary_output("wtpsplit-sat-3l-sm", words, probability=0.95)
    qwen4 = _output(
        "qwen3-4b-exact-mark",
        _proposal(words[1], TrailingPunctuation.PERIOD, 0.99, sentence_break=0.99),
    )
    qwen14 = _output(
        "qwen3-14b-exact-mark",
        _proposal(words[2], TrailingPunctuation.QUESTION, 0.99, sentence_break=0.99),
    )
    input_digest = presentation_input_digest(words)

    def retained(
        output: ScorerOutput,
        receipt_character: str,
        *,
        admitted: bool,
    ) -> RetainedScorerEvidence:
        return RetainedScorerEvidence(
            output=output,
            presentation_input_digest=input_digest,
            output_sha256=scorer_output_sha256(output),
            receipt_sha256=receipt_character * 64,
            disposition=(
                RetainedEvidenceDisposition.ADMIT
                if admitted
                else RetainedEvidenceDisposition.ABSTAIN
            ),
            abstention_reason=None if admitted else "quality_gate_failed",
        )

    plan = compose_retained_presentation(
        words,
        (
            retained(source, "1", admitted=True),
            retained(apple, "2", admitted=True),
            retained(sat, "3", admitted=True),
            retained(qwen4, "4", admitted=False),
            retained(qwen14, "5", admitted=False),
        ),
    )

    assert words == original_words
    assert apply_presentation_plan(words, plan) == ("We", "are", "ready.")
    assert {item.provenance.scorer_id for item in plan.abstentions} == {
        "qwen3-4b-exact-mark",
        "qwen3-14b-exact-mark",
    }
    assert {
        provenance.scorer_id
        for decision in plan.decisions
        for provenance in decision.contributing_provenance
    }.isdisjoint({"qwen3-4b-exact-mark", "qwen3-14b-exact-mark"})
    final_support = plan.decisions[-1].boundary_support
    assert {item.scorer_id for item in final_support.exact_mark_provenance} == {
        "apple-foundation.window-aggregate"
    }
    assert {item.scorer_id for item in final_support.boundary_provenance} == {"wtpsplit-sat-3l-sm"}
    assert all(output.provenance.configuration_digest is not None for output in plan.scorer_outputs)
    assert deserialize_presentation_plan(serialize_presentation_plan(plan)) == plan


def test_retained_hybrid_composer_fails_closed_on_binding_or_identity_tamper() -> None:
    words = (_word(0, "ready"),)
    output = _output(
        "retained-mark-model",
        _proposal(words[0], TrailingPunctuation.PERIOD, 0.99, sentence_break=0.99),
    )
    digest = presentation_input_digest(words)
    valid = RetainedScorerEvidence(
        output=output,
        presentation_input_digest=digest,
        output_sha256=scorer_output_sha256(output),
        receipt_sha256="a" * 64,
        disposition=RetainedEvidenceDisposition.ADMIT,
    )

    with pytest.raises(ValueError, match="output digest"):
        replace(valid, output_sha256="0" * 64)
    with pytest.raises(ValueError, match="input binding"):
        compose_retained_presentation(
            words,
            (replace(valid, presentation_input_digest="b" * 64),),
        )

    duplicate = ScorerOutput(
        provenance=replace(output.provenance, configuration_digest="c" * 64),
        proposals=output.proposals,
    )
    with pytest.raises(ValueError, match="duplicate scorer identity"):
        compose_retained_presentation(
            words,
            (
                valid,
                RetainedScorerEvidence(
                    output=duplicate,
                    presentation_input_digest=digest,
                    output_sha256=scorer_output_sha256(duplicate),
                    receipt_sha256="d" * 64,
                    disposition=RetainedEvidenceDisposition.ABSTAIN,
                    abstention_reason="correlated_evidence",
                ),
            ),
        )


def test_indexed_exact_mark_adapter_is_full_coverage_mark_only_evidence() -> None:
    words = (_word(0, "we"), _word(1, "agree"))
    groups = (
        _indexed_mark_evidence(0, None),
        _indexed_mark_evidence(1, TrailingPunctuation.PERIOD),
    )
    output = build_indexed_exact_mark_scorer_output(
        words,
        ScorerProvenance("retained-exact-mark", "1.0", "a" * 64),
        groups,
    )

    assert len(output.proposals) == len(words) * len(TrailingPunctuation)
    assert {proposal.evidence_roles for proposal in output.proposals} == {
        (EvidenceRole.EXACT_MARK,)
    }
    assert all(proposal.capitalization_probability == 0.5 for proposal in output.proposals)
    assert all(proposal.sentence_break_probability == 0.5 for proposal in output.proposals)
    assert {
        proposal.lexical_text_digest for proposal in output.proposals if proposal.word_index == 1
    } == {lexical_text_digest(words[1].lexical_text)}

    with pytest.raises(ValueError, match="cover every word"):
        build_indexed_exact_mark_scorer_output(words, output.provenance, groups[:1])
    with pytest.raises(ValueError, match="canonical punctuation order"):
        IndexedExactMarkEvidence(
            word_index=0,
            punctuation_probabilities=tuple(reversed(groups[0].punctuation_probabilities)),
        )


def test_editorial_adjudication_is_receipt_bound_single_source_authority() -> None:
    words = (_word(0, "we"), _word(1, "agree"))
    raw = build_indexed_editorial_adjudication_scorer_output(
        words,
        ScorerProvenance("auralign.editorial-adjudication.review", "1.0"),
        (
            _editorial_evidence(0, None),
            _editorial_evidence(
                1,
                TrailingPunctuation.PERIOD,
                sentence_break=0.99,
            ),
        ),
    )

    with pytest.raises(ValueError, match="not receipt-bound"):
        decode_presentation(words, (raw,))

    plan = compose_retained_presentation(words, (_retained(words, raw),))
    restored = deserialize_presentation_plan(serialize_presentation_plan(plan))

    assert apply_presentation_plan(words, restored) == ("We", "agree.")
    support = restored.decisions[-1].boundary_support
    assert not support.exact_mark_provenance
    assert not support.boundary_provenance
    assert {item.scorer_id for item in support.editorial_adjudication_provenance} == {
        "auralign.editorial-adjudication.review"
    }
    assert support.supported
    assert {proposal.evidence_roles for proposal in raw.proposals} == {
        (EvidenceRole.EDITORIAL_ADJUDICATION,)
    }
    assert all(proposal.capitalization_probability == 0.5 for proposal in raw.proposals)
    assert all(proposal.casing_surface is None for proposal in raw.proposals)


def test_editorial_adjudication_can_demote_only_reviewed_source_strong_mark() -> None:
    words = (_word(0, "continues."), _word(1, "here"))
    original_words = tuple(words)
    raw = build_indexed_editorial_adjudication_scorer_output(
        words,
        ScorerProvenance("auralign.editorial-adjudication.review", "1.0"),
        (
            _editorial_evidence(
                0,
                TrailingPunctuation.NONE,
                sentence_break=0.01,
                source_mark_veto=True,
            ),
            _editorial_evidence(1, None),
        ),
    )

    plan = compose_retained_presentation(words, (_retained(words, raw),))

    assert words == original_words
    assert apply_presentation_plan(words, plan) == ("Continues", "here")
    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.NONE
    assert {item.scorer_id for item in plan.decisions[0].contributing_provenance} == {
        "auralign.editorial-adjudication.review"
    }
    assert not words[0].incomplete_continuation
    assert words[0].speaker_id == "SPEAKER_00"
    assert words[0].start_seconds == 0.0


def test_editorial_adjudication_can_demote_only_reviewed_source_weak_mark() -> None:
    words = (_word(0, "continues,"), _word(1, "here"))
    raw = build_indexed_editorial_adjudication_scorer_output(
        words,
        ScorerProvenance("auralign.editorial-adjudication.review", "1.0"),
        (
            _editorial_evidence(
                0,
                TrailingPunctuation.NONE,
                sentence_break=0.01,
                source_mark_veto=True,
            ),
            _editorial_evidence(1, None),
        ),
    )

    plan = compose_retained_presentation(words, (_retained(words, raw),))

    assert apply_presentation_plan(words, plan) == ("Continues", "here")
    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.NONE


def test_editorial_adjudication_preserves_unreviewed_source_weak_mark() -> None:
    words = (_word(0, "continues,"), _word(1, "here"))
    raw = build_indexed_editorial_adjudication_scorer_output(
        words,
        ScorerProvenance("auralign.editorial-adjudication.review", "1.0"),
        (_editorial_evidence(0, None), _editorial_evidence(1, None)),
    )

    plan = compose_retained_presentation(words, (_retained(words, raw),))

    assert apply_presentation_plan(words, plan) == ("Continues,", "here")
    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.COMMA


@pytest.mark.parametrize(
    "word,selected",
    (
        (_word(0, "because", incomplete=True), TrailingPunctuation.PERIOD),
        (
            _word(0, "I'm", incomplete_contraction=True),
            TrailingPunctuation.COMMA,
        ),
    ),
)
def test_editorial_adjudication_remains_subordinate_to_structural_guards(
    word: DecoderWord,
    selected: TrailingPunctuation,
) -> None:
    words = (word,)
    raw = build_indexed_editorial_adjudication_scorer_output(
        words,
        ScorerProvenance("auralign.editorial-adjudication.review", "1.0"),
        (
            _editorial_evidence(
                0,
                selected,
                sentence_break=(0.99 if selected is TrailingPunctuation.PERIOD else 0.01),
            ),
        ),
    )

    plan = compose_retained_presentation(words, (_retained(words, raw),))

    assert plan.decisions[0].trailing_punctuation is TrailingPunctuation.NONE


def test_editorial_adjudication_rejects_role_confusion_and_invalid_veto() -> None:
    source = _word(0, "ready.")
    with pytest.raises(ValueError, match="requires editorial adjudication"):
        _proposal(
            source,
            TrailingPunctuation.NONE,
            0.99,
            roles=(EvidenceRole.EXACT_MARK,),
            source_mark_veto=True,
        )

    raw = build_indexed_editorial_adjudication_scorer_output(
        (source,),
        ScorerProvenance("auralign.editorial-adjudication.review", "1.0"),
        (
            _editorial_evidence(
                0,
                TrailingPunctuation.PERIOD,
                sentence_break=0.99,
                source_mark_veto=True,
            ),
        ),
    )
    with pytest.raises(ValueError, match="lacks a replacement"):
        compose_retained_presentation((source,), (_retained((source,), raw),))

    no_source_stop = (_word(0, "ready"),)
    invalid = build_indexed_editorial_adjudication_scorer_output(
        no_source_stop,
        ScorerProvenance("auralign.editorial-adjudication.review", "1.0"),
        (
            _editorial_evidence(
                0,
                TrailingPunctuation.NONE,
                sentence_break=0.01,
                source_mark_veto=True,
            ),
        ),
    )
    with pytest.raises(ValueError, match="lacks a replacement"):
        compose_retained_presentation(
            no_source_stop,
            (_retained(no_source_stop, invalid),),
        )

    with pytest.raises(ValueError, match="identity"):
        build_indexed_editorial_adjudication_scorer_output(
            (source,),
            ScorerProvenance("generic-model", "1.0"),
            (_editorial_evidence(0, None),),
        )


def test_editorial_adjudication_replay_rejects_veto_or_provenance_tamper() -> None:
    words = (_word(0, "continues."), _word(1, "here"))
    raw = build_indexed_editorial_adjudication_scorer_output(
        words,
        ScorerProvenance("auralign.editorial-adjudication.review", "1.0"),
        (
            _editorial_evidence(
                0,
                TrailingPunctuation.NONE,
                sentence_break=0.01,
                source_mark_veto=True,
            ),
            _editorial_evidence(1, None),
        ),
    )
    plan = compose_retained_presentation(words, (_retained(words, raw),))
    output = plan.scorer_outputs[0]
    altered_proposals = tuple(
        replace(proposal, source_mark_veto=False) for proposal in output.proposals
    )
    forged = replace(
        plan,
        scorer_outputs=(replace(output, proposals=altered_proposals),),
    )

    with pytest.raises(ValueError, match="source punctuation policy"):
        apply_presentation_plan(words, forged)

    alternate_receipt_plan = compose_retained_presentation(
        words,
        (_retained(words, raw, receipt_character="b"),),
    )
    assert presentation_plan_sha256(alternate_receipt_plan) != presentation_plan_sha256(plan)
    with pytest.raises(ValueError, match="contributing provenance"):
        apply_presentation_plan(
            words,
            replace(
                plan,
                decisions=(
                    replace(
                        plan.decisions[0],
                        contributing_provenance=(),
                    ),
                    plan.decisions[1],
                ),
            ),
        )


def test_editorial_adjudication_adapter_requires_complete_canonical_coverage() -> None:
    words = (_word(0, "we"), _word(1, "agree"))
    provenance = ScorerProvenance("auralign.editorial-adjudication.review", "1.0")
    evidence = (_editorial_evidence(0, None), _editorial_evidence(1, None))

    with pytest.raises(ValueError, match="cover every word"):
        build_indexed_editorial_adjudication_scorer_output(
            words,
            provenance,
            evidence[:1],
        )
    with pytest.raises(ValueError, match="canonical punctuation order"):
        IndexedEditorialAdjudicationEvidence(
            word_index=0,
            punctuation_probabilities=tuple(reversed(evidence[0].punctuation_probabilities)),
            sentence_break_probability=0.5,
        )


def test_same_scorer_id_cannot_self_corroborate_across_configuration_digests() -> None:
    word = _word(0, "ready")
    exact = ScorerOutput(
        provenance=ScorerProvenance("correlated-family", "1.0", "1" * 64),
        proposals=(_proposal(word, TrailingPunctuation.PERIOD, 0.99, sentence_break=0.99),),
    )
    boundary = ScorerOutput(
        provenance=ScorerProvenance("correlated-family", "1.0", "2" * 64),
        proposals=(
            _proposal(
                word,
                TrailingPunctuation.NONE,
                1.0 / len(TrailingPunctuation),
                capitalization=0.5,
                sentence_break=0.99,
                paragraph_break=0.5,
                roles=(EvidenceRole.BOUNDARY_ONLY,),
            ),
        ),
    )

    with pytest.raises(ValueError, match="correlation identity"):
        decode_presentation((word,), (exact, boundary))
    assert not BoundarySupport(
        exact_mark_provenance=(exact.provenance,),
        boundary_provenance=(boundary.provenance,),
    ).supported


def test_decoder_is_deterministic_under_scorer_and_proposal_reordering() -> None:
    words = tuple(_word(index, text) for index, text in enumerate(("we", "are", "ready")))
    first = _output(
        "alpha",
        _proposal(words[1], TrailingPunctuation.NONE, 0.6),
        _proposal(words[2], TrailingPunctuation.PERIOD, 0.85, sentence_break=0.9),
    )
    second = _output(
        "beta",
        _proposal(words[1], TrailingPunctuation.COMMA, 0.4),
        _proposal(words[2], TrailingPunctuation.PERIOD, 0.75, sentence_break=0.8),
    )
    reversed_first = ScorerOutput(
        provenance=first.provenance,
        proposals=tuple(reversed(first.proposals)),
    )
    reversed_second = ScorerOutput(
        provenance=second.provenance,
        proposals=tuple(reversed(second.proposals)),
    )

    normal = decode_presentation(words, (first, second))
    reordered = decode_presentation(words, (reversed_second, reversed_first))

    assert reordered == normal
    assert apply_presentation_plan(words, reordered) == apply_presentation_plan(words, normal)
    assert normal.tie_break_policy == "lexicographic-source-preserving-v1"
    assert all(decision.confidence is None for decision in normal.decisions)
    assert all(isinstance(decision.local_score_margin, float) for decision in normal.decisions)


def test_input_and_proposal_windows_fail_closed_on_malformed_bindings() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        decode_presentation((_word(0, "one"), _word(2, "three")), ())

    words = (_word(0, "one"), _word(1, "two"), _word(2, "three"))
    with pytest.raises(ValueError, match="contiguous window"):
        ScorerOutput(
            provenance=ScorerProvenance("gapped", "1.0"),
            proposals=(
                _proposal(words[0], TrailingPunctuation.NONE, 0.8),
                _proposal(words[2], TrailingPunctuation.PERIOD, 0.8),
            ),
        )

    mismatched = PresentationProposal(
        word_index=1,
        lexical_signature=words[0].lexical_signature,
        lexical_text_digest=lexical_text_digest(words[0].lexical_text),
        trailing_punctuation=TrailingPunctuation.NONE,
        punctuation_probability=0.8,
        capitalization_probability=0.1,
        sentence_break_probability=0.1,
        paragraph_break_probability=0.01,
    )
    with pytest.raises(ValueError, match="lexical binding"):
        decode_presentation(words, (_output("mismatch", mismatched),))


def test_plan_binding_rejects_a_different_lexical_or_feature_sequence() -> None:
    words = (_word(0, "hello"), _word(1, "there"))
    plan = decode_presentation(words, ())
    changed_words = (_word(0, "hello"), _word(1, "their"))

    with pytest.raises(ValueError, match="input binding"):
        apply_presentation_plan(changed_words, plan)


def test_contract_is_immutable_and_has_no_lexical_mutation_field() -> None:
    proposal_fields = {field.name for field in fields(PresentationProposal)}
    decision_fields = {field.name for field in fields(PresentationDecision)}
    forbidden = {
        "candidate_text",
        "replacement_text",
        "replacement_word",
        "lexical_edit",
        "text",
    }
    assert proposal_fields.isdisjoint(forbidden)
    assert decision_fields.isdisjoint(forbidden)

    word = _word(0, "word")
    with pytest.raises(FrozenInstanceError):
        word.lexical_text = "replacement"  # type: ignore[misc]

    with pytest.raises(ValueError, match="immutable tuple"):
        ScorerOutput(  # type: ignore[arg-type]
            provenance=ScorerProvenance("mutable", "1.0"),
            proposals=[_proposal(word, TrailingPunctuation.NONE, 1.0)],
        )
    with pytest.raises(ValueError, match="lexical word"):
        lexical_signature("...")


def test_apply_preserves_every_lexical_signature() -> None:
    words = (
        _word(0, "hello"),
        _word(1, "world"),
        _word(2, "again"),
    )
    scorer = _output(
        "local",
        _proposal(words[1], TrailingPunctuation.COMMA, 0.95),
        _proposal(
            words[2],
            TrailingPunctuation.PERIOD,
            0.95,
            capitalization=0.1,
            sentence_break=0.95,
        ),
    )

    plan = decode_presentation(words, (scorer,))
    rendered = apply_presentation_plan(words, plan)

    assert tuple(map(lexical_signature, rendered)) == tuple(
        word.lexical_signature for word in words
    )
    assert len(rendered) == len(words) == len(plan.decisions)
    assert isinstance(plan, PresentationPlan)


def test_unicode_casefold_expansion_is_not_a_safe_capitalization() -> None:
    words = (_word(0, "ß"),)
    plan = decode_presentation(words, ())

    assert not plan.decisions[0].capitalize
    assert apply_presentation_plan(words, plan) == ("ß",)

    unsafe_decision = replace(plan.decisions[0], capitalize=True)
    unsafe_plan = replace(plan, decisions=(unsafe_decision,))
    with pytest.raises(ValueError, match="code-point shape"):
        apply_presentation_plan(words, unsafe_plan)

    casefold_collision = PresentationProposal(
        word_index=0,
        lexical_signature=lexical_signature("SS"),
        lexical_text_digest=lexical_text_digest("SS"),
        trailing_punctuation=TrailingPunctuation.NONE,
        punctuation_probability=1.0,
        capitalization_probability=0.0,
        sentence_break_probability=0.0,
        paragraph_break_probability=0.0,
    )
    with pytest.raises(ValueError, match="surface binding"):
        decode_presentation(words, (_output("collision", casefold_collision),))


@pytest.mark.parametrize(
    ("source", "desired"),
    [("openai", "OpenAI"), ("api", "API"), ("macos", "macOS")],
)
def test_exact_casing_surface_supports_names_and_acronyms(
    source: str,
    desired: str,
) -> None:
    words = (_word(0, source),)
    scorer = _output(
        "case-model",
        _proposal(
            words[0],
            TrailingPunctuation.PERIOD,
            0.99,
            capitalization=0.999 if desired[0].isupper() else 0.001,
            sentence_break=0.99,
            casing_surface=desired,
            casing_probability=0.999,
        ),
    )

    plan = decode_presentation(words, _corroborated(scorer, words[0]))

    assert plan.decisions[0].casing_surface == desired
    assert apply_presentation_plan(words, plan) == (f"{desired}.",)


def test_exact_casing_surface_rejects_lexical_or_unicode_shape_drift() -> None:
    word = _word(0, "ß")
    with pytest.raises(ValueError, match="code-point shape"):
        decode_presentation(
            (word,),
            (
                _output(
                    "unsafe-case-model",
                    _proposal(
                        word,
                        TrailingPunctuation.NONE,
                        1.0,
                        capitalization=1.0,
                        casing_surface="SS",
                        casing_probability=1.0,
                    ),
                ),
            ),
        )
    with pytest.raises(ValueError, match="lexical identity"):
        _proposal(
            _word(0, "monday"),
            TrailingPunctuation.NONE,
            1.0,
            capitalization=1.0,
            casing_surface="Friday",
            casing_probability=1.0,
        )

    accented = _word(0, "café")
    canonically_equivalent_drift = _proposal(
        accented,
        TrailingPunctuation.NONE,
        1.0,
        capitalization=1.0,
        casing_surface="Cafe\u0301",
        casing_probability=1.0,
    )
    with pytest.raises(ValueError, match="code-point shape"):
        decode_presentation(
            (accented,),
            (_output("normalization-drift", canonically_equivalent_drift),),
        )


def test_plan_serialization_and_hash_are_canonical_and_complete() -> None:
    words = (_word(0, "hello"), _word(1, "there"))
    plan = decode_presentation(
        words,
        (
            ScorerOutput.abstain(
                ScorerProvenance("model", "1.0"),
                "insufficient_evidence",
            ),
        ),
    )

    serialized = serialize_presentation_plan(plan)
    payload = json.loads(serialized)

    assert serialized.endswith(b"\n")
    assert serialize_presentation_plan(plan) == serialized
    assert presentation_plan_sha256(plan) == hashlib.sha256(serialized).hexdigest()
    assert payload["input_digest"] == plan.input_digest
    assert payload["hard_constraints"] == {
        "application_count": 0,
        "violation_count": 0,
    }
    assert payload["tie_break_policy"] == plan.tie_break_policy
    assert payload["escalation_policy"] == plan.escalation_policy
    assert payload["abstentions"][0]["reason"] == "insufficient_evidence"


def test_long_transcript_uses_only_supported_stops_to_bound_sentence_spans() -> None:
    words = tuple(_word(index, f"word{index}") for index in range(200))
    scorers = tuple(
        _output(
            f"boundary-model-{index}",
            _proposal(
                words[index],
                TrailingPunctuation.PERIOD,
                0.99,
                sentence_break=0.99,
            ),
        )
        for index in (63, 127, 191)
    )

    plan = decode_presentation(
        words,
        (
            *scorers,
            _boundary_output("long-span-sat", words, probability=0.95),
        ),
    )

    assert len(plan.decisions) == 200
    assert len(apply_presentation_plan(words, plan)) == 200
    span = 0
    for decision in plan.decisions:
        span += 1
        assert span <= 64
        if decision.sentence_break:
            span = 0
