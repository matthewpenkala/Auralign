# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import local_transcript_max.cleanup as cleanup_module
import local_transcript_max.presentation as presentation_module
from local_transcript_max.cleanup import (
    CleanupRejectedError,
    PunctuationEdit,
    cleanup_is_safe,
    project_punctuation_cleanup,
    project_punctuation_edits,
    request_apple_foundation_cleanup,
    request_punctuation_cleanup,
    run_cleanup,
    validate_cleanup_endpoint,
)
from local_transcript_max.config import load_config
from local_transcript_max.export.srt_export import render_srt
from local_transcript_max.presentation import (
    DecoderWord,
    ScorerOutput,
    ScorerProvenance,
    apply_presentation_plan,
    decode_presentation,
)
from local_transcript_max.presentation_sat import SaTMode, SaTSourceOptions
from local_transcript_max.schemas import AlignedWord, ASRSegment, SpeakerTurn, Transcript


def test_cleanup_disabled_default(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    report = run_cleanup(Transcript(input_path="x"), tmp_path, config)
    assert report["enabled"] is False


def test_localhost_accepted_remote_rejected() -> None:
    validate_cleanup_endpoint("http://127.0.0.1:11434/v1", allow_remote_cleanup=False)
    with pytest.raises(CleanupRejectedError):
        validate_cleanup_endpoint("https://example.com/v1", allow_remote_cleanup=False)
    validate_cleanup_endpoint("https://example.com/v1", allow_remote_cleanup=True)
    with pytest.raises(CleanupRejectedError, match="inline credentials"):
        validate_cleanup_endpoint(
            "http://user:pass@127.0.0.1:11434/v1",
            allow_remote_cleanup=False,
        )
    with pytest.raises(CleanupRejectedError, match="inline credentials"):
        validate_cleanup_endpoint(
            "http://127.0.0.1:11434/v1?token=secret",
            allow_remote_cleanup=False,
        )
    with pytest.raises(CleanupRejectedError, match="Offline mode"):
        validate_cleanup_endpoint(
            "https://example.com/v1",
            allow_remote_cleanup=True,
            offline=True,
        )


def test_divergence_rejection() -> None:
    accepted, reason = cleanup_is_safe(
        "one two three four",
        "one two three four five six seven eight",
        max_word_delta_ratio=0.15,
    )
    assert accepted is False
    assert reason == "word_count_diverged_too_far"


def test_punctuation_cleanup_requires_exact_lexical_sequence() -> None:
    accepted, reason = cleanup_is_safe(
        "good pretty good it was i guess",
        "Good, quite good. It was, I guess.",
        max_word_delta_ratio=0.15,
        preserve_lexical_sequence=True,
    )

    assert accepted is False
    assert reason == "lexical_word_changed_at_index_1"


def test_configured_casing_is_encoded_in_the_global_presentation_plan() -> None:
    source = ("openai", "api", "macos")
    words = tuple(DecoderWord.from_text(index, token) for index, token in enumerate(source))
    scorer = cleanup_module._source_and_grammar_scorer(
        words,
        source,
        proper_nouns=("OpenAI", "API", "macOS"),
    )

    plan = decode_presentation(words, (scorer,))
    rendered = apply_presentation_plan(words, plan)

    assert tuple(decision.casing_surface for decision in plan.decisions) == (
        "OpenAI",
        "API",
        "macOS",
    )
    assert rendered == ("OpenAI,", "API,", "macOS")


def test_source_grammar_abstains_on_unpunctuated_incomplete_trailing_speech() -> None:
    source = ("we", "could", "finish", "this", "because")
    words = tuple(DecoderWord.from_text(index, token) for index, token in enumerate(source))
    scorer = cleanup_module._source_and_grammar_scorer(
        words,
        source,
        proper_nouns=(),
    )

    plan = decode_presentation(words, (scorer,))
    rendered = apply_presentation_plan(words, plan)

    assert rendered[-1] == "because"
    assert plan.decisions[-1].sentence_break is False


def test_readability_scorer_proposes_minimal_safe_weak_boundaries() -> None:
    tokens = tuple(f"word{index}" for index in range(70))
    words = tuple(
        DecoderWord.from_text(
            index,
            token,
            start_seconds=index * 0.2,
            end_seconds=index * 0.2 + 0.15,
            pause_after_seconds=0.5 if index in {22, 46} else 0.05,
            speaker_id="SPEAKER_00",
            begins_turn=index == 0,
            ends_turn=index == len(tokens) - 1,
        )
        for index, token in enumerate(tokens)
    )

    selected = cleanup_module._readability_comma_indices(words, tokens)

    assert selected == frozenset({22, 46})
    punctuation_boundaries = (-1, *sorted(selected), len(tokens) - 1)
    assert (
        max(
            following - previous
            for previous, following in zip(
                punctuation_boundaries,
                punctuation_boundaries[1:],
                strict=False,
            )
        )
        <= 34
    )
    scorer = cleanup_module._source_and_grammar_scorer(
        words,
        tokens,
        proper_nouns=(),
    )
    selected_proposals = {
        proposal.word_index: proposal
        for proposal in scorer.proposals
        if proposal.word_index in selected
    }
    assert set(selected_proposals) == selected
    assert all(
        proposal.trailing_punctuation.value == ","
        and proposal.punctuation_probability == pytest.approx(0.985)
        and not proposal.sentence_break_probability >= 0.5
        for proposal in selected_proposals.values()
    )


def test_readability_scorer_abstains_without_a_safe_weak_boundary_path() -> None:
    tokens = tuple("and" if index % 2 == 0 else "the" for index in range(40))
    words = tuple(
        DecoderWord.from_text(
            index,
            token,
            pause_after_seconds=0.0,
            begins_turn=index == 0,
            ends_turn=index == len(tokens) - 1,
            incomplete_continuation=True,
        )
        for index, token in enumerate(tokens)
    )

    assert cleanup_module._readability_comma_indices(words, tokens) == frozenset()


def test_source_scorer_does_not_promote_subthreshold_pause_to_strong_stop() -> None:
    tokens = tuple(f"word{index}" for index in range(70))
    words = tuple(
        DecoderWord.from_text(
            index,
            token,
            pause_after_seconds=0.02,
            speaker_id="SPEAKER_00",
            container_index=0,
            begins_turn=index == 0,
            ends_turn=index == len(tokens) - 1,
        )
        for index, token in enumerate(tokens)
    )

    scorer = cleanup_module._source_and_grammar_scorer(
        words,
        tokens,
        proper_nouns=(),
    )

    assert all(
        proposal.trailing_punctuation.value not in {".", "?", "!", "…"}
        for proposal in scorer.proposals
    )


def test_source_scorer_does_not_fragment_embedded_or_repeated_stance_phrases() -> None:
    tokens = tuple(
        [*(f"word{index}" for index in range(30)), "i", "think"]
        + [*(f"middle{index}" for index in range(30)), "i", "believe", "i", "guess"]
    )
    words = tuple(
        DecoderWord.from_text(
            index,
            token,
            pause_after_seconds=0.02,
            speaker_id="SPEAKER_00",
            container_index=0,
            begins_turn=index == 0,
            ends_turn=index == len(tokens) - 1,
        )
        for index, token in enumerate(tokens)
    )
    scorer = cleanup_module._source_and_grammar_scorer(words, tokens, proper_nouns=())

    assert all(
        proposal.trailing_punctuation.value not in {".", "?", "!", "…"}
        for proposal in scorer.proposals
    )


def test_source_scorer_preserves_comma_and_does_not_bridge_containers() -> None:
    tokens = ("first,", "i", "think", "second", "i", "believe")
    words = tuple(
        DecoderWord.from_text(
            index,
            token,
            pause_after_seconds=0.02,
            container_index=0 if index < 3 else 1,
            begins_turn=index in {0, 3},
            ends_turn=index in {2, 5},
        )
        for index, token in enumerate(tokens)
    )
    scorer = cleanup_module._source_and_grammar_scorer(words, tokens, proper_nouns=())

    assert scorer.proposals[0].trailing_punctuation.value == ","
    assert all(
        proposal.trailing_punctuation.value not in {".", "?", "!", "…"}
        for proposal in scorer.proposals[1:]
    )


@pytest.mark.parametrize(
    ("original", "cleaned"),
    [
        ("can't", "cant"),
        ("co-op", "coop"),
        ("C++", "C"),
        ("3.14", "314"),
        ("ß", "SS"),
        (".", "!"),
    ],
)
def test_punctuation_cleanup_rejects_internal_or_punctuation_only_changes(
    original: str,
    cleaned: str,
) -> None:
    accepted, reason = cleanup_is_safe(
        original,
        cleaned,
        max_word_delta_ratio=0.15,
        preserve_lexical_sequence=True,
    )

    assert accepted is False
    assert reason == "lexical_word_changed_at_index_0"


def test_punctuation_cleanup_projects_only_text_onto_timed_words() -> None:
    words = [
        AlignedWord(
            text=text,
            start=index * 0.5,
            end=(index + 1) * 0.5,
            alignment_source="forced",
        )
        for index, text in enumerate("good pretty good it was i guess".split())
    ]
    transcript = Transcript(
        input_path="x.wav",
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=3.5,
                text="good pretty good it was i guess",
                words=words,
            )
        ],
        words=words,
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                mapped_name="Matthew Penkala",
                start=0.0,
                end=3.5,
                text="good pretty good it was i guess",
                words=words,
            )
        ],
    )

    repaired = project_punctuation_cleanup(
        transcript,
        "Good, pretty good. It was, I guess.",
    )

    repaired_turn = repaired.speaker_turns[0]
    assert repaired_turn.text == "Good, pretty good. It was, I guess."
    assert [word.text for word in repaired_turn.words] == [
        "Good,",
        "pretty",
        "good.",
        "It",
        "was,",
        "I",
        "guess.",
    ]
    assert [(word.start, word.end, word.alignment_source) for word in repaired_turn.words] == [
        (word.start, word.end, word.alignment_source) for word in words
    ]
    assert repaired_turn.mapped_name == "Matthew Penkala"
    assert [word.text for word in repaired.words] == [
        "Good,",
        "pretty",
        "good.",
        "It",
        "was,",
        "I",
        "guess.",
    ]
    assert repaired.segments[0].text == "Good, pretty good. It was, I guess."
    assert [word.text for word in repaired.segments[0].words] == [
        word.text for word in repaired.words
    ]
    base_srt = render_srt(transcript)
    overlaid_srt = render_srt(
        transcript,
        word_surfaces=[
            word.text for turn in repaired.speaker_turns for word in turn.words if word.text.strip()
        ],
    )
    assert [line for line in base_srt.splitlines() if " --> " in line] == [
        line for line in overlaid_srt.splitlines() if " --> " in line
    ]
    assert "Good, pretty good. It was, I guess." in " ".join(overlaid_srt.splitlines())
    assert transcript.speaker_turns[0].text == "good pretty good it was i guess"


