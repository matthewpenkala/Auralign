# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import pytest

from local_transcript_max.config import SubtitleExportConfig
from local_transcript_max.export.srt_export import render_srt, srt_timestamp
from local_transcript_max.export.subtitle import (
    SubtitleCue,
    format_cue_text,
    overlay_cue_word_surfaces,
)
from local_transcript_max.export.vtt_export import render_vtt, vtt_timestamp
from local_transcript_max.schemas import AlignedWord, SpeakerTurn, Transcript


def test_timestamp_formatting() -> None:
    assert srt_timestamp(3661.234) == "01:01:01,234"
    assert vtt_timestamp(3661.234) == "01:01:01.234"


def test_serialized_cue_interval_remains_positive_at_half_even_rounding_boundary() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0015,
                end=0.0025,
                text="brief",
            )
        ],
    )

    assert "00:00:00,002 --> 00:00:00,003" in render_srt(transcript)
    assert "00:00:00.002 --> 00:00:00.003" in render_vtt(transcript)


def test_srt_and_vtt_render_speaker_turns() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[SpeakerTurn(speaker="SPEAKER_00", start=0, end=1.5, text="Hello")],
    )
    assert "SPEAKER_00: Hello" in render_srt(transcript)
    assert render_vtt(transcript).startswith("WEBVTT")


def test_long_srt_cue_splits_by_word_timestamps() -> None:
    words = [
        AlignedWord(text=f"word{index}", start=index * 0.4, end=index * 0.4 + 0.2)
        for index in range(12)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=5.0,
                text=" ".join(word.text for word in words),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=18, max_line_count=1),
    )

    assert srt.count("-->") > 1
    assert "00:00:00,000 -->" in srt
    assert "SPEAKER_00:" in srt


def test_long_srt_cue_balances_instead_of_stranding_an_orphan_tail() -> None:
    tokens = (
        "about like, you know, her not wanting to go in and like being almost like "
        "infantile or childish about it."
    ).split()
    words = [
        AlignedWord(text=token, start=index * 0.5, end=(index + 1) * 0.5)
        for index, token in enumerate(tokens)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        language="en",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=len(tokens) * 0.5,
                text=" ".join(tokens),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=45,
            max_line_count=2,
            max_cue_duration=30.0,
        ),
    )
    cue_texts = [
        " ".join(block.splitlines()[2:]).split(": ", 1)[1]
        for block in srt.strip().split("\n\n")
    ]

    assert len(cue_texts) == 2
    assert all(len(text.split()) >= 4 for text in cue_texts)
    assert cue_texts[-1] != "childish about it."
    assert " ".join(" ".join(cue_texts).split()) == " ".join(tokens)


def test_subtitle_split_enforces_maximum_duration_without_character_pressure() -> None:
    words = [
        AlignedWord(text=f"w{index}", start=float(index), end=float(index + 1))
        for index in range(10)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=10.0,
                text=" ".join(word.text for word in words),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=200,
            max_line_count=2,
            max_cue_duration=4.0,
        ),
    )
    timing_lines = [
        line for line in srt.splitlines() if " --> " in line
    ]

    assert len(timing_lines) == 3
    assert timing_lines == [
        "00:00:00,000 --> 00:00:03,000",
        "00:00:03,000 --> 00:00:06,000",
        "00:00:06,000 --> 00:00:10,000",
    ]


def test_subtitle_split_prefers_sentence_boundary_when_balance_is_comparable() -> None:
    tokens = "one two three four five six. seven eight nine ten eleven twelve".split()
    words = [
        AlignedWord(text=token, start=index * 0.5, end=(index + 1) * 0.5)
        for index, token in enumerate(tokens)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=6.0,
                text=" ".join(tokens),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=20,
            max_line_count=2,
            speaker_labels=False,
        ),
    )
    cue_texts = [
        " ".join(block.splitlines()[2:])
        for block in srt.strip().split("\n\n")
    ]

    assert cue_texts[0].endswith("six.")
    assert cue_texts[1].startswith("seven")


