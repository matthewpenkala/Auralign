# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Simple deterministic transcript and diarization QA heuristics."""

from __future__ import annotations

import math
from collections import Counter

from local_transcript_max.schemas import DiarizationTurn, QAFlag, QAReport, Transcript

SPEAKER_FLAPPING_MAX_GAP_SECONDS = 1.0
SPEAKER_FLAPPING_MAX_TURN_SECONDS = 2.0
SPEAKER_FLAPPING_WARNING_THRESHOLD = 0.35
EXCESSIVELY_SHORT_TURN_SECONDS = 0.75
# Review threshold, not a phonetic limit or a rule for deleting/re-timing words.
LONG_WORD_DURATION_SECONDS = 2.0


def run_qa(
    transcript: Transcript,
    *,
    requested_speaker_count: int | None = None,
) -> QAReport:
    flags: list[QAFlag] = []
    for segment in transcript.segments:
        if (
            not math.isfinite(segment.start)
            or not math.isfinite(segment.end)
            or segment.start < 0
            or segment.end < segment.start
        ):
            flags.append(
                QAFlag(
                    code="impossible_timestamp",
                    severity="error",
                    message="Segment timing is nonfinite, negative, or reversed.",
                    start=segment.start if math.isfinite(segment.start) else None,
                    end=segment.end if math.isfinite(segment.end) else None,
                )
            )
            continue
        if segment.avg_logprob is not None and segment.avg_logprob < -1.0:
            flags.append(
                QAFlag(
                    code="low_avg_logprob",
                    severity="warning",
                    message="ASR segment has low average log probability.",
                    start=segment.start,
                    end=segment.end,
                    evidence={"avg_logprob": segment.avg_logprob},
                )
            )
        if segment.compression_ratio is not None and segment.compression_ratio > 2.4:
            flags.append(
                QAFlag(
                    code="high_compression_ratio",
                    severity="warning",
                    message=(
                        "ASR segment has high compression ratio, "
                        "which can indicate repetition."
                    ),
                    start=segment.start,
                    end=segment.end,
                    evidence={"compression_ratio": segment.compression_ratio},
                )
            )
        if segment.no_speech_prob is not None and segment.no_speech_prob > 0.8 and segment.text:
            flags.append(
                QAFlag(
                    code="speech_in_no_speech",
                    severity="warning",
                    message="ASR emitted text despite high no-speech probability.",
                    start=segment.start,
                    end=segment.end,
                    evidence={"no_speech_prob": segment.no_speech_prob},
                )
            )
        if segment.end - segment.start > 60:
            flags.append(
                QAFlag(
                    code="very_long_segment",
                    severity="warning",
                    message="ASR segment is unusually long.",
                    start=segment.start,
                    end=segment.end,
                )
            )
    labels = [word.speaker for word in transcript.words if word.speaker]
    unlabeled = sum(1 for word in transcript.words if not word.speaker)
    uncertain = sum(1 for word in transcript.words if word.uncertain_speaker)
    missing_timestamps = sum(
        1 for word in transcript.words if word.start is None or word.end is None
    )
    invalid_timestamps = 0
    long_word_count = 0
    for index, word in enumerate(transcript.words):
        if word.start is None or word.end is None:
            continue
        if (
            not math.isfinite(word.start)
            or not math.isfinite(word.end)
            or word.start < 0
            or word.end <= word.start
        ):
            invalid_timestamps += 1
            flags.append(QAFlag(
                code="invalid_word_timestamp",
                severity="error",
                message="Word timing is nonfinite, negative, zero-duration, or reversed.",
                start=word.start if math.isfinite(word.start) else None,
                end=word.end if math.isfinite(word.end) else None,
                evidence={"word_index": index, "alignment_source": word.alignment_source},
            ))
        elif word.end - word.start >= LONG_WORD_DURATION_SECONDS:
            long_word_count += 1
            flags.append(QAFlag(
                code="long_word_duration",
                severity="warning",
                message=(
                    "One word spans unusually long audio. Review the source context for "
                    "sustained speech, silence, or an omitted restart; timing alone cannot decide."
                ),
                start=word.start,
                end=word.end,
                evidence={
                    "word_index": index,
                    "alignment_source": word.alignment_source,
                    "duration_seconds": round(word.end - word.start, 6),
                    "threshold_seconds": LONG_WORD_DURATION_SECONDS,
                },
            ))
    non_monotonic_words = count_non_monotonic_words(transcript)
    if transcript.words and unlabeled / len(transcript.words) > 0.2:
        flags.append(
            QAFlag(
                code="many_words_without_speaker",
                severity="warning",
                message="More than 20 percent of words have no speaker label.",
                evidence={"unlabeled": unlabeled, "total": len(transcript.words)},
            )
        )
    if missing_timestamps:
        flags.append(
            QAFlag(
                code="missing_word_timestamps",
                severity="warning",
                message="Some words do not have usable timestamps.",
                evidence={"missing_word_timestamps": missing_timestamps},
            )
        )
    if non_monotonic_words:
        flags.append(
            QAFlag(
                code="non_monotonic_word_timestamps",
                severity="warning",
                message="Word timestamps are not monotonic.",
                evidence={"non_monotonic_words": non_monotonic_words},
            )
        )
    repeated = repeated_phrase_flags(" ".join(segment.text for segment in transcript.segments))
    flags.extend(repeated)
    overlap_spans = overlap_spans_from_turns(transcript.diarization_turns)
    if overlap_spans:
        flags.append(
            QAFlag(
                code="overlapping_diarization_turns",
                severity="info",
                message="Diarization contains overlapping speaker turns.",
                evidence={"overlap_span_count": len(overlap_spans)},
            )
        )
    flapping = speaker_flapping_score(transcript.diarization_turns)
    if flapping > SPEAKER_FLAPPING_WARNING_THRESHOLD:
        flags.append(
            QAFlag(
                code="speaker_flapping",
                severity="warning",
                message="Diarization has frequent short speaker changes.",
                evidence={"speaker_flapping_score": flapping},
            )
        )
    alignment_counts = Counter(word.alignment_source for word in transcript.words)
    assignment_counts = Counter(
        word.assignment_source or "unassigned" for word in transcript.words
    )
    word_count = len(transcript.words)
    detected_speaker_count = len(set(labels))
    short_turn_count = sum(
        1
        for turn in transcript.diarization_turns
        if max(0.0, turn.end - turn.start) < EXCESSIVELY_SHORT_TURN_SECONDS
    )
    error_flag_count = sum(flag.severity == "error" for flag in flags)
    warning_flag_count = sum(flag.severity == "warning" for flag in flags)
    summary = {
        "flag_count": len(flags),
        "error_flag_count": error_flag_count,
        "warning_flag_count": warning_flag_count,
        "critical_qa_defect_count": error_flag_count,
        "major_qa_defect_count": warning_flag_count,
        "speaker_count": detected_speaker_count,
        "requested_speaker_count": requested_speaker_count,
        "detected_speaker_count": detected_speaker_count,
        "validated_speaker_count": None,
        "word_count": word_count,
        "assigned_word_count": word_count - unlabeled,
        "unassigned_word_count": unlabeled,
        "uncertain_speaker_word_count": uncertain,
        "percent_words_assigned_to_speakers": percent(word_count - unlabeled, word_count),
        "percent_words_unassigned": percent(unlabeled, word_count),
        "percent_words_uncertain": percent(uncertain, word_count),
        "assignment_source_counts": dict(sorted(assignment_counts.items())),
        "forced_alignment_percentage": percent(alignment_counts["forced"], word_count),
        "whisper_alignment_percentage": percent(alignment_counts["whisper"], word_count),
        "estimated_alignment_percentage": percent(alignment_counts["estimated"], word_count),
        "missing_alignment_percentage": percent(alignment_counts["missing"], word_count),
        "timestamp_coverage_percentage": percent(
            word_count - missing_timestamps - invalid_timestamps, word_count
        ),
        "missing_timestamp_count": missing_timestamps,
        "invalid_word_timestamp_count": invalid_timestamps,
        "long_word_duration_count": long_word_count,
        "long_word_duration_threshold_seconds": LONG_WORD_DURATION_SECONDS,
        "non_monotonic_timestamp_count": non_monotonic_words,
        "missing_segment_timestamp_count": 0,
        "overlap_spans": overlap_spans,
        "overlap_span_count": len(overlap_spans),
        "excessively_short_turn_count": short_turn_count,
        "excessively_short_turn_rate": percent(
            short_turn_count,
            len(transcript.diarization_turns),
        ),
        "speaker_flapping_score": flapping,
        "speaker_flapping_definition": {
            "max_gap_seconds": SPEAKER_FLAPPING_MAX_GAP_SECONDS,
            "max_current_turn_seconds": SPEAKER_FLAPPING_MAX_TURN_SECONDS,
            "warning_threshold": SPEAKER_FLAPPING_WARNING_THRESHOLD,
        },
        "short_turn_definition_seconds": EXCESSIVELY_SHORT_TURN_SECONDS,
        "overall_quality_summary": "review_warnings" if flags else "no_deterministic_warnings",
        "review_scope": {
            "timing": "deterministic_checks_only",
            "lexical_completeness": "not_established",
            "take_selection": "not_evaluated",
            "perceptual_review": "not_performed",
        },
        "actionable_remediation_suggestions": remediation_suggestions(flags),
    }
    return QAReport(flags=flags, summary=summary)