def test_word_indexed_punctuation_edits_cannot_change_lexical_words() -> None:
    words = [
        AlignedWord(text=text, start=index, end=index + 0.5, alignment_source="forced")
        for index, text in enumerate("good pretty good it was i guess".split())
    ]
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=6.5,
                text="good pretty good it was i guess",
                words=words,
            )
        ],
    )

    repaired = project_punctuation_edits(
        transcript,
        [
            PunctuationEdit(
                word_index=0,
                trailing_punctuation=",",
                capitalize=True,
            ),
        ],
    )

    assert repaired.speaker_turns[0].text == "Good, pretty good it was I guess"
    assert [
        (word.start, word.end, word.alignment_source) for word in repaired.speaker_turns[0].words
    ] == [(word.start, word.end, word.alignment_source) for word in words]
    with pytest.raises(ValueError, match="duplicate_punctuation_edit_index"):
        project_punctuation_edits(
            transcript,
            [
                PunctuationEdit(word_index=1, trailing_punctuation=","),
                PunctuationEdit(word_index=1, trailing_punctuation="."),
            ],
        )
    with pytest.raises(ValueError, match="punctuation_edit_index_out_of_range"):
        project_punctuation_edits(
            transcript,
            [PunctuationEdit(word_index=len(words), trailing_punctuation=".")],
        )


def test_proper_noun_casing_repairs_possessives_without_lexical_change() -> None:
    repaired = cleanup_module._apply_proper_noun_casing(
        ["katie's", "KATIE’S,", "katie"],
        ["Katie"],
    )

    assert repaired == ["Katie's", "Katie’S,", "Katie"]
    for source, candidate in zip(["katie's", "KATIE’S,", "katie"], repaired, strict=True):
        accepted, reason = cleanup_is_safe(
            source,
            candidate,
            max_word_delta_ratio=0.0,
            preserve_lexical_sequence=True,
        )
        assert accepted is True, reason


def test_punctuation_windows_cover_each_word_once_and_fallback_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_tokens = [
        f"word{index}." if index % 50 == 49 else f"word{index}" for index in range(500)
    ]
    call_count = 0

    def restore_window(
        endpoint: str,
        *,
        model: str | None,
        scope: str,
        text: str,
    ) -> str:
        del endpoint, model, scope
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            tokens = text.split()
            tokens[20] = "changed"
            return " ".join(tokens)
        return text

    monkeypatch.setattr(cleanup_module, "request_local_cleanup", restore_window)

    cleaned, report = request_punctuation_cleanup(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=" ".join(source_tokens),
    )

    assert cleanup_is_safe(
        " ".join(source_tokens),
        cleaned,
        max_word_delta_ratio=0.0,
        preserve_lexical_sequence=True,
    ) == (True, "accepted")
    assert report["window_count"] == 3
    assert report["accepted_window_count"] == 2
    assert report["fallback_window_count"] == 1
    assert report["fallback_reasons"] == {"lexical_validation_failed": 1}


def test_apple_punctuation_window_stops_after_exact_final_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def restore(**kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return str(kwargs["text"])

    monkeypatch.setattr(cleanup_module, "request_cleanup_backend", restore)
    source = " ".join(f"word{index}." if index == 63 else f"word{index}" for index in range(80))

    cleaned, report = request_punctuation_cleanup(
        "unused",
        model=None,
        mode="apple-foundation",
        text=source,
        required_complete=True,
    )

    assert cleanup_is_safe(
        source,
        cleaned,
        max_word_delta_ratio=0.0,
        preserve_lexical_sequence=True,
    ) == (True, "accepted")
    assert calls == 1
    assert report["window_count"] == 1
    assert report["fallback_window_count"] == 0


def test_required_cleanup_rejects_surface_quality_blockers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_decoded_surface_quality",
        lambda *args, **kwargs: {
            "schema_version": "global_presentation_quality.v7",
            "lexical_sequence_preserved": True,
            "suspicious_fragment_addition_count": 1,
            "contraction_punctuation_violation_count": 0,
            "long_unpunctuated_run_count": 0,
        },
    )

    with pytest.raises(CleanupRejectedError, match="surface-quality validation"):
        request_punctuation_cleanup(
            "http://127.0.0.1:11434/v1",
            model=None,
            text="we should go now",
            required_complete=True,
        )

    _, report = request_punctuation_cleanup(
        "http://127.0.0.1:11434/v1",
        model=None,
        text="we should go now",
        required_complete=False,
    )
    assert report["surface_quality"]["suspicious_fragment_addition_count"] == 1  # type: ignore[index]


