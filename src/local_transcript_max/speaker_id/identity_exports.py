# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Identity-only display-label resolution and non-destructive derived exports."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from local_transcript_max.export.csv_export import formula_safe_cell
from local_transcript_max.export.subtitle import timestamp_milliseconds
from local_transcript_max.schemas import SpeakerTurn, Transcript

from .identity_models import (
    AssignmentScope,
    IdentityDecision,
    ReasonCode,
    ResolutionStatus,
)

SRT_TIMING_RE = re.compile(
    r"^(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3}) --> "
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})$"
)
_RESERVED_SPEAKER_PREFIX_RE = re.compile(
    r"^(?:SPEAKER_\d+|UNKNOWN|UNATTRIBUTED):",
    re.IGNORECASE,
)
_GLOBAL_ALIAS_BLOCKING_REASONS = frozenset(
    {
        ReasonCode.CANONICAL_UNKNOWN_FOLDED_INTO_CLUSTER,
        ReasonCode.DIARIZATION_UNSTABLE_BOUNDARY,
        ReasonCode.ENSEMBLE_DISAGREEMENT,
        ReasonCode.MIXED_TURN_BOUNDARY,
        ReasonCode.NO_DIARIZATION_COVERAGE,
        ReasonCode.PARTIAL_LINE_SPEAKER_CHANGE,
        ReasonCode.POST_VAD_TAIL,
        ReasonCode.SOURCE_MEDIA_TAIL_ARTIFACT,
        ReasonCode.UNATTRIBUTED_MORE_HONEST,
    }
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
        if decision.display_label is None:
            raise ValueError("mapped identity decision is missing its display label")
        return decision.display_label
    return original_label


def effective_display_labels(
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> Mapping[int, str]:
    """Resolve non-destructive labels from cluster and per-turn decisions.

    A mapped or explicitly unattributed turn wins. Otherwise a safe mapped
    cluster alias remains a display-only property even when word-level
    uncertainty keeps the child decision in review. Canonical UNKNOWN,
    no-coverage, mixed-speaker, conflict, and rejected turns remain protected.
    """

    by_cluster, by_turn = _indexed_decisions(transcript, decisions)
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
            continue
        if _turn_allows_global_alias(turn, turn_decision) and cluster_decision is not None:
            labels[index] = display_label_for_decision(cluster_decision, turn.speaker)
            continue
        labels[index] = turn.speaker
    return labels


def build_identity_transcript(
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> Transcript:
    labels = effective_display_labels(transcript, decisions)
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
    decisions: Sequence[IdentityDecision],
) -> str:
    labels = effective_display_labels(transcript, decisions)
    cluster_labels = _safe_global_labels(transcript, decisions)
    lines = canonical_srt.splitlines(keepends=True)
    blocks = _subtitle_block_indexes(lines)
    if transcript.speaker_turns and not blocks:
        raise ValueError("canonical SRT contains no cues")
    for block in blocks:
        if len(block) < 3:
            raise ValueError("canonical SRT contains a malformed cue")
        if not _line_content(lines[block[0]]).isdigit():
            raise ValueError("canonical SRT contains an invalid cue identifier")
        timing = SRT_TIMING_RE.fullmatch(_line_content(lines[block[1]]))
        if timing is None:
            raise ValueError("canonical SRT contains an invalid timing line")
        _render_subtitle_payload(
            lines,
            block[2:],
            transcript,
            labels,
            cluster_labels,
            start=_timestamp_seconds(timing, prefix="s"),
        )
    return "".join(lines)


def render_identity_vtt(
    canonical_vtt: str,
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> str:
    labels = effective_display_labels(transcript, decisions)
    cluster_labels = _safe_global_labels(transcript, decisions)
    lines = canonical_vtt.splitlines(keepends=True)
    if not lines or _line_content(lines[0]) != "WEBVTT":
        raise ValueError("canonical VTT is missing its WEBVTT header")
    cues = _vtt_cue_indexes(lines)
    if transcript.speaker_turns and not cues:
        raise ValueError("canonical VTT contains no cues")
    for timing_index, payload_indexes in cues:
        timing = VTT_TIMING_RE.fullmatch(_line_content(lines[timing_index]))
        if timing is None:
            raise ValueError("canonical VTT contains an invalid timing line")
        _render_subtitle_payload(
            lines,
            payload_indexes,
            transcript,
            labels,
            cluster_labels,
            start=_timestamp_seconds(timing, prefix="s"),
        )
    return "".join(lines)


def render_identity_markdown(
    canonical_markdown: str,
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> str:
    labels = effective_display_labels(transcript, decisions)
    lines = canonical_markdown.splitlines(keepends=True)
    label_indexes = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(r"\*\*(.+?)\*\*\s{0,2}", _line_content(line))
    ]
    if len(label_indexes) != len(transcript.speaker_turns):
        raise ValueError("canonical Markdown turn count does not match transcript.json")
    for turn_index, line_index in enumerate(label_indexes):
        ending = lines[line_index][len(_line_content(lines[line_index])) :]
        lines[line_index] = f"**{_markdown_escape(labels[turn_index])}**  {ending}"
    return "".join(lines)


def render_identity_text(
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> str:
    labels = effective_display_labels(transcript, decisions)
    return "\n\n".join(
        f"{labels[index]}: {turn.text}" for index, turn in enumerate(transcript.speaker_turns)
    ) + ("\n" if transcript.speaker_turns else "")


def render_identity_segments_csv(
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> str:
    labels = effective_display_labels(transcript, decisions)
    by_cluster, by_turn = _indexed_decisions(transcript, decisions)
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
        decision = by_turn.get(index) or by_cluster.get(turn.speaker)
        if decision is None:  # Kept explicit if coverage validation changes later.
            raise ValueError("identity decisions do not cover every canonical turn")
        writer.writerow(
            {
                "turn_index": index,
                "decision_id": _csv_safe(decision.decision_id),
                "original_label": _csv_safe(decision.original_diarization_label or turn.speaker),
                "canonical_label": _csv_safe(decision.canonical_label or turn.speaker),
                "target_label": _csv_safe(decision.target_label or turn.speaker),
                "display_label": _csv_safe(labels[index]),
                "start": f"{turn.start:.3f}",
                "end": f"{turn.end:.3f}",
                "text": _csv_safe(turn.text),
            }
        )
    return output.getvalue()


def validate_srt_label_only(canonical_srt: str, identity_srt: str) -> tuple[bool, int]:
    canonical_lines = canonical_srt.splitlines()
    identity_lines = identity_srt.splitlines()
    canonical_blocks = _subtitle_block_indexes(canonical_lines)
    identity_blocks = _subtitle_block_indexes(identity_lines)
    if len(canonical_blocks) != len(identity_blocks):
        return False, len(canonical_blocks)
    for canonical_block, identity_block in zip(canonical_blocks, identity_blocks, strict=True):
        if len(canonical_block) < 3 or len(identity_block) < 3:
            raise ValueError("malformed SRT")
        canonical_cue = [canonical_lines[index] for index in canonical_block]
        identity_cue = [identity_lines[index] for index in identity_block]
        if canonical_cue[:2] != identity_cue[:2]:
            return False, len(canonical_blocks)
        if not _label_only_payload_matches(canonical_cue[2:], identity_cue[2:]):
            return False, len(canonical_blocks)
    return True, len(canonical_blocks)


def validate_vtt_label_only(canonical_vtt: str, identity_vtt: str) -> tuple[bool, int]:
    canonical_lines = canonical_vtt.splitlines()
    identity_lines = identity_vtt.splitlines()
    if not canonical_lines or canonical_lines[0] != "WEBVTT":
        raise ValueError("malformed VTT")
    if not identity_lines or identity_lines[0] != "WEBVTT":
        return False, 0
    canonical_cues = _vtt_cue_indexes(canonical_lines)
    identity_cues = _vtt_cue_indexes(identity_lines)
    if len(canonical_cues) != len(identity_cues):
        return False, len(canonical_cues)
    for canonical_cue, identity_cue in zip(canonical_cues, identity_cues, strict=True):
        canonical_timing, canonical_payload = canonical_cue
        identity_timing, identity_payload = identity_cue
        if canonical_lines[canonical_timing] != identity_lines[identity_timing]:
            return False, len(canonical_cues)
        if not _label_only_payload_matches(
            [canonical_lines[index] for index in canonical_payload],
            [identity_lines[index] for index in identity_payload],
        ):
            return False, len(canonical_cues)
    return True, len(canonical_cues)


def _label_only_payload_matches(
    canonical_lines: Sequence[str], identity_lines: Sequence[str]
) -> bool:
    if len(canonical_lines) != len(identity_lines):
        return False
    for canonical_line, identity_line in zip(canonical_lines, identity_lines, strict=True):
        if canonical_line == identity_line:
            continue
        if not _same_payload_after_label(canonical_line, identity_line):
            return False
    return True


def _same_payload_after_label(canonical_line: str, identity_line: str) -> bool:
    canonical_split = _verified_canonical_label_split(canonical_line)
    identity_split = _label_delimiter_split(identity_line)
    if canonical_split is None or identity_split is None:
        return False
    _, canonical_suffix = canonical_split
    _, identity_suffix = identity_split
    return canonical_suffix == identity_suffix


def _verified_canonical_label_split(line: str) -> tuple[str, str] | None:
    """Split only an Auralign-reserved canonical speaker-label prefix."""

    match = _RESERVED_SPEAKER_PREFIX_RE.match(line)
    if match is None:
        return None
    label = line[: match.end() - 1]
    suffix = line[match.end() :]
    if suffix and not suffix.startswith(" "):
        return None
    return label, suffix


def _label_delimiter_split(line: str) -> tuple[str, str] | None:
    """Return the one structural leading label split, never a payload colon."""

    label, delimiter, suffix = line.partition(":")
    if (
        delimiter
        and label
        and len(label) <= 80
        and (not suffix or suffix.startswith(" "))
        and not any(value in label for value in ("\r", "\n", "-->"))
    ):
        return label, suffix
    return None


def _subtitle_block_indexes(lines: Sequence[str]) -> list[list[int]]:
    """Return non-empty subtitle blocks without normalizing source newlines."""

    blocks: list[list[int]] = []
    current: list[int] = []
    for index, line in enumerate(lines):
        if _line_content(line):
            current.append(index)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _line_content(line: str) -> str:
    return line.rstrip("\r\n")


def _replace_label_line(line: str, original_label: str, display_label: str) -> str:
    content = _line_content(line)
    return _replace_label_prefix(content, original_label, display_label) + line[len(content) :]


def _indexed_decisions(
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> tuple[dict[str, IdentityDecision], dict[int, IdentityDecision]]:
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
        if decision.speaker != transcript.speaker_turns[decision.turn_index].speaker:
            raise ValueError("identity decision speaker does not match its canonical turn")
        by_turn[decision.turn_index] = decision
    return by_cluster, by_turn


def _turn_allows_global_alias(
    turn: SpeakerTurn,
    decision: IdentityDecision | None,
) -> bool:
    if turn.speaker.upper() in {"UNKNOWN", "UNATTRIBUTED"}:
        return False
    if decision is None:
        return True
    if decision.status in {ResolutionStatus.CONFLICT, ResolutionStatus.REJECTED}:
        return False
    canonical_label = decision.canonical_label or turn.speaker
    if canonical_label.upper() in {"UNKNOWN", "UNATTRIBUTED"}:
        return False
    return not _GLOBAL_ALIAS_BLOCKING_REASONS.intersection(decision.reason_codes)


def _safe_global_labels(
    transcript: Transcript,
    decisions: Sequence[IdentityDecision],
) -> Mapping[str, str]:
    by_cluster, _ = _indexed_decisions(transcript, decisions)
    return {
        speaker: display_label_for_decision(decision, speaker)
        for speaker, decision in by_cluster.items()
        if decision.status in {ResolutionStatus.MAPPED, ResolutionStatus.UNATTRIBUTED}
    }


def _render_subtitle_payload(
    lines: list[str],
    payload_indexes: Sequence[int],
    transcript: Transcript,
    labels: Mapping[int, str],
    cluster_labels: Mapping[str, str],
    *,
    start: float,
) -> None:
    if not payload_indexes:
        raise ValueError("canonical subtitle contains an empty cue")
    first_line = _line_content(lines[payload_indexes[0]])
    owner_speaker = _canonical_speaker_prefix(first_line, transcript)
    if owner_speaker is None:
        raise ValueError("canonical subtitle label prefix does not match transcript.json")
    turn_index = _turn_at_time(transcript, start, expected_speaker=owner_speaker)
    if turn_index is None:
        raise ValueError("canonical subtitle cue does not match a canonical turn")
    owner = transcript.speaker_turns[turn_index]
    for line_index in payload_indexes:
        content = _line_content(lines[line_index])
        canonical_speaker = _canonical_speaker_prefix(content, transcript)
        if canonical_speaker is None:
            if _RESERVED_SPEAKER_PREFIX_RE.match(content):
                raise ValueError("canonical subtitle contains an unmatched speaker label")
            continue
        if canonical_speaker == owner.speaker:
            display_label = labels[turn_index]
        elif canonical_speaker.upper() in {"UNKNOWN", "UNATTRIBUTED"}:
            display_label = canonical_speaker
        else:
            global_label = cluster_labels.get(canonical_speaker)
            if global_label is None:
                raise ValueError(
                    "cross-speaker subtitle label lacks a safe global identity mapping"
                )
            display_label = global_label
        lines[line_index] = _replace_label_line(lines[line_index], canonical_speaker, display_label)


def _vtt_cue_indexes(lines: Sequence[str]) -> list[tuple[int, list[int]]]:
    cues: list[tuple[int, list[int]]] = []
    index = 1
    while index < len(lines):
        if not _line_content(lines[index]):
            index += 1
            continue
        block: list[int] = []
        while index < len(lines) and _line_content(lines[index]):
            block.append(index)
            index += 1
        timing_positions = [
            position
            for position, line_index in enumerate(block)
            if "-->" in _line_content(lines[line_index])
        ]
        if len(timing_positions) != 1:
            raise ValueError("canonical VTT contains a malformed cue")
        timing_position = timing_positions[0]
        if timing_position > 1 or timing_position + 1 >= len(block):
            raise ValueError("canonical VTT contains a malformed cue")
        timing_index = block[timing_position]
        if VTT_TIMING_RE.fullmatch(_line_content(lines[timing_index])) is None:
            raise ValueError("canonical VTT contains an invalid timing line")
        cues.append((timing_index, block[timing_position + 1 :]))
    return cues


def _turn_at_time(
    transcript: Transcript,
    timestamp: float,
    *,
    expected_speaker: str | None = None,
) -> int | None:
    indexed_turns = list(enumerate(transcript.speaker_turns))
    expected = [
        (index, turn)
        for index, turn in indexed_turns
        if expected_speaker is not None and turn.speaker == expected_speaker
    ]
    timestamp_tick = timestamp_milliseconds(timestamp)

    # The exporter rounds starts to milliseconds. Resolve within that same
    # quantization domain and use the canonical label prefix when two turns
    # fall in the same rounding bucket.
    for index, turn in expected:
        if timestamp_milliseconds(turn.start) == timestamp_tick:
            return index
    for index, turn in expected:
        if turn.start <= timestamp < turn.end:
            return index
    for index, turn in expected:
        if turn.start - 0.001 <= timestamp <= turn.end + 0.001:
            return index

    if expected_speaker is not None:
        return None

    for index, turn in indexed_turns:
        if timestamp_milliseconds(turn.start) == timestamp_tick:
            return index
    for index, turn in indexed_turns:
        if turn.start <= timestamp < turn.end:
            return index
    for index, turn in indexed_turns:
        if turn.start - 0.001 <= timestamp <= turn.end + 0.001:
            return index
    if transcript.speaker_turns and abs(transcript.speaker_turns[-1].end - timestamp) <= 0.001:
        return len(transcript.speaker_turns) - 1
    return None


def _canonical_speaker_prefix(line: str, transcript: Transcript) -> str | None:
    speakers = sorted(
        {turn.speaker for turn in transcript.speaker_turns},
        key=len,
        reverse=True,
    )
    return next((speaker for speaker in speakers if line.startswith(f"{speaker}:")), None)


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
