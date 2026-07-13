# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic speaker/diarization artifact validation."""

from __future__ import annotations

import json
import os
import stat
import statistics
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from local_transcript_max.errors import LTMError
from local_transcript_max.platform_security import UnsafePathError, open_regular_nofollow
from local_transcript_max.schemas import AlignedWord, DiarizationTurn, Transcript
from local_transcript_max.utils import write_json

MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
MAX_RTTM_BYTES = 16 * 1024 * 1024
MAX_WORDS_JSONL_BYTES = 64 * 1024 * 1024
MAX_VALIDATION_RECORDS = 250_000
MAX_OVERLAP_SPANS = 10_000


class OverlapSpan(BaseModel):
    start: float
    end: float
    speakers: list[str]


class SpeakerValidationReport(BaseModel):
    expected_speakers: int
    unique_speakers: int
    speaker_count_mismatch: bool = False
    speaker_labels: list[str]
    speaker_turn_count: int
    median_turn_duration: float | None
    speaker_flapping_score: float
    percent_words_assigned_to_speakers: float
    percent_words_marked_uncertain: float
    overlap_spans: list[OverlapSpan] = Field(default_factory=list)
    suspected_failure_modes: list[str] = Field(default_factory=list)
    suggested_remediation_commands: list[str] = Field(default_factory=list)


def validate_speaker_artifacts(job_dir: Path, expected_speakers: int) -> SpeakerValidationReport:
    transcript = load_transcript(job_dir)
    turns = transcript.diarization_turns or load_rttm(job_dir / "transcript.rttm")
    words = transcript.words or load_words_jsonl(job_dir / "transcript.words.jsonl")
    for label in (
        [turn.speaker for turn in turns]
        + [word.speaker for word in words if word.speaker is not None]
    ):
        if len(label) > 128 or any(ord(character) < 32 for character in label):
            raise LTMError("Speaker validation encountered an unsafe speaker label.")
    speakers = sorted({turn.speaker for turn in turns if turn.speaker})
    durations = [max(0.0, turn.end - turn.start) for turn in turns]
    assigned_words = [word for word in words if word.speaker]
    uncertain_words = [word for word in words if word.uncertain_speaker]
    report = SpeakerValidationReport(
        expected_speakers=expected_speakers,
        unique_speakers=len(speakers),
        speaker_count_mismatch=len(speakers) != expected_speakers,
        speaker_labels=speakers,
        speaker_turn_count=len(turns),
        median_turn_duration=statistics.median(durations) if durations else None,
        speaker_flapping_score=flapping_score(turns),
        percent_words_assigned_to_speakers=percent(len(assigned_words), len(words)),
        percent_words_marked_uncertain=percent(len(uncertain_words), len(words)),
        overlap_spans=find_overlap_spans(turns),
    )
    report.suspected_failure_modes.extend(failure_modes(report))
    report.suggested_remediation_commands.extend(remediation_commands(report))
    write_json(job_dir / "speaker_validation_report.json", report.model_dump(mode="json"))
    return report


def load_transcript(job_dir: Path) -> Transcript:
    path = job_dir / "transcript.diarized.json"
    payload_bytes = _read_regular_bytes(path, max_bytes=MAX_TRANSCRIPT_BYTES)
    if payload_bytes is None:
        return Transcript(input_path=str(job_dir))
    try:
        payload: dict[str, Any] = json.loads(payload_bytes)
        transcript = Transcript.model_validate(payload)
        embedded_counts = (
            len(transcript.segments),
            len(transcript.words),
            len(transcript.diarization_turns),
            len(transcript.exclusive_diarization_turns),
            len(transcript.speaker_turns),
        )
        if any(count > MAX_VALIDATION_RECORDS for count in embedded_counts):
            raise ValueError("record limit exceeded")
        return transcript
    except Exception as exc:
        raise LTMError("Speaker validation transcript artifact is malformed.") from exc


def load_words_jsonl(path: Path) -> list[AlignedWord]:
    payload = _read_regular_bytes(path, max_bytes=MAX_WORDS_JSONL_BYTES)
    if payload is None:
        return []
    words: list[AlignedWord] = []
    try:
        for line in payload.splitlines():
            if line.strip():
                if len(words) >= MAX_VALIDATION_RECORDS:
                    raise ValueError("record limit exceeded")
                words.append(AlignedWord.model_validate(json.loads(line)))
    except Exception as exc:
        raise LTMError("Speaker validation word artifact is malformed.") from exc
    return words


