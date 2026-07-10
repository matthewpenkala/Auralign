# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic, dependency-free diarization error rate (DER) scoring.

The fixed ``rttm_der.v1`` contract uses a zero-second collar, scores overlap,
chooses one global optimal one-to-one permutation of anonymous speaker labels,
and evaluates the union of reference and hypothesis speech regions.  Error is
measured in speaker-seconds and normalized by reference speaker-seconds::

    DER = (miss + false alarm + speaker confusion) / reference speaker time

RTTM inputs are intentionally restricted to one recording each.  The two files
may use different recording identifiers because benchmark reference and output
files commonly do so.  Present-but-malformed inputs raise
``RTTMValidationError``; unavailable files and zero reference speech are
represented explicitly by ``DERResult.status``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, TypedDict

DER_RESULT_SCHEMA_VERSION: Final = "rttm_der_result.v1"
MAX_RTTM_BYTES: Final = 16 * 1024 * 1024
MAX_RTTM_LINE_LENGTH: Final = 4_096
MAX_RTTM_SEGMENTS: Final = 100_000
MAX_RTTM_SPEAKERS: Final = 64

_DECIMAL_PATTERN: Final = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")


class DERStatus(StrEnum):
    """Outcome of a DER scoring request."""

    OK = "ok"
    MISSING_REFERENCE = "missing_reference"
    MISSING_HYPOTHESIS = "missing_hypothesis"
    NO_REFERENCE_SPEECH = "no_reference_speech"


class RTTMValidationError(ValueError):
    """A present RTTM input violates the strict single-recording contract."""

    def __init__(self, message: str, *, source: str, line_number: int | None = None) -> None:
        self.source = source
        self.line_number = line_number
        location = f"{source} RTTM"
        if line_number is not None:
            location += f" line {line_number}"
        super().__init__(f"{location}: {message}")


class DERContractMetadata(TypedDict):
    """Serializable public description of the scoring policy."""

    schema_version: str
    collar_seconds: float
    overlap_scored: bool
    evaluation_region: str
    speaker_mapping: str


class DERResultMetadata(TypedDict):
    """Privacy-minimal DER metadata suitable for benchmark reports."""

    schema_version: str
    status: str
    der: float | None
    missed_speaker_seconds: float
    false_alarm_speaker_seconds: float
    speaker_confusion_seconds: float
    reference_speaker_seconds: float
    scored_region_seconds: float
    reference_segment_count: int
    hypothesis_segment_count: int
    reference_speaker_count: int
    hypothesis_speaker_count: int
    contract: DERContractMetadata


@dataclass(frozen=True, slots=True)
class DERContract:
    """The immutable public scoring contract."""

    schema_version: str = "rttm_der.v1"
    collar_seconds: float = 0.0
    overlap_scored: bool = True
    evaluation_region: str = "union"
    speaker_mapping: str = "optimal_one_to_one"

    def to_metadata(self) -> DERContractMetadata:
        return {
            "schema_version": self.schema_version,
            "collar_seconds": self.collar_seconds,
            "overlap_scored": self.overlap_scored,
            "evaluation_region": self.evaluation_region,
            "speaker_mapping": self.speaker_mapping,
        }


DER_CONTRACT_V1: Final = DERContract()


@dataclass(frozen=True, slots=True)
class SpeakerMapping:
    """One positive-duration pair in the optimal anonymous-label mapping."""

    hypothesis_speaker: str
    reference_speaker: str
    correct_speaker_seconds: float


@dataclass(frozen=True, slots=True)
class DERResult:
    """A typed DER result with independently inspectable error components."""

    status: DERStatus
    der: float | None
    missed_speaker_seconds: float
    false_alarm_speaker_seconds: float
    speaker_confusion_seconds: float
    reference_speaker_seconds: float
    scored_region_seconds: float
    reference_segment_count: int
    hypothesis_segment_count: int
    reference_speaker_count: int
    hypothesis_speaker_count: int
    mapping: tuple[SpeakerMapping, ...] = ()
    schema_version: str = DER_RESULT_SCHEMA_VERSION
    contract: DERContract = DER_CONTRACT_V1

    @property
    def error_speaker_seconds(self) -> float:
        """Return the unnormalized total speaker error."""

        return math.fsum(
            (
                self.missed_speaker_seconds,
                self.false_alarm_speaker_seconds,
                self.speaker_confusion_seconds,
            )
        )

    def to_metadata(self) -> DERResultMetadata:
        """Serialize metric metadata without recording or speaker identifiers."""

        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "der": self.der,
            "missed_speaker_seconds": self.missed_speaker_seconds,
            "false_alarm_speaker_seconds": self.false_alarm_speaker_seconds,
            "speaker_confusion_seconds": self.speaker_confusion_seconds,
            "reference_speaker_seconds": self.reference_speaker_seconds,
            "scored_region_seconds": self.scored_region_seconds,
            "reference_segment_count": self.reference_segment_count,
            "hypothesis_segment_count": self.hypothesis_segment_count,
            "reference_speaker_count": self.reference_speaker_count,
            "hypothesis_speaker_count": self.hypothesis_speaker_count,
            "contract": self.contract.to_metadata(),
        }