def test_required_cleanup_treats_typed_supported_negative_margin_as_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_decoded_surface_quality",
        lambda *args, **kwargs: {
            "schema_version": "global_presentation_quality.v7",
            "lexical_sequence_preserved": True,
            "suspicious_fragment_addition_count": 0,
            "contraction_punctuation_violation_count": 0,
            "added_short_strong_stop_burst_count": 0,
            "added_strong_stop_burst_count": 0,
            "long_unpunctuated_run_count": 0,
            "overlong_sentence_span_count": 0,
            "locally_adverse_added_strong_stop_count": 4,
            "typed_supported_added_strong_stop_count": 4,
            "unsupported_added_strong_stop_count": 0,
            "incomplete_abandoned_fragment_stop_violation_count": 0,
            "lowercase_after_strong_stop_count": 0,
            "typed_question_scope_without_question_mark_count": 0,
        },
    )

    _cleaned, report = request_punctuation_cleanup(
        "http://127.0.0.1:11434/v1",
        model=None,
        text="we should go now",
        required_complete=True,
    )

    assert report["surface_quality"]["locally_adverse_added_strong_stop_count"] == 4  # type: ignore[index]


def test_required_cleanup_blocks_added_stop_without_typed_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_decoded_surface_quality",
        lambda *args, **kwargs: {
            "schema_version": "global_presentation_quality.v7",
            "lexical_sequence_preserved": True,
            "suspicious_fragment_addition_count": 0,
            "contraction_punctuation_violation_count": 0,
            "added_short_strong_stop_burst_count": 0,
            "added_strong_stop_burst_count": 0,
            "long_unpunctuated_run_count": 0,
            "overlong_sentence_span_count": 0,
            "unsupported_added_strong_stop_count": 1,
            "incomplete_abandoned_fragment_stop_violation_count": 0,
            "lowercase_after_strong_stop_count": 0,
            "typed_question_scope_without_question_mark_count": 0,
        },
    )

    with pytest.raises(CleanupRejectedError, match="unsupported_added_strong_stop_count"):
        request_punctuation_cleanup(
            "http://127.0.0.1:11434/v1",
            model=None,
            text="we should go now",
            required_complete=True,
        )


def test_cleanup_writes_diff_and_preserves_raw(tmp_path: Path, monkeypatch) -> None:
    config = load_config(cwd=tmp_path)
    config.cleanup.mode = "local-llm"
    config.cleanup.write_diff = True
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[SpeakerTurn(speaker="SPEAKER_00", start=0, end=1, text="hello world")],
    )
    monkeypatch.setattr(
        "local_transcript_max.cleanup.request_local_cleanup",
        lambda endpoint, model, scope, text: "Hello world.",
    )
    report = run_cleanup(transcript, tmp_path, config)
    assert report["accepted"] is True
    request = report["request"]
    assert request["sat_receipt"]["status"] == "abstained"  # type: ignore[index]
    assert request["sat_receipt"]["mode"] == "optional"  # type: ignore[index]
    assert request["sat_receipt"]["reason_code"] == "assets_not_configured"  # type: ignore[index]
    assert request["sat_receipt"]["network_access"] is False  # type: ignore[index]
    assert request["word_model_receipt"] == {  # type: ignore[index]
        "schema_version": "auralign.presentation-word-model.routing.v1",
        "status": "off",
        "backend": "none",
        "reason_code": "no_approved_checkpoint",
        "authoritative_text": False,
        "network_access": False,
    }
    assert {
        item["key"]
        for item in request["proposal_source_registry"]  # type: ignore[index]
    } >= {"source-grammar-features", "cleanup-backend-indexed-windows", "sat-local"}
    assert (tmp_path / "cleanup_diff.json").exists()
    assert (tmp_path / "transcript.cleaned.json").exists()
    assert (tmp_path / "semantic_cleanup_receipt.json").exists()
    assert (tmp_path / "transcript.cleaned.srt").exists()
    assert (tmp_path / "transcript.cleaned.vtt").exists()
    assert not (tmp_path / "transcript.raw.json").exists()


def test_zero_weight_sat_receipt_is_retained_without_entering_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "Hello world.",
    )
    baseline = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text="hello world",
        mode="local-llm",
        proper_nouns=(),
        required_complete=False,
        transcript=None,
        sat_options=SaTSourceOptions(mode=SaTMode.OFF),
    )
    provenance = ScorerProvenance(
        "wtpsplit-sat-3l-sm",
        "2.2.1",
        "a" * 64,
    )
    sat_output = ScorerOutput.abstain(provenance, "signal_weight_zero")
    sat_receipt = SimpleNamespace(signal_weight=0.0, output_digest="c" * 64)
    monkeypatch.setattr(
        cleanup_module,
        "run_registered_sat_source",
        lambda words, options: SimpleNamespace(
            output=sat_output,
            receipt=sat_receipt,
        ),
    )
    monkeypatch.setattr(
        cleanup_module,
        "sat_score_receipt_to_dict",
        lambda receipt: {
            "schema_version": "auralign.presentation.sat.receipt.v2",
            "status": "succeeded",
            "signal_weight": receipt.signal_weight,
        },
    )
    monkeypatch.setattr(
        cleanup_module,
        "sat_score_receipt_sha256",
        lambda receipt: "b" * 64,
    )

    zero_weight = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text="hello world",
        mode="local-llm",
        proper_nouns=(),
        required_complete=False,
        transcript=None,
        sat_options=SaTSourceOptions(
            mode=SaTMode.OPTIONAL,
            model_dir=Path("unused-model"),
            tokenizer_dir=Path("unused-tokenizer"),
            signal_weight=0.0,
        ),
    )

    assert zero_weight.plan == baseline.plan
    assert zero_weight.text == baseline.text
    assert zero_weight.report["scorer_count"] == baseline.report["scorer_count"]
    assert (
        zero_weight.report["scorer_abstention_count"] == baseline.report["scorer_abstention_count"]
    )
    sat_receipts = [
        item
        for item in zero_weight.report["proposal_source_receipts"]
        if item["scorer_id"] == "wtpsplit-sat-3l-sm"
    ]
    assert len(sat_receipts) == 1
    assert sat_receipts[0]["status"] == "abstained"
    assert sat_receipts[0]["proposal_count"] == 0
    assert sat_receipts[0]["output_digest"] == "c" * 64
    assert sat_receipts[0]["source_receipt_sha256"] == "b" * 64
    assert zero_weight.report["sat_receipt"]["receipt_sha256"] == "b" * 64


def test_semantically_faithful_cleanup_removes_only_bound_filler_and_preserves_hedge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_tokens = "Um, I think maybe we should go Friday".split()
    words = [
        AlignedWord(
            text=token,
            start=index * 0.2,
            end=index * 0.2 + 0.15,
            speaker="SPEAKER_00",
            alignment_source="forced",
        )
        for index, token in enumerate(source_tokens)
    ]
    canonical = Transcript(
        input_path="x.wav",
        language="en",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=words[-1].end or 1.0,
                text=" ".join(source_tokens),
                words=words,
            )
        ],
    )
    config = load_config(cwd=tmp_path)
    config.cleanup.mode = "local-llm"
    config.cleanup.required_complete = True
    config.cleanup.quality_profile = "semantically-faithful-clean-v1"
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )

    report = run_cleanup(canonical, tmp_path, config)
    cleaned = Transcript.model_validate_json(
        (tmp_path / "transcript.cleaned.json").read_text(encoding="utf-8")
    )
    semantic_plan = json.loads(
        (tmp_path / "semantic_cleanup_plan.json").read_text(encoding="utf-8")
    )

    assert report["accepted"] is True
    assert report["request"]["semantic_edit_count"] == 1  # type: ignore[index]
    assert report["request"]["semantic_clean_word_count"] == 7  # type: ignore[index]
    assert [edit["edit_class"] for edit in semantic_plan["selected_edits"]] == ["filler_removal"]
    assert cleaned.speaker_turns[0].words[0].text == ""
    assert "maybe" in cleaned.speaker_turns[0].text.casefold().split()
    assert "um" not in cleaned.speaker_turns[0].text.casefold().replace(",", "").split()
    assert [
        (word.start, word.end, word.alignment_source) for word in cleaned.speaker_turns[0].words
    ] == [(word.start, word.end, word.alignment_source) for word in words]
    assert canonical.speaker_turns[0].words[0].text == "Um,"


