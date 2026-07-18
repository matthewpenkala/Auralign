# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Runtime diagnostics and debug-report helpers."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import re
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
from .asr.mlx_whisper_backend import (
    mlx_compute_type,
    mlx_model_status,
    mlx_runtime_evidence,
)
from .config import PipelineConfig, default_hf_home, default_hf_hub_cache
from .errors import ModelCacheError
from .models import audit_selected_models
from .redaction import redact_obj, redact_text
from .resource_usage import framework_allocator_counters
from .runtime_policy import collect_host_capabilities, resolve_runtime_policy
from .schemas import AlignedWord
from .utils import run_command, which


def runtime_environment(config: PipelineConfig) -> dict[str, Any]:
    torch_info = torch_runtime_info()
    asr_device_compute = asr_device_compute_environment(config)
    capabilities = collect_host_capabilities()
    device_policy = resolve_runtime_policy(config, capabilities)
    memory_bytes = capabilities.physical_memory_bytes or physical_memory_bytes()
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
        "ffmpeg_architecture": executable_architecture("ffmpeg", capabilities),
        "ffprobe_architecture": executable_architecture("ffprobe", capabilities),
        "physical_memory_bytes": memory_bytes,
        "power": power_state(),
        "model_cache": str(config.model_cache),
        "hf_home": str(default_hf_home()),
        "hf_hub_cache": str(default_hf_hub_cache()),
        "offline": config.offline,
        "model_lifecycle": config.runtime.model_lifecycle,
        "ltm_device": os.getenv("LTM_DEVICE"),
        "ltm_asr_device": os.getenv("LTM_ASR_DEVICE"),
        "ltm_diarization_device": os.getenv("LTM_DIARIZATION_DEVICE"),
        "ltm_alignment_device": os.getenv("LTM_ALIGNMENT_DEVICE"),
        "torch_mps_environment": torch_mps_environment(),
        "thread_environment": thread_environment(),
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
        "host_capabilities": capabilities.model_dump(mode="json"),
        "device_policy": device_policy.model_dump(mode="json"),
        "torch": torch_info,
        "mlx": mlx_runtime_evidence(load_if_needed=False),
        "framework_memory": framework_allocator_counters(),
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


