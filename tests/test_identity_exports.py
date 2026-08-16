# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import csv
import io

import pytest

from local_transcript_max.schemas import AlignedWord, SpeakerTurn, Transcript
from local_transcript_max.speaker_id.identity_exports import (
    build_identity_transcript,
    display_label_for_decision,
    effective_display_labels,
    render_identity_markdown,
    render_identity_segments_csv,
    render_identity_srt,
    render_identity_text,
    render_identity_vtt,
    validate_srt_label_only,
    validate_vtt_label_only,
)
from local_transcript_max.speaker_id.identity_models import (
    AssignmentScope,
    IdentityDecision,
    ReasonCode,
    ResolutionStatus,
)


def synthetic_transcript() -> Transcript:
    first_word = AlignedWord(
        text="Synthetic line.",
        start=0.0,
        end=1.0,
        speaker="SPEAKER_00",
        uncertain_speaker=True,
        speaker_overlap_seconds=0.1,
        speaker_candidates=[{"speaker": "SPEAKER_00", "overlap": 0.9}],
        assignment_source="exclusive_diarization",
    )
    second_word = AlignedWord(
        text="Generic response.",
        start=1.5,
        end=2.5,
        speaker="SPEAKER_01",
        speaker_profile_id="profile-beta",
    )
    return Transcript(
        input_path="synthetic.wav",
        words=[first_word, second_word],
        speaker_turns=[
            SpeakerTurn(
                speaker="SPEAKER_00",
                start=0.0,
                end=1.0,
                text="Synthetic line.",
                words=[first_word],
            ),
            SpeakerTurn(
                speaker="SPEAKER_01",
                start=1.5,
                end=2.5,
                text="Generic response.",
                words=[second_word],
            ),
        ],
    )


def turn_decision(
    turn_index: int,
    *,
    speaker: str,
    status: ResolutionStatus = ResolutionStatus.MAPPED,
    display_label: str | None = None,
    reason: ReasonCode = ReasonCode.LOCAL_PROFILE_MATCH,
    scope: AssignmentScope = AssignmentScope.TURN,
) -> IdentityDecision:
    return IdentityDecision(
        decision_id=f"decision:turn:{turn_index}:{reason.value}",
        assignment_scope=scope,
        scope_key=f"turn:{turn_index}",
        speaker=speaker,
        turn_index=turn_index,
        original_diarization_label=speaker,
        canonical_label=speaker,
        target_label=speaker,
        status=status,
        display_label=display_label,
        confidence=0.95,
        reason_codes=[reason],
    )


def cluster_decision(speaker: str, display_label: str) -> IdentityDecision:
    return IdentityDecision(
        decision_id=f"decision:cluster:{speaker}",
        assignment_scope=AssignmentScope.CLUSTER,
        scope_key=f"cluster:{speaker}",
        speaker=speaker,
        member_turn_indexes=[0],
        original_diarization_label=speaker,
        canonical_label=speaker,
        target_label=speaker,
        status=ResolutionStatus.MAPPED,
        display_label=display_label,
        confidence=0.95,
        reason_codes=[ReasonCode.LOCAL_PROFILE_MATCH],
    )


def complete_decisions() -> list[IdentityDecision]:
    return [
        turn_decision(0, speaker="SPEAKER_00", display_label="Participant Alpha"),
        turn_decision(
            1,
            speaker="SPEAKER_01",
            status=ResolutionStatus.UNATTRIBUTED,
            display_label="Unattributed",
            reason=ReasonCode.UNATTRIBUTED_MORE_HONEST,
        ),
    ]