def repeated_phrase_flags(text: str) -> list[QAFlag]:
    tokens = [token.strip(".,!?;:").lower() for token in text.split() if token.strip()]
    trigrams = [" ".join(tokens[index : index + 3]) for index in range(max(0, len(tokens) - 2))]
    counts = Counter(trigrams)
    return [
        QAFlag(
            code="repeated_phrase",
            severity="warning",
            message="A short phrase repeats many times, which can indicate an ASR loop.",
            evidence={"phrase": phrase, "count": count},
        )
        for phrase, count in counts.items()
        if phrase and count >= 4
    ]


def count_non_monotonic_words(transcript: Transcript) -> int:
    count = 0
    previous_end = 0.0
    for word in transcript.words:
        if word.start is None or word.end is None:
            continue
        if not math.isfinite(word.start) or not math.isfinite(word.end):
            continue
        if word.start < previous_end or word.end < word.start:
            count += 1
        previous_end = max(previous_end, word.end)
    return count


def overlap_spans_from_turns(turns: list[DiarizationTurn]) -> list[dict[str, object]]:
    spans: list[dict[str, object]] = []
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if right.start >= left.end:
                break
            start = max(left.start, right.start)
            end = min(left.end, right.end)
            if end > start:
                spans.append(
                    {
                        "start": round(start, 3),
                        "end": round(end, 3),
                        "speakers": sorted({left.speaker, right.speaker}),
                    }
                )
    return spans


