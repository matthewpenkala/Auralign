# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic local evidence for one window candidate.

Every number here is derived from artifacts the job already produced locally.
Model probabilities and beam scores are uncalibrated: they may rank candidates,
but they are never reported as semantic confidence.
"""

from __future__ import annotations

import hashlib
import math
from typing import Literal, NamedTuple

from pydantic import Field

from local_transcript_max.schemas import AlignedWord, ASRResult, LTMBaseModel

from .policy import WINDOW_BOUNDS_TOLERANCE_SECONDS, normalized_tokens, repetition_ratio

EvidenceStatus = Literal["generated", "failed"]


class CandidateEvidence(LTMBaseModel):
    """Privacy-safe metrics binding one decoded window candidate."""

    role: Literal["control", "wide"]
    beam_size: int = Field(ge=1)
    status: EvidenceStatus
    failure_reason: str | None = None
    text_sha256: str
    normalized_text_sha256: str
    segment_count: int = Field(ge=0)
    word_count: int = Field(ge=0)
    character_count: int = Field(ge=0)
    language: str | None = None
    avg_logprob: float | None = None
    compression_ratio: float | None = None
    no_speech_prob: float | None = None
    repetition_ratio: float | None = None
    alignment_coverage: float | None = None
    alignment_word_count: int | None = None
    alignment_failure_reason: str | None = None
    timestamps_present: bool = False
    timestamps_valid: bool = False
    timestamps_monotonic: bool = False
    timestamps_within_window: bool = False
    covered_seconds: float | None = None
    words_per_second: float | None = None
    window_duration_seconds: float = Field(ge=0.0)
    backend_warning_count: int = Field(default=0, ge=0)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def candidate_text(result: ASRResult | None) -> str:
    """Return the deterministic single-line surface for a window candidate."""

    if result is None:
        return ""
    return " ".join(segment.text.strip() for segment in result.segments if segment.text.strip())


def failed_evidence(
    *,
    role: Literal["control", "wide"],
    beam_size: int,
    failure_reason: str,
    window_duration_seconds: float,
) -> CandidateEvidence:
    """Return the fail-closed evidence record for an unusable candidate."""

    return CandidateEvidence(
        role=role,
        beam_size=beam_size,
        status="failed",
        failure_reason=failure_reason,
        text_sha256=sha256_text(""),
        normalized_text_sha256=sha256_text(""),
        segment_count=0,
        word_count=0,
        character_count=0,
        window_duration_seconds=round(window_duration_seconds, 6),
    )


def candidate_evidence(
    result: ASRResult,
    *,
    role: Literal["control", "wide"],
    beam_size: int,
    window_duration_seconds: float,
    alignment_words: list[AlignedWord] | None = None,
    alignment_failure_reason: str | None = None,
) -> CandidateEvidence:
    """Compute every comparable metric for one decoded window candidate."""

    text = candidate_text(result)
    tokens = [token for token in normalized_tokens(text) if token]
    timing = _timing_evidence(result, window_duration_seconds=window_duration_seconds)
    coverage, aligned_count = _alignment_evidence(alignment_words, expected_tokens=len(tokens))
    covered = timing.covered_seconds
    return CandidateEvidence(
        role=role,
        beam_size=beam_size,
        status="generated",
        failure_reason=None,
        text_sha256=sha256_text(text),
        normalized_text_sha256=sha256_text(" ".join(tokens)),
        segment_count=len(result.segments),
        word_count=len(tokens),
        character_count=len(text),
        language=result.language,
        avg_logprob=_duration_weighted_logprob(result),
        compression_ratio=_worst(
            [segment.compression_ratio for segment in result.segments],
        ),
        no_speech_prob=_worst([segment.no_speech_prob for segment in result.segments]),
        repetition_ratio=round(repetition_ratio(text), 6),
        alignment_coverage=coverage,
        alignment_word_count=aligned_count,
        alignment_failure_reason=alignment_failure_reason,
        timestamps_present=timing.present,
        timestamps_valid=timing.valid,
        timestamps_monotonic=timing.monotonic,
        timestamps_within_window=timing.within_window,
        covered_seconds=covered,
        words_per_second=(
            round(len(tokens) / covered, 6) if covered is not None and covered > 0 else None
        ),
        window_duration_seconds=round(window_duration_seconds, 6),
        backend_warning_count=len(result.warnings),
    )


class _TimingEvidence(NamedTuple):
    present: bool
    valid: bool
    monotonic: bool
    within_window: bool
    covered_seconds: float | None


def _timing_evidence(result: ASRResult, *, window_duration_seconds: float) -> _TimingEvidence:
    if not result.segments:
        return _TimingEvidence(
            present=False,
            valid=False,
            monotonic=False,
            within_window=False,
            covered_seconds=None,
        )
    present = True
    valid = True
    monotonic = True
    within_window = True
    previous_end = -math.inf
    covered = 0.0
    upper_bound = window_duration_seconds + WINDOW_BOUNDS_TOLERANCE_SECONDS
    for segment in result.segments:
        start = segment.start
        end = segment.end
        if not math.isfinite(start) or not math.isfinite(end):
            present = False
            valid = False
            continue
        if end <= start or start < 0:
            valid = False
        if start < previous_end - WINDOW_BOUNDS_TOLERANCE_SECONDS:
            monotonic = False
        if start < -WINDOW_BOUNDS_TOLERANCE_SECONDS or end > upper_bound:
            within_window = False
        covered += max(0.0, end - start)
        previous_end = max(previous_end, end)
    return _TimingEvidence(
        present=present,
        valid=valid,
        monotonic=monotonic,
        within_window=within_window,
        covered_seconds=round(covered, 6),
    )


def _alignment_evidence(
    alignment_words: list[AlignedWord] | None,
    *,
    expected_tokens: int,
) -> tuple[float | None, int | None]:
    """Return forced-alignment coverage as supporting — never decisive — evidence."""

    if alignment_words is None:
        return None, None
    usable = sum(
        1
        for word in alignment_words
        if word.text.strip()
        and word.start is not None
        and word.end is not None
        and math.isfinite(word.start)
        and math.isfinite(word.end)
        and word.end > word.start
        and word.alignment_source != "missing"
    )
    if expected_tokens <= 0:
        # No comparable tokens means no coverage evidence, which fails the
        # coverage gate and preserves the first pass.
        return 0.0, usable
    return round(min(1.0, usable / expected_tokens), 6), usable


def _duration_weighted_logprob(result: ASRResult) -> float | None:
    """Average segment log-probability weighted by segment duration.

    Whisper reports one average per segment; weighting by duration keeps a long
    confident segment from being outvoted by a very short one.
    """

    total_weight = 0.0
    total = 0.0
    for segment in result.segments:
        value = segment.avg_logprob
        if value is None or not math.isfinite(value):
            continue
        weight = max(0.0, segment.end - segment.start)
        if weight <= 0:
            weight = 1.0
        total += value * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return round(total / total_weight, 6)


def _worst(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None and math.isfinite(value)]
    if not present:
        return None
    return round(max(present), 6)