def test_display_label_applies_only_successful_mapped_or_unattributed_decisions() -> None:
    mapped = turn_decision(0, speaker="SPEAKER_00", display_label="Participant Alpha")
    unattributed = turn_decision(
        0,
        speaker="SPEAKER_00",
        status=ResolutionStatus.UNATTRIBUTED,
        display_label="Unattributed",
        reason=ReasonCode.UNATTRIBUTED_MORE_HONEST,
    )
    review = turn_decision(
        0,
        speaker="SPEAKER_00",
        status=ResolutionStatus.REVIEW,
        display_label="Unapproved Candidate",
        reason=ReasonCode.DIRECT_ADDRESS_ANCHOR,
    )
    assert display_label_for_decision(mapped, "SPEAKER_00") == "Participant Alpha"
    assert display_label_for_decision(unattributed, "SPEAKER_00") == "Unattributed"
    assert display_label_for_decision(review, "SPEAKER_00") == "SPEAKER_00"


@pytest.mark.parametrize("reverse", [False, True])
def test_display_precedence_is_independent_of_input_order(reverse: bool) -> None:
    transcript = synthetic_transcript()
    decisions = [
        cluster_decision("SPEAKER_00", "Cluster Match"),
        cluster_decision("SPEAKER_01", "Participant Beta"),
        turn_decision(
            0,
            speaker="SPEAKER_00",
            display_label="Approved Override",
            reason=ReasonCode.EXPLICIT_APPROVED_MAP,
        ),
    ]
    if reverse:
        decisions.reverse()

    derived = build_identity_transcript(transcript, decisions)
    assert [turn.mapped_name for turn in derived.speaker_turns] == [
        "Approved Override",
        "Participant Beta",
    ]


def test_validated_turn_decision_precedes_cluster_mapping_and_review_does_not() -> None:
    transcript = synthetic_transcript()
    decisions = [
        cluster_decision("SPEAKER_00", "Cluster Match"),
        cluster_decision("SPEAKER_01", "Participant Beta"),
        turn_decision(
            0,
            speaker="SPEAKER_00",
            display_label="Turn Match",
            reason=ReasonCode.TURN_LEVEL_OVERRIDE,
        ),
        turn_decision(
            1,
            speaker="SPEAKER_01",
            status=ResolutionStatus.REVIEW,
            display_label="Unapproved Turn Candidate",
            reason=ReasonCode.DIRECT_ADDRESS_ANCHOR,
        ),
    ]
    derived = build_identity_transcript(transcript, decisions)
    assert derived.speaker_turns[0].mapped_name == "Turn Match"
    assert derived.speaker_turns[1].mapped_name == "Participant Beta"


def test_safe_global_alias_renders_for_word_uncertainty_without_changing_turn_status() -> None:
    transcript = synthetic_transcript()
    uncertain = turn_decision(
        0,
        speaker="SPEAKER_00",
        status=ResolutionStatus.REVIEW,
        reason=ReasonCode.SAME_SPEAKER_BOUNDED_FRAGMENT,
    )
    decisions = [
        cluster_decision("SPEAKER_00", "Roger"),
        cluster_decision("SPEAKER_01", "Matthew"),
        uncertain,
        turn_decision(
            1,
            speaker="SPEAKER_01",
            status=ResolutionStatus.UNRESOLVED,
            reason=ReasonCode.INSUFFICIENT_EVIDENCE,
        ),
    ]

    labels = effective_display_labels(transcript, decisions)
    derived = build_identity_transcript(transcript, decisions)

    assert uncertain.status is ResolutionStatus.REVIEW
    assert labels == {0: "Roger", 1: "Matthew"}
    assert [turn.mapped_name for turn in derived.speaker_turns] == ["Roger", "Matthew"]


