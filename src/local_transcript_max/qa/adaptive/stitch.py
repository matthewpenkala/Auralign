# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Bounded replacement of first-pass segments by a promoted window candidate.

The first-pass result is never mutated. This builds a new derived result and
fails closed on anything that would duplicate, drop, reorder, or relocate text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from local_transcript_max.errors import LTMError
from local_transcript_max.schemas import AlignedWord, ASRResult, ASRSegment

from .policy import WINDOW_BOUNDS_TOLERANCE_SECONDS, normalized_tokens


@dataclass(frozen=True)
class SpanPromotion:
    """One authorized, bounded replacement of contiguous first-pass segments."""

    span_index: int
    replaced_segment_ids: tuple[int, ...]
    replace_start: float
    replace_end: float
    window_offset_seconds: float
    candidate: ASRResult


def clipped_candidate_segments(promotion: SpanPromotion) -> list[ASRSegment]:
    """Return candidate segments whose midpoint falls inside the authorized span.

    Midpoint containment is what keeps padded context out of the replacement:
    a segment that mostly lives in the neighbouring, unchanged transcript is
    dropped instead of being duplicated across the boundary.
    """

    offset = promotion.window_offset_seconds
    kept: list[ASRSegment] = []
    for segment in promotion.candidate.segments:
        start = segment.start + offset
        end = segment.end + offset
        if not (math.isfinite(start) and math.isfinite(end)) or end <= start:
            raise LTMError(
                f"Promoted candidate for span {promotion.span_index} has an invalid timestamp."
            )
        midpoint = (start + end) / 2
        if midpoint < promotion.replace_start or midpoint > promotion.replace_end:
            continue
        clamped_start = min(max(start, promotion.replace_start), promotion.replace_end)
        clamped_end = min(max(end, clamped_start), promotion.replace_end)
        if clamped_end <= clamped_start:
            continue
        kept.append(
            segment.model_copy(
                update={
                    "start": round(clamped_start, 6),
                    "end": round(clamped_end, 6),
                    "words": _shift_words(segment.words, offset, clamped_start, clamped_end),
                }
            )
        )
    return kept


def _shift_words(
    words: list[AlignedWord],
    offset: float,
    lower: float,
    upper: float,
) -> list[AlignedWord]:
    shifted: list[AlignedWord] = []
    for word in words:
        start = None if word.start is None else min(max(word.start + offset, lower), upper)
        end = None if word.end is None else min(max(word.end + offset, lower), upper)
        shifted.append(
            word.model_copy(
                update={
                    "start": None if start is None else round(start, 6),
                    "end": None if end is None else round(end, 6),
                }
            )
        )
    return shifted


def apply_selected_spans(
    first_pass: ASRResult,
    promotions: list[SpanPromotion],
) -> ASRResult:
    """Return a derived ASR result with every promoted span applied exactly once."""

    if not promotions:
        raise LTMError("Adaptive application requires at least one promoted span.")
    _reject_overlapping_promotions(promotions)
    by_first_segment = {
        promotion.replaced_segment_ids[0]: promotion for promotion in promotions
    }
    consumed = {
        segment_id for promotion in promotions for segment_id in promotion.replaced_segment_ids
    }
    known_ids = {segment.id for segment in first_pass.segments}
    unknown = sorted(consumed - known_ids)
    if unknown:
        raise LTMError(f"Adaptive plan references unknown first-pass segment ids: {unknown}.")

    segments: list[ASRSegment] = []
    for segment in first_pass.segments:
        promotion = by_first_segment.get(segment.id)
        if promotion is not None:
            replacement = clipped_candidate_segments(promotion)
            if not replacement:
                raise LTMError(
                    f"Promoted candidate for span {promotion.span_index} produced no text "
                    "inside its authorized source span."
                )
            segments.extend(replacement)
            continue
        if segment.id in consumed:
            continue
        segments.append(segment)

    renumbered = [
        segment.model_copy(update={"id": index}) for index, segment in enumerate(segments)
    ]
    _validate_derived_segments(renumbered)
    _reject_boundary_duplication(first_pass, renumbered)
    return first_pass.model_copy(
        update={
            "segments": renumbered,
            "warnings": sorted({*first_pass.warnings, "adaptive_redecode_applied"}),
        }
    )


def _reject_overlapping_promotions(promotions: list[SpanPromotion]) -> None:
    seen_segments: set[int] = set()
    for promotion in promotions:
        if not promotion.replaced_segment_ids:
            raise LTMError(
                f"Promotion for span {promotion.span_index} names no source segment."
            )
        if promotion.replace_end <= promotion.replace_start:
            raise LTMError(f"Promotion for span {promotion.span_index} has an empty source span.")
        overlap = seen_segments & set(promotion.replaced_segment_ids)
        if overlap:
            raise LTMError(
                "Adaptive promotions overlap on first-pass segments "
                f"{sorted(overlap)}; refusing partial or duplicated application."
            )
        seen_segments.update(promotion.replaced_segment_ids)
    ordered = sorted(promotions, key=lambda item: item.replace_start)
    previous_end = -math.inf
    for promotion in ordered:
        if promotion.replace_start < previous_end:
            raise LTMError("Adaptive promotions overlap in time; refusing to apply.")
        previous_end = promotion.replace_end


def _validate_derived_segments(segments: list[ASRSegment]) -> None:
    if not segments:
        raise LTMError("Adaptive application produced an empty transcript.")
    previous_end = -math.inf
    for segment in segments:
        if not (math.isfinite(segment.start) and math.isfinite(segment.end)):
            raise LTMError("Adaptive application produced a non-finite timestamp.")
        if segment.end <= segment.start or segment.start < 0:
            raise LTMError("Adaptive application produced an invalid segment timestamp.")
        if segment.start < previous_end - WINDOW_BOUNDS_TOLERANCE_SECONDS:
            raise LTMError("Adaptive application produced non-monotonic segment timestamps.")
        previous_end = max(previous_end, segment.end)


def _reject_boundary_duplication(first_pass: ASRResult, derived: list[ASRSegment]) -> None:
    """Reject adjacent repeats that neither the first pass nor the candidate had."""

    derived_pairs = _adjacent_pairs([segment.text for segment in derived])
    source_pairs = _adjacent_pairs([segment.text for segment in first_pass.segments])
    introduced = derived_pairs - source_pairs
    if introduced:
        # The offending tokens are transcript content, so the message reports
        # only how many adjacent repeats appeared. The candidate surfaces stay
        # in the sensitive candidates artifact.
        raise LTMError(
            "Adaptive stitching introduced duplicated words at a span boundary "
            f"({len(introduced)} adjacent repeat pairs); refusing to apply."
        )


def _adjacent_pairs(texts: list[str]) -> set[tuple[str, str]]:
    tokens = [token for text in texts for token in normalized_tokens(text) if token]
    return {
        (tokens[index - 1], tokens[index])
        for index in range(1, len(tokens))
        if tokens[index] == tokens[index - 1]
    }