def test_semantic_cleanup_supports_segment_only_transcripts_without_losing_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_tokens = "Um, I think maybe this works".split()
    words = [
        AlignedWord(
            text=token,
            start=index * 0.25,
            end=index * 0.25 + 0.2,
            alignment_source="forced",
        )
        for index, token in enumerate(source_tokens)
    ]
    canonical = Transcript(
        input_path="segment-only.wav",
        language="en",
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=words[-1].end or 1.0,
                text=" ".join(source_tokens),
                words=words,
            )
        ],
    )
    config = load_config(cwd=tmp_path)
    config.cleanup.mode = "local-llm"
    config.cleanup.required_complete = True
    config.cleanup.quality_profile = "semantically-faithful-clean-v1"
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )

    report = run_cleanup(canonical, tmp_path, config)
    cleaned = Transcript.model_validate_json(
        (tmp_path / "transcript.cleaned.json").read_text(encoding="utf-8")
    )

    assert report["accepted"] is True
    assert report["request"]["semantic_edit_count"] == 1  # type: ignore[index]
    assert cleaned.speaker_turns == []
    assert cleaned.segments[0].words[0].text == ""
    assert cleaned.segments[0].text == "I think maybe this works"
    assert [
        (word.start, word.end, word.alignment_source) for word in cleaned.segments[0].words
    ] == [(word.start, word.end, word.alignment_source) for word in words]
    assert "Um," in canonical.segments[0].text


def test_apple_foundation_unavailable_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableModel:
        def is_available(self) -> tuple[bool, object]:
            return False, SimpleNamespace(name="MODEL_NOT_READY")

    fake_sdk = SimpleNamespace(SystemLanguageModel=UnavailableModel)
    monkeypatch.setattr(cleanup_module.importlib, "import_module", lambda _name: fake_sdk)

    with pytest.raises(CleanupRejectedError, match="MODEL_NOT_READY"):
        request_apple_foundation_cleanup(scope="punctuation", text="hello world")


def test_apple_foundation_native_loader_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def native_loader_failure(_name: str) -> object:
        raise OSError("incompatible native deployment target")

    monkeypatch.setattr(cleanup_module.importlib, "import_module", native_loader_failure)

    with pytest.raises(CleanupRejectedError, match="optional apple-fm-sdk"):
        request_apple_foundation_cleanup(scope="punctuation", text="hello world")


def test_apple_foundation_malformed_guided_response_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AvailableModel:
        def is_available(self) -> tuple[bool, None]:
            return True, None

    class SamplingMode:
        @staticmethod
        def greedy() -> str:
            return "greedy"

    class GenerationOptions:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class Session:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def respond(self, *_args: object, **_kwargs: object) -> object:
            return object()

    fake_sdk = SimpleNamespace(
        SystemLanguageModel=AvailableModel,
        SamplingMode=SamplingMode,
        GenerationOptions=GenerationOptions,
        LanguageModelSession=Session,
    )
    monkeypatch.setattr(cleanup_module.importlib, "import_module", lambda _name: fake_sdk)

    with pytest.raises(CleanupRejectedError, match="malformed guided content"):
        request_apple_foundation_cleanup(scope="punctuation", text="hello world")


def test_apple_foundation_rejects_lexical_drift_and_uses_safe_structured_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AvailableModel:
        def is_available(self) -> tuple[bool, None]:
            return True, None

    class SamplingMode:
        @staticmethod
        def greedy() -> str:
            return "greedy"

    class GenerationOptions:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class GuidedResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def value(self) -> dict[str, object]:
            return self.payload

    class Session:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def respond(
            self,
            _prompt: str,
            *,
            json_schema: dict[str, object],
            options: object,
        ) -> GuidedResponse:
            del options
            if json_schema["title"] == "VerbatimPunctuation":
                return GuidedResponse({"corrected_transcript": "hello altered"})
            return GuidedResponse(
                {
                    "edits": [
                        {
                            "word_index": 1,
                            "trailing_punctuation": ".",
                            "capitalize": False,
                        }
                    ]
                }
            )

    fake_sdk = SimpleNamespace(
        SystemLanguageModel=AvailableModel,
        SamplingMode=SamplingMode,
        GenerationOptions=GenerationOptions,
        LanguageModelSession=Session,
    )
    monkeypatch.setattr(cleanup_module.importlib, "import_module", lambda _name: fake_sdk)
    diagnostics: dict[str, int] = {}

    cleaned = request_apple_foundation_cleanup(
        scope="punctuation",
        text="hello world",
        diagnostics=diagnostics,
    )

    assert cleaned == "hello world."
    assert diagnostics == {"structured_edit_lexical_recovery_count": 1}


def test_apple_foundation_safety_guardrail_uses_wordless_structured_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SafetyGuardrailError(Exception):
        pass

    class AvailableModel:
        def is_available(self) -> tuple[bool, None]:
            return True, None

    class SamplingMode:
        @staticmethod
        def greedy() -> str:
            return "greedy"

    class GenerationOptions:
        def __init__(self, **_kwargs: object) -> None:
            pass

    class GuidedResponse:
        @staticmethod
        def value() -> dict[str, object]:
            return {
                "edits": [
                    {
                        "word_index": 1,
                        "trailing_punctuation": ".",
                        "capitalize": False,
                    }
                ]
            }

    class Session:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def respond(
            self,
            _prompt: str,
            *,
            json_schema: dict[str, object],
            options: object,
        ) -> GuidedResponse:
            del options
            if json_schema["title"] == "VerbatimPunctuation":
                raise SafetyGuardrailError("local safety guardrail")
            return GuidedResponse()

    fake_sdk = SimpleNamespace(
        SystemLanguageModel=AvailableModel,
        SamplingMode=SamplingMode,
        GenerationOptions=GenerationOptions,
        LanguageModelSession=Session,
        GuardrailViolationError=SafetyGuardrailError,
    )
    monkeypatch.setattr(cleanup_module.importlib, "import_module", lambda _name: fake_sdk)
    diagnostics: dict[str, int] = {}

    cleaned = request_apple_foundation_cleanup(
        scope="punctuation",
        text="hello world",
        diagnostics=diagnostics,
    )

    assert cleaned == "hello world."
    assert diagnostics == {"structured_edit_safety_recovery_count": 1}


def test_apple_foundation_cleanup_preserves_timing_and_applies_safe_casing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class AvailableModel:
        def is_available(self) -> tuple[bool, None]:
            return True, None

    class SamplingMode:
        @staticmethod
        def greedy() -> str:
            return "greedy"

    class GenerationOptions:
        def __init__(self, **kwargs: object) -> None:
            captured["options"] = kwargs

    class Session:
        def __init__(self, *, instructions: str, model: object) -> None:
            captured["instructions"] = instructions
            captured["model"] = model

        async def respond(
            self,
            prompt: str,
            *,
            json_schema: object,
            options: object,
        ) -> object:
            captured["prompt"] = prompt
            captured["json_schema"] = json_schema
            captured["response_options"] = options

            class GuidedResponse:
                @staticmethod
                def value() -> dict[str, str]:
                    return {
                        "corrected_transcript": "i met natalie and michael.",
                    }

            return GuidedResponse()

    fake_sdk = SimpleNamespace(
        SystemLanguageModel=AvailableModel,
        SamplingMode=SamplingMode,
        GenerationOptions=GenerationOptions,
        LanguageModelSession=Session,
    )
    monkeypatch.setattr(cleanup_module.importlib, "import_module", lambda _name: fake_sdk)
    words = [
        AlignedWord(
            text=text,
            start=index * 0.25,
            end=(index + 1) * 0.25,
            alignment_source="forced",
        )
        for index, text in enumerate("i met natalie and michael".split())
    ]
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=1.25,
                text="i met natalie and michael",
                words=words,
            )
        ],
    )
    config = load_config(cwd=tmp_path)
    config.cleanup.mode = "apple-foundation"
    config.cleanup.required_complete = True
    config.cleanup.proper_nouns = ["Natalie", "Michael"]

    report = run_cleanup(transcript, tmp_path, config)

    cleaned = Transcript.model_validate_json(
        (tmp_path / "transcript.cleaned.json").read_text(encoding="utf-8")
    )
    assert report["accepted"] is True
    assert report["request"]["backend"] == "apple-foundation"  # type: ignore[index]
    assert report["request"]["locality"] == "on-device-system-model"  # type: ignore[index]
    assert report["request"]["proper_noun_count"] == 2  # type: ignore[index]
    assert report["request"]["response_contract"] == (  # type: ignore[index]
        "guided-verbatim-punctuation.v1"
    )
    assert report["request"]["window_size_words"] == 80  # type: ignore[index]
    # The model's terminal mark is not accepted without independent boundary
    # evidence; safe casing remains independently applicable.
    assert cleaned.speaker_turns[0].text == "I met Natalie and Michael"
    assert [
        (word.start, word.end, word.alignment_source) for word in cleaned.speaker_turns[0].words
    ] == [(word.start, word.end, word.alignment_source) for word in words]
    assert "Natalie" in str(captured["instructions"])
    assert "Michael" in str(captured["instructions"])
    assert "corrected_transcript" in str(captured["json_schema"])
    assert captured["options"] == {
        "sampling": "greedy",
        "temperature": 0.0,
        "maximum_response_tokens": 1024,
    }