def test_explicit_unattributed_and_canonical_unknown_override_global_alias() -> None:
    transcript = synthetic_transcript()
    unknown_word = AlignedWord(
        text="Unknown tail.",
        start=3.0,
        end=3.5,
        speaker="UNKNOWN",
        uncertain_speaker=True,
    )
    transcript = transcript.model_copy(
        update={
            "words": [*transcript.words, unknown_word],
            "speaker_turns": [
                *transcript.speaker_turns,
                SpeakerTurn(
                    speaker="UNKNOWN",
                    start=3.0,
                    end=3.5,
                    text=unknown_word.text,
                    words=[unknown_word],
                ),
            ],
        }
    )
    decisions = [
        cluster_decision("SPEAKER_00", "Roger"),
        cluster_decision("SPEAKER_01", "Matthew"),
        turn_decision(
            0,
            speaker="SPEAKER_00",
            status=ResolutionStatus.UNATTRIBUTED,
            display_label="Unattributed",
            reason=ReasonCode.NO_DIARIZATION_COVERAGE,
        ),
        turn_decision(
            1,
            speaker="SPEAKER_01",
            status=ResolutionStatus.REVIEW,
            reason=ReasonCode.INSUFFICIENT_EVIDENCE,
        ),
        turn_decision(
            2,
            speaker="UNKNOWN",
            status=ResolutionStatus.UNRESOLVED,
            reason=ReasonCode.NO_DIARIZATION_COVERAGE,
            scope=AssignmentScope.FRAGMENT,
        ),
    ]

    assert effective_display_labels(transcript, decisions) == {
        0: "Unattributed",
        1: "Matthew",
        2: "UNKNOWN",
    }


def test_mixed_or_unstable_turn_does_not_inherit_global_alias() -> None:
    transcript = synthetic_transcript()
    decisions = [
        cluster_decision("SPEAKER_00", "Roger"),
        cluster_decision("SPEAKER_01", "Matthew"),
        turn_decision(
            0,
            speaker="SPEAKER_00",
            status=ResolutionStatus.REVIEW,
            reason=ReasonCode.MIXED_TURN_BOUNDARY,
        ),
        turn_decision(
            1,
            speaker="SPEAKER_01",
            status=ResolutionStatus.REVIEW,
            reason=ReasonCode.DIARIZATION_UNSTABLE_BOUNDARY,
        ),
    ]
    assert effective_display_labels(transcript, decisions) == {
        0: "SPEAKER_00",
        1: "SPEAKER_01",
    }


def test_build_identity_transcript_preserves_canonical_model_and_word_metadata() -> None:
    transcript = synthetic_transcript()
    snapshot = transcript.model_dump(mode="json")
    derived = build_identity_transcript(transcript, complete_decisions())

    assert transcript.model_dump(mode="json") == snapshot
    assert transcript.speaker_turns[0].mapped_name is None
    assert derived is not transcript
    assert derived.speaker_turns[0] is not transcript.speaker_turns[0]
    assert derived.speaker_turns[0].mapped_name == "Participant Alpha"
    assert derived.speaker_turns[1].mapped_name == "Unattributed"
    assert derived.words == transcript.words
    assert derived.speaker_turns[0].words == transcript.speaker_turns[0].words
    assert derived.speaker_turns[0].words[0].speaker == "SPEAKER_00"
    assert derived.speaker_turns[0].words[0].uncertain_speaker is True
    assert derived.speaker_turns[0].words[0].speaker_candidates == [
        {"speaker": "SPEAKER_00", "overlap": 0.9}
    ]


def test_srt_export_changes_only_the_label_prefix() -> None:
    transcript = synthetic_transcript()
    canonical = (
        "1\n"
        "00:00:00,000 --> 00:00:01,000\n"
        "SPEAKER_00: Synthetic line.\n"
        "continued payload\n\n"
        "2\n"
        "00:00:01,500 --> 00:00:02,500\n"
        "SPEAKER_01: Generic response.\n"
    )
    rendered = render_identity_srt(canonical, transcript, complete_decisions())

    assert rendered == canonical.replace("SPEAKER_00:", "Participant Alpha:").replace(
        "SPEAKER_01:", "Unattributed:"
    )
    assert validate_srt_label_only(canonical, rendered) == (True, 2)
    assert rendered.count("-->") == canonical.count("-->")