def speaker_flapping_score(turns: list[DiarizationTurn]) -> float:
    if len(turns) < 2:
        return 0.0
    rapid_switches = 0
    ordered = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    for previous, current in zip(ordered, ordered[1:], strict=False):
        gap = max(0.0, current.start - previous.end)
        if (
            previous.speaker != current.speaker
            and gap <= SPEAKER_FLAPPING_MAX_GAP_SECONDS
            and current.end - current.start < SPEAKER_FLAPPING_MAX_TURN_SECONDS
        ):
            rapid_switches += 1
    return round(rapid_switches / (len(ordered) - 1), 6)


def percent(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(100.0 * numerator / denominator, 3)


def remediation_suggestions(flags: list[QAFlag]) -> list[str]:
    codes = {flag.code for flag in flags}
    suggestions: list[str] = []
    if {"low_avg_logprob", "high_compression_ratio", "repeated_phrase"} & codes:
        suggestions.append(
            "Retry with --preset max and verify the source audio for noise or loops."
        )
    if "missing_word_timestamps" in codes:
        suggestions.append("Keep --word-timestamps enabled or use --forced-alignment auto.")
    if "invalid_word_timestamp" in codes:
        suggestions.append(
            "Review invalid word indexes and alignment provenance before using timings."
        )
    if "long_word_duration" in codes:
        suggestions.append(
            "Listen to the flagged source spans with surrounding context; compare raw ASR and "
            "alignment timings, and use independent recognition when wording is disputed."
        )
    if {"many_words_without_speaker", "speaker_flapping"} & codes:
        suggestions.append("Retry with --num-speakers, --min-speakers, or --max-speakers.")
    return suggestions