def load_rttm(path: Path) -> list[DiarizationTurn]:
    payload = _read_regular_bytes(path, max_bytes=MAX_RTTM_BYTES)
    if payload is None:
        return []
    turns: list[DiarizationTurn] = []
    try:
        for line in payload.decode("utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 8 and parts[0] == "SPEAKER":
                if len(turns) >= MAX_VALIDATION_RECORDS:
                    raise ValueError("record limit exceeded")
                start = float(parts[3])
                duration = float(parts[4])
                turns.append(
                    DiarizationTurn(
                        start=start,
                        end=start + duration,
                        speaker=parts[7],
                        source="rttm",
                    )
                )
    except Exception as exc:
        raise LTMError("Speaker validation RTTM artifact is malformed.") from exc
    return sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))


def _read_regular_bytes(path: Path, *, max_bytes: int) -> bytes | None:
    """Read a bounded regular artifact without following the final symlink."""
    try:
        descriptor = open_regular_nofollow(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except (OSError, UnsafePathError) as exc:
        raise LTMError("Speaker validation could not safely open an artifact.") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise LTMError("Speaker validation artifacts must be regular files.")
        if metadata.st_size > max_bytes:
            raise LTMError("Speaker validation artifact exceeds its size limit.")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise LTMError("Speaker validation artifact exceeds its size limit.")
        return payload
    finally:
        os.close(descriptor)


def flapping_score(turns: list[DiarizationTurn]) -> float:
    if len(turns) < 2:
        return 0.0
    rapid_switches = 0
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        gap = max(0.0, current.start - previous.end)
        if previous.speaker != current.speaker and gap <= 1.0 and current.end - current.start < 2.0:
            rapid_switches += 1
    return rapid_switches / (len(ordered) - 1)


def find_overlap_spans(turns: list[DiarizationTurn]) -> list[OverlapSpan]:
    spans: list[OverlapSpan] = []
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if right.start >= left.end:
                break
            start = max(left.start, right.start)
            end = min(left.end, right.end)
            if end > start:
                if len(spans) >= MAX_OVERLAP_SPANS:
                    raise LTMError("Speaker validation overlap span limit was exceeded.")
                spans.append(
                    OverlapSpan(
                        start=start,
                        end=end,
                        speakers=sorted({left.speaker, right.speaker}),
                    )
                )
    return spans


def failure_modes(report: SpeakerValidationReport) -> list[str]:
    modes: list[str] = []
    if report.unique_speakers != report.expected_speakers:
        modes.append(
            f"expected {report.expected_speakers} speakers but found {report.unique_speakers}"
        )
    if report.speaker_turn_count == 0:
        modes.append("no diarization turns found")
    if report.median_turn_duration is not None and report.median_turn_duration < 0.75:
        modes.append("median speaker turn duration is very short")
    if report.speaker_flapping_score > 0.35:
        modes.append("high rapid-speaker-switching score")
    if report.percent_words_assigned_to_speakers < 90.0:
        modes.append("low percentage of words assigned to speakers")
    if report.percent_words_marked_uncertain > 20.0:
        modes.append("high percentage of uncertain word-speaker assignments")
    if report.overlap_spans:
        modes.append("overlapping diarization turns detected")
    return modes


def remediation_commands(report: SpeakerValidationReport) -> list[str]:
    commands: list[str] = []
    if report.speaker_count_mismatch:
        commands.append(
            "ltm transcribe INPUT --num-speakers "
            f"{report.expected_speakers} --out OUT --debug-report"
        )
        commands.append(
            "ltm transcribe INPUT --min-speakers "
            f"{report.expected_speakers} --max-speakers {report.expected_speakers} --out OUT"
        )
    if report.speaker_flapping_score > 0.35:
        commands.append("ltm transcribe INPUT --diarization-backend pyannote --out OUT")
    if report.percent_words_assigned_to_speakers < 90.0:
        commands.append("ltm inspect JOB_DIR")
    return commands


def percent(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(100.0 * numerator / denominator, 3)


def render_validation_report(report: SpeakerValidationReport) -> str:
    lines = [
        f"unique_speakers={report.unique_speakers}",
        f"expected_speakers={report.expected_speakers}",
        f"speaker_count_mismatch={report.speaker_count_mismatch}",
        f"speaker_turn_count={report.speaker_turn_count}",
        f"median_turn_duration={report.median_turn_duration}",
        f"speaker_flapping_score={report.speaker_flapping_score:.3f}",
        f"percent_words_assigned_to_speakers={report.percent_words_assigned_to_speakers:.3f}",
        f"percent_words_marked_uncertain={report.percent_words_marked_uncertain:.3f}",
        f"overlap_spans={len(report.overlap_spans)}",
    ]
    if report.suspected_failure_modes:
        lines.append("suspected_failure_modes:")
        lines.extend(f"- {mode}" for mode in report.suspected_failure_modes)
    else:
        lines.append("suspected_failure_modes: none")
    if report.suggested_remediation_commands:
        lines.append("suggested_remediation_commands:")
        lines.extend(f"- {command}" for command in report.suggested_remediation_commands)
    return "\n".join(lines)