def test_srt_export_maps_every_known_label_line_in_a_cue() -> None:
    transcript = synthetic_transcript()
    decisions = [
        cluster_decision("SPEAKER_00", "Roger"),
        cluster_decision("SPEAKER_01", "Matthew"),
        *complete_decisions(),
    ]
    canonical = (
        "1\n"
        "00:00:00,000 --> 00:00:01,000\n"
        "SPEAKER_00: Synthetic line.\n"
        "SPEAKER_00: continued payload\n\n"
        "2\n"
        "00:00:01,500 --> 00:00:02,500\n"
        "SPEAKER_01: Generic response.\n"
    )

    rendered = render_identity_srt(canonical, transcript, decisions)

    assert rendered.count("Participant Alpha:") == 2
    assert rendered.count("Unattributed:") == 1
    assert "SPEAKER_00:" not in rendered
    assert "SPEAKER_01:" not in rendered
    assert validate_srt_label_only(canonical, rendered) == (True, 2)


def test_srt_export_maps_cross_speaker_line_only_with_safe_global_alias() -> None:
    transcript = synthetic_transcript()
    canonical = (
        "1\n"
        "00:00:00,000 --> 00:00:01,000\n"
        "SPEAKER_00: Synthetic line.\n"
        "SPEAKER_01: cross-speaker continuation\n"
    )
    decisions = [
        cluster_decision("SPEAKER_00", "Roger"),
        cluster_decision("SPEAKER_01", "Matthew"),
        turn_decision(
            0,
            speaker="SPEAKER_00",
            status=ResolutionStatus.REVIEW,
            reason=ReasonCode.INSUFFICIENT_EVIDENCE,
        ),
        turn_decision(
            1,
            speaker="SPEAKER_01",
            status=ResolutionStatus.REVIEW,
            reason=ReasonCode.INSUFFICIENT_EVIDENCE,
        ),
    ]
    assert render_identity_srt(canonical, transcript, decisions).endswith(
        "Roger: Synthetic line.\nMatthew: cross-speaker continuation\n"
    )

    without_global_second = [
        decisions[0],
        decisions[2],
        decisions[3],
    ]
    with pytest.raises(ValueError, match="cross-speaker"):
        render_identity_srt(canonical, transcript, without_global_second)


@pytest.mark.parametrize("terminal_newline", [False, True])
def test_srt_export_preserves_crlf_without_treating_it_as_content(
    terminal_newline: bool,
) -> None:
    transcript = synthetic_transcript()
    canonical = (
        "1\r\n"
        "00:00:00,000 --> 00:00:01,000\r\n"
        "SPEAKER_00: Synthetic line.\r\n"
        "continued payload\r\n\r\n"
        "2\r\n"
        "00:00:01,500 --> 00:00:02,500\r\n"
        "SPEAKER_01: Generic response."
    )
    if terminal_newline:
        canonical += "\r\n"

    rendered = render_identity_srt(canonical, transcript, complete_decisions())

    assert "\r\n\r\n" in rendered
    assert rendered == canonical.replace("SPEAKER_00:", "Participant Alpha:").replace(
        "SPEAKER_01:", "Unattributed:"
    )
    assert validate_srt_label_only(canonical, rendered) == (True, 2)


def test_srt_validation_detects_text_or_timing_changes() -> None:
    canonical = "1\n00:00:00,000 --> 00:00:01,000\nSPEAKER_00: Synthetic line.\n"
    assert validate_srt_label_only(
        canonical,
        "1\n00:00:00,000 --> 00:00:01,000\nParticipant Alpha: Changed.\n",
    ) == (False, 1)
    assert validate_srt_label_only(
        canonical,
        "1\n00:00:00,100 --> 00:00:01,000\nParticipant Alpha: Synthetic line.\n",
    ) == (False, 1)


def test_subtitle_validation_uses_only_the_structural_first_colon() -> None:
    canonical_srt = "1\n00:00:00,000 --> 00:00:01,000\nSPEAKER_00: Time: 12:30 remains payload.\n"
    valid_srt = canonical_srt.replace("SPEAKER_00:", "Roger:", 1)
    injected_srt = valid_srt.replace("Time:", "Changed: Time:")
    assert validate_srt_label_only(canonical_srt, valid_srt) == (True, 1)
    assert validate_srt_label_only(canonical_srt, injected_srt) == (False, 1)

    canonical_vtt = (
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSPEAKER_00: Time: 12:30 remains payload.\n"
    )
    valid_vtt = canonical_vtt.replace("SPEAKER_00:", "Roger:", 1)
    injected_vtt = valid_vtt.replace("Time:", "Changed: Time:")
    assert validate_vtt_label_only(canonical_vtt, valid_vtt) == (True, 1)
    assert validate_vtt_label_only(canonical_vtt, injected_vtt) == (False, 1)