def test_subtitle_split_honors_hard_pause_even_when_full_cue_otherwise_fits() -> None:
    words = [
        AlignedWord(text="before", start=0.0, end=0.5),
        AlignedWord(text="pause", start=0.5, end=1.0),
        AlignedWord(text="after", start=5.0, end=5.5),
        AlignedWord(text="pause", start=5.5, end=6.0),
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=6.0,
                text="before pause after pause",
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=200, max_line_count=2),
    )

    assert [line for line in srt.splitlines() if " --> " in line] == [
        "00:00:00,000 --> 00:00:01,000",
        "00:00:05,000 --> 00:00:06,000",
    ]


@pytest.mark.parametrize(
    ("gap", "expected_cues"),
    [
        (2.5, 1),
        (2.501, 2),
    ],
)
def test_hard_pause_threshold_is_exact(gap: float, expected_cues: int) -> None:
    words = [
        AlignedWord(text="before", start=0.0, end=1.0),
        AlignedWord(text="after", start=1.0 + gap, end=2.0 + gap),
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=2.0 + gap,
                text="before after",
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=200, max_line_count=2),
    )

    assert srt.count("-->") == expected_cues


def test_subtitle_split_preserves_truthful_turn_when_word_timing_is_incomplete() -> None:
    words = [
        AlignedWord(text="one", start=0.0, end=1.0),
        AlignedWord(text="two", start=None, end=None),
        AlignedWord(text="three", start=9.0, end=10.0),
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=10.0,
                text="one two three",
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=200,
            max_line_count=2,
            max_cue_duration=4.0,
        ),
    )

    assert srt.count("-->") == 1
    assert "00:00:00,000 --> 00:00:10,000" in srt
    assert "SPEAKER_00: one two three" in srt


@pytest.mark.parametrize(
    "invalid_word",
    [
        AlignedWord(text="two", start=float("nan"), end=2.0),
        AlignedWord(text="two", start=1.0, end=float("inf")),
        AlignedWord(text="two", start=2.0, end=1.0),
        AlignedWord(text="two", start=2.0, end=2.0),
        AlignedWord(text="two", start=1.0, end=11.0),
    ],
)
def test_subtitle_split_rejects_invalid_word_timing_without_inventing_cues(
    invalid_word: AlignedWord,
) -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=10.0,
                text="one two three",
                words=[
                    AlignedWord(text="one", start=0.0, end=1.0),
                    invalid_word,
                    AlignedWord(text="three", start=9.0, end=10.0),
                ],
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=200,
            max_line_count=2,
            max_cue_duration=4.0,
        ),
    )

    assert srt.count("-->") == 1
    assert "00:00:00,000 --> 00:00:10,000" in srt


def test_subtitle_split_preserves_truthful_turn_when_word_timing_overlaps() -> None:
    words = [
        AlignedWord(text="one", start=0.0, end=2.0),
        AlignedWord(text="two", start=1.5, end=3.0),
        AlignedWord(text="three", start=3.0, end=8.0),
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=8.0,
                text="one two three",
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=200,
            max_line_count=2,
            max_cue_duration=4.0,
        ),
    )

    assert srt.count("-->") == 1
    assert "00:00:00,000 --> 00:00:08,000" in srt


def test_single_indivisible_word_keeps_truthful_duration() -> None:
    word = AlignedWord(text="antidisestablishmentarianism", start=0.0, end=10.0)
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=10.0,
                text=word.text,
                words=[word],
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=10,
            max_line_count=1,
            max_cue_duration=4.0,
        ),
    )

    assert srt.count("-->") == 1
    assert "00:00:00,000 --> 00:00:10,000" in srt
    assert word.text in srt


def test_oversized_indivisible_word_is_isolated_from_neighboring_words() -> None:
    long_word = "antidisestablishmentarianism"
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=2.0,
                text=f"{long_word} neighbor",
                words=[
                    AlignedWord(text=long_word, start=0.0, end=1.0),
                    AlignedWord(text="neighbor", start=1.0, end=2.0),
                ],
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=10, max_line_count=2),
    )
    cue_texts = [
        " ".join(block.splitlines()[2:]).split(": ", 1)[1]
        for block in srt.strip().split("\n\n")
    ]

    assert cue_texts == [long_word, "neighbor"]


