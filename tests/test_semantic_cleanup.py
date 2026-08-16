# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from local_transcript_max.semantic_cleanup import (
    CanonicalWord,
    EditProvenance,
    LexicalCleanupPolicy,
    LexicalEditClass,
    LexicalEditProposal,
    ProposalDisposition,
    SemanticAbstentionReason,
    SemanticProposalBatch,
    SemanticSafetyChecks,
    canonical_evidence_digest,
    deserialize_semantic_cleanup_plan,
    deserialize_semantic_reconciliation_result,
    propose_safe_speech_artifact_edits,
    reconcile_lexical_edits,
    reconcile_semantic_proposal_batches,
    semantic_cleanup_plan_sha256,
    semantic_reconciliation_result_sha256,
    serialize_semantic_cleanup_plan,
    serialize_semantic_reconciliation_result,
    validate_semantic_cleanup_plan,
)


def _words(*surfaces: str, speaker: str = "SPEAKER_00") -> tuple[CanonicalWord, ...]:
    return tuple(
        CanonicalWord(index, surface, speaker, index * 0.5, index * 0.5 + 0.4)
        for index, surface in enumerate(surfaces)
    )


def _checks(**overrides: bool) -> SemanticSafetyChecks:
    values = {
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
        "isolated_filler": False,
        "accidental_repetition": False,
        "abandoned_surface_evidence": False,
        "explicit_correction_evidence": False,
        "deterministic_display_equivalence": False,
        "deterministic_grammar_rule": False,
    }
    values.update(overrides)
    return SemanticSafetyChecks(**values)


def _proposal(
    words: tuple[CanonicalWord, ...],
    edit_class: LexicalEditClass,
    start: int,
    end: int,
    result: tuple[str, ...],
    *,
    checks: SemanticSafetyChecks,
    confidence: float = 0.999,
    proposal_id: str = "proposal-1",
    rule_id: str | None = None,
) -> LexicalEditProposal:
    return LexicalEditProposal.bind(
        proposal_id=proposal_id,
        edit_class=edit_class,
        canonical_words=words,
        source_start=start,
        source_end=end,
        result_surfaces=result,
        provenance=EditProvenance("test-scorer", "1.0", "a" * 64),
        semantic_safety_confidence=confidence,
        safety_checks=checks,
        rule_id=rule_id,
    )


def test_isolated_um_is_removed_with_reversible_mapping() -> None:
    words = _words("Well", "um", "I", "think", "we", "should", "go", "Friday")
    proposal = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
    )

    plan = reconcile_lexical_edits(words, (proposal,))

    assert plan.clean_surfaces == ("Well", "I", "think", "we", "should", "go", "Friday")
    assert plan.source_to_clean[1].clean_indices == ()
    assert plan.source_to_clean[1].selected_proposal_id == "proposal-1"
    assert plan.clean_to_source_indices[1] == (2,)


def test_stutter_collapse_preserves_one_exact_canonical_word() -> None:
    words = _words("Well", "I—", "I", "think", "we", "should", "go", "Friday")
    proposal = _proposal(
        words,
        LexicalEditClass.STUTTER_COLLAPSE,
        1,
        3,
        ("I",),
        checks=_checks(accidental_repetition=True),
    )

    plan = reconcile_lexical_edits(words, (proposal,))

    assert plan.clean_surfaces == ("Well", "I", "think", "we", "should", "go", "Friday")
    assert plan.clean_to_source_indices[1] == (2,)
    assert plan.source_to_clean[1].clean_indices == ()


def test_safe_generator_removes_leading_isolated_filler_but_not_standalone_hesitation() -> None:
    sentence = _words("Um,", "I", "think", "we", "should", "go")
    proposals = propose_safe_speech_artifact_edits(sentence)
    plan = reconcile_lexical_edits(sentence, proposals)
    assert plan.clean_surfaces == ("I", "think", "we", "should", "go")

    standalone = _words("Um.")
    assert propose_safe_speech_artifact_edits(standalone) == ()


