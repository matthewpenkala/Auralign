# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Identity-only display-label resolution and non-destructive derived exports."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass

from local_transcript_max.export.csv_export import formula_safe_cell
from local_transcript_max.schemas import SpeakerTurn, Transcript

from .identity_models import (
    AssignmentScope,
    IdentityDecision,
    ResolutionStatus,
)

SRT_TIMING_RE = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3}) --> "
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)
VTT_TIMING_RE = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})\.(?P<sms>\d{3}) --> "
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})\.(?P<ems>\d{3})$"
)


@dataclass(frozen=True)
class IdentityExportBundle:
    transcript: Transcript
    srt: str
    markdown: str | None = None
    vtt: str | None = None
    text: str | None = None
    segments_csv: str | None = None


def display_label_for_decision(decision: IdentityDecision, original_label: str) -> str:
    if decision.status in {ResolutionStatus.MAPPED, ResolutionStatus.UNATTRIBUTED}:
        return decision.display_label or original_label
    return original_label


def build_identity_transcript(
    transcript: Transcript,
    decisions: list[IdentityDecision],
) -> Transcript:
    labels = _decision_labels(transcript, decisions)
    turns: list[SpeakerTurn] = []
    for index, turn in enumerate(transcript.speaker_turns):
        label = labels[index]
        turns.append(
            turn.model_copy(
                deep=True,
                update={
                    "mapped_name": label if label != turn.speaker else None,
                },
            )
        )
    return transcript.model_copy(deep=True, update={"speaker_turns": turns})


def render_identity_srt(
    canonical_srt: str,
    transcript: Transcript,
    decisions: list[IdentityDecision],
) -> str:
    labels = _decision_labels(transcript, decisions)
    blocks = canonical_srt.rstrip("\n").split("\n\n") if canonical_srt.strip() else []
    rendered: list[str] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError("canonical SRT contains a malformed cue")
        timing = SRT_TIMING_RE.fullmatch(lines[1])
        if timing is None:
            raise ValueError("canonical SRT contains an invalid timing line")
        start = _timestamp_seconds(timing, prefix="s")
        turn_index = _turn_at_time(transcript, start)
        if turn_index is not None:
            lines[2] = _replace_label_prefix(
                lines[2], transcript.speaker_turns[turn_index].speaker, labels[turn_index]
            )
        rendered.append("\n".join(lines))
    return "\n\n".join(rendered) + ("\n" if rendered else "")


def render_identity_vtt(
    canonical_vtt: str,
    transcript: Transcript,
    decisions: list[IdentityDecision],
) -> str:
    labels = _decision_labels(transcript, decisions)
    lines = canonical_vtt.splitlines()
    for index, line in enumerate(lines):
        timing = VTT_TIMING_RE.fullmatch(line)
        if timing is None or index + 1 >= len(lines):
            continue
        start = _timestamp_seconds(timing, prefix="s")
        turn_index = _turn_at_time(transcript, start)
        if turn_index is not None:
            lines[index + 1] = _replace_label_prefix(
                lines[index + 1],
                transcript.speaker_turns[turn_index].speaker,
                labels[turn_index],
            )
    return "\n".join(lines) + ("\n" if canonical_vtt.endswith("\n") else "")


def render_identity_markdown(
    canonical_markdown: str,
    transcript: Transcript,
    decisions: list[IdentityDecision],
) -> str:
    labels = _decision_labels(transcript, decisions)
    lines = canonical_markdown.splitlines()
    label_indexes = [
        index for index, line in enumerate(lines) if re.fullmatch(r"\*\*(.+?)\*\*\s{0,2}", line)
    ]
    if len(label_indexes) != len(transcript.speaker_turns):
        raise ValueError("canonical Markdown turn count does not match transcript.json")
    for turn_index, line_index in enumerate(label_indexes):
        lines[line_index] = f"**{_markdown_escape(labels[turn_index])}**  "
    return "\n".join(lines) + ("\n" if canonical_markdown.endswith("\n") else "")


def render_identity_text(
    transcript: Transcript,
    decisions: list[IdentityDecision],
) -> str:
    labels = _decision_labels(transcript, decisions)
    return "\n\n".join(
        f"{labels[index]}: {turn.text}" for index, turn in enumerate(transcript.speaker_turns)
    ) + ("\n" if transcript.speaker_turns else "")


