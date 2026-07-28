# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_transcript_max.alignment.forced_alignment import (
    WhisperXForcedAlignment,
    _assert_alignment_model_inference_ready,
    _configure_wav2vec2_for_inference,
    _load_inference_alignment_model,
    _words_from_whisperx,
    preflight_forced_alignment,
)
from local_transcript_max.config import load_config
from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.pipeline import align_words
from local_transcript_max.schemas import AlignedWord, ASRResult, ASRSegment


class MissingForcedAlignment:
    name = "missing-forced"

    def align(self, audio_path: Path, asr_result: ASRResult, config):
        raise BackendUnavailableError("mock forced alignment unavailable")


class SuccessfulForcedAlignment:
    name = "mock-forced"

    def __init__(self) -> None:
        self.last_device: str | None = None

    def align(self, audio_path: Path, asr_result: ASRResult, config):
        self.last_device = config.alignment.device
        return [AlignedWord(text="hello", start=0.0, end=1.0, alignment_source="forced")]


class StaticForcedAlignment:
    name = "static-forced"

    def __init__(self, words: list[AlignedWord]) -> None:
        self.words = words

    def align(self, audio_path: Path, asr_result: ASRResult, config):
        return self.words


class LocalizedFallbackAlignment(StaticForcedAlignment):
    name = "mock-hybrid"

    def __init__(self, words: list[AlignedWord]) -> None:
        super().__init__(words)
        self.last_localized_fallback_segments = [
            {
                "segment_index": 1,
                "reason": "missing_timestamp",
                "fallback_word_count": 1,
            }
        ]
        self.last_localized_fallback_word_count = 1
        self.last_forced_word_count = 1
        self.last_interpolation_method = "ignore"