def test_safe_generator_requires_explicit_abandonment_surface_for_stutter() -> None:
    dashed = _words("Well", "I—", "I", "think")
    dashed_plan = reconcile_lexical_edits(
        dashed,
        propose_safe_speech_artifact_edits(dashed),
    )
    assert dashed_plan.clean_surfaces == ("Well", "I", "think")

    tightly_timed = _words("Well", "go", "go", "now")
    assert propose_safe_speech_artifact_edits(tightly_timed) == ()


@pytest.mark.parametrize("word", ["very", "really", "no", "never"])
def test_meaning_bearing_emphasis_repetition_cannot_be_proposed(word: str) -> None:
    words = _words("It", "is", word, word, "important")
    with pytest.raises(ValueError, match="emphasis"):
        _proposal(
            words,
            LexicalEditClass.STUTTER_COLLAPSE,
            2,
            4,
            (word,),
            checks=_checks(accidental_repetition=True),
        )


def test_uncertainty_and_hedges_cannot_be_deleted_as_fillers() -> None:
    for hedge in ("think", "maybe", "probably", "actually", "like"):
        words = _words("I", hedge, "we", "should", "go")
        with pytest.raises(ValueError, match="filler"):
            _proposal(
                words,
                LexicalEditClass.FILLER_REMOVAL,
                1,
                2,
                (),
                checks=_checks(isolated_filler=True),
            )


def test_common_semantic_guard_failure_forces_abstention() -> None:
    words = _words("Well", "um", "I", "think")
    proposal = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True, preserves_uncertainty=False),
    )
    plan = reconcile_lexical_edits(words, (proposal,))
    assert plan.clean_surfaces == tuple(word.surface for word in words)
    assert plan.rejected_proposal_ids == ("proposal-1",)


def test_default_policy_abstains_from_self_correction_despite_strong_evidence() -> None:
    words = _words("We", "should", "go", "Fri—", "actually", "Monday")
    proposal = _proposal(
        words,
        LexicalEditClass.EXPLICIT_SELF_CORRECTION,
        3,
        6,
        ("Monday",),
        checks=_checks(explicit_correction_evidence=True),
        confidence=1.0,
    )
    plan = reconcile_lexical_edits(words, (proposal,))
    assert plan.clean_surfaces == tuple(word.surface for word in words)


def test_explicit_policy_can_select_mechanically_bounded_self_correction() -> None:
    words = _words("We", "should", "go", "Fri—", "actually", "Monday")
    proposal = _proposal(
        words,
        LexicalEditClass.EXPLICIT_SELF_CORRECTION,
        3,
        6,
        ("Monday",),
        checks=_checks(explicit_correction_evidence=True),
        confidence=1.0,
    )
    policy = LexicalCleanupPolicy(((LexicalEditClass.EXPLICIT_SELF_CORRECTION, 1.0),))
    plan = reconcile_lexical_edits(words, (proposal,), policy=policy)
    assert plan.clean_surfaces == ("We", "should", "go", "Monday")
    assert plan.clean_to_source_indices[-1] == (5,)


def test_ambiguous_correction_without_marker_is_impossible() -> None:
    words = _words("We", "should", "go", "Friday", "or", "Monday")
    with pytest.raises(ValueError, match="explicit correction marker"):
        _proposal(
            words,
            LexicalEditClass.EXPLICIT_SELF_CORRECTION,
            3,
            6,
            ("Monday",),
            checks=_checks(explicit_correction_evidence=True),
        )


def test_false_start_requires_surface_abandonment_and_explicit_policy() -> None:
    words = _words("We", "should", "go", "Fri—", "Monday")
    proposal = _proposal(
        words,
        LexicalEditClass.ABANDONED_FALSE_START_REMOVAL,
        3,
        4,
        (),
        checks=_checks(abandoned_surface_evidence=True),
        confidence=1.0,
    )
    assert reconcile_lexical_edits(words, (proposal,)).selected_edits == ()
    policy = LexicalCleanupPolicy(((LexicalEditClass.ABANDONED_FALSE_START_REMOVAL, 1.0),))
    assert reconcile_lexical_edits(words, (proposal,), policy=policy).clean_surfaces == (
        "We",
        "should",
        "go",
        "Monday",
    )


