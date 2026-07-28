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
    render_identity_markdown,
    render_identity_segments_csv,
    render_identity_srt,
    render_identity_text,
    render_identity_vtt,
    validate_srt_label_only,
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
    canonical = (
        f"1\n{rendered_timestamp} --> 00:00:02,500\n"
        "SPEAKER_01: Generic response.\n"
    )

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