@dataclass(frozen=True, slots=True)
class _RTTMSegment:
    uri: str
    channel: int
    start: float
    end: float
    speaker: str


@dataclass(frozen=True, slots=True)
class _RTTMDocument:
    segments: tuple[_RTTMSegment, ...]
    speakers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ScoreAccumulator:
    missed_speaker_seconds: float
    false_alarm_speaker_seconds: float
    coactive_speaker_seconds: float
    reference_speaker_seconds: float
    scored_region_seconds: float
    pair_overlap: dict[tuple[str, str], float]


def score_rttm(reference: Path | None, hypothesis: Path | None) -> DERResult:
    """Score two single-recording RTTM files under ``rttm_der.v1``.

    Missing or non-file paths produce a typed unavailable status.  Once both
    files are present, malformed input is rejected with ``RTTMValidationError``.
    An empty hypothesis is valid; an empty reference has no valid DER
    denominator and therefore produces ``NO_REFERENCE_SPEECH`` with ``der=None``.
    """

    if reference is None or not reference.is_file():
        return _unavailable_result(DERStatus.MISSING_REFERENCE)
    if hypothesis is None or not hypothesis.is_file():
        return _unavailable_result(DERStatus.MISSING_HYPOTHESIS)

    reference_document = _read_rttm(reference, source="reference")
    hypothesis_document = _read_rttm(hypothesis, source="hypothesis")
    accumulated = _accumulate(reference_document.segments, hypothesis_document.segments)
    mapping, correct_speaker_seconds = _optimal_mapping(
        reference_document.speakers,
        hypothesis_document.speakers,
        accumulated.pair_overlap,
    )
    speaker_confusion_seconds = max(
        0.0, accumulated.coactive_speaker_seconds - correct_speaker_seconds
    )

    if accumulated.reference_speaker_seconds == 0.0:
        return DERResult(
            status=DERStatus.NO_REFERENCE_SPEECH,
            der=None,
            missed_speaker_seconds=accumulated.missed_speaker_seconds,
            false_alarm_speaker_seconds=accumulated.false_alarm_speaker_seconds,
            speaker_confusion_seconds=speaker_confusion_seconds,
            reference_speaker_seconds=accumulated.reference_speaker_seconds,
            scored_region_seconds=accumulated.scored_region_seconds,
            reference_segment_count=len(reference_document.segments),
            hypothesis_segment_count=len(hypothesis_document.segments),
            reference_speaker_count=len(reference_document.speakers),
            hypothesis_speaker_count=len(hypothesis_document.speakers),
            mapping=mapping,
        )

    error = math.fsum(
        (
            accumulated.missed_speaker_seconds,
            accumulated.false_alarm_speaker_seconds,
            speaker_confusion_seconds,
        )
    )
    return DERResult(
        status=DERStatus.OK,
        der=error / accumulated.reference_speaker_seconds,
        missed_speaker_seconds=accumulated.missed_speaker_seconds,
        false_alarm_speaker_seconds=accumulated.false_alarm_speaker_seconds,
        speaker_confusion_seconds=speaker_confusion_seconds,
        reference_speaker_seconds=accumulated.reference_speaker_seconds,
        scored_region_seconds=accumulated.scored_region_seconds,
        reference_segment_count=len(reference_document.segments),
        hypothesis_segment_count=len(hypothesis_document.segments),
        reference_speaker_count=len(reference_document.speakers),
        hypothesis_speaker_count=len(hypothesis_document.speakers),
        mapping=mapping,
    )


def der_from_rttm(reference: Path | None, hypothesis: Path | None) -> float | None:
    """Compatibility wrapper returning only DER when it is well-defined.

    The historical ``None`` behavior for unavailable paths is preserved.  The
    wrapper also returns ``None`` for zero reference speech, where a numeric DER
    would be misleading.  Malformed present inputs are rejected.
    """

    return score_rttm(reference, hypothesis).der


def _unavailable_result(status: DERStatus) -> DERResult:
    return DERResult(
        status=status,
        der=None,
        missed_speaker_seconds=0.0,
        false_alarm_speaker_seconds=0.0,
        speaker_confusion_seconds=0.0,
        reference_speaker_seconds=0.0,
        scored_region_seconds=0.0,
        reference_segment_count=0,
        hypothesis_segment_count=0,
        reference_speaker_count=0,
        hypothesis_speaker_count=0,
    )