def test_auto_alignment_falls_back_to_estimated_words(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "auto"
    asr = ASRResult(segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello world")])
    outcome = align_words(tmp_path / "audio.wav", asr, config, MissingForcedAlignment())
    assert outcome.backend == "whisper-word-timestamps"
    assert outcome.warnings == ["forced_alignment_backend_unavailable"]
    assert outcome.metadata is not None
    assert outcome.metadata["fallback_reason"] == "forced_alignment_backend_unavailable"
    assert [word.alignment_source for word in outcome.words] == ["estimated", "estimated"]


def test_forced_alignment_records_explicit_mps_device(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    config.alignment.device = "mps"
    asr = ASRResult(
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=1.0,
                text="hello",
                words=[],
            )
        ]
    )

    outcome = align_words(tmp_path / "audio.wav", asr, config, SuccessfulForcedAlignment())

    assert outcome.backend == "mock-forced"
    assert outcome.device == "mps"


def test_forced_alignment_mode_does_not_fake_success(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    asr = ASRResult(segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")])
    with pytest.raises(BackendUnavailableError, match="backend unavailable"):
        align_words(tmp_path / "audio.wav", asr, config, MissingForcedAlignment())


def test_alignment_backend_exception_text_is_not_published(tmp_path: Path) -> None:
    class PrivateFailure:
        def align(self, audio_path: Path, asr_result: ASRResult, config):
            raise BackendUnavailableError(
                "upstream failed while aligning private phrase dad medical detail"
            )

    config = load_config(cwd=tmp_path)
    config.alignment.backend = "auto"
    asr = ASRResult(
        segments=[ASRSegment(id=0, start=0.0, end=1.0, text="private phrase")]
    )

    outcome = align_words(tmp_path / "audio.wav", asr, config, PrivateFailure())

    assert outcome.warnings == ["forced_alignment_backend_unavailable"]
    assert outcome.metadata is not None
    assert outcome.metadata["fallback_reason"] == "forced_alignment_backend_unavailable"
    assert "dad medical detail" not in str(outcome)


@pytest.mark.parametrize(
    "words",
    [
        [],
        [AlignedWord(text="missing", start=None, end=None, alignment_source="missing")],
        [AlignedWord(text="nan", start=float("nan"), end=1.0, alignment_source="forced")],
        [AlignedWord(text="negative", start=-0.1, end=1.0, alignment_source="forced")],
        [AlignedWord(text="reverse", start=1.0, end=0.5, alignment_source="forced")],
        [AlignedWord(text="zero", start=0.5, end=0.5, alignment_source="forced")],
        [
            AlignedWord(text="later", start=1.0, end=2.0, alignment_source="forced"),
            AlignedWord(text="earlier", start=0.5, end=2.1, alignment_source="forced"),
        ],
    ],
)
def test_strict_forced_alignment_rejects_incomplete_or_invalid_words(
    tmp_path: Path, words: list[AlignedWord]
) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    asr = ASRResult(segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")])

    with pytest.raises(BackendUnavailableError, match="backend unavailable"):
        align_words(tmp_path / "audio.wav", asr, config, StaticForcedAlignment(words))


def test_auto_alignment_falls_back_when_forced_output_is_partial(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "auto"
    asr = ASRResult(segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")])
    partial = [AlignedWord(text="hello", start=None, end=None, alignment_source="missing")]

    outcome = align_words(tmp_path / "audio.wav", asr, config, StaticForcedAlignment(partial))

    assert outcome.backend == "whisper-word-timestamps"
    assert outcome.warnings == ["forced_alignment_backend_unavailable"]


def test_auto_alignment_accepts_truthful_localized_fallback(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "auto"
    asr = ASRResult(
        language="en",
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=2.0,
                text="hello fallback",
                words=[
                    AlignedWord(
                        text="hello",
                        start=0.0,
                        end=0.8,
                        alignment_source="whisper",
                    ),
                    AlignedWord(
                        text="fallback",
                        start=1.0,
                        end=1.8,
                        alignment_source="whisper",
                    ),
                ],
            )
        ],
    )
    hybrid = [
        AlignedWord(text="hello", start=0.0, end=0.8, alignment_source="forced"),
        AlignedWord(text="fallback", start=1.0, end=1.8, alignment_source="whisper"),
    ]

    outcome = align_words(
        tmp_path / "audio.wav",
        asr,
        config,
        LocalizedFallbackAlignment(hybrid),
    )

    assert outcome.backend == "mock-hybrid"
    assert [word.alignment_source for word in outcome.words] == ["forced", "whisper"]
    assert outcome.metadata is not None
    assert outcome.metadata["localized_fallback_word_count"] == 1
    assert outcome.metadata["interpolation_method"] == "ignore"
    assert outcome.warnings == [
        "WhisperX forced alignment used localized ASR word-timestamp fallback "
        "for 1 segment(s)."
    ]


def test_strict_forced_alignment_rejects_localized_fallback(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    asr = ASRResult(
        language="en",
        segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")],
    )
    hybrid = [
        AlignedWord(text="hello", start=0.0, end=1.0, alignment_source="whisper")
    ]

    with pytest.raises(BackendUnavailableError, match="backend unavailable"):
        align_words(
            tmp_path / "audio.wav",
            asr,
            config,
            LocalizedFallbackAlignment(hybrid),
        )


def test_strict_forced_alignment_rejects_lexically_incomplete_output(
    tmp_path: Path,
) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    asr = ASRResult(
        language="en",
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=2.0,
                text="hello complete transcript",
            )
        ],
    )
    incomplete = [
        AlignedWord(text="hello", start=0.0, end=1.0, alignment_source="forced")
    ]

    with pytest.raises(BackendUnavailableError, match="backend unavailable"):
        align_words(
            tmp_path / "audio.wav",
            asr,
            config,
            StaticForcedAlignment(incomplete),
        )


def test_whisperx_localizes_one_failed_segment_without_discarding_ctc_words() -> None:
    expected = [
        ASRSegment(
            id=0,
            start=0.0,
            end=1.0,
            text="hello there",
            words=[
                AlignedWord(
                    text="hello",
                    start=0.0,
                    end=0.4,
                    alignment_source="whisper",
                ),
                AlignedWord(
                    text="there",
                    start=0.5,
                    end=0.9,
                    alignment_source="whisper",
                ),
            ],
        ),
        ASRSegment(
            id=1,
            start=1.0,
            end=2.0,
            text="short failure",
            words=[
                AlignedWord(
                    text="short",
                    start=1.0,
                    end=1.4,
                    alignment_source="whisper",
                ),
                AlignedWord(
                    text="failure",
                    start=1.5,
                    end=1.9,
                    alignment_source="whisper",
                ),
            ],
        ),
    ]
    aligned_by_source = [
        [
            {
                "text": "hello there",
                "words": [
                    {"word": "hello", "start": 0.0, "end": 0.4, "score": 0.9},
                    {"word": "there", "start": 0.5, "end": 0.9, "score": 0.8},
                ],
            }
        ],
        [
            {
                "text": "short failure",
                "words": [
                    {"word": "short", "start": None, "end": None, "score": 0.1},
                    {"word": "failure", "start": 1.5, "end": 1.9, "score": 0.8},
                ],
            }
        ],
    ]

    words, coverage = _words_from_whisperx(
        aligned_by_source,
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.text for word in words] == ["hello", "there", "short", "failure"]
    assert [word.alignment_source for word in words] == [
        "forced",
        "forced",
        "whisper",
        "whisper",
    ]
    assert coverage == {
        "expected_segment_count": 2,
        "aligned_segment_count": 1,
        "missing_segment_count": 0,
        "forced_word_count": 2,
        "fallback_word_count": 2,
        "fallback_segments": [
            {
                "segment_index": 1,
                "segment_id": 1,
                "reason": "missing_timestamp",
                "fallback_word_count": 2,
            }
        ],
    }
    assert "forced_alignment_localized_fallback" in words[-1].warnings
    assert "forced_alignment_fallback_missing_timestamp" in words[-1].warnings


def test_whisperx_sentence_splitting_preserves_source_segment_identity() -> None:
    expected = [
        ASRSegment(
            id=10,
            start=0.0,
            end=2.0,
            text="One. Two.",
            words=[
                AlignedWord(
                    text="One.",
                    start=0.0,
                    end=0.8,
                    alignment_source="whisper",
                ),
                AlignedWord(
                    text="Two.",
                    start=1.0,
                    end=1.8,
                    alignment_source="whisper",
                ),
            ],
        ),
        ASRSegment(
            id=11,
            start=2.0,
            end=3.0,
            text="Three.",
            words=[
                AlignedWord(
                    text="Three.",
                    start=2.1,
                    end=2.8,
                    alignment_source="whisper",
                )
            ],
        ),
    ]
    aligned_by_source = [
        [
            {
                "text": "One.",
                "words": [{"word": "One.", "start": 0.0, "end": 0.8}],
            },
            {
                "text": "Two.",
                "words": [{"word": "Two.", "start": 1.0, "end": 1.8}],
            },
        ],
        [
            {
                "text": "Three.",
                "words": [{"word": "Three.", "start": 2.1, "end": 2.8}],
            }
        ],
    ]

    words, coverage = _words_from_whisperx(
        aligned_by_source,
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.text for word in words] == ["One.", "Two.", "Three."]
    assert {word.alignment_source for word in words} == {"forced"}
    assert coverage["aligned_segment_count"] == 2
    assert coverage["fallback_word_count"] == 0
    assert coverage["fallback_segments"] == []


def test_whisperx_text_mismatch_falls_back_to_exact_asr_words() -> None:
    expected = [
        ASRSegment(
            id=5,
            start=0.0,
            end=1.0,
            text="Keep exact wording.",
            words=[
                AlignedWord(
                    text="Keep",
                    start=0.0,
                    end=0.2,
                    alignment_source="whisper",
                ),
                AlignedWord(
                    text="exact",
                    start=0.3,
                    end=0.5,
                    alignment_source="whisper",
                ),
                AlignedWord(
                    text="wording.",
                    start=0.6,
                    end=0.9,
                    alignment_source="whisper",
                ),
            ],
        )
    ]

    words, coverage = _words_from_whisperx(
        [
            [
                {
                    "text": "Keep altered wording.",
                    "words": [
                        {"word": "Keep", "start": 0.0, "end": 0.2},
                        {"word": "altered", "start": 0.3, "end": 0.5},
                        {"word": "wording.", "start": 0.6, "end": 0.9},
                    ],
                }
            ]
        ],
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.text for word in words] == ["Keep", "exact", "wording."]
    assert {word.alignment_source for word in words} == {"whisper"}
    assert coverage["fallback_segments"][0]["reason"] == "text_mismatch"


def test_whisperx_word_tokenization_mismatch_preserves_asr_word_sequence() -> None:
    expected = [
        ASRSegment(
            id=6,
            start=0.0,
            end=1.0,
            text="can not",
            words=[
                AlignedWord(
                    text="can",
                    start=0.0,
                    end=0.4,
                    alignment_source="whisper",
                ),
                AlignedWord(
                    text="not",
                    start=0.5,
                    end=0.9,
                    alignment_source="whisper",
                ),
            ],
        )
    ]

    words, coverage = _words_from_whisperx(
        [
            [
                {
                    "text": "cannot",
                    "words": [{"word": "cannot", "start": 0.0, "end": 0.9}],
                }
            ]
        ],
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.text for word in words] == ["can", "not"]
    assert {word.alignment_source for word in words} == {"whisper"}
    assert coverage["fallback_segments"][0]["reason"] == "word_sequence_mismatch"


def test_whisperx_character_alignment_projects_ctc_time_to_asr_words() -> None:
    expected = [
        ASRSegment(
            id=7,
            start=0.0,
            end=1.0,
            text="can not",
            words=[
                AlignedWord(
                    text="can",
                    start=0.0,
                    end=0.4,
                    alignment_source="whisper",
                ),
                AlignedWord(
                    text="not",
                    start=0.5,
                    end=0.9,
                    alignment_source="whisper",
                ),
            ],
        )
    ]
    aligned_characters = [
        {"char": "c", "start": 0.0, "end": 0.1, "score": 0.9},
        {"char": "a", "start": 0.1, "end": 0.2, "score": 0.8},
        {"char": "n", "start": 0.2, "end": 0.3, "score": 0.7},
        {"char": " ", "start": 0.3, "end": 0.4, "score": 0.6},
        {"char": "n", "start": 0.5, "end": 0.6, "score": 0.9},
        {"char": "o", "start": 0.6, "end": 0.7, "score": 0.8},
        {"char": "t", "start": 0.7, "end": 0.8, "score": 0.7},
    ]

    words, coverage = _words_from_whisperx(
        [
            [
                {
                    "text": "cannot",
                    "words": [{"word": "cannot", "start": 0.0, "end": 0.8}],
                    "chars": aligned_characters,
                }
            ]
        ],
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.text for word in words] == ["can", "not"]
    assert [word.alignment_source for word in words] == ["forced", "forced"]
    assert [(word.start, word.end) for word in words] == [(0.0, 0.3), (0.5, 0.8)]
    assert coverage["fallback_segments"] == []


def test_whisperx_character_projection_rejects_partial_character_timing() -> None:
    expected = [
        ASRSegment(
            id=7,
            start=0.0,
            end=1.0,
            text="ab",
            words=[
                AlignedWord(
                    text="ab",
                    start=0.0,
                    end=0.8,
                    alignment_source="whisper",
                )
            ],
        )
    ]

    words, coverage = _words_from_whisperx(
        [
            [
                {
                    "text": "ab",
                    "chars": [
                        {"char": "a", "start": 0.0, "end": 0.2},
                        {"char": "b", "start": None, "end": 0.4},
                    ],
                }
            ]
        ],
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.alignment_source for word in words] == ["whisper"]
    assert coverage["fallback_segments"][0]["reason"] == "missing_character_timestamp"


def test_whisperx_zero_duration_word_uses_localized_fallback() -> None:
    expected = [
        ASRSegment(
            id=4,
            start=0.0,
            end=1.0,
            text="zero",
            words=[
                AlignedWord(
                    text="zero",
                    start=0.1,
                    end=0.8,
                    alignment_source="whisper",
                )
            ],
        )
    ]

    words, coverage = _words_from_whisperx(
        [[{"words": [{"word": "zero", "start": 0.4, "end": 0.4}]}]],
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.alignment_source for word in words] == ["whisper"]
    assert coverage["fallback_segments"][0]["reason"] == "non_positive_duration"


def test_localized_fallback_fails_when_monotonic_repair_escapes_source_bounds() -> None:
    expected = [
        ASRSegment(
            id=0,
            start=0.0,
            end=1.0,
            text="first",
            words=[
                AlignedWord(
                    text="first",
                    start=0.0,
                    end=1.0,
                    alignment_source="whisper",
                )
            ],
        ),
        ASRSegment(
            id=1,
            start=0.8,
            end=0.9,
            text="second",
            words=[
                AlignedWord(
                    text="second",
                    start=0.8,
                    end=0.9,
                    alignment_source="whisper",
                )
            ],
        ),
    ]

    with pytest.raises(BackendUnavailableError, match="preserve source bounds"):
        _words_from_whisperx(
            [
                [{"words": [{"word": "first", "start": 0.0, "end": 1.0}]}],
                [{"words": [{"word": "second", "start": None, "end": None}]}],
            ],
            expected_segments=expected,
            allow_localized_fallback=True,
        )


def test_whisperx_cross_segment_conflict_downgrades_only_offending_segment() -> None:
    expected = [
        ASRSegment(
            id=0,
            start=0.0,
            end=1.0,
            text="first",
            words=[
                AlignedWord(
                    text="first",
                    start=0.9,
                    end=1.0,
                    alignment_source="whisper",
                )
            ],
        ),
        ASRSegment(
            id=1,
            start=0.8,
            end=2.0,
            text="second",
            words=[
                AlignedWord(
                    text="second",
                    start=0.8,
                    end=1.8,
                    alignment_source="whisper",
                )
            ],
        ),
    ]

    words, coverage = _words_from_whisperx(
        [
            [{"words": [{"word": "first", "start": 0.9, "end": 1.0}]}],
            [{"words": [{"word": "second", "start": 0.8, "end": 1.8}]}],
        ],
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.alignment_source for word in words] == ["forced", "whisper"]
    assert words[1].start == 1.0
    assert coverage["fallback_segments"] == [
        {
            "segment_index": 1,
            "segment_id": 1,
            "reason": "non_monotonic_timestamp",
            "fallback_word_count": 1,
        }
    ]


def test_whisperx_segment_without_word_timestamps_is_explicitly_estimated() -> None:
    expected = [
        ASRSegment(
            id=3,
            start=2.0,
            end=3.0,
            text="needs estimate",
            words=[],
        )
    ]

    words, coverage = _words_from_whisperx(
        [[]],
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.text for word in words] == ["needs", "estimate"]
    assert {word.alignment_source for word in words} == {"estimated"}
    assert all("estimated_from_segment_timing" in word.warnings for word in words)
    assert coverage["fallback_word_count"] == 2


def test_whisperx_segment_with_words_and_blank_text_is_not_omitted() -> None:
    expected = [
        ASRSegment(
            id=8,
            start=0.0,
            end=1.0,
            text="",
            words=[
                AlignedWord(
                    text="retained",
                    start=0.0,
                    end=0.9,
                    alignment_source="whisper",
                )
            ],
        )
    ]

    words, coverage = _words_from_whisperx(
        [[{"words": [{"word": "retained", "start": 0.0, "end": 0.9}]}]],
        expected_segments=expected,
        allow_localized_fallback=True,
    )

    assert [word.text for word in words] == ["retained"]
    assert [word.alignment_source for word in words] == ["forced"]
    assert coverage["expected_segment_count"] == 1


def test_whisperx_strict_mode_fails_at_source_segment_reason() -> None:
    expected = [
        ASRSegment(
            id=0,
            start=0.0,
            end=1.0,
            text="missing",
            words=[
                AlignedWord(
                    text="missing",
                    start=0.0,
                    end=1.0,
                    alignment_source="whisper",
                )
            ],
        )
    ]

    with pytest.raises(
        BackendUnavailableError,
        match="source segment 0: missing_timestamp",
    ):
        _words_from_whisperx(
            [
                [
                    {
                        "text": "missing",
                        "words": [
                            {"word": "missing", "start": None, "end": None}
                        ],
                    }
                ]
            ],
            expected_segments=expected,
            allow_localized_fallback=False,
        )


def test_whisperx_disables_upstream_interpolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_align(*args, **kwargs):
        captured.update(kwargs)
        return {
            "segments": [
                {
                    "text": "hello",
                    "words": [{"word": "hello", "start": 0.0, "end": 1.0}],
                }
            ]
        }

    fake_model = SimpleNamespace(
        training=False,
        config=SimpleNamespace(
            apply_spec_augment=False,
            mask_time_prob=0.0,
            mask_feature_prob=0.0,
        ),
        named_parameters=lambda: [],
    )
    fake_whisperx = SimpleNamespace(
        load_audio=lambda _path: object(),
        align=fake_align,
    )
    monkeypatch.setitem(sys.modules, "whisperx", fake_whisperx)
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment.importlib.util.find_spec",
        lambda _name: object(),
    )
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment.preflight_forced_alignment",
        lambda _config: "local-model",
    )
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment._load_inference_alignment_model",
        lambda **_kwargs: (fake_model, {}),
    )
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "auto"
    asr = ASRResult(
        language="en",
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=1.0,
                text="hello",
                words=[
                    AlignedWord(
                        text="hello",
                        start=0.0,
                        end=1.0,
                        alignment_source="whisper",
                    )
                ],
            )
        ],
    )

    words = WhisperXForcedAlignment().align(tmp_path / "audio.wav", asr, config)

    assert [word.alignment_source for word in words] == ["forced"]
    assert captured["interpolate_method"] == "ignore"
    assert captured["return_char_alignments"] is True