def torch_runtime_info() -> dict[str, Any]:
    """Return optional Torch runtime and thread-pool evidence."""

    unavailable: dict[str, Any] = {
        "installed": False,
        "intra_op_threads": None,
        "inter_op_threads": None,
    }
    try:
        import torch

        torch_info: dict[str, Any] = {
            "installed": True,
            "version": getattr(torch, "__version__", None),
            "cuda_available": torch.cuda.is_available(),
            "mps_built": bool(
                getattr(torch.backends, "mps", None) and torch.backends.mps.is_built()
            ),
            "mps_available": bool(
                getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()
            ),
            "intra_op_threads": _optional_positive_int_call(torch, "get_num_threads"),
            "inter_op_threads": _optional_positive_int_call(
                torch,
                "get_num_interop_threads",
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
        return torch_info
    except Exception as exc:  # pragma: no cover - host-specific import guard
        return {**unavailable, "error": str(exc)}


def _optional_positive_int_call(source: object, name: str) -> int | None:
    function = getattr(source, name, None)
    if not callable(function):
        return None
    try:
        value = int(function())
    except (RuntimeError, TypeError, ValueError):
        return None
    return value if value > 0 else None


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
    invalid_or_nonmonotonic = alignment_timestamp_defect_count(words)
    fallback_used = requested_backend in {"auto", "forced"} and backend == "whisper-word-timestamps"
    metadata_payload = metadata or {}
    forced_count = source_counts["forced"]
    whisper_count = source_counts["whisper"]
    estimated_count = source_counts["estimated"]
    missing_count = word_count - timed_word_count
    fallback_reasons = [
        reason
        for reason in (
            metadata_payload.get("fallback_reason"),
            *warnings,
        )
        if reason
    ]
    return {
        "schema_version": "alignment_report.v1",
        "canonical_metrics_version": "alignment_qa.v1",
        "requested_backend": requested_backend,
        "backend": backend,
        "alignment_backend": backend,
        "device": device,
        "fallback_used": fallback_used,
        "fallback_reasons": fallback_reasons,
        "word_count": word_count,
        "timed_word_count": timed_word_count,
        "forced_aligned_word_count": forced_count,
        "forced_aligned_word_percentage": percent(forced_count, word_count),
        "whisper_timestamp_word_count": whisper_count,
        "whisper_timestamp_word_percentage": percent(whisper_count, word_count),
        "estimated_interpolated_word_count": estimated_count,
        "estimated_interpolated_word_percentage": percent(
            estimated_count,
            word_count,
        ),
        "missing_timestamp_count": missing_count,
        "segments_with_missing_timestamps": int(
            metadata_payload.get("missing_segment_count") or 0
        ),
        "invalid_or_non_monotonic_timestamp_count": invalid_or_nonmonotonic,
        "timestamp_coverage_percentage": percent(timed_word_count, word_count),
        "source_counts": dict(sorted(source_counts.items())),
        "source_percentages": {
            source: round((count / word_count) * 100, 3) if word_count else 0.0
            for source, count in sorted(source_counts.items())
        },
        "word_warning_counts": dict(sorted(word_warning_counts.items())),
        "missing_timestamp_spans": missing_spans,
        "backend_warnings": warnings,
        "duration_seconds": duration_seconds,
        "metadata": metadata_payload,
    }


def alignment_timestamp_defect_count(words: list[AlignedWord]) -> int:
    """Count invalid or non-monotonic timestamps without inspecting word text."""

    defects = 0
    previous_start = float("-inf")
    previous_end = float("-inf")
    for word in words:
        start = word.start
        end = word.end
        if start is None or end is None:
            continue
        if (
            start < 0
            or end < start
            or start < previous_start
            or end < previous_end
        ):
            defects += 1
        previous_start = max(previous_start, start)
        previous_end = max(previous_end, end)
    return defects


def percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 3)


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


def executable_architecture(
    command: str,
    capabilities: Any | None = None,
) -> dict[str, Any]:
    """Report executable architecture and native-host suitability when detectable."""

    path = which(command)
    if not path:
        return {
            "available": False,
            "path": None,
            "description": None,
            "suitable_for_host": False,
        }
    description: str | None = None
    try:
        completed = run_command(["file", "-b", path], check=False)
        description = (completed.stdout or completed.stderr).strip() or None
    except Exception:
        description = None
    host = capabilities or collect_host_capabilities()
    suitable: bool | None = None
    if host.system == "Darwin" and host.is_apple_silicon and description:
        normalized = (description or "").lower()
        suitable = "arm64" in normalized or "universal binary" in normalized
    return {
        "available": True,
        "path": path,
        "description": description,
        "suitable_for_host": suitable,
    }


def physical_memory_bytes() -> int | None:
    """Return physical memory through unprivileged portable/native interfaces."""

    if platform.system() == "Darwin":
        try:
            completed = run_command(["sysctl", "-n", "hw.memsize"], check=False)
            value = int(completed.stdout.strip())
            return value if value > 0 else None
        except (OSError, ValueError):
            pass
    sysconf = getattr(os, "sysconf", None)
    if not callable(sysconf):
        return None
    try:
        page_size = int(sysconf("SC_PAGE_SIZE"))
        page_count = int(sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError, TypeError):
        return None
    value = page_size * page_count
    return value if value > 0 else None


def power_state() -> dict[str, Any]:
    """Collect unprivileged macOS power-source and low-power-mode evidence."""

    if platform.system() != "Darwin":
        return {
            "available": False,
            "source": None,
            "battery_percent": None,
            "charging": None,
            "low_power_mode": None,
            "power_mode": None,
            "thermal_state": None,
            "thermal_pressure": None,
            "serious_thermal_pressure": None,
        }
    try:
        battery = run_command(["pmset", "-g", "batt"], check=False)
        custom = run_command(["pmset", "-g", "custom"], check=False)
        thermal = run_command(["pmset", "-g", "therm"], check=False)
    except Exception:
        return {
            "available": False,
            "source": None,
            "battery_percent": None,
            "charging": None,
            "low_power_mode": None,
            "power_mode": None,
            "thermal_state": None,
            "thermal_pressure": None,
            "serious_thermal_pressure": None,
        }
    battery_text = battery.stdout or battery.stderr
    custom_text = custom.stdout or custom.stderr
    thermal_text = thermal.stdout or thermal.stderr
    source_match = re.search(r"Now drawing from '([^']+)'", battery_text)
    percent_match = re.search(r"(\d+)%", battery_text)
    normalized_battery = battery_text.lower()
    source = source_match.group(1) if source_match else None
    active_profile = _active_pmset_profile(custom_text, source)
    low_power_values = re.findall(
        r"lowpowermode\s+(\d+)",
        active_profile.lower(),
    )
    power_mode_values = re.findall(
        r"powermode\s+(\d+)",
        active_profile.lower(),
    )
    thermal_payload = _thermal_state(thermal_text, returncode=thermal.returncode)
    return {
        "available": battery.returncode == 0,
        "source": source,
        "battery_percent": int(percent_match.group(1)) if percent_match else None,
        "charging": (
            False
            if "not charging" in normalized_battery
            else
            True
            if "charging" in normalized_battery and "discharging" not in normalized_battery
            else False
            if "discharging" in normalized_battery or "charged" in normalized_battery
            else None
        ),
        "low_power_mode": (
            low_power_values[-1] == "1"
            if low_power_values
            else None
        ),
        "power_mode": (
            int(power_mode_values[-1])
            if power_mode_values
            else None
        ),
        **thermal_payload,
    }


def _active_pmset_profile(custom_text: str, source: str | None) -> str:
    """Return only the `pmset -g custom` section for the active power source."""

    if not source:
        return ""
    sections: dict[str, list[str]] = {}
    active_section: str | None = None
    for line in custom_text.splitlines():
        stripped = line.strip()
        header = re.fullmatch(r"([^:]+):", stripped)
        if header is not None:
            active_section = header.group(1).strip().lower()
            sections.setdefault(active_section, [])
            continue
        if active_section is not None:
            sections[active_section].append(stripped)
    return "\n".join(sections.get(source.strip().lower(), []))


def torch_mps_environment() -> dict[str, str | None]:
    """Return the documented Torch MPS tuning/fallback environment surface."""

    names = (
        "PYTORCH_DEBUG_MPS_ALLOCATOR",
        "PYTORCH_MPS_LOG_PROFILE_INFO",
        "PYTORCH_MPS_TRACE_SIGNPOSTS",
        "PYTORCH_MPS_HIGH_WATERMARK_RATIO",
        "PYTORCH_MPS_LOW_WATERMARK_RATIO",
        "PYTORCH_MPS_FAST_MATH",
        "PYTORCH_MPS_PREFER_METAL",
        "PYTORCH_ENABLE_MPS_FALLBACK",
    )
    return {name: os.getenv(name) for name in names}


def thread_environment() -> dict[str, str | None]:
    """Return non-secret CPU thread-pool tuning environment values."""

    names = (
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "OPENBLAS_NUM_THREADS",
    )
    return {name: os.getenv(name) for name in names}


def _thermal_state(text: str, *, returncode: int) -> dict[str, object]:
    if returncode != 0:
        return {
            "thermal_state": None,
            "thermal_pressure": None,
            "serious_thermal_pressure": None,
        }
    normalized = text.lower()
    no_thermal_warning = "no thermal warning level has been recorded" in normalized
    no_performance_warning = "no performance warning level has been recorded" in normalized
    limits = [
        int(value)
        for value in re.findall(
            r"(?:cpu_speed_limit|scheduler_limit)\s*=\s*(\d+)",
            normalized,
        )
    ]
    constrained = any(value < 100 for value in limits)
    nominal = no_thermal_warning and no_performance_warning and not constrained
    warning_recorded = (
        ("thermal warning level" in normalized and not no_thermal_warning)
        or ("performance warning level" in normalized and not no_performance_warning)
        or constrained
    )
    return {
        "thermal_state": "nominal" if nominal else "warning" if warning_recorded else "unknown",
        "thermal_pressure": "nominal" if nominal else "elevated" if warning_recorded else None,
        "serious_thermal_pressure": True if warning_recorded else False if nominal else None,
    }
