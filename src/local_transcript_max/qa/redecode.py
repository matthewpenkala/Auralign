# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Targeted local ASR re-decode candidate generation."""

from __future__ import annotations

import copy
import hashlib
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

from local_transcript_max.asr.base import ASRBackend
from local_transcript_max.config import PipelineConfig
from local_transcript_max.configuration_evidence import configuration_evidence
from local_transcript_max.errors import LTMError
from local_transcript_max.export.json_export import write_json_artifact
from local_transcript_max.platform_security import ensure_private_directory
from local_transcript_max.qa.heuristics import count_non_monotonic_words
from local_transcript_max.redaction import privacy_safe_obj, redact_obj
from local_transcript_max.schemas import (
    ASRResult,
    ModelMetadata,
    QAFlag,
    QAReport,
    Transcript,
)
from local_transcript_max.utils import run_command, sha256_file

RedecodeMode = Literal["off", "low-confidence", "hallucination", "all-suspect"]

REDECODE_FLAG_CODES = {
    "low-confidence": {"low_avg_logprob"},
    "hallucination": {
        "speech_in_no_speech",
        "high_compression_ratio",
        "repeated_phrase",
    },
    "all-suspect": {
        "low_avg_logprob",
        "speech_in_no_speech",
        "high_compression_ratio",
        "repeated_phrase",
        "missing_word_timestamps",
        "non_monotonic_word_timestamps",
    },
}


def run_targeted_redecode(
    canonical_wav: Path,
    transcript: Transcript,
    qa_report: QAReport,
    config: PipelineConfig,
    asr_backend: ASRBackend,
    out_dir: Path,
    *,
    first_pass_result: ASRResult | None = None,
) -> dict[str, Any]:
    if config.redecode.write_repaired_transcript:
        raise LTMError(
            "write_repaired_transcript=true is no longer supported: targeted re-decode "
            "is candidate-only. Set it to false and review "
            "transcript.redecode_candidates.json instead."
        )
    if config.redecode.mode == "off":
        report = {
            "enabled": False,
            "reason": "targeted re-decode disabled",
            "candidate_spans": [],
        }
        if config.redecode.write_report:
            write_json_artifact(out_dir / "redecode_report.json", report)
        return report

    spans = select_redecode_spans(
        transcript,
        qa_report,
        mode=config.redecode.mode,
        max_spans=config.redecode.max_spans,
        pad_seconds=config.redecode.pad_seconds,
    )
    try:
        canonical_audio_sha256: str | None = sha256_file(canonical_wav)
    except OSError:
        canonical_audio_sha256 = None
    first_pass_safe_config, first_pass_config_sha256 = configuration_evidence(
        config
    )
    fallback_backend = str(
        getattr(asr_backend, "name", type(asr_backend).__name__)
    )
    first_pass_provenance = asr_output_provenance(
        first_pass_result,
        fallback_backend=fallback_backend,
        configuration=first_pass_safe_config,
        configuration_sha256=first_pass_config_sha256,
        canonical_audio_sha256=canonical_audio_sha256,
    )
    candidates: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ltm-redecode-") as window_dir_name:
        window_dir = ensure_private_directory(Path(window_dir_name))
        for index, span in enumerate(spans, start=1):
            window = window_dir / f"span_{index:03d}.wav"
            redecode_config = copy.deepcopy(config)
            redecode_config.asr.beam_size = config.redecode.beam_size
            redecode_config.asr.temperature = config.redecode.temperature
            candidate_safe_config, candidate_config_sha256 = (
                configuration_evidence(redecode_config)
            )
            window_audio_sha256: str | None = None
            try:
                extract_audio_window(
                    canonical_wav,
                    window,
                    start=float(span["padded_start"]),
                    end=float(span["padded_end"]),
                )
                window_audio_sha256 = sha256_file(window)
                result = asr_backend.transcribe(window, redecode_config)
                candidate = redecode_candidate_record(
                    span,
                    result,
                    failure_code=None,
                    first_pass_provenance=first_pass_provenance,
                    candidate_provenance=asr_output_provenance(
                        result,
                        fallback_backend=fallback_backend,
                        configuration=candidate_safe_config,
                        configuration_sha256=candidate_config_sha256,
                        canonical_audio_sha256=canonical_audio_sha256,
                        window_audio_sha256=window_audio_sha256,
                    ),
                )
            except Exception:  # pragma: no cover - defensive around ffmpeg/backends
                candidate = redecode_candidate_record(
                    span,
                    None,
                    failure_code="redecode_failed",
                    first_pass_provenance=first_pass_provenance,
                    candidate_provenance=asr_output_provenance(
                        None,
                        fallback_backend=fallback_backend,
                        configuration=candidate_safe_config,
                        configuration_sha256=candidate_config_sha256,
                        canonical_audio_sha256=canonical_audio_sha256,
                        window_audio_sha256=window_audio_sha256,
                    ),
                )
            candidates.append(candidate)

    payload = {
        "enabled": True,
        "mode": config.redecode.mode,
        "candidate_count": len(candidates),
        "replacement_policy": "preserve_raw_record_candidates_only",
        "retry_policy": {
            "maximum_attempts_per_span": 1,
            "recursive_redecode": False,
            "maximum_spans": config.redecode.max_spans,
            "maximum_context_seconds_each_side": config.redecode.pad_seconds,
        },
        "candidates": candidates,
    }
    write_json_artifact(out_dir / "transcript.redecode_candidates.json", redact_obj(payload))
    if config.redecode.write_report:
        write_json_artifact(out_dir / "redecode_report.json", redact_obj(payload))
    return payload