def test_surface_quality_rejects_fragmentation_and_conserves_names_and_timing() -> None:
    source_words = [
        AlignedWord(
            text=text,
            start=start,
            end=end,
            speaker="SPEAKER_00",
            alignment_source="forced",
            assignment_source="exclusive_diarization",
        )
        for text, start, end in [
            ("i", 0.0, 0.2),
            ("think", 0.2, 0.4),
            ("we", 0.4, 0.6),
            ("did", 0.6, 0.8),
            ("kind", 0.8, 1.0),
            ("of", 1.0, 1.2),
            ("talk", 1.2, 1.4),
            ("with", 1.4, 1.6),
            ("natalie", 1.6, 1.8),
            ("michael", 1.8, 2.0),
            ("and", 2.0, 2.2),
            ("potentially", 2.2, 2.4),
            ("billy", 2.4, 2.6),
            ("i", 2.6, 2.8),
            ("remember", 2.8, 3.0),
        ]
    ]
    transcript = Transcript(
        input_path="x.wav",
        language="en",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=3.0,
                text=" ".join(word.text for word in source_words),
                words=source_words,
            )
        ],
    )
    candidate = (
        "I think we did Kind. Of. Talk. With Natalie Michael And. Potentially Billy I remember."
    ).split()

    repaired, quality = cleanup_module._conservative_punctuation_surfaces(
        transcript,
        [word.text for word in source_words],
        candidate,
    )
    repaired, list_count = cleanup_module._apply_proper_noun_list_punctuation(
        repaired,
        ["Natalie", "Michael", "Billy"],
    )
    repaired = cleanup_module._apply_safe_capitalization(transcript, repaired)
    repaired = cleanup_module._apply_proper_noun_casing(
        repaired,
        ["Natalie", "Michael", "Billy"],
    )
    cleaned = project_punctuation_cleanup(transcript, " ".join(repaired))

    assert cleaned.speaker_turns[0].text == (
        "I think we did kind of talk with Natalie, Michael, and potentially Billy. I remember."
    )
    assert quality["rejected_model_punctuation_addition_count"] == 4
    assert quality["suspicious_fragment_addition_count"] == 0
    assert list_count == 3
    assert [
        (word.start, word.end, word.alignment_source, word.assignment_source)
        for word in cleaned.speaker_turns[0].words
    ] == [
        (word.start, word.end, word.alignment_source, word.assignment_source)
        for word in source_words
    ]


def test_surface_quality_rejects_contraction_breaks_and_same_speaker_turn_stops() -> None:
    first_text = "and the answer is just"
    second_text = "doing them in passes it'll do and we'll have enough time"
    cursor = 0.0
    turns: list[SpeakerTurn] = []
    for text in (first_text, second_text):
        words: list[AlignedWord] = []
        for token in text.split():
            words.append(
                AlignedWord(
                    text=token,
                    start=cursor,
                    end=cursor + 0.15,
                    speaker="SPEAKER_00",
                    alignment_source="forced",
                )
            )
            cursor += 0.2
        turns.append(
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=words[0].start or 0.0,
                end=words[-1].end or cursor,
                text=text,
                words=words,
            )
        )
    transcript = Transcript(input_path="x.wav", language="en", speaker_turns=turns)
    source = f"{first_text} {second_text}".split()
    candidate = (
        "And the answer is just. Doing them in passes it'll. do and we'll, have enough time."
    ).split()

    repaired, quality = cleanup_module._conservative_punctuation_surfaces(
        transcript,
        source,
        candidate,
    )
    repaired = cleanup_module._apply_safe_capitalization(transcript, repaired)

    assert " ".join(repaired) == (
        "And the answer is just doing them in passes it'll do and we'll have enough time."
    )
    assert quality["rejected_model_punctuation_addition_count"] == 3
    assert quality["same_speaker_short_break_addition_count"] == 0
    assert quality["suspicious_fragment_addition_count"] == 0


def test_surface_quality_adds_question_mark_at_speaker_response_boundary() -> None:
    source_texts = ("how's it going man,", "pretty good thanks")
    turns: list[SpeakerTurn] = []
    cursor = 0.0
    for speaker, text in zip(("SPEAKER_00", "SPEAKER_01"), source_texts, strict=True):
        words: list[AlignedWord] = []
        for token in text.split():
            words.append(
                AlignedWord(
                    text=token,
                    start=cursor,
                    end=cursor + 0.15,
                    speaker=speaker,
                    alignment_source="forced",
                )
            )
            cursor += 0.2
        turns.append(
            SpeakerTurn(
                speaker=speaker,
                start=words[0].start or 0.0,
                end=words[-1].end or cursor,
                text=text,
                words=words,
            )
        )
    transcript = Transcript(input_path="x.wav", language="en", speaker_turns=turns)
    source = " ".join(source_texts).split()

    repaired, quality = cleanup_module._conservative_punctuation_surfaces(
        transcript,
        source,
        source,
    )
    repaired = cleanup_module._apply_safe_capitalization(transcript, repaired)

    assert " ".join(repaired) == "How's it going man? Pretty good thanks."
    assert quality["source_punctuation_repaired_count"] == 1
    assert quality["question_leading_turn_without_question_mark_count"] == 0
    assert quality["same_speaker_short_break_addition_count"] == 0


def test_production_decoder_types_direct_contracted_question_from_response_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _timed_turn("how's it going man", start=0.0, speaker="SPEAKER_00"),
        _timed_turn("pretty good thanks", start=2.1, speaker="SPEAKER_01"),
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    source = "how's it going man pretty good thanks"
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "how's it going man. pretty good thanks.",
    )

    decoder_words = cleanup_module._build_presentation_decoder_words(
        transcript,
        source.split(),
    )
    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    typed_sites = [word for word in decoder_words if word.question_evidence >= 0.8]
    assert [(word.word_index, word.question_scope_start_index) for word in typed_sites] == [(3, 0)]
    assert result.text.startswith("How's it going man?")
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "typed_question_scope_without_question_mark_count"
        ]
        == 0
    )