def _read_rttm(path: Path, *, source: str) -> _RTTMDocument:
    try:
        byte_count = path.stat().st_size
    except OSError as exc:
        raise RTTMValidationError("could not be inspected", source=source) from exc
    if byte_count > MAX_RTTM_BYTES:
        raise RTTMValidationError(f"exceeds the {MAX_RTTM_BYTES}-byte safety limit", source=source)

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RTTMValidationError("must be readable UTF-8 text", source=source) from exc

    segments: list[_RTTMSegment] = []
    speakers: set[str] = set()
    recording_uri: str | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        if len(line) > MAX_RTTM_LINE_LENGTH:
            raise RTTMValidationError(
                f"exceeds the {MAX_RTTM_LINE_LENGTH}-character line limit",
                source=source,
                line_number=line_number,
            )
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 10:
            raise RTTMValidationError(
                "must contain exactly 10 whitespace-delimited fields",
                source=source,
                line_number=line_number,
            )
        record_type, uri, channel_text, start_text, duration_text = parts[:5]
        speaker = parts[7]
        if record_type != "SPEAKER":
            raise RTTMValidationError(
                "only SPEAKER records are supported",
                source=source,
                line_number=line_number,
            )
        if not uri or uri == "<NA>":
            raise RTTMValidationError(
                "recording identifier must not be <NA>",
                source=source,
                line_number=line_number,
            )
        if recording_uri is None:
            recording_uri = uri
        elif uri != recording_uri:
            raise RTTMValidationError(
                "multiple recording identifiers are not supported",
                source=source,
                line_number=line_number,
            )
        if not channel_text.isascii() or not channel_text.isdecimal() or int(channel_text) <= 0:
            raise RTTMValidationError(
                "channel must be a positive integer",
                source=source,
                line_number=line_number,
            )
        channel = int(channel_text)
        start = _parse_finite_decimal(start_text, source=source, line_number=line_number)
        duration = _parse_finite_decimal(duration_text, source=source, line_number=line_number)
        if start < 0.0:
            raise RTTMValidationError(
                "start time must be non-negative", source=source, line_number=line_number
            )
        if duration <= 0.0:
            raise RTTMValidationError(
                "duration must be greater than zero", source=source, line_number=line_number
            )
        end = start + duration
        if not math.isfinite(end):
            raise RTTMValidationError(
                "segment end must be finite", source=source, line_number=line_number
            )
        if not speaker or speaker == "<NA>":
            raise RTTMValidationError(
                "speaker identifier must not be <NA>",
                source=source,
                line_number=line_number,
            )

        segments.append(
            _RTTMSegment(uri=uri, channel=channel, start=start, end=end, speaker=speaker)
        )
        if len(segments) > MAX_RTTM_SEGMENTS:
            raise RTTMValidationError(
                f"exceeds the {MAX_RTTM_SEGMENTS}-segment safety limit", source=source
            )
        speakers.add(speaker)
        if len(speakers) > MAX_RTTM_SPEAKERS:
            raise RTTMValidationError(
                f"exceeds the {MAX_RTTM_SPEAKERS}-speaker safety limit", source=source
            )

    segments.sort(key=lambda item: (item.start, item.end, item.speaker, item.channel))
    return _RTTMDocument(segments=tuple(segments), speakers=tuple(sorted(speakers)))


def _parse_finite_decimal(value: str, *, source: str, line_number: int) -> float:
    if _DECIMAL_PATTERN.fullmatch(value) is None:
        raise RTTMValidationError(
            "start and duration must be finite decimal numbers",
            source=source,
            line_number=line_number,
        )
    parsed = float(value)
    if not math.isfinite(parsed):
        raise RTTMValidationError(
            "start and duration must be finite decimal numbers",
            source=source,
            line_number=line_number,
        )
    return parsed