def test_subtitle_validation_rejects_colon_led_payload_rewrite_as_label_change() -> None:
    canonical_srt = "1\n00:00:00,000 --> 00:00:01,000\nTime: 12:30 remains payload.\n"
    rewritten_srt = canonical_srt.replace("Time:", "Date:", 1)
    assert validate_srt_label_only(canonical_srt, rewritten_srt) == (False, 1)

    canonical_vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nTime: 12:30 remains payload.\n"
    rewritten_vtt = canonical_vtt.replace("Time:", "Date:", 1)
    assert validate_vtt_label_only(canonical_vtt, rewritten_vtt) == (False, 1)


def test_display_labels_reject_structural_colons() -> None:
    with pytest.raises(ValueError, match="colon"):
        turn_decision(0, speaker="SPEAKER_00", display_label="Roger: Changed")


def test_srt_validation_checks_every_changed_label_line() -> None:
    canonical = (
        "1\n00:00:00,000 --> 00:00:01,000\n"
        "SPEAKER_00: Synthetic line.\nSPEAKER_00: continued payload\n"
    )
    valid = canonical.replace("SPEAKER_00:", "Roger:")
    drifted = valid.replace("continued payload", "changed payload")
    assert validate_srt_label_only(canonical, valid) == (True, 1)
    assert validate_srt_label_only(canonical, drifted) == (False, 1)


def test_subtitle_cue_at_shared_boundary_maps_to_the_turn_that_starts_there() -> None:
    transcript = synthetic_transcript()
    transcript.speaker_turns[0].end = 1.5
    canonical = "1\n00:00:01,500 --> 00:00:02,500\nSPEAKER_01: Generic response.\n"
    rendered = render_identity_srt(canonical, transcript, complete_decisions())
    assert "Unattributed: Generic response." in rendered


def test_subtitle_cue_one_millisecond_before_boundary_stays_with_containing_turn() -> None:
    transcript = synthetic_transcript()
    transcript.speaker_turns[0].end = 1.5
    transcript.speaker_turns[1].start = 1.5
    canonical = "1\n00:00:01,499 --> 00:00:01,500\nSPEAKER_00: Synthetic line.\n"

    rendered = render_identity_srt(canonical, transcript, complete_decisions())

    assert "Participant Alpha: Synthetic line." in rendered


@pytest.mark.parametrize(
    ("boundary", "rendered_timestamp"),
    [
        (1.5005, "00:00:01,500"),
        (1.5015, "00:00:01,502"),
    ],
)
def test_subtitle_cue_at_half_millisecond_boundary_uses_canonical_speaker_prefix(
    boundary: float,
    rendered_timestamp: str,
) -> None:
    transcript = synthetic_transcript()
    transcript.speaker_turns[0].end = boundary
    transcript.speaker_turns[1].start = boundary
    canonical = f"1\n{rendered_timestamp} --> 00:00:02,500\nSPEAKER_01: Generic response.\n"

    rendered = render_identity_srt(canonical, transcript, complete_decisions())

    assert "Unattributed: Generic response." in rendered


def test_vtt_export_changes_only_the_label_prefix() -> None:
    transcript = synthetic_transcript()
    canonical = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "SPEAKER_00: Synthetic line.\n\n"
        "00:00:01.500 --> 00:00:02.500\n"
        "SPEAKER_01: Generic response.\n"
    )
    rendered = render_identity_vtt(canonical, transcript, complete_decisions())
    assert rendered == canonical.replace("SPEAKER_00:", "Participant Alpha:").replace(
        "SPEAKER_01:", "Unattributed:"
    )
    assert validate_vtt_label_only(canonical, rendered) == (True, 2)