def test_typed_reviewed_question_is_not_reclassified_as_a_suspicious_fragment() -> None:
    source = "you can hear me".split()
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn(" ".join(source), speaker="SPEAKER_00")],
    )
    words = tuple(
        DecoderWord.from_text(
            index,
            token,
            speaker_id="SPEAKER_00",
            container_index=0,
            begins_turn=index == 0,
            ends_turn=index == len(source) - 1,
            question_evidence=0.9999 if index == len(source) - 1 else 0.0,
            question_scope_start_index=0 if index == len(source) - 1 else None,
        )
        for index, token in enumerate(source)
    )
    order = tuple(presentation_module.TrailingPunctuation)
    groups = tuple(
        presentation_module.IndexedEditorialAdjudicationEvidence(
            word_index=index,
            punctuation_probabilities=tuple(
                (
                    mark,
                    0.9999
                    if (index == len(source) - 1 and mark.value == "?")
                    or (index < len(source) - 1 and mark.value == "")
                    else 0.0001 / 7,
                )
                for mark in order
            ),
            sentence_break_probability=0.9999 if index == len(source) - 1 else 0.0001,
        )
        for index in range(len(source))
    )
    output = presentation_module.build_indexed_editorial_adjudication_scorer_output(
        words,
        ScorerProvenance("auralign.editorial-adjudication.test", "1.0"),
        groups,
    )
    plan = presentation_module.compose_retained_presentation(
        words,
        (
            presentation_module.RetainedScorerEvidence(
                output=output,
                presentation_input_digest=presentation_module.presentation_input_digest(words),
                output_sha256=presentation_module.scorer_output_sha256(output),
                receipt_sha256="a" * 64,
                disposition=presentation_module.RetainedEvidenceDisposition.ADMIT,
            ),
        ),
    )
    surfaces = apply_presentation_plan(words, plan)

    quality = cleanup_module._decoded_surface_quality(
        transcript,
        source,
        surfaces,
        plan,
    )

    assert surfaces[-1].endswith("?")
    assert quality["suspicious_fragment_addition_count"] == 0
    assert quality["typed_question_scope_without_question_mark_count"] == 0


def test_contracted_pseudo_cleft_remains_declarative_in_production_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "what's important is this works"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(source)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "what's important is this works?",
    )

    decoder_words = cleanup_module._build_presentation_decoder_words(
        transcript,
        source.split(),
    )
    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert all(word.question_evidence == 0.0 for word in decoder_words)
    assert "?" not in result.text


@pytest.mark.parametrize(
    "source",
    (
        "can opener remains here",
        "will document remains here",
        "are records stored here",
    ),
)
def test_ambiguous_noun_modal_or_copular_declarative_abstains_from_question(
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(source)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: f"{text}?",
    )

    decoder_words = cleanup_module._build_presentation_decoder_words(
        transcript,
        source.split(),
    )
    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert all(word.question_evidence == 0.0 for word in decoder_words)
    assert "?" not in result.text


@pytest.mark.parametrize("source", ("can you remain here", "are the records stored here"))
def test_auxiliary_inversion_with_typed_subject_remains_a_question(
    source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(source)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: f"{text}?",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text.endswith("?")


def test_production_decoder_rejects_abandoned_pronoun_fragment_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "before I So I continued afterward"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(source)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "before I. So I continued afterward.",
    )

    decoder_words = cleanup_module._build_presentation_decoder_words(
        transcript,
        source.split(),
    )
    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert decoder_words[1].incomplete_abandoned_fragment is True
    assert "I." not in result.text
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "incomplete_abandoned_fragment_stop_violation_count"
        ]
        == 0
    )


def test_surface_quality_detects_forged_abandoned_fragment_stop() -> None:
    source = "before I So I continued afterward".split()
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(" ".join(source))])
    contexts, turn_end_indexes = cleanup_module._cleanup_token_contexts(transcript)
    repaired = list(source)
    repaired[1] += "."

    quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        source,
        repaired,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )

    assert quality["incomplete_abandoned_fragment_stop_violation_count"] == 1


@pytest.mark.parametrize(
    ("question_marks", "expected_violation"),
    (((), 1), ((3,), 0), ((0, 3), 1)),
)
def test_surface_quality_requires_exactly_one_mark_at_typed_question_endpoint(
    question_marks: tuple[int, ...],
    expected_violation: int,
) -> None:
    turns = [
        _timed_turn("how's it going man", start=0.0, speaker="SPEAKER_00"),
        _timed_turn("pretty good thanks", start=2.1, speaker="SPEAKER_01"),
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    source = "how's it going man pretty good thanks".split()
    repaired = [
        f"{token}?" if index in question_marks else token for index, token in enumerate(source)
    ]
    contexts, turn_end_indexes = cleanup_module._cleanup_token_contexts(transcript)

    quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        source,
        repaired,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )

    assert quality["typed_question_scope_without_question_mark_count"] == expected_violation


def test_surface_quality_repairs_source_stop_inside_contracted_auxiliary() -> None:
    text = "i think it'll. work tomorrow"
    words = [
        AlignedWord(
            text=token,
            start=index * 0.2,
            end=index * 0.2 + 0.15,
            speaker="SPEAKER_00",
            alignment_source="forced",
        )
        for index, token in enumerate(text.split())
    ]
    transcript = Transcript(
        input_path="x.wav",
        language="en",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=words[-1].end or 1.0,
                text=text,
                words=words,
            )
        ],
    )

    repaired, quality = cleanup_module._conservative_punctuation_surfaces(
        transcript,
        text.split(),
        text.split(),
    )
    repaired = cleanup_module._apply_safe_capitalization(transcript, repaired)

    assert " ".join(repaired) == "I think it'll work tomorrow."
    assert quality["source_punctuation_repaired_count"] == 1
    assert quality["suspicious_fragment_addition_count"] == 0


def _timed_turn(
    text: str,
    *,
    start: float = 0.0,
    speaker: str = "SPEAKER_00",
) -> SpeakerTurn:
    words = [
        AlignedWord(
            text=token,
            start=start + (index * 0.2),
            end=start + (index * 0.2) + 0.15,
            speaker=speaker,
            alignment_source="forced",
        )
        for index, token in enumerate(text.split())
    ]
    return SpeakerTurn(
        speaker=speaker,
        start=words[0].start or start,
        end=words[-1].end or start,
        text=text,
        words=words,
    )


def test_surface_quality_detects_cross_turn_long_sentence_despite_weak_punctuation() -> None:
    raw_tokens = [f"token{index}" for index in range(120)]
    turns = [
        _timed_turn(
            " ".join(raw_tokens[start : start + 40]),
            start=float(start),
        )
        for start in range(0, 120, 40)
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    repaired = list(raw_tokens)
    for index in (19, 39, 59, 79, 99):
        repaired[index] += ","
    repaired[-1] += "."
    contexts, turn_end_indexes = cleanup_module._cleanup_token_contexts(transcript)

    quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        raw_tokens,
        repaired,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )

    assert quality["long_unpunctuated_run_count"] == 0
    assert quality["long_sentence_span_count"] == 1
    assert quality["overlong_sentence_span_count"] == 1
    assert quality["max_sentence_span_words"] == 120


def test_surface_quality_binds_distinct_overlong_span_placements() -> None:
    raw_tokens = [f"token{index}" for index in range(250)]
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn(" ".join(raw_tokens))],
    )
    contexts, turn_end_indexes = cleanup_module._cleanup_token_contexts(transcript)

    first = list(raw_tokens)
    second = list(raw_tokens)
    for index in (119, 239):
        first[index] += "."
    for index in (109, 229):
        second[index] += "."
    first_quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        raw_tokens,
        first,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )
    second_quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        raw_tokens,
        second,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )

    assert first_quality["overlong_sentence_span_count"] == 2
    assert second_quality["overlong_sentence_span_count"] == 2
    assert first_quality["max_sentence_span_words"] == 120
    assert second_quality["max_sentence_span_words"] == 120
    assert (
        first_quality["overlong_sentence_span_receipt_sha256"]
        != second_quality["overlong_sentence_span_receipt_sha256"]
    )


def test_surface_quality_records_explicit_incomplete_trailing_fragment() -> None:
    source = "we can finish because".split()
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn(" ".join(source))],
    )
    contexts, turn_end_indexes = cleanup_module._cleanup_token_contexts(transcript)

    decoder_words = cleanup_module._build_presentation_decoder_words(transcript, source)
    quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        source,
        source,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )

    assert decoder_words[-1].incomplete_trailing_fragment is True
    assert quality["incomplete_trailing_fragment_count"] == 1
    assert "incomplete_trailing_fragment_preserved" in quality["quality_warnings"]