def test_complete_short_sentence_is_not_penalized_as_an_orphan() -> None:
    tokens = "one two three four. I don't know.".split()
    words = [
        AlignedWord(text=token, start=index * 0.5, end=(index + 1) * 0.5)
        for index, token in enumerate(tokens)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=len(words) * 0.5,
                text=" ".join(tokens),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=12, max_line_count=2),
    )
    cue_texts = [
        " ".join(block.splitlines()[2:]).split(": ", 1)[1]
        for block in srt.strip().split("\n\n")
    ]

    assert cue_texts == ["one two three four.", "I don't know."]


def test_additive_severity_dp_matches_exhaustive_counterexample_optimum() -> None:
    texts = ["the", "no?", "ccc", "well,", "dddd", "yes.", "bb"]
    durations = [0.15, 0.3, 0.3, 0.8, 1.2, 0.15, 0.3]
    gaps = [2.5, 0.1, 0.6, 0.1, 1.0, 2.5]
    words: list[AlignedWord] = []
    cursor = 0.0
    for index, (text, duration) in enumerate(zip(texts, durations, strict=True)):
        words.append(AlignedWord(text=text, start=cursor, end=cursor + duration))
        if index < len(gaps):
            cursor += duration + gaps[index]
        else:
            cursor += duration
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=cursor,
                text=" ".join(texts),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=10,
            max_line_count=1,
            max_cue_duration=3.0,
        ),
    )
    cue_texts = [
        " ".join(block.splitlines()[2:]).split(": ", 1)[1]
        for block in srt.strip().split("\n\n")
    ]

    assert cue_texts == ["the no?", "ccc well,", "dddd yes.", "bb"]


def test_semantic_partition_keeps_going_to_dependency_together() -> None:
    tokens = (
        "one two three four five six seven you're going to make this work "
        "eight nine ten eleven twelve"
    ).split()
    words = [
        AlignedWord(text=token, start=index * 0.5, end=(index + 1) * 0.5)
        for index, token in enumerate(tokens)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=len(words) * 0.5,
                text=" ".join(tokens),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=14,
            max_line_count=2,
            speaker_labels=False,
        ),
    )
    cue_texts = [
        " ".join(block.splitlines()[2:])
        for block in srt.strip().split("\n\n")
    ]

    assert "going to make this work" in cue_texts
    assert not any(cue.endswith("going") for cue in cue_texts)


def test_cjk_cues_split_and_wrap_by_character_units() -> None:
    characters = list("这是一个用于验证中文转录分段的完整句子。")
    words = [
        AlignedWord(text=character, start=index * 0.5, end=(index + 1) * 0.5)
        for index, character in enumerate(characters)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        language="zh-CN",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=len(words) * 0.5,
                text="".join(characters),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=5, max_line_count=2),
    )
    blocks = srt.strip().split("\n\n")
    bodies: list[str] = []
    for block in blocks:
        lines = block.splitlines()[2:]
        lines[0] = lines[0].split(": ", 1)[1]
        bodies.extend(lines)

    assert len(blocks) > 1
    assert all(len(line) <= 5 for line in bodies)
    assert "".join(bodies) == "".join(characters)


def test_unicode_sentence_punctuation_before_closing_quote_is_preferred() -> None:
    tokens = "one two three four five six!” seven eight nine ten eleven twelve".split()
    words = [
        AlignedWord(text=token, start=index * 0.5, end=(index + 1) * 0.5)
        for index, token in enumerate(tokens)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=6.0,
                text=" ".join(tokens),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            max_line_width=20,
            max_line_count=2,
            speaker_labels=False,
        ),
    )
    cue_texts = [
        " ".join(block.splitlines()[2:])
        for block in srt.strip().split("\n\n")
    ]

    assert cue_texts[0].endswith("six!”")
    assert cue_texts[1].startswith("seven")