def test_display_and_grammar_outputs_must_match_deterministic_rules() -> None:
    number_words = _words("I", "need", "two", "copies")
    display = _proposal(
        number_words,
        LexicalEditClass.DISPLAY_NORMALIZATION,
        2,
        3,
        ("2",),
        checks=_checks(deterministic_display_equivalence=True),
        rule_id="small-cardinal-v1",
    )
    assert reconcile_lexical_edits(number_words, (display,)).selected_edits == ()
    with pytest.raises(ValueError, match="allowlisted deterministic rule"):
        _proposal(
            number_words,
            LexicalEditClass.DISPLAY_NORMALIZATION,
            2,
            3,
            ("several",),
            checks=_checks(deterministic_display_equivalence=True),
            rule_id="small-cardinal-v1",
        )

    grammar_words = _words("I", "dont", "know")
    grammar = _proposal(
        grammar_words,
        LexicalEditClass.NARROW_GRAMMATICAL_CORRECTION,
        1,
        2,
        ("don't",),
        checks=_checks(deterministic_grammar_rule=True),
        rule_id="unambiguous-contraction-spelling-v1",
    )
    assert grammar.result_lexical_sequence == ("don't",)


def test_source_digest_speaker_and_timing_tampering_fail_closed() -> None:
    words = _words("Well", "uh", "we", "can", "go")
    proposal = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
    )
    changed = list(words)
    changed[1] = replace(changed[1], end_seconds=9.0)
    with pytest.raises(ValueError, match="does not bind"):
        reconcile_lexical_edits(tuple(changed), (proposal,))
    with pytest.raises(FrozenInstanceError):
        words[1].surface = "changed"  # type: ignore[misc]


def test_cross_speaker_edit_is_rejected() -> None:
    words = list(_words("I—", "I", "agree"))
    words[1] = replace(words[1], speaker_id="SPEAKER_01")
    with pytest.raises(ValueError, match="cross speaker"):
        _proposal(
            tuple(words),
            LexicalEditClass.STUTTER_COLLAPSE,
            0,
            2,
            ("I",),
            checks=_checks(accidental_repetition=True),
        )


def test_overlapping_edits_are_globally_reconciled_deterministically() -> None:
    words = _words("Well", "um", "um", "I", "agree")
    first = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
        confidence=0.99,
        proposal_id="a",
    )
    collapse = _proposal(
        words,
        LexicalEditClass.STUTTER_COLLAPSE,
        1,
        3,
        ("um",),
        checks=_checks(accidental_repetition=True),
        confidence=0.999,
        proposal_id="b",
    )
    second = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        2,
        3,
        (),
        checks=_checks(isolated_filler=True),
        confidence=0.99,
        proposal_id="c",
    )
    plan = reconcile_lexical_edits(words, (collapse, second, first))
    assert tuple(edit.proposal_id for edit in plan.selected_edits) == ("a", "c")
    assert plan.clean_surfaces == ("Well", "I", "agree")


def test_plan_serialization_and_hash_are_canonical() -> None:
    words = _words("Well", "um", "I", "agree")
    proposal = _proposal(
        words, LexicalEditClass.FILLER_REMOVAL, 1, 2, (), checks=_checks(isolated_filler=True)
    )
    first = reconcile_lexical_edits(words, (proposal,))
    second = reconcile_lexical_edits(tuple(words), tuple((proposal,)))
    assert canonical_evidence_digest(words) == first.canonical_digest
    assert serialize_semantic_cleanup_plan(first) == serialize_semantic_cleanup_plan(second)
    assert semantic_cleanup_plan_sha256(first) == semantic_cleanup_plan_sha256(second)


