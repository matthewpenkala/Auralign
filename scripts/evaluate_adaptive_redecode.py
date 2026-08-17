# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Bounded local comparison of beam 5, global beam 10, and adaptive 5 to 10.

This harness runs entirely on the scripted, non-private fixture corpus in
``tests/fixtures/adaptive_redecode_corpus.json``. It exercises the real
selection policy against programmed decoder behaviour, so it measures the
policy's decisions — not Whisper's accuracy.

It is development-set, synthetic invariant evidence. It is NOT held-out quality
evidence and must never be reported as such. Promoting adaptive-safe to a MAX
default requires the separate held-out corpus described in ``docs/accuracy.md``.
"""

from __future__ import annotations

import argparse
import json
import time
import tracemalloc
from pathlib import Path
from typing import Any

from local_transcript_max.qa.adaptive.decision import SpanEvaluationInput, evaluate_span
from local_transcript_max.qa.adaptive.evidence import candidate_evidence
from local_transcript_max.qa.adaptive.policy import (
    ADAPTIVE_POLICY_VERSION,
    normalized_tokens,
    protected_classes,
)
from local_transcript_max.schemas import AlignedWord, ASRResult, ASRSegment

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "tests" / "fixtures" / "adaptive_redecode_corpus.json"
EVALUATION_SCHEMA_VERSION = "adaptive_redecode_evaluation.v1"
EVIDENCE_CLASS = "synthetic_development_fixture"

MINIMUM_ALIGNMENT_COVERAGE = 0.9
MINIMUM_LOGPROB_MARGIN = 0.05
PROTECTED_MARGIN_MULTIPLIER = 3.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    tracemalloc.start()
    started = time.perf_counter()
    report = evaluate_corpus(corpus)
    report["runtime_seconds"] = round(time.perf_counter() - started, 6)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report["policy_peak_traced_bytes"] = int(peak)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    report["report_bytes"] = len(encoded)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


def evaluate_corpus(corpus: dict[str, Any]) -> dict[str, Any]:
    arms = {
        "beam_5": {"errors": 0, "characters": 0, "tokens": 0, "char_errors": 0},
        "global_beam_10": {"errors": 0, "characters": 0, "tokens": 0, "char_errors": 0},
        "adaptive_5_to_10": {"errors": 0, "characters": 0, "tokens": 0, "char_errors": 0},
    }
    promoted = 0
    abstained = 0
    false_promotions = 0
    missed_corrections = 0
    protected_changes = 0
    reason_counts: dict[str, int] = {}
    decisions: list[dict[str, Any]] = []

    for case in corpus["cases"]:
        reference = case["reference"]
        control = _evidence(case["beam_5"], role="control", beam_size=5)
        wide = _evidence(case["beam_10"], role="wide", beam_size=10)
        decision = evaluate_span(
            SpanEvaluationInput(
                source_text=case["first_pass_text"],
                control_text=case["beam_5"]["text"],
                wide_text=case["beam_10"]["text"],
                expected_language="en",
                speaker_count=case.get("speaker_count", 1),
                minimum_alignment_coverage=MINIMUM_ALIGNMENT_COVERAGE,
                minimum_logprob_margin=MINIMUM_LOGPROB_MARGIN,
                protected_margin_multiplier=PROTECTED_MARGIN_MULTIPLIER,
            ),
            control,
            wide,
        )
        reason_counts[decision.decision_reason] = (
            reason_counts.get(decision.decision_reason, 0) + 1
        )
        adaptive_text = (
            case["beam_10"]["text"] if decision.selected == "wide" else case["first_pass_text"]
        )
        if decision.selected == "wide":
            promoted += 1
            if _wer(reference, adaptive_text) > _wer(reference, case["first_pass_text"]):
                false_promotions += 1
            if protected_classes(case["first_pass_text"], case["beam_10"]["text"]):
                protected_changes += 1
        else:
            abstained += 1
            if _wer(reference, case["beam_10"]["text"]) < _wer(reference, case["first_pass_text"]):
                missed_corrections += 1

        _accumulate(arms["beam_5"], reference, case["first_pass_text"])
        _accumulate(arms["global_beam_10"], reference, case["beam_10"]["text"])
        _accumulate(arms["adaptive_5_to_10"], reference, adaptive_text)
        decisions.append(
            {
                "id": case["id"],
                "expected_outcome": case["expected_outcome"],
                "decision_reason": decision.decision_reason,
                "selected": decision.selected,
                "independent_support_count": decision.independent_support_count,
                "protected_classes": list(decision.protected_classes),
            }
        )

    total = len(corpus["cases"])
    mismatches = [
        item["id"]
        for item in decisions
        if item["selected"] != _expected_selection(item["expected_outcome"])
    ]
    return {
        "schema_version": EVALUATION_SCHEMA_VERSION,
        "evidence_class": EVIDENCE_CLASS,
        "held_out_quality_evidence": False,
        "policy_version": ADAPTIVE_POLICY_VERSION,
        "corpus_id": corpus["corpus_id"],
        "corpus_case_count": total,
        "arms": {name: _rates(values) for name, values in arms.items()},
        "selected_span_rate": round(promoted / total, 6) if total else 0.0,
        "abstention_rate": round(abstained / total, 6) if total else 0.0,
        "promoted_span_count": promoted,
        "abstained_span_count": abstained,
        "false_promotion_count": false_promotions,
        "missed_correction_count": missed_corrections,
        "protected_token_change_count": protected_changes,
        "decision_reason_counts": dict(sorted(reason_counts.items())),
        "policy_expectation_mismatches": mismatches,
        "deterministic_replay": _replay_is_stable(corpus),
        "decisions": decisions,
    }


def _expected_selection(expected_outcome: str) -> str:
    return "wide" if expected_outcome == "promote" else "source"


def _replay_is_stable(corpus: dict[str, Any]) -> bool:
    first = json.dumps(_decision_digest(corpus), sort_keys=True)
    second = json.dumps(_decision_digest(corpus), sort_keys=True)
    return first == second


def _decision_digest(corpus: dict[str, Any]) -> list[str]:
    digest: list[str] = []
    for case in corpus["cases"]:
        decision = evaluate_span(
            SpanEvaluationInput(
                source_text=case["first_pass_text"],
                control_text=case["beam_5"]["text"],
                wide_text=case["beam_10"]["text"],
                expected_language="en",
                speaker_count=case.get("speaker_count", 1),
                minimum_alignment_coverage=MINIMUM_ALIGNMENT_COVERAGE,
                minimum_logprob_margin=MINIMUM_LOGPROB_MARGIN,
                protected_margin_multiplier=PROTECTED_MARGIN_MULTIPLIER,
            ),
            _evidence(case["beam_5"], role="control", beam_size=5),
            _evidence(case["beam_10"], role="wide", beam_size=10),
        )
        digest.append(f"{case['id']}:{decision.selected}:{decision.decision_reason}")
    return digest


def _evidence(case: dict[str, Any], *, role: str, beam_size: int) -> Any:
    text = case["text"]
    duration = float(case.get("duration_seconds", 5.0))
    result = ASRResult(
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=duration,
                text=text,
                avg_logprob=float(case["avg_logprob"]),
                compression_ratio=float(case.get("compression_ratio", 1.0)),
                no_speech_prob=float(case.get("no_speech_prob", 0.1)),
            )
        ],
        language=case.get("language", "en"),
    )
    coverage = float(case.get("alignment_coverage", 1.0))
    tokens = [token for token in text.split() if token]
    usable = int(round(coverage * len(tokens)))
    words = [
        AlignedWord(
            text=token,
            start=index * 0.2,
            end=index * 0.2 + 0.19,
            alignment_source="forced",
        )
        if index < usable
        else AlignedWord(text=token, alignment_source="missing")
        for index, token in enumerate(tokens)
    ]
    return candidate_evidence(
        result,
        role=role,  # type: ignore[arg-type]
        beam_size=beam_size,
        window_duration_seconds=duration,
        alignment_words=words,
    )


def _accumulate(bucket: dict[str, int], reference: str, hypothesis: str) -> None:
    reference_tokens = normalized_tokens(reference)
    bucket["tokens"] += len(reference_tokens)
    bucket["errors"] += _edit_distance(reference_tokens, normalized_tokens(hypothesis))
    bucket["characters"] += len(reference)
    bucket["char_errors"] += _edit_distance(list(reference), list(hypothesis))


def _rates(bucket: dict[str, int]) -> dict[str, float]:
    return {
        "wer": round(bucket["errors"] / bucket["tokens"], 6) if bucket["tokens"] else 0.0,
        "cer": (
            round(bucket["char_errors"] / bucket["characters"], 6)
            if bucket["characters"]
            else 0.0
        ),
        "reference_tokens": bucket["tokens"],
    }


def _wer(reference: str, hypothesis: str) -> float:
    reference_tokens = normalized_tokens(reference)
    if not reference_tokens:
        return 0.0
    return _edit_distance(reference_tokens, normalized_tokens(hypothesis)) / len(
        reference_tokens
    )


def _edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for row, left_item in enumerate(left, start=1):
        current = [row]
        for column, right_item in enumerate(right, start=1):
            current.append(
                min(
                    previous[column] + 1,
                    current[column - 1] + 1,
                    previous[column - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


if __name__ == "__main__":
    raise SystemExit(main())