def test_line_width_budget_includes_speaker_prefix() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                mapped_name="Matthew Penkala",
                start=0.0,
                end=3.0,
                text="alpha beta gamma delta epsilon zeta",
                words=[
                    AlignedWord(text="alpha", start=0.0, end=0.5),
                    AlignedWord(text="beta", start=0.5, end=1.0),
                    AlignedWord(text="gamma", start=1.0, end=1.5),
                    AlignedWord(text="delta", start=1.5, end=2.0),
                    AlignedWord(text="epsilon", start=2.0, end=2.5),
                    AlignedWord(text="zeta", start=2.5, end=3.0),
                ],
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=30, max_line_count=2),
    )
    text_lines = srt.strip().splitlines()[2:]

    assert text_lines[0].startswith("Matthew Penkala: ")
    assert all(len(line) <= 30 for line in text_lines)
    assert " ".join(text_lines).removeprefix("Matthew Penkala: ") == (
        "alpha beta gamma delta epsilon zeta"
    )


def test_indivisible_speaker_label_longer_than_width_is_preserved_best_effort() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="A_VERY_LONG_SPEAKER_LABEL",
                start=0.0,
                end=1.0,
                text="alpha",
                words=[AlignedWord(text="alpha", start=0.0, end=1.0)],
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=10, max_line_count=1),
    )

    assert "A_VERY_LONG_SPEAKER_LABEL: alpha" in srt


def test_speaker_label_uses_own_line_when_first_word_cannot_share_width() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="A",
                start=0.0,
                end=1.0,
                text="abcdefgh",
                words=[AlignedWord(text="abcdefgh", start=0.0, end=1.0)],
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(max_line_width=10, max_line_count=2),
    )
    text_lines = srt.strip().splitlines()[2:]

    assert text_lines == ["A:", "abcdefgh"]
    assert all(len(line) <= 10 for line in text_lines)


def test_punctuation_overlay_falls_back_locally_when_frozen_cue_cannot_fit() -> None:
    words = [
        AlignedWord(text=text, start=index * 0.5, end=(index + 1) * 0.5)
        for index, text in enumerate(["abcd", "ef", "ghij", "klmno"])
    ]
    options = SubtitleExportConfig(
        max_line_width=10,
        max_line_count=2,
    )
    base = SubtitleCue(
        speaker="A",
        start=0.0,
        end=2.0,
        text="abcd ef\nghij klmno",
        words=words,
    )
    [overlaid] = overlay_cue_word_surfaces(
        [base],
        ["abcd", "ef,", "ghij", "klmno"],
        options,
        "en",
    )
    rendered_lines = format_cue_text(overlaid, options).splitlines()

    assert overlaid == base
    assert max(len(line) for line in rendered_lines) <= 10
    assert len(rendered_lines) == 2


def test_punctuation_overlay_drops_weak_mark_before_strong_mark_to_fit() -> None:
    words = [
        AlignedWord(text=text, start=index * 0.5, end=(index + 1) * 0.5)
        for index, text in enumerate(["abcd", "ef", "ghij", "klmn"])
    ]
    options = SubtitleExportConfig(max_line_width=10, max_line_count=2)
    base = SubtitleCue(
        speaker="A",
        start=0.0,
        end=2.0,
        text="abcd ef\nghij klmn",
        words=words,
    )

    [overlaid] = overlay_cue_word_surfaces(
        [base],
        ["Abcd", "ef,", "ghij", "klmn."],
        options,
        "en",
    )
    rendered_lines = format_cue_text(overlaid, options).splitlines()

    assert rendered_lines == ["A: Abcd ef", "ghij klmn."]
    assert len(rendered_lines) == 2
    assert all(len(line) <= 10 for line in rendered_lines)


def test_srt_and_vtt_segmentation_is_deterministic_and_equivalent() -> None:
    words = [
        AlignedWord(text=f"word{index}", start=index * 0.5, end=(index + 1) * 0.5)
        for index in range(18)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=9.0,
                text=" ".join(word.text for word in words),
                words=words,
            )
        ],
    )
    options = SubtitleExportConfig(max_line_width=24, max_line_count=2)

    first_srt = render_srt(transcript, options)
    second_srt = render_srt(transcript, options)
    vtt = render_vtt(transcript, options)
    srt_payload = [
        line.replace(",", ".")
        for line in first_srt.splitlines()
        if " --> " in line or line.startswith("SPEAKER_00:")
    ]
    vtt_payload = [
        line
        for line in vtt.splitlines()
        if " --> " in line or line.startswith("SPEAKER_00:")
    ]

    assert first_srt == second_srt
    assert srt_payload == vtt_payload