def test_serialized_plan_round_trip_revalidates_policy_and_canonical_evidence() -> None:
    words = _words("Well", "um", "I", "agree")
    plan = reconcile_lexical_edits(
        words,
        propose_safe_speech_artifact_edits(words),
    )
    decoded = deserialize_semantic_cleanup_plan(serialize_semantic_cleanup_plan(plan))

    validate_semantic_cleanup_plan(
        decoded,
        words,
        policy=LexicalCleanupPolicy.safe_default(),
    )
    unsafe_edit = replace(
        decoded.selected_edits[0],
        safety_checks=replace(
            decoded.selected_edits[0].safety_checks,
            preserves_meaning=False,
        ),
    )
    unsafe_plan = replace(decoded, selected_edits=(unsafe_edit,))
    with pytest.raises(ValueError, match="deterministic production proposals"):
        validate_semantic_cleanup_plan(
            unsafe_plan,
            words,
            policy=LexicalCleanupPolicy.safe_default(),
        )
    with pytest.raises(ValueError, match="canonical evidence digest"):
        validate_semantic_cleanup_plan(
            decoded,
            tuple((*words[:-1], replace(words[-1], surface="disagree"))),
            policy=LexicalCleanupPolicy.safe_default(),
        )


def test_semantic_plan_parser_rejects_noncanonical_or_extended_json() -> None:
    words = _words("Well", "um", "I", "agree")
    plan = reconcile_lexical_edits(
        words,
        (
            _proposal(
                words,
                LexicalEditClass.FILLER_REMOVAL,
                1,
                2,
                (),
                checks=_checks(isolated_filler=True),
            ),
        ),
    )
    serialized = serialize_semantic_cleanup_plan(plan)

    with pytest.raises(ValueError, match="canonical JSON"):
        deserialize_semantic_cleanup_plan(b" " + serialized)
    with pytest.raises(ValueError, match="typed schema"):
        deserialize_semantic_cleanup_plan(serialized[:-1] + b',"rewrite":"free-form"}')


def test_arbitrary_substitution_has_no_valid_operation_shape() -> None:
    words = _words("He", "said", "we", "could", "probably", "do", "it")
    with pytest.raises(ValueError):
        _proposal(
            words,
            LexicalEditClass.NARROW_GRAMMATICAL_CORRECTION,
            0,
            7,
            ("He", "approved", "it"),
            checks=_checks(deterministic_grammar_rule=True),
            rule_id="unambiguous-contraction-spelling-v1",
        )


def test_default_plan_revalidation_rejects_self_asserted_stutter_edit() -> None:
    words = _words("go", "go", "now")
    forged = _proposal(
        words,
        LexicalEditClass.STUTTER_COLLAPSE,
        0,
        2,
        ("go",),
        checks=_checks(accidental_repetition=True),
    )
    plan = reconcile_lexical_edits(words, (forged,))

    assert propose_safe_speech_artifact_edits(words) == ()
    with pytest.raises(ValueError, match="deterministic production proposals"):
        validate_semantic_cleanup_plan(
            plan,
            words,
            policy=LexicalCleanupPolicy.safe_default(),
        )


def test_stutter_shape_requires_exact_retained_source_surface() -> None:
    words = _words("ß—", "ß")
    with pytest.raises(ValueError, match="exact final source surface"):
        _proposal(
            words,
            LexicalEditClass.STUTTER_COLLAPSE,
            0,
            2,
            ("SS!!!",),
            checks=_checks(accidental_repetition=True),
        )


def test_filler_cleanup_cannot_erase_a_complete_turn() -> None:
    words = (
        CanonicalWord(0, "Well.", "SPEAKER_00", 0.0, 0.2, 0),
        CanonicalWord(1, "um", "SPEAKER_00", 0.3, 0.5, 1),
        CanonicalWord(2, "Okay.", "SPEAKER_00", 0.6, 0.8, 2),
    )
    proposal = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
    )
    plan = reconcile_lexical_edits(words, (proposal,))

    assert plan.selected_edits == ()
    assert plan.clean_surfaces == ("Well.", "um", "Okay.")


def test_backend_batches_produce_selected_and_policy_disabled_receipts() -> None:
    words = _words("Well", "um", "we", "go", "Fri—", "actually", "Monday")
    filler = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
        confidence=0.99,
        proposal_id="backend.filler",
    )
    correction = _proposal(
        words,
        LexicalEditClass.EXPLICIT_SELF_CORRECTION,
        4,
        7,
        ("Monday",),
        checks=_checks(explicit_correction_evidence=True),
        confidence=1.0,
        proposal_id="backend.correction",
    )
    provenance = EditProvenance("test-scorer", "1.0", "a" * 64)
    batch = SemanticProposalBatch.from_proposals(
        batch_id="backend-window-1",
        canonical_words=words,
        provenance=provenance,
        proposals=(correction, filler),
    )

    result = reconcile_semantic_proposal_batches(words, (batch,))

    assert result.plan.clean_surfaces == ("Well", "we", "go", "Fri—", "actually", "Monday")
    assert tuple(
        (receipt.proposal_id, receipt.disposition) for receipt in result.proposal_receipts
    ) == (
        ("backend.correction", ProposalDisposition.POLICY_DISABLED),
        ("backend.filler", ProposalDisposition.SELECTED),
    )