def _accumulate(
    reference: tuple[_RTTMSegment, ...], hypothesis: tuple[_RTTMSegment, ...]
) -> _ScoreAccumulator:
    events: dict[float, list[tuple[bool, str, int]]] = {}
    for is_reference, segments in ((True, reference), (False, hypothesis)):
        for segment in segments:
            events.setdefault(segment.start, []).append((is_reference, segment.speaker, 1))
            events.setdefault(segment.end, []).append((is_reference, segment.speaker, -1))

    active_reference: dict[str, int] = {}
    active_hypothesis: dict[str, int] = {}
    missed = 0.0
    false_alarm = 0.0
    coactive = 0.0
    reference_time = 0.0
    scored_region = 0.0
    pair_overlap: dict[tuple[str, str], float] = {}
    previous: float | None = None

    for boundary in sorted(events):
        if previous is not None and boundary > previous:
            duration = boundary - previous
            reference_count = len(active_reference)
            hypothesis_count = len(active_hypothesis)
            if reference_count or hypothesis_count:
                scored_region += duration
            reference_time += duration * reference_count
            missed += duration * max(reference_count - hypothesis_count, 0)
            false_alarm += duration * max(hypothesis_count - reference_count, 0)
            coactive += duration * min(reference_count, hypothesis_count)
            if active_reference and active_hypothesis:
                for hypothesis_speaker in sorted(active_hypothesis):
                    for reference_speaker in sorted(active_reference):
                        pair = (hypothesis_speaker, reference_speaker)
                        pair_overlap[pair] = pair_overlap.get(pair, 0.0) + duration

        for is_reference, speaker, delta in events[boundary]:
            active = active_reference if is_reference else active_hypothesis
            updated = active.get(speaker, 0) + delta
            if updated:
                active[speaker] = updated
            else:
                active.pop(speaker, None)
        previous = boundary

    return _ScoreAccumulator(
        missed_speaker_seconds=missed,
        false_alarm_speaker_seconds=false_alarm,
        coactive_speaker_seconds=coactive,
        reference_speaker_seconds=reference_time,
        scored_region_seconds=scored_region,
        pair_overlap=pair_overlap,
    )


def _optimal_mapping(
    reference_speakers: tuple[str, ...],
    hypothesis_speakers: tuple[str, ...],
    pair_overlap: dict[tuple[str, str], float],
) -> tuple[tuple[SpeakerMapping, ...], float]:
    if not reference_speakers or not hypothesis_speakers:
        return (), 0.0

    size = max(len(reference_speakers), len(hypothesis_speakers))
    weights = [[0.0 for _ in range(size)] for _ in range(size)]
    for row, hypothesis_speaker in enumerate(hypothesis_speakers):
        for column, reference_speaker in enumerate(reference_speakers):
            weights[row][column] = pair_overlap.get((hypothesis_speaker, reference_speaker), 0.0)

    assignment = _maximum_weight_assignment(weights)
    mappings: list[SpeakerMapping] = []
    correct_weights: list[float] = []
    for row, column in enumerate(assignment):
        if row >= len(hypothesis_speakers) or column >= len(reference_speakers):
            continue
        weight = weights[row][column]
        if weight <= 0.0:
            continue
        mappings.append(
            SpeakerMapping(
                hypothesis_speaker=hypothesis_speakers[row],
                reference_speaker=reference_speakers[column],
                correct_speaker_seconds=weight,
            )
        )
        correct_weights.append(weight)
    return tuple(mappings), math.fsum(correct_weights)


def _maximum_weight_assignment(weights: list[list[float]]) -> list[int]:
    """Return an exact deterministic maximum-weight square assignment.

    This is the O(n^3) Hungarian algorithm.  Rows and columns are already in
    sorted speaker-label order, and strict comparisons retain the first
    (lowest-index) option when objective values tie.
    """

    size = len(weights)
    if size == 0:
        return []
    if any(len(row) != size for row in weights):
        raise ValueError("assignment matrix must be square")
    maximum = max(max(row) for row in weights)
    costs = [[maximum - value for value in row] for row in weights]

    row_potential = [0.0] * (size + 1)
    column_potential = [0.0] * (size + 1)
    matched_row = [0] * (size + 1)
    predecessor = [0] * (size + 1)

    for row in range(1, size + 1):
        matched_row[0] = row
        column = 0
        minimum = [math.inf] * (size + 1)
        used = [False] * (size + 1)
        while True:
            used[column] = True
            current_row = matched_row[column]
            delta = math.inf
            next_column = 0
            for candidate_column in range(1, size + 1):
                if used[candidate_column]:
                    continue
                reduced_cost = (
                    costs[current_row - 1][candidate_column - 1]
                    - row_potential[current_row]
                    - column_potential[candidate_column]
                )
                if reduced_cost < minimum[candidate_column]:
                    minimum[candidate_column] = reduced_cost
                    predecessor[candidate_column] = column
                if minimum[candidate_column] < delta:
                    delta = minimum[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(size + 1):
                if used[candidate_column]:
                    row_potential[matched_row[candidate_column]] += delta
                    column_potential[candidate_column] -= delta
                else:
                    minimum[candidate_column] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            previous_column = predecessor[column]
            matched_row[column] = matched_row[previous_column]
            column = previous_column
            if column == 0:
                break

    assignment = [0] * size
    for column in range(1, size + 1):
        assignment[matched_row[column] - 1] = column - 1
    return assignment
