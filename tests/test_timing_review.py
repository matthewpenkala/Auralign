# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from local_transcript_max.diagnostics import timestamp_displacement_payload
from local_transcript_max.schemas import AlignedWord


def test_timing_review_locates_large_shift_and_preserves_score_provenance() -> None:
    raw = [AlignedWord(text="private", start=149.52, end=149.90, confidence=0.999)]
    published = [AlignedWord(text="private", start=140.309, end=143.853,
                             confidence=0.854, alignment_source="forced")]
    before = published[0].model_dump()
    report = timestamp_displacement_payload(raw, published)
    cue = report["review_spans"][0]
    assert cue["word_index"] == 0
    assert cue["start_delta_seconds"] == -9.211
    assert cue["reason_codes"] == ["large_timestamp_displacement", "long_word_duration"]
    assert cue["asr_confidence"] == 0.999
    assert cue["published_confidence"] == 0.854
    assert cue["published_confidence_kind"] == "ctc_alignment_score"
    assert "private" not in str(report)
    assert published[0].model_dump() == before


def test_timing_review_is_bounded_and_reports_truncation() -> None:
    raw = [AlignedWord(text="word", start=3 * i, end=3 * i + 0.5) for i in range(102)]
    published = [word.model_copy(update={"end": word.start + 2.5}) for word in raw]
    report = timestamp_displacement_payload(raw, published)
    assert report["review_span_count"] == 102
    assert len(report["review_spans"]) == 100
    assert report["review_spans_truncated"] is True


def test_timing_review_requires_lexical_identity_and_finite_endpoints() -> None:
    raw = [AlignedWord(text="one", start=0, end=0.5)]
    changed = [AlignedWord(text="two", start=0, end=0.5)]
    assert timestamp_displacement_payload(raw, changed)["available"] is False
    invalid = [AlignedWord(text="one", start=float("nan"), end=float("inf"))]
    report = timestamp_displacement_payload(raw, invalid)
    assert report["start"]["comparable_count"] == 0
    assert report["end"]["comparable_count"] == 0
    assert report["final_endpoint_delta_seconds"] is None
    assert report["review_span_count"] == 0


def test_positive_forced_timing_keeps_raw_zero_duration_visible() -> None:
    raw = [AlignedWord(text="word", start=206.46, end=206.46, confidence=0.9)]
    published = [AlignedWord(text="word", start=206.48, end=206.681,
                             alignment_source="forced")]
    cue = timestamp_displacement_payload(raw, published)["review_spans"][0]
    assert cue["reason_codes"] == ["invalid_asr_word_timestamp"]
    assert cue["asr_start"] == cue["asr_end"] == 206.46
    assert published[0].end == 206.681