def test_backend_abstention_is_explicit_source_bound_and_serialized() -> None:
    words = _words("I", "think", "maybe", "we", "should", "go", "Friday")
    provenance = EditProvenance("local-llm", "indexed-v1", "b" * 64)
    batch = SemanticProposalBatch.abstain(
        batch_id="local-llm-window-7",
        canonical_words=words,
        provenance=provenance,
        reason=SemanticAbstentionReason.AMBIGUOUS,
        detail_digest="c" * 64,
    )

    result = reconcile_semantic_proposal_batches(words, (batch,))
    encoded = serialize_semantic_reconciliation_result(result)

    assert result.plan.clean_surfaces == tuple(word.surface for word in words)
    assert result.proposal_receipts == ()
    assert result.abstention_receipts == (batch.abstention,)
    assert b'"reason":"ambiguous"' in encoded
    assert b'"detail_digest"' in encoded
    assert semantic_reconciliation_result_sha256(result) == (
        semantic_reconciliation_result_sha256(result)
    )
    assert deserialize_semantic_reconciliation_result(encoded) == result


def test_semantic_reconciliation_parser_rejects_nested_receipt_drift() -> None:
    words = _words("Well", "um", "we", "go")
    filler = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
        confidence=0.99,
        proposal_id="backend.filler",
    )
    batch = SemanticProposalBatch.from_proposals(
        batch_id="backend-window-1",
        canonical_words=words,
        provenance=filler.provenance,
        proposals=(filler,),
    )
    result = reconcile_semantic_proposal_batches(words, (batch,))
    payload = json.loads(serialize_semantic_reconciliation_result(result))
    payload["proposal_receipts"][0]["source_digest"] = "f" * 64
    tampered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    with pytest.raises(ValueError, match="does not bind its plan edit"):
        deserialize_semantic_reconciliation_result(tampered)


def test_semantic_reconciliation_parser_rejects_noncanonical_json() -> None:
    words = _words("I", "think", "maybe")
    batch = SemanticProposalBatch.abstain(
        batch_id="backend-window-1",
        canonical_words=words,
        provenance=EditProvenance("test-scorer", "1.0", "a" * 64),
        reason=SemanticAbstentionReason.NO_SUPPORTED_EDIT,
    )
    encoded = serialize_semantic_reconciliation_result(
        reconcile_semantic_proposal_batches(words, (batch,))
    )

    with pytest.raises(ValueError, match="canonical JSON"):
        deserialize_semantic_reconciliation_result(b" " + encoded)


def test_backend_batch_rejects_empty_or_mismatched_provenance() -> None:
    words = _words("Well", "um", "we", "go")
    proposal = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
    )
    with pytest.raises(ValueError, match="cannot be empty"):
        SemanticProposalBatch.from_proposals(
            batch_id="empty",
            canonical_words=words,
            provenance=proposal.provenance,
            proposals=(),
        )
    with pytest.raises(ValueError, match="provenance does not bind"):
        SemanticProposalBatch.from_proposals(
            batch_id="mismatch",
            canonical_words=words,
            provenance=EditProvenance("other", "1.0", "d" * 64),
            proposals=(proposal,),
        )