def test_wav2vec2_inference_config_does_not_create_specaugment_parameter() -> None:
    from transformers import Wav2Vec2Config, Wav2Vec2ForCTC

    model_config = Wav2Vec2Config(
        vocab_size=8,
        hidden_size=8,
        num_hidden_layers=1,
        num_attention_heads=2,
        intermediate_size=16,
        conv_dim=(8, 8, 8),
        conv_stride=(5, 2, 2),
        conv_kernel=(10, 3, 3),
        num_conv_pos_embedding_groups=2,
        num_conv_pos_embeddings=16,
        mask_time_prob=0.05,
        mask_feature_prob=0.1,
        apply_spec_augment=True,
    )

    _configure_wav2vec2_for_inference(model_config)
    model = Wav2Vec2ForCTC(model_config)
    model.eval()
    _assert_alignment_model_inference_ready(model)

    assert model.config.apply_spec_augment is False
    assert model.config.mask_time_prob == 0.0
    assert model.config.mask_feature_prob == 0.0
    assert not any(
        name.endswith("masked_spec_embed")
        for name, _parameter in model.named_parameters()
    )
    assert not hasattr(model.wav2vec2, "masked_spec_embed")
    assert not any(name.endswith("masked_spec_embed") for name in model.state_dict())


def test_shared_wav2vec2_loader_applies_inference_overrides_before_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from transformers import (
        Wav2Vec2Config,
        Wav2Vec2ForCTC,
        Wav2Vec2Processor,
    )

    captured: dict[str, object] = {}
    model_config = SimpleNamespace(
        apply_spec_augment=True,
        mask_time_prob=0.05,
        mask_feature_prob=0.1,
    )

    class FakeTokenizer:
        @staticmethod
        def get_vocab() -> dict[str, int]:
            return {"A": 1, "|": 2}

    fake_processor = SimpleNamespace(tokenizer=FakeTokenizer())

    class FakeModel:
        def __init__(self) -> None:
            self.config = model_config
            self.training = True
            self.wav2vec2 = SimpleNamespace()

        def to(self, device: str):
            captured["device"] = device
            return self

        def eval(self):
            captured["eval_called"] = True
            self.training = False
            return self

        @staticmethod
        def named_parameters():
            return []

    fake_model = FakeModel()

    def fake_model_load(
        name: str,
        *,
        config,
        cache_dir: str,
        local_files_only: bool,
    ):
        captured.update(
            {
                "model_name": name,
                "config_at_construction": (
                    config.apply_spec_augment,
                    config.mask_time_prob,
                    config.mask_feature_prob,
                ),
                "cache_dir": cache_dir,
                "local_files_only": local_files_only,
            }
        )
        return fake_model

    monkeypatch.setattr(
        Wav2Vec2Processor,
        "from_pretrained",
        lambda *_args, **_kwargs: fake_processor,
    )
    monkeypatch.setattr(
        Wav2Vec2Config,
        "from_pretrained",
        lambda *_args, **_kwargs: model_config,
    )
    monkeypatch.setattr(Wav2Vec2ForCTC, "from_pretrained", fake_model_load)

    model, metadata = _load_inference_alignment_model(
        language="en",
        device="cpu",
        model_name="local-model",
        model_dir=tmp_path,
    )

    assert model is fake_model
    assert captured["config_at_construction"] == (False, 0.0, 0.0)
    assert captured["local_files_only"] is True
    assert captured["device"] == "cpu"
    assert captured["eval_called"] is True
    assert metadata == {
        "language": "en",
        "dictionary": {"a": 1, "|": 2},
        "type": "huggingface",
    }