def test_vtt_export_maps_every_known_label_line_and_validates_all_payload() -> None:
    transcript = synthetic_transcript()
    decisions = [
        cluster_decision("SPEAKER_00", "Roger"),
        cluster_decision("SPEAKER_01", "Matthew"),
        *complete_decisions(),
    ]
    canonical = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:01.000\n"
        "SPEAKER_00: Synthetic line.\n"
        "SPEAKER_00: continued payload\n\n"
        "00:00:01.500 --> 00:00:02.500\n"
        "SPEAKER_01: Generic response.\n"
    )
    rendered = render_identity_vtt(canonical, transcript, decisions)
    assert rendered.count("Participant Alpha:") == 2
    assert validate_vtt_label_only(canonical, rendered) == (True, 2)
    assert validate_vtt_label_only(
        canonical, rendered.replace("continued payload", "changed payload")
    ) == (False, 2)


def test_markdown_export_changes_only_label_lines_and_escapes_markup() -> None:
    transcript = synthetic_transcript()
    canonical = (
        "# Transcript\n\n**SPEAKER_00**  \nSynthetic line.\n\n**SPEAKER_01**  \nGeneric response.\n"
    )
    decisions = [
        turn_decision(0, speaker="SPEAKER_00", display_label="Participant_*Alpha*"),
        complete_decisions()[1],
    ]
    rendered = render_identity_markdown(canonical, transcript, decisions)
    assert rendered == (
        "# Transcript\n\n"
        "**Participant\\_\\*Alpha\\***  \n"
        "Synthetic line.\n\n"
        "**Unattributed**  \n"
        "Generic response.\n"
    )


def test_plain_text_export_preserves_turn_text_exactly() -> None:
    transcript = synthetic_transcript()
    rendered = render_identity_text(transcript, complete_decisions())
    assert rendered == ("Participant Alpha: Synthetic line.\n\nUnattributed: Generic response.\n")
    assert [line.split(": ", 1)[1] for line in rendered.splitlines() if line] == [
        turn.text for turn in transcript.speaker_turns
    ]


def test_segments_csv_preserves_payload_and_defends_every_formula_capable_cell() -> None:
    transcript = synthetic_transcript()
    transcript.speaker_turns[0].text = '=HYPERLINK("synthetic")'
    decisions = [
        turn_decision(0, speaker="SPEAKER_00", display_label="=Synthetic Formula"),
        complete_decisions()[1],
    ]
    payload = render_identity_segments_csv(transcript, decisions)
    rows = list(csv.DictReader(io.StringIO(payload)))

    assert list(rows[0]) == [
        "turn_index",
        "decision_id",
        "original_label",
        "canonical_label",
        "target_label",
        "display_label",
        "start",
        "end",
        "text",
    ]
    assert rows[0]["original_label"] == "SPEAKER_00"
    assert rows[0]["canonical_label"] == "SPEAKER_00"
    assert rows[0]["target_label"] == "SPEAKER_00"
    assert rows[0]["display_label"] == "'=Synthetic Formula"
    assert rows[0]["text"] == '\'=HYPERLINK("synthetic")'
    assert rows[0]["start"] == "0.000"
    assert rows[0]["end"] == "1.000"
    assert rows[1]["display_label"] == "Unattributed"