@pytest.mark.parametrize(
    "failed_guard",
    [
        "preserves_meaning",
        "preserves_uncertainty",
        "preserves_modality",
        "preserves_negation",
        "preserves_attribution",
        "preserves_quantities_names_dates",
        "preserves_tense",
        "preserves_commitment",
        "preserves_discourse_meaning",
    ],
)
def test_every_semantic_fidelity_guard_has_a_fail_closed_receipt(
    failed_guard: str,
) -> None:
    words = _words("Well", "um", "we", "go")
    proposal = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True, **{failed_guard: False}),
    )
    batch = SemanticProposalBatch.from_proposals(
        batch_id=f"unsafe-{failed_guard}",
        canonical_words=words,
        provenance=proposal.provenance,
        proposals=(proposal,),
    )

    result = reconcile_semantic_proposal_batches(words, (batch,))

    assert result.plan.clean_surfaces == tuple(word.surface for word in words)
    assert result.proposal_receipts[0].disposition is ProposalDisposition.COMMON_SAFETY_FAILED


def test_below_threshold_and_overlap_conflicts_are_distinguished() -> None:
    # A stutter proposal cannot span different lexical surfaces, so use a
    # second exact proposal for the same source span to exercise tie-breaking.
    words = _words("Well", "I—", "I", "go")
    first = _proposal(
        words,
        LexicalEditClass.STUTTER_COLLAPSE,
        1,
        3,
        ("I",),
        checks=_checks(accidental_repetition=True),
        confidence=0.999,
        proposal_id="overlap-a",
    )
    second = replace(first, proposal_id="overlap-b")
    low_words = _words("Well", "um", "we", "go")
    low = _proposal(
        low_words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
        confidence=0.97,
        proposal_id="low",
    )
    first_batch = SemanticProposalBatch.from_proposals(
        batch_id="overlaps",
        canonical_words=words,
        provenance=first.provenance,
        proposals=(first, second),
    )
    overlap_result = reconcile_semantic_proposal_batches(words, (first_batch,))
    assert tuple(receipt.disposition for receipt in overlap_result.proposal_receipts) == (
        ProposalDisposition.SELECTED,
        ProposalDisposition.OVERLAP_CONFLICT,
    )
    low_batch = SemanticProposalBatch.from_proposals(
        batch_id="low",
        canonical_words=low_words,
        provenance=low.provenance,
        proposals=(low,),
    )
    low_result = reconcile_semantic_proposal_batches(low_words, (low_batch,))
    assert low_result.proposal_receipts[0].disposition is ProposalDisposition.BELOW_THRESHOLD


def test_backend_batch_fails_if_canonical_timing_changes() -> None:
    words = _words("Well", "um", "we", "go")
    proposal = _proposal(
        words,
        LexicalEditClass.FILLER_REMOVAL,
        1,
        2,
        (),
        checks=_checks(isolated_filler=True),
    )
    batch = SemanticProposalBatch.from_proposals(
        batch_id="bound",
        canonical_words=words,
        provenance=proposal.provenance,
        proposals=(proposal,),
    )
    changed = tuple((*words[:-1], replace(words[-1], end_seconds=99.0)))
    with pytest.raises(ValueError, match="canonical evidence changed"):
        reconcile_semantic_proposal_batches(changed, (batch,))


def test_policy_cannot_lower_class_specific_safety_floors() -> None:
    for edit_class, unsafe_threshold in (
        (LexicalEditClass.FILLER_REMOVAL, 0.979),
        (LexicalEditClass.STUTTER_COLLAPSE, 0.994),
        (LexicalEditClass.ABANDONED_FALSE_START_REMOVAL, 0.998),
        (LexicalEditClass.EXPLICIT_SELF_CORRECTION, 0.998),
        (LexicalEditClass.DISPLAY_NORMALIZATION, 0.999),
        (LexicalEditClass.NARROW_GRAMMATICAL_CORRECTION, 0.999),
    ):
        with pytest.raises(ValueError, match="fail-closed class minimum"):
            LexicalCleanupPolicy(((edit_class, unsafe_threshold),))


def test_semantic_safety_checks_reject_truthy_non_booleans() -> None:
    values = (
        dict(_checks().__dict__)
        if hasattr(_checks(), "__dict__")
        else {
            field_name: getattr(_checks(), field_name)
            for field_name in _checks().__dataclass_fields__
        }
    )
    values["preserves_meaning"] = 1
    with pytest.raises(ValueError, match="explicit booleans"):
        SemanticSafetyChecks(**values)