def select_redecode_spans(
    transcript: Transcript,
    report: QAReport,
    *,
    mode: RedecodeMode,
    max_spans: int,
    pad_seconds: float,
) -> list[dict[str, Any]]:
    if mode == "off":
        return []
    codes = REDECODE_FLAG_CODES[mode]
    spans: list[dict[str, Any]] = []
    for flag in report.flags:
        if flag.code not in codes:
            continue
        start, end = flag_span(flag)
        if start is None or end is None:
            continue
        spans.append(
            {
                "reason_codes": [flag.code],
                "original_start": round(start, 3),
                "original_end": round(end, 3),
                "padded_start": round(max(0.0, start - pad_seconds), 3),
                "padded_end": round(max(start, end + pad_seconds), 3),
                "original_text": text_for_span(transcript, start, end),
                "original_metrics": flag.evidence,
                "warnings": [],
            }
        )
    if mode == "all-suspect" and count_non_monotonic_words(transcript):
        spans.extend(non_monotonic_word_spans(transcript, pad_seconds))
    return merge_spans(spans)[:max_spans]


def flag_span(flag: QAFlag) -> tuple[float | None, float | None]:
    if flag.start is not None and flag.end is not None:
        return flag.start, flag.end
    # A transcript-wide fallback turns an informational QA flag without
    # coordinates (for example, a repeated-phrase aggregate) into a full-file
    # ASR retry. Keep those flags available for review, but only re-decode
    # windows whose location is known.
    return None, None


def text_for_span(transcript: Transcript, start: float, end: float) -> str:
    texts = [
        segment.text
        for segment in transcript.segments
        if segment.end >= start and segment.start <= end
    ]
    return " ".join(text.strip() for text in texts if text.strip())


def non_monotonic_word_spans(transcript: Transcript, pad_seconds: float) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    previous_end = 0.0
    for word in transcript.words:
        if (
            word.start is None
            or word.end is None
            or word.start < previous_end
            or word.end < word.start
        ):
            start = 0.0 if word.start is None else max(0.0, word.start)
            end = start + 1.0 if word.end is None else max(start, word.end)
            spans.append(
                {
                    "reason_codes": ["non_monotonic_word_timestamps"],
                    "original_start": round(start, 3),
                    "original_end": round(end, 3),
                    "padded_start": round(max(0.0, start - pad_seconds), 3),
                    "padded_end": round(end + pad_seconds, 3),
                    "original_text": word.text,
                    "original_metrics": {},
                    "warnings": [],
                }
            )
        if word.end is not None:
            previous_end = max(previous_end, word.end)
    return spans


def merge_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(spans, key=lambda span: (span["padded_start"], span["padded_end"]))
    merged: list[dict[str, Any]] = []
    for span in ordered:
        if not merged or span["padded_start"] > merged[-1]["padded_end"]:
            merged.append(span)
            continue
        current = merged[-1]
        current["padded_end"] = max(current["padded_end"], span["padded_end"])
        current["original_end"] = max(current["original_end"], span["original_end"])
        current["reason_codes"] = sorted(set(current["reason_codes"]) | set(span["reason_codes"]))
        if span["original_text"] and span["original_text"] not in current["original_text"]:
            current["original_text"] = f"{current['original_text']} {span['original_text']}".strip()
    return merged


def extract_audio_window(source: Path, output: Path, *, start: float, end: float) -> None:
    duration = max(0.01, end - start)
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(output),
        ]
    )