def render_identity_segments_csv(
    transcript: Transcript,
    decisions: list[IdentityDecision],
) -> str:
    by_turn = {
        decision.turn_index: decision for decision in decisions if decision.turn_index is not None
    }
    output = io.StringIO(newline="")
    fieldnames = [
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
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for index, turn in enumerate(transcript.speaker_turns):
        decision = by_turn[index]
        writer.writerow(
            {
                "turn_index": index,
                "decision_id": _csv_safe(decision.decision_id),
                "original_label": _csv_safe(decision.original_diarization_label or turn.speaker),
                "canonical_label": _csv_safe(decision.canonical_label or turn.speaker),
                "target_label": _csv_safe(decision.target_label or turn.speaker),
                "display_label": _csv_safe(display_label_for_decision(decision, turn.speaker)),
                "start": f"{turn.start:.3f}",
                "end": f"{turn.end:.3f}",
                "text": _csv_safe(turn.text),
            }
        )
    return output.getvalue()


def validate_srt_label_only(canonical_srt: str, identity_srt: str) -> tuple[bool, int]:
    canonical = _normalized_srt_payload(canonical_srt)
    identity = _normalized_srt_payload(identity_srt)
    return canonical == identity, len(canonical)


def _normalized_srt_payload(value: str) -> list[tuple[str, str]]:
    blocks = value.rstrip("\n").split("\n\n") if value.strip() else []
    payload: list[tuple[str, str]] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError("malformed SRT")
        text_lines = list(lines[2:])
        text_lines[0] = text_lines[0].split(": ", 1)[1] if ": " in text_lines[0] else text_lines[0]
        payload.append((lines[1], "\n".join(text_lines)))
    return payload


def _decision_labels(
    transcript: Transcript,
    decisions: list[IdentityDecision],
) -> Mapping[int, str]:
    by_cluster: dict[str, IdentityDecision] = {}
    by_turn: dict[int, IdentityDecision] = {}
    for decision in decisions:
        if decision.assignment_scope == AssignmentScope.CLUSTER:
            if not decision.speaker or decision.speaker in by_cluster:
                raise ValueError("identity decisions contain a duplicate or invalid cluster")
            by_cluster[decision.speaker] = decision
            continue
        if decision.assignment_scope not in {AssignmentScope.TURN, AssignmentScope.FRAGMENT}:
            continue
        if decision.turn_index is None or decision.turn_index in by_turn:
            raise ValueError("identity decisions contain a duplicate or invalid turn")
        if decision.turn_index >= len(transcript.speaker_turns):
            raise ValueError("identity decision turn index is out of range")
        by_turn[decision.turn_index] = decision

    labels: dict[int, str] = {}
    for index, turn in enumerate(transcript.speaker_turns):
        turn_decision = by_turn.get(index)
        cluster_decision = by_cluster.get(turn.speaker)
        if turn_decision is None and cluster_decision is None:
            raise ValueError("identity decisions do not cover every canonical turn")
        if turn_decision is not None and turn_decision.status in {
            ResolutionStatus.MAPPED,
            ResolutionStatus.UNATTRIBUTED,
        }:
            labels[index] = display_label_for_decision(turn_decision, turn.speaker)
        elif cluster_decision is not None:
            labels[index] = display_label_for_decision(cluster_decision, turn.speaker)
        else:
            labels[index] = turn.speaker
    return labels


def _turn_at_time(transcript: Transcript, timestamp: float) -> int | None:
    for index, turn in enumerate(transcript.speaker_turns):
        if abs(turn.start - timestamp) <= 0.001:
            return index
    for index, turn in enumerate(transcript.speaker_turns):
        if turn.start - 0.001 <= timestamp < turn.end - 0.001:
            return index
    if transcript.speaker_turns and abs(transcript.speaker_turns[-1].end - timestamp) <= 0.001:
        return len(transcript.speaker_turns) - 1
    return None


def _timestamp_seconds(match: re.Match[str], *, prefix: str) -> float:
    return (
        int(match.group(f"{prefix}h")) * 3600
        + int(match.group(f"{prefix}m")) * 60
        + int(match.group(f"{prefix}s"))
        + int(match.group(f"{prefix}ms")) / 1000
    )


def _replace_label_prefix(line: str, canonical_label: str, display_label: str) -> str:
    prefix = f"{canonical_label}:"
    if not line.startswith(prefix):
        raise ValueError("canonical subtitle label prefix does not match transcript.json")
    return f"{display_label}:{line[len(prefix) :]}"


def _markdown_escape(label: str) -> str:
    return label.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_")


def _csv_safe(value: str) -> str:
    return formula_safe_cell(value)