def test_required_decoder_retains_228_word_cross_turn_run_as_review_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_tokens = [f"token{index}" for index in range(228)]
    turns = [
        _timed_turn(
            " ".join(raw_tokens[start : start + 57]),
            start=(start // 57) * 11.4,
        )
        for start in range(0, 228, 57)
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=" ".join(raw_tokens),
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    quality = result.report["surface_quality"]
    assert quality["overlong_sentence_span_count"] == 1
    assert quality["unresolved_overlong_sentence_span_count"] == 1
    assert "unresolved_overlong_sentence_spans_need_editorial_review" in quality["quality_warnings"]


def test_required_production_decoder_allows_soft_overlong_span_to_supported_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = [f"word{index}" for index in range(80)]
    tokens[-1] += "."
    text = " ".join(tokens)
    candidate_tokens = list(tokens)
    candidate_tokens[31] += ","
    candidate_tokens[63] += ","
    candidate = " ".join(candidate_tokens)
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn(text)],
    )
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: candidate,
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.plan is not None
    assert result.plan.decisions[-1].trailing_punctuation.value == "."
    assert result.plan.decisions[-1].sentence_break is True
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "long_sentence_span_count"
        ]
        == 1
    )
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "overlong_sentence_span_count"
        ]
        == 0
    )
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "max_sentence_span_words"
        ]
        == 80
    )


def test_surface_quality_detects_same_speaker_added_short_stop_burst() -> None:
    raw_tokens = [f"token{index}" for index in range(10)]
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn(" ".join(raw_tokens))],
    )
    repaired = [f"{token}." for token in raw_tokens]
    contexts, turn_end_indexes = cleanup_module._cleanup_token_contexts(transcript)

    quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        raw_tokens,
        repaired,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )

    assert quality["added_short_strong_stop_count"] == 10
    assert quality["added_short_strong_stop_burst_count"] == 1
    assert quality["max_added_short_strong_stop_streak"] == 10
    assert quality["added_strong_stop_burst_count"] == 1
    assert quality["max_added_strong_stop_cluster"] == 10


def test_surface_quality_detects_added_question_after_incomplete_contraction() -> None:
    source = "although the result looks good i think it's,".split()
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn(" ".join(source))],
    )
    repaired = [*source[:-1], "it's?"]
    contexts, turn_end_indexes = cleanup_module._cleanup_token_contexts(transcript)

    quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        source,
        repaired,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )

    assert quality["contraction_punctuation_violation_count"] == 1


def test_surface_quality_accepts_source_authored_negative_contraction_reply() -> None:
    source = ["Can't."]
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn(" ".join(source))],
    )
    contexts, turn_end_indexes = cleanup_module._cleanup_token_contexts(transcript)

    quality = cleanup_module._surface_quality_diagnostics(
        transcript,
        source,
        source,
        contexts=contexts,
        turn_end_indexes=turn_end_indexes,
    )

    assert quality["contraction_punctuation_violation_count"] == 0


def test_production_decoder_rejects_close_contracted_auxiliary_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "i think it'll work tomorrow"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "i think it'll. work tomorrow.",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "I think it'll work tomorrow"
    assert result.report["hard_constraint_application_count"] == 1
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "contraction_punctuation_violation_count"
        ]
        == 0
    )


def test_complete_repeated_negative_contraction_is_not_a_false_blocker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "he wasn't. he wasn't because i sent it"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "he wasn't. he wasn't because i sent it.",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "He wasn't. He wasn't because I sent it"
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "contraction_punctuation_violation_count"
        ]
        == 0
    )


def test_same_speaker_short_turn_boundary_is_evidence_not_a_prohibition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _timed_turn("i know that works.", start=0.0),
        _timed_turn("i agree.", start=2.0),
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "i know that works. i agree.",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text="i know that works. i agree.",
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "I know that works. I agree."


def test_same_speaker_cross_turn_syntactic_continuation_is_constrained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _timed_turn("can you help because", start=0.0),
        _timed_turn("i need this today", start=0.9),
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "can you help because? i need this today.",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text="can you help because i need this today",
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert "because?" not in result.text
    assert result.text.endswith("today?")
    assert result.text.count("?") == 1
    assert result.report["hard_constraint_application_count"] >= 1


def test_production_question_conflict_at_incomplete_contraction_abstains_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _timed_turn("can you verify whether i think it's,", start=0.0),
        _timed_turn("no this remains pending", start=2.7),
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    source = "can you verify whether i think it's, no this remains pending"
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: (
            "can you verify whether i think it's? no, this remains pending."
        ),
    )

    decoder_words = cleanup_module._build_presentation_decoder_words(
        transcript,
        source.split(),
    )
    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    contraction = decoder_words[6]
    assert contraction.incomplete_contraction is True
    assert contraction.question_scope_start_index == 0
    assert contraction.question_evidence == 0.85
    assert "it's?" not in result.text
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "contraction_punctuation_violation_count"
        ]
        == 0
    )
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "question_leading_turn_without_question_mark_count"
        ]
        == 0
    )


def test_unsafe_source_question_migrates_over_typed_same_clause_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _timed_turn("can you help because?", start=0.0),
        _timed_turn("i need this today", start=0.9),
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    source = "can you help because? i need this today"
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: source,
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert "because?" not in result.text
    assert result.text.endswith("today?")
    assert result.text.count("?") == 1


def test_question_scope_does_not_migrate_across_untyped_turn_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    turns = [
        _timed_turn("can you help", start=0.0),
        _timed_turn("i need this today", start=0.75),
    ]
    transcript = Transcript(input_path="x.wav", speaker_turns=turns)
    source = "can you help i need this today"
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "can you help i need this today?",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert "help?" in result.text
    assert not result.text.endswith("today?")
    assert result.text.count("?") == 1


def test_incomplete_terminal_question_shaped_speech_repairs_unsafe_source_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "can you finish because?"
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn(source)],
    )
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: source,
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=source,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "Can you finish because"
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "incomplete_trailing_fragment_count"
        ]
        == 1
    )
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "question_leading_turn_without_question_mark_count"
        ]
        == 0
    )


def test_structural_constraint_can_repair_unsafe_source_punctuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "we discussed this and. continued tomorrow."
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "we discussed this and. continued tomorrow.",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "We discussed this and continued tomorrow."
    assert result.report["hard_constraint_application_count"] >= 1


def test_low_punctuation_density_alone_is_review_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokens = [f"word{index}" for index in range(38)]
    tokens[18] += ","
    tokens[-1] += "."
    text = " ".join(tokens)
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "long_low_punctuation_turn_count"
        ]
        == 1
    )


def test_two_word_question_is_supported_at_a_turn_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[_timed_turn("are you")],
    )
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "are you?",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text="are you",
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "Are you?"


@pytest.mark.parametrize(
    "text",
    (
        "what's more this remains correct",
        "what's important is preserving context",
        "what's really important is preserving context",
        "what's important for us is preserving context",
        "what's most important for every person involved in this difficult "
        "decision is preserving context",
    ),
)
def test_contracted_wh_declaratives_are_not_forced_into_questions(
    text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: f"{text}.",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert "?" not in result.text


def test_contracted_wh_interrogative_remains_a_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "how's that for a result"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "how's that for a result?",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "How's that for a result?"


def test_backend_question_without_typed_clause_scope_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "this remains a declarative observation"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "this remains a declarative observation?",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert "?" not in result.text


def test_backend_strong_mark_abstains_without_independent_boundary_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "we are ready"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "we are ready.",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "We are ready"
    assert result.plan is not None
    assert not result.plan.decisions[-1].sentence_break


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("does it work? not always", "Does it work? Not always"),
        ("stop! never do that", "Stop! Never do that"),
        ("he agreed. nor did she object", "He agreed. Nor did she object"),
        ("it works. not always", "It works. Not always"),
    ),
)
def test_source_strong_stop_before_negating_next_sentence_is_preserved(
    text: str,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == expected


def test_typed_relational_continuation_demotes_unsafe_source_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "we discussed this and. continued tomorrow."
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )

    words = cleanup_module._build_presentation_decoder_words(transcript, text.split())
    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert words[3].incomplete_continuation is True
    assert result.text == "We discussed this and continued tomorrow."