def redecode_candidate_record(
    span: dict[str, Any],
    result: ASRResult | None,
    *,
    failure_code: Literal["redecode_failed"] | None,
    first_pass_provenance: dict[str, Any] | None = None,
    candidate_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candidate_text = "" if result is None else " ".join(segment.text for segment in result.segments)
    original_text = str(span.get("original_text", ""))
    return cast(
        dict[str, Any],
        redact_obj(
            {
                "original_text": original_text,
                "candidate_text": candidate_text,
                "original_timestamps": {
                    "start": span["original_start"],
                    "end": span["original_end"],
                },
                "candidate_timestamps": candidate_timestamps(result),
                "candidate_timestamps_canonical": canonical_candidate_timestamps(
                    result,
                    offset_seconds=float(span["padded_start"]),
                ),
                "original_metrics": span.get("original_metrics", {}),
                "candidate_metrics": candidate_metrics(result),
                "reason_codes": span["reason_codes"],
                "status": "failed" if failure_code else "candidate_generated",
                "decision": "recorded_not_replaced",
                "provenance": {
                    "first_pass": first_pass_provenance or {},
                    "repair_candidate": candidate_provenance or {},
                    "attempt_index": 1,
                    "maximum_attempts": 1,
                    "recursive_redecode": False,
                    "original_text_sha256": sha256_text(original_text),
                    "candidate_text_sha256": sha256_text(candidate_text),
                },
                "quality_deltas": {
                    "text_length_delta": len(candidate_text) - len(original_text),
                },
                "warnings": [failure_code] if failure_code else [],
            },
        ),
    )


def asr_output_provenance(
    result: ASRResult | None,
    *,
    fallback_backend: str,
    configuration: dict[str, Any],
    configuration_sha256: str,
    canonical_audio_sha256: str | None,
    window_audio_sha256: str | None = None,
) -> dict[str, Any]:
    backend = fallback_backend
    inference_mode: str | None = None
    runtime_device: str | None = None
    model: dict[str, Any] | None = None
    if result is not None:
        configured_backend = result.config.get("backend")
        if configured_backend:
            backend = str(configured_backend)
        inference_value = result.config.get("inference_mode")
        if inference_value:
            inference_mode = str(inference_value)
        device_value = (
            result.config.get("runtime_device")
            or result.config.get("actual_device")
            or result.config.get("device")
        )
        if device_value:
            runtime_device = str(device_value)
        model = model_provenance(result.model)
    asr_configuration = configuration.get("asr")
    payload = {
        "backend": backend,
        "inference_mode": inference_mode,
        "runtime_device": runtime_device,
        "model": model,
        "configuration_sha256": configuration_sha256,
        "asr_configuration": asr_configuration
        if isinstance(asr_configuration, dict)
        else {},
        "canonical_audio_sha256": canonical_audio_sha256,
        "window_audio_sha256": window_audio_sha256,
    }
    safe = privacy_safe_obj(payload)
    if not isinstance(safe, dict):  # pragma: no cover - dict invariant
        raise TypeError("ASR provenance must remain an object.")
    return safe


def model_provenance(model: ModelMetadata | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return {
        "role": model.role,
        "model_id": model.model_id,
        "revision": model.revision,
        "revision_verified": model.revision_verified,
        "checkpoint_sha256": model.checkpoint_sha256,
        "gated": model.gated,
        "present": model.present,
    }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def candidate_timestamps(result: ASRResult | None) -> dict[str, float | str | None]:
    if result is None or not result.segments:
        return {"coordinate_system": "window_relative", "start": None, "end": None}
    return {
        "coordinate_system": "window_relative",
        "start": result.segments[0].start,
        "end": result.segments[-1].end,
    }


def canonical_candidate_timestamps(
    result: ASRResult | None,
    *,
    offset_seconds: float,
) -> dict[str, float | str | None]:
    offset = round(offset_seconds, 3)
    if result is None or not result.segments:
        return {
            "coordinate_system": "canonical",
            "offset_seconds": offset,
            "start": None,
            "end": None,
        }
    return {
        "coordinate_system": "canonical",
        "offset_seconds": offset,
        "start": round(offset_seconds + result.segments[0].start, 3),
        "end": round(offset_seconds + result.segments[-1].end, 3),
    }


def candidate_metrics(result: ASRResult | None) -> dict[str, Any]:
    if result is None:
        return {}
    return {
        "segment_count": len(result.segments),
        "avg_logprob": average_optional([segment.avg_logprob for segment in result.segments]),
        "compression_ratio": average_optional(
            [segment.compression_ratio for segment in result.segments]
        ),
        "warnings": result.warnings,
    }


def average_optional(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 6)