def test_disabled_alignment_rejects_nonfinite_asr_word_timestamps(
    tmp_path: Path,
) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "whisper"
    asr = ASRResult(
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=1.0,
                text="invalid",
                words=[
                    AlignedWord(
                        text="invalid",
                        start=float("nan"),
                        end=float("inf"),
                        alignment_source="whisper",
                    )
                ],
            )
        ]
    )

    with pytest.raises(BackendUnavailableError, match="invalid timestamp"):
        align_words(tmp_path / "audio.wav", asr, config)


def test_whisperx_alignment_requires_local_cache_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified: list[Path] = []
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment.verify_generated_punkt",
        lambda cache: verified.append(cache),
    )
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment.importlib.util.find_spec",
        lambda _name: object(),
    )
    config = load_config(cwd=tmp_path)
    config.offline = True
    config.alignment.backend = "forced"
    asr = ASRResult(
        language="en",
        segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")],
    )

    with pytest.raises(BackendUnavailableError, match="ltm models pull --alignment en"):
        WhisperXForcedAlignment().align(tmp_path / "audio.wav", asr, config)
    assert verified == [config.model_cache]


def test_whisperx_preflight_requires_installed_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment.verify_generated_punkt",
        lambda _cache: None,
    )
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment.importlib.util.find_spec",
        lambda _name: None,
    )
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"

    with pytest.raises(BackendUnavailableError, match="uv sync --extra alignment"):
        preflight_forced_alignment(config)


def test_whisperx_alignment_rejects_non_ctc_local_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment.verify_generated_punkt",
        lambda _cache: None,
    )
    monkeypatch.setattr(
        "local_transcript_max.alignment.forced_alignment.importlib.util.find_spec",
        lambda _name: object(),
    )
    model_dir = tmp_path / "facebook--wav2vec2-large-xlsr-53"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "preprocessor_config.json").write_text("{}", encoding="utf-8")
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    config.alignment.model = str(model_dir)
    asr = ASRResult(
        language="en",
        segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")],
    )

    with pytest.raises(BackendUnavailableError, match="not a WhisperX-compatible CTC"):
        WhisperXForcedAlignment().align(tmp_path / "audio.wav", asr, config)