def test_production_path_blocks_positive_contraction_comma_without_typed_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "i'm ready"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "i'm, ready",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "I'm ready"


def test_production_path_preserves_typed_contraction_repetition_comma(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "i'm i'm ready"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: "i'm, i'm ready",
    )

    result = cleanup_module._request_punctuation_presentation(
        "http://127.0.0.1:11434/v1",
        model=None,
        text=text,
        mode="local-llm",
        proper_nouns=(),
        required_complete=True,
        transcript=transcript,
    )

    assert result.text == "I'm, I'm ready"
    assert (
        result.report["surface_quality"][  # type: ignore[index]
            "suspicious_fragment_addition_count"
        ]
        == 0
    )


def test_nonadjacent_repeated_contraction_does_not_create_exception() -> None:
    text = "i'm sure because i'm ready"
    transcript = Transcript(input_path="x.wav", speaker_turns=[_timed_turn(text)])

    words = cleanup_module._build_presentation_decoder_words(transcript, text.split())

    assert words[0].contraction_repetition_exception is False


def test_overlapping_backend_windows_are_one_reconciled_vote() -> None:
    words = tuple(
        DecoderWord.from_text(index, token)
        for index, token in enumerate(("one", "two", "three", "four", "five"))
    )
    window = cleanup_module._PunctuationWindowEvidence(
        window_index=0,
        start_word_index=0,
        end_word_index=5,
        source_tokens=("one", "two", "three", "four", "five"),
        candidate_tokens=("one", "two", "three.", "four", "five"),
        response_path="guided_verbatim",
    )
    duplicate = cleanup_module._PunctuationWindowEvidence(
        window_index=1,
        start_word_index=0,
        end_word_index=5,
        source_tokens=window.source_tokens,
        candidate_tokens=window.candidate_tokens,
        response_path="guided_verbatim",
    )

    once = cleanup_module._aggregate_window_presentation_scorer(
        (window,), words, mode="local-llm", model=None, window_size=5
    )
    twice = cleanup_module._aggregate_window_presentation_scorer(
        (window, duplicate), words, mode="local-llm", model=None, window_size=5
    )
    once_plan = decode_presentation(words, (once,))
    twice_plan = decode_presentation(words, (twice,))

    assert len(once.proposals) == len(twice.proposals) == 8 * len(words)
    assert apply_presentation_plan(words, once_plan) == apply_presentation_plan(
        words,
        twice_plan,
    )
    assert once_plan.total_score == pytest.approx(twice_plan.total_score)


def test_fallback_only_words_have_neutral_scores_and_no_backend_provenance() -> None:
    words = tuple(
        DecoderWord.from_text(index, token)
        for index, token in enumerate(("one", "two", "three", "four"))
    )
    accepted = cleanup_module._PunctuationWindowEvidence(
        window_index=0,
        start_word_index=0,
        end_word_index=2,
        source_tokens=("one", "two"),
        candidate_tokens=("one", "two."),
        response_path="guided_verbatim",
    )
    fallback = cleanup_module._PunctuationWindowEvidence(
        window_index=1,
        start_word_index=2,
        end_word_index=4,
        source_tokens=("three", "four"),
        candidate_tokens=("three", "four"),
        response_path="source_fallback",
    )
    output = cleanup_module._aggregate_window_presentation_scorer(
        (accepted, fallback),
        words,
        mode="local-llm",
        model=None,
        window_size=2,
    )

    plan = decode_presentation(words, (output,))

    assert len(output.proposals) == 8 * len(words)
    for decision in plan.decisions[2:]:
        assert output.provenance not in decision.contributing_provenance


def test_conflicting_overlap_is_averaged_once_and_reported() -> None:
    words = tuple(
        DecoderWord.from_text(index, token) for index, token in enumerate(("one", "two", "three"))
    )
    period = cleanup_module._PunctuationWindowEvidence(
        window_index=0,
        start_word_index=0,
        end_word_index=3,
        source_tokens=("one", "two", "three"),
        candidate_tokens=("one", "two.", "three"),
        response_path="guided_verbatim",
    )
    none = cleanup_module._PunctuationWindowEvidence(
        window_index=1,
        start_word_index=0,
        end_word_index=3,
        source_tokens=period.source_tokens,
        candidate_tokens=("one", "two", "three"),
        response_path="guided_verbatim",
    )

    output = cleanup_module._aggregate_window_presentation_scorer(
        (period, none),
        words,
        mode="local-llm",
        model=None,
        window_size=3,
    )
    middle = [proposal for proposal in output.proposals if proposal.word_index == 1]
    period_probability = next(
        proposal.punctuation_probability
        for proposal in middle
        if proposal.trailing_punctuation.value == "."
    )

    assert 0.125 < period_probability < 0.94
    assert cleanup_module._overlap_disagreement_count((period, none)) == 1


def test_required_cleanup_blocks_missing_question_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cleanup_module,
        "request_local_cleanup",
        lambda endpoint, model, scope, text: text,
    )
    monkeypatch.setattr(
        cleanup_module,
        "_decoded_surface_quality",
        lambda *args, **kwargs: {
            "schema_version": "global_presentation_quality.v7",
            "lexical_sequence_preserved": True,
            "suspicious_fragment_addition_count": 0,
            "contraction_punctuation_violation_count": 0,
            "long_unpunctuated_run_count": 0,
            "locally_adverse_added_strong_stop_count": 0,
            "unsupported_added_strong_stop_count": 0,
            "incomplete_abandoned_fragment_stop_violation_count": 0,
            "lowercase_after_strong_stop_count": 0,
            "long_low_punctuation_turn_count": 0,
            "question_leading_turn_without_question_mark_count": 1,
            "typed_question_scope_without_question_mark_count": 1,
        },
    )

    with pytest.raises(
        CleanupRejectedError,
        match="typed_question_scope_without_question_mark_count",
    ):
        request_punctuation_cleanup(
            "http://127.0.0.1:11434/v1",
            model=None,
            text="one two three four",
            required_complete=True,
        )


def test_declarative_wh_clause_is_not_forced_into_a_question() -> None:
    text = "what i mean is this works"
    words = [
        AlignedWord(
            text=token,
            start=index * 0.2,
            end=index * 0.2 + 0.15,
            speaker="SPEAKER_00",
            alignment_source="forced",
        )
        for index, token in enumerate(text.split())
    ]
    transcript = Transcript(
        input_path="x.wav",
        language="en",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=words[-1].end or 1.0,
                text=text,
                words=words,
            )
        ],
    )

    repaired, quality = cleanup_module._conservative_punctuation_surfaces(
        transcript,
        text.split(),
        "What i mean is this works?".split(),
    )
    repaired = cleanup_module._apply_safe_capitalization(transcript, repaired)

    assert " ".join(repaired) == "What I mean is this works."
    assert quality["question_leading_turn_without_question_mark_count"] == 0


def test_required_complete_cleanup_rejects_any_window_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def lexical_drift(
        endpoint: str,
        *,
        model: str | None,
        scope: str,
        text: str,
    ) -> str:
        del endpoint, model, scope
        tokens = text.split()
        tokens[0] = "different"
        return " ".join(tokens)

    monkeypatch.setattr(cleanup_module, "request_local_cleanup", lexical_drift)

    with pytest.raises(CleanupRejectedError, match="lexical sequence"):
        request_punctuation_cleanup(
            "http://127.0.0.1:11434/v1",
            model=None,
            text="one two three",
            required_complete=True,
        )
