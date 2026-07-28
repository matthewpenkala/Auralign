# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

import local_transcript_max.cleanup as cleanup_module
from local_transcript_max.cleanup import (
    CleanupRejectedError,
    PunctuationEdit,
    cleanup_is_safe,
    project_punctuation_cleanup,
    project_punctuation_edits,
    request_punctuation_cleanup,
    run_cleanup,
    validate_cleanup_endpoint,
)
from local_transcript_max.config import load_config
from local_transcript_max.export.srt_export import render_srt
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


@pytest.mark.parametrize(
    ("original", "cleaned"),
    [
        ("can't", "cant"),
        ("co-op", "coop"),
        ("C++", "C"),
        ("3.14", "314"),
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
    assert [
        (word.start, word.end, word.alignment_source) for word in repaired_turn.words
    ] == [
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
            word.text
            for turn in repaired.speaker_turns
            for word in turn.words
            if word.text.strip()
        ],
    )
    assert [line for line in base_srt.splitlines() if " --> " in line] == [
        line for line in overlaid_srt.splitlines() if " --> " in line
    ]
    assert "Good, pretty good. It was, I guess." in " ".join(
        overlaid_srt.splitlines()
    )
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
        (word.start, word.end, word.alignment_source)
        for word in repaired.speaker_turns[0].words
    ] == [
        (word.start, word.end, word.alignment_source)
        for word in words
    ]
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


def test_punctuation_windows_cover_each_word_once_and_fallback_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_tokens = [f"word{index}" for index in range(500)]
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

    assert cleaned.split() == source_tokens
    assert report["window_count"] == 3
    assert report["accepted_window_count"] == 2
    assert report["fallback_window_count"] == 1
    assert report["fallback_reasons"] == {"lexical_validation_failed": 1}


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
    assert (tmp_path / "cleanup_diff.json").exists()
    assert (tmp_path / "transcript.cleaned.json").exists()
    assert (tmp_path / "transcript.cleaned.srt").exists()
    assert (tmp_path / "transcript.cleaned.vtt").exists()
    assert not (tmp_path / "transcript.raw.json").exists()
