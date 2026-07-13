# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Runtime diagnostics and debug-report helpers."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .asr.faster_whisper_backend import (
    resolve_compute_type,
    resolve_cpu_threads,
    resolve_device,
    resolve_num_workers,
    supported_compute_types,
)
from .asr.mlx_whisper_backend import mlx_compute_type, mlx_model_status
from .config import PipelineConfig, default_hf_home, default_hf_hub_cache
from .errors import ModelCacheError
from .models import audit_selected_models
from .redaction import redact_obj, redact_text
from .schemas import AlignedWord
from .utils import run_command, which


def runtime_environment(config: PipelineConfig) -> dict[str, Any]:
    torch_info: dict[str, Any] = {"installed": False}
    try:
        import torch

        torch_info = {
            "installed": True,
            "version": getattr(torch, "__version__", None),
            "cuda_available": torch.cuda.is_available(),
            "mps_available": bool(
                getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
            ),
        }
        if torch.cuda.is_available():
            torch_info["cuda_device_count"] = torch.cuda.device_count()
            torch_info["cuda_device_name"] = torch.cuda.get_device_name(0)
            try:
                torch_info["cuda_device_memory_bytes"] = torch.cuda.get_device_properties(
                    0
                ).total_memory
            except Exception:
                pass
    except Exception as exc:  # pragma: no cover - host-specific import guard
        torch_info = {"installed": False, "error": str(exc)}

    asr_device_compute = asr_device_compute_environment(config)
    return {
        "python": sys.version,
        "executable": sys.executable,
        "python_version": platform.python_version(),
        "os": platform.system(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "architecture": platform.machine(),
        "ffmpeg": command_version("ffmpeg"),
        "ffprobe": command_version("ffprobe"),
        "model_cache": str(config.model_cache),
        "hf_home": str(default_hf_home()),
        "hf_hub_cache": str(default_hf_hub_cache()),
        "offline": config.offline,
        "ltm_device": os.getenv("LTM_DEVICE"),
        "ltm_asr_device": os.getenv("LTM_ASR_DEVICE"),
        "ltm_diarization_device": os.getenv("LTM_DIARIZATION_DEVICE"),
        "ltm_alignment_device": os.getenv("LTM_ALIGNMENT_DEVICE"),
        "hf_token_present": bool(os.getenv("HF_TOKEN")),
        "ltm_hf_token_present": bool(os.getenv("LTM_HF_TOKEN")),
        "asr_device_compute": asr_device_compute,
        "diarization_device": {
            "requested_device": config.diarization.device,
            "backend": config.diarization.backend,
            "enabled": config.diarization.enabled,
        },
        "alignment_device": {
            "requested_device": config.alignment.device,
            "backend": config.alignment.backend,
            "enabled": config.alignment.enabled,
        },
        "torch": torch_info,
        "imports": {
            "faster_whisper": module_status("faster_whisper", "faster-whisper"),
            "ctranslate2": module_status("ctranslate2", "ctranslate2"),
            "mlx_whisper": module_status("mlx_whisper", "mlx-whisper"),
            "mlx": module_status("mlx", "mlx"),
            "pyannote.audio": module_status("pyannote.audio", "pyannote.audio"),
            "whisperx": module_status("whisperx", "whisperx"),
            "speechbrain": module_status("speechbrain", "speechbrain"),
        },
    }


def asr_device_compute_environment(config: PipelineConfig) -> dict[str, Any]:
    if config.asr.backend == "mlx-whisper":
        try:
            status = mlx_model_status(config)
            model_path = str(status.path)
            model_complete = status.present
            problem = None if status.present else status.problem_summary(config.model_cache)
        except ModelCacheError as exc:
            model_path = str(config.asr.mlx_model_path or "")
            model_complete = False
            problem = str(exc)
        return {
            "backend": "mlx-whisper",
            "requested_model": config.asr.mlx_model,
            "requested_model_path": str(config.asr.mlx_model_path)
            if config.asr.mlx_model_path
            else None,
            "resolved_model_path": model_path,
            "model_complete": model_complete,
            "runtime_downloads_disabled": True,
            "resolved_device": "mlx",
            "resolved_compute_type": mlx_compute_type(config),
            "mlx_fp16": config.asr.mlx_fp16,
            "mlx_vad_windowed": config.asr.mlx_vad_windowed,
            "mlx_vad_window_seconds": config.asr.mlx_vad_window_seconds,
            "mlx_vad_window_padding_seconds": config.asr.mlx_vad_window_padding_seconds,
            "requested_cpu_threads": config.asr.cpu_threads,
            "effective_cpu_threads": None,
            "cpu_threads_state": "ignored" if config.asr.cpu_threads is not None else "unset",
            "cpu_threads_applies": False,
            "requested_num_workers": config.asr.num_workers,
            "effective_num_workers": None,
            "num_workers_state": "ignored" if config.asr.num_workers is not None else "unset",
            "batched": False,
            "batch_size": None,
            "supported_compute_types": ["float16", "float32"],
            "supported": model_complete,
            "problem": problem,
        }

    asr_device = resolve_device(config.asr.device)
    asr_compute_type = resolve_compute_type(config.asr.compute_type, asr_device)
    asr_cpu_threads = resolve_cpu_threads(config.asr.cpu_threads, asr_device)
    asr_num_workers = resolve_num_workers(config.asr.num_workers)
    asr_supported = supported_compute_types(asr_device)
    cpu_threads_requested = config.asr.cpu_threads is not None
    num_workers_requested = config.asr.num_workers is not None
    return {
        "backend": "faster-whisper",
        "requested_device": config.asr.device,
        "resolved_device": asr_device,
        "requested_compute_type": config.asr.compute_type,
        "resolved_compute_type": asr_compute_type,
        "requested_cpu_threads": config.asr.cpu_threads,
        "effective_cpu_threads": asr_cpu_threads,
        "cpu_threads_state": "explicit" if cpu_threads_requested else "unset",
        "cpu_threads_applies": asr_device == "cpu",
        "requested_num_workers": config.asr.num_workers,
        "effective_num_workers": asr_num_workers,
        "num_workers_state": "explicit" if num_workers_requested else "unset",
        "batched": config.asr.batched,
        "batch_size": config.asr.batch_size if config.asr.batched else None,
        "supported_compute_types": sorted(asr_supported) if asr_supported else None,
        "supported": asr_supported is not None and asr_compute_type in asr_supported,
    }


def base_debug_report(input_file: Path, out_dir: Path, config: PipelineConfig) -> dict[str, Any]:
    device = "mlx" if config.asr.backend == "mlx-whisper" else resolve_device(config.asr.device)
    compute_type = (
        mlx_compute_type(config)
        if config.asr.backend == "mlx-whisper"
        else resolve_compute_type(config.asr.compute_type, device)
    )
    if config.asr.backend == "mlx-whisper":
        statuses = [mlx_model_status(config)]
        statuses.extend(
            audit_selected_models(
                config.model_cache,
                asr=config.asr.model,
                diarization=config.diarization.model if config.diarization.enabled else None,
                alignment=(
                    config.alignment.model
                    if config.alignment.model
                    else ("en" if config.alignment.enabled else None)
                ),
            )[1:]
        )
    else:
        statuses = audit_selected_models(
            config.model_cache,
            asr=config.asr.model,
            diarization=config.diarization.model if config.diarization.enabled else None,
            alignment=(
                config.alignment.model
                if config.alignment.model
                else ("en" if config.alignment.enabled else None)
            ),
        )
    return {
        "input_file": str(input_file),
        "out_dir": str(out_dir),
        "asr_config": config.asr.model_dump(mode="json"),
        "diarization_config": config.diarization.model_dump(mode="json"),
        "alignment_config": config.alignment.model_dump(mode="json"),
        "audio_config": config.audio.model_dump(mode="json"),
        "vad_config": {"backend": "energy", "frame_ms": 30, "threshold_ratio": 0.12},
        "speaker_id_config": {
            "enabled": config.speaker_id.mode != "off",
            "mode": config.speaker_id.mode,
            "profile_dir": str(config.speaker_id.profile_dir)
            if config.speaker_id.profile_dir
            else None,
            "threshold": config.speaker_id.threshold,
            "ambiguity_margin": config.speaker_id.ambiguity_margin,
        },
        "cleanup_config": {
            "enabled": config.cleanup.mode != "off",
            "mode": config.cleanup.mode,
            "endpoint": redact_text(config.cleanup.endpoint),
            "model": config.cleanup.model,
            "scope": config.cleanup.scope,
            "allow_remote_cleanup": config.cleanup.allow_remote_cleanup,
            "write_diff": config.cleanup.write_diff,
            "max_word_delta_ratio": config.cleanup.max_word_delta_ratio,
        },
        "targeted_redecode_config": {
            "enabled": config.redecode.mode != "off" or config.redecode.write_report,
            "mode": config.redecode.mode,
            "max_spans": config.redecode.max_spans,
            "pad_seconds": config.redecode.pad_seconds,
            "beam_size": config.redecode.beam_size,
            "temperature": config.redecode.temperature,
            "write_report": config.redecode.write_report,
            "write_repaired_transcript": config.redecode.write_repaired_transcript,
            "replacement_policy": "preserve_raw_record_candidates_only",
            "temporary_audio_policy": "private_ephemeral_cleanup",
            "candidate_timestamp_coordinates": ["window_relative", "canonical"],
        },
        "device": device,
        "compute_type": compute_type,
        "environment": runtime_environment(config),
        "model_audit": [status.to_audit_record(config.model_cache) for status in statuses],
        "remediation_suggestions": [
            status.entry.pull_command(str(config.model_cache))
            for status in statuses
            if not status.present
        ],
    }


def alignment_diagnostic_payload(
    *,
    requested_backend: str,
    backend: str,
    device: str | None,
    words: list[AlignedWord],
    warnings: list[str],
    duration_seconds: float | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_counts = Counter(word.alignment_source for word in words)
    word_warning_counts: Counter[str] = Counter()
    missing_spans: list[dict[str, int]] = []
    span_start: int | None = None
    for index, word in enumerate(words):
        word_warning_counts.update(word.warnings)
        missing = word.start is None or word.end is None or word.alignment_source == "missing"
        if missing and span_start is None:
            span_start = index
        if not missing and span_start is not None:
            missing_spans.append(
                {
                    "start_word_index": span_start,
                    "end_word_index": index - 1,
                    "word_count": index - span_start,
                }
            )
            span_start = None
    if span_start is not None:
        missing_spans.append(
            {
                "start_word_index": span_start,
                "end_word_index": len(words) - 1,
                "word_count": len(words) - span_start,
            }
        )
    word_count = len(words)
    timed_word_count = sum(1 for word in words if word.start is not None and word.end is not None)
    fallback_used = requested_backend in {"auto", "forced"} and backend == "whisper-word-timestamps"
    return {
        "schema_version": "alignment_report.v1",
        "requested_backend": requested_backend,
        "backend": backend,
        "device": device,
        "fallback_used": fallback_used,
        "word_count": word_count,
        "timed_word_count": timed_word_count,
        "missing_timestamp_count": word_count - timed_word_count,
        "source_counts": dict(sorted(source_counts.items())),
        "source_percentages": {
            source: round((count / word_count) * 100, 3) if word_count else 0.0
            for source, count in sorted(source_counts.items())
        },
        "word_warning_counts": dict(sorted(word_warning_counts.items())),
        "missing_timestamp_spans": missing_spans,
        "backend_warnings": warnings,
        "duration_seconds": duration_seconds,
        "metadata": metadata or {},
    }


def write_debug_report(path: Path, payload: dict[str, Any]) -> None:
    from .utils import write_json

    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, redact_obj(payload))


def module_status(module: str, package: str) -> dict[str, Any]:
    try:
        available = importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError):
        # Dotted optional modules such as ``pyannote.audio`` raise instead of
        # returning ``None`` when their parent package is not installed. A
        # diagnostic probe must report that absence, not break debug reporting.
        available = False
    version: str | None = None
    if available:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = None
    return {"available": available, "version": version}


def command_version(command: str) -> dict[str, str | bool | None]:
    path = which(command)
    if not path:
        return {"available": False, "path": None, "version": None}
    try:
        completed = run_command([command, "-version"], check=False)
        first_line = (completed.stdout or completed.stderr).splitlines()[0]
    except Exception as exc:
        first_line = str(exc)
    return {"available": True, "path": path, "version": first_line}