def test_highlight_words_renders_timed_word_cues() -> None:
    words = [
        AlignedWord(text="hello", start=0.0, end=0.4),
        AlignedWord(text="world", start=0.4, end=0.9),
    ]
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=0.9,
                text="hello world",
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(highlight_words=True, max_line_width=80),
    )

    assert srt.count("-->") == 2
    assert "SPEAKER_00: <u>hello</u> world" in srt
    assert "SPEAKER_00: hello <u>world</u>" in srt


def test_cjk_highlight_markup_remains_balanced_and_outside_wrap_accounting() -> None:
    characters = list("你好世界")
    words = [
        AlignedWord(text=character, start=index * 0.5, end=(index + 1) * 0.5)
        for index, character in enumerate(characters)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        language="zh",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=2.0,
                text="".join(characters),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            highlight_words=True,
            max_line_width=3,
            max_line_count=2,
        ),
    )
    for block in srt.strip().split("\n\n"):
        lines = block.splitlines()[2:]
        lines[0] = lines[0].split(": ", 1)[1]
        marked = "\n".join(lines)
        assert marked.count("<u>") == 1
        assert marked.count("</u>") == 1
        assert "<\n" not in marked
        assert "\n/u>" not in marked
        assert marked.replace("<u>", "").replace("</u>", "").replace("\n", "") == (
            "".join(characters)
        )


def test_english_highlight_offsets_account_for_spaces_replaced_by_wraps() -> None:
    tokens = ["hello", "world", "again"]
    words = [
        AlignedWord(text=token, start=index * 0.5, end=(index + 1) * 0.5)
        for index, token in enumerate(tokens)
    ]
    transcript = Transcript(
        input_path="sample.wav",
        language="en",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=1.5,
                text=" ".join(tokens),
                words=words,
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            split_long_cues=False,
            highlight_words=True,
            max_line_width=5,
            max_line_count=3,
        ),
    )
    marked_bodies: list[str] = []
    for block in srt.strip().split("\n\n"):
        lines = block.splitlines()[2:]
        lines[0] = lines[0].split(": ", 1)[1]
        marked_bodies.append("\n".join(lines))

    assert marked_bodies == [
        "<u>hello</u>\nworld\nagain",
        "hello\n<u>world</u>\nagain",
        "hello\nworld\n<u>again</u>",
    ]


@pytest.mark.parametrize(
    "invalid_word",
    [
        AlignedWord(text="two", start=None, end=None),
        AlignedWord(text="two", start=float("nan"), end=2.0),
        AlignedWord(text="two", start=1.0, end=float("inf")),
        AlignedWord(text="two", start=2.0, end=1.0),
        AlignedWord(text="two", start=0.5, end=2.0),
    ],
)
def test_highlight_mode_preserves_whole_truthful_cue_for_invalid_timing(
    invalid_word: AlignedWord,
) -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=10.0,
                text="one two three",
                words=[
                    AlignedWord(text="one", start=0.0, end=1.0),
                    invalid_word,
                    AlignedWord(text="three", start=9.0, end=10.0),
                ],
            )
        ],
    )

    srt = render_srt(
        transcript,
        SubtitleExportConfig(
            highlight_words=True,
            max_line_width=200,
            max_line_count=2,
            max_cue_duration=4.0,
        ),
    )

    assert srt.count("-->") == 1
    assert "00:00:00,000 --> 00:00:10,000" in srt
    assert "SPEAKER_00: one two three" in srt
    assert "<u>" not in srt


def test_subtitle_text_sanitizes_arrow_token() -> None:
    transcript = Transcript(
        input_path="sample.wav",
        speaker_turns=[
            SpeakerTurn(speaker="SPEAKER_00", start=0.0, end=1.0, text="not --> a cue")
        ],
    )

    srt = render_srt(transcript)

    assert "not -> a cue" in srt
    assert "not --> a cue" not in srt


def test_vtt_segment_fallback_still_works_without_speaker_turns() -> None:
    from local_transcript_max.schemas import ASRSegment

    transcript = Transcript(
        input_path="sample.wav",
        segments=[ASRSegment(id=0, start=0.0, end=1.0, text="segment only")],
    )

    assert "segment only" in render_vtt(transcript)