def test_global_alias_and_uncertain_turn_compose_across_every_identity_format() -> None:
    transcript = synthetic_transcript()
    decisions = [
        cluster_decision("SPEAKER_00", "Roger"),
        cluster_decision("SPEAKER_01", "Matthew"),
        turn_decision(
            0,
            speaker="SPEAKER_00",
            status=ResolutionStatus.REVIEW,
            reason=ReasonCode.SAME_SPEAKER_BOUNDED_FRAGMENT,
        ),
        turn_decision(
            1,
            speaker="SPEAKER_01",
            status=ResolutionStatus.UNRESOLVED,
            reason=ReasonCode.INSUFFICIENT_EVIDENCE,
        ),
    ]
    canonical_srt = (
        "1\n00:00:00,000 --> 00:00:01,000\nSPEAKER_00: Synthetic line.\n\n"
        "2\n00:00:01,500 --> 00:00:02,500\nSPEAKER_01: Generic response.\n"
    )
    canonical_vtt = (
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSPEAKER_00: Synthetic line.\n\n"
        "00:00:01.500 --> 00:00:02.500\nSPEAKER_01: Generic response.\n"
    )
    canonical_md = (
        "# Transcript\n\n**SPEAKER_00**  \nSynthetic line.\n\n**SPEAKER_01**  \nGeneric response.\n"
    )
    artifacts = [
        render_identity_srt(canonical_srt, transcript, decisions),
        render_identity_vtt(canonical_vtt, transcript, decisions),
        render_identity_markdown(canonical_md, transcript, decisions),
        render_identity_text(transcript, decisions),
        render_identity_segments_csv(transcript, decisions),
        build_identity_transcript(transcript, decisions).model_dump_json(),
    ]
    combined = "\n".join(artifacts)
    assert "Roger" in combined
    assert "Matthew" in combined
    assert "SPEAKER_00:" not in combined
    assert "SPEAKER_01:" not in combined
    rows = list(csv.DictReader(io.StringIO(artifacts[4])))
    assert [row["display_label"] for row in rows] == ["Roger", "Matthew"]


@pytest.mark.parametrize(
    "renderer,canonical",
    [
        (
            render_identity_srt,
            "1\n00:00:00,000 --> 00:00:01,000\nWRONG: Synthetic line.\n",
        ),
        (
            render_identity_vtt,
            "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nWRONG: Synthetic line.\n",
        ),
    ],
)
def test_subtitle_exports_reject_label_text_join_or_prefix_drift(
    renderer: object, canonical: str
) -> None:
    with pytest.raises(ValueError, match="label prefix"):
        renderer(canonical, synthetic_transcript(), complete_decisions())  # type: ignore[operator]


@pytest.mark.parametrize(
    "renderer,canonical,error",
    [
        (
            render_identity_srt,
            (
                "1\n00:00:00,000 --> 00:00:01,000\n"
                "SPEAKER_00: Synthetic line.\nSPEAKER_99: unmatched\n"
            ),
            "unmatched speaker label",
        ),
        (
            render_identity_srt,
            "1\nnot-a-timestamp\nSPEAKER_00: Synthetic line.\n",
            "timing line",
        ),
        (
            render_identity_vtt,
            "WEBVTT\n\nnot-a-timestamp\nSPEAKER_00: Synthetic line.\n",
            "malformed cue",
        ),
        (
            render_identity_vtt,
            "WEBVTT\n\n00:00:10.000 --> 00:00:11.000\nSPEAKER_00: Synthetic line.\n",
            "canonical turn",
        ),
    ],
)
def test_subtitle_exports_fail_closed_for_malformed_or_unmatched_cues(
    renderer: object,
    canonical: str,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        renderer(canonical, synthetic_transcript(), complete_decisions())  # type: ignore[operator]


def test_exporters_reject_missing_duplicate_and_out_of_range_turn_decisions() -> None:
    transcript = synthetic_transcript()
    missing = complete_decisions()[:1]
    duplicate = [complete_decisions()[0], complete_decisions()[0], complete_decisions()[1]]
    out_of_range = [
        *complete_decisions(),
        turn_decision(2, speaker="SPEAKER_02", display_label="Participant Gamma"),
    ]
    for decisions in [missing, duplicate, out_of_range]:
        with pytest.raises(ValueError):
            build_identity_transcript(transcript, decisions)
        with pytest.raises(ValueError):
            render_identity_srt(
                "1\n00:00:00,000 --> 00:00:01,000\nSPEAKER_00: Synthetic line.\n",
                transcript,
                decisions,
            )
