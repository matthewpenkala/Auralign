# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Central, side-effect-free runtime backend and device resolution policy."""

from __future__ import annotations

import importlib.util
import platform
import subprocess
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .config import PipelineConfig
from .errors import LTMError

RuntimeRole = Literal["asr", "alignment", "diarization"]


class HostCapabilities(BaseModel):
    """Locally observed capabilities used by the pure runtime resolver."""

    model_config = ConfigDict(frozen=True)

    system: str
    process_architecture: str
    hardware_architecture: str
    macos_version: str | None = None
    mac_hardware_model: str | None = None
    mac_chip: str | None = None
    physical_memory_bytes: int | None = None
    physical_cpu_count: int | None = None
    logical_cpu_count: int | None = None
    is_apple_silicon: bool = False
    is_native_arm64: bool = False
    is_native_apple_silicon: bool = False
    is_rosetta: bool = False
    torch_available: bool = False
    cuda_available: bool = False
    mps_built: bool = False
    mps_available: bool = False
    mlx_available: bool = False
    mlx_whisper_available: bool = False


class DeviceDecision(BaseModel):
    """One independently resolved runtime role."""

    model_config = ConfigDict(frozen=True)

    role: RuntimeRole
    requested_backend: str | None
    resolved_backend: str | None
    requested_device: str | None
    resolved_device: str | None
    actual_device: str | None = None
    explicit_override: bool = False
    experimental: bool = False
    fallback_reason: str | None = None
    warnings: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()


class RuntimePolicy(BaseModel):
    """Complete host-bound resolution for the three heavy inference roles."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["runtime_policy.v1"] = "runtime_policy.v1"
    host: HostCapabilities
    asr: DeviceDecision
    alignment: DeviceDecision
    diarization: DeviceDecision
    warnings: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    valid: bool


def collect_host_capabilities() -> HostCapabilities:
    """Collect local capabilities without model loading or network access."""

    system = platform.system()
    process_architecture = platform.machine()
    macos_version = (platform.mac_ver()[0] or None) if system == "Darwin" else None
    hardware_arm64 = system == "Darwin" and _sysctl_value("hw.optional.arm64") == "1"
    translated_value = _sysctl_value("sysctl.proc_translated") if system == "Darwin" else None
    process_is_arm64 = process_architecture.lower() in {"arm64", "aarch64"}
    is_rosetta = system == "Darwin" and (
        translated_value == "1" or (hardware_arm64 and not process_is_arm64)
    )
    is_apple_silicon = system == "Darwin" and (hardware_arm64 or process_is_arm64 or is_rosetta)
    is_native_arm64 = process_is_arm64 and not is_rosetta
    hardware_architecture = "arm64" if is_apple_silicon else process_architecture
    mac_hardware_model = _sysctl_value("hw.model") if system == "Darwin" else None
    mac_chip = _sysctl_value("machdep.cpu.brand_string") if system == "Darwin" else None
    physical_memory_bytes = (
        _sysctl_positive_int("hw.memsize") if system == "Darwin" else None
    )
    physical_cpu_count = (
        _sysctl_positive_int("hw.physicalcpu") if system == "Darwin" else None
    )
    logical_cpu_count = (
        _sysctl_positive_int("hw.logicalcpu") if system == "Darwin" else None
    )
    torch_available = _module_available("torch")
    cuda_available, mps_built, mps_available = (
        _torch_capabilities() if torch_available else (False, False, False)
    )
    return HostCapabilities(
        system=system,
        process_architecture=process_architecture,
        hardware_architecture=hardware_architecture,
        macos_version=macos_version,
        mac_hardware_model=mac_hardware_model,
        mac_chip=mac_chip,
        physical_memory_bytes=physical_memory_bytes,
        physical_cpu_count=physical_cpu_count,
        logical_cpu_count=logical_cpu_count,
        is_apple_silicon=is_apple_silicon,
        is_native_arm64=is_native_arm64,
        is_native_apple_silicon=is_apple_silicon and is_native_arm64,
        is_rosetta=is_rosetta,
        torch_available=torch_available,
        cuda_available=cuda_available,
        mps_built=mps_built,
        mps_available=mps_available,
        mlx_available=_module_available("mlx"),
        mlx_whisper_available=_module_available("mlx_whisper"),
    )


def resolve_runtime_policy(
    config: PipelineConfig,
    capabilities: HostCapabilities | None = None,
) -> RuntimePolicy:
    """Resolve ASR, alignment, and diarization independently.

    Resolution retains invalid explicit requests as diagnostic issues. Call
    :func:`validate_runtime_policy` before startup or inference to reject them.
    """

    host = capabilities or collect_host_capabilities()
    asr = _resolve_asr(config, host)
    alignment = _resolve_alignment(config, host)
    diarization = _resolve_diarization(config, host)
    warnings = _ordered_unique(
        warning for decision in (asr, alignment, diarization) for warning in decision.warnings
    )
    issues = _ordered_unique(
        issue for decision in (asr, alignment, diarization) for issue in decision.issues
    )
    return RuntimePolicy(
        host=host,
        asr=asr,
        alignment=alignment,
        diarization=diarization,
        warnings=warnings,
        issues=issues,
        valid=not issues,
    )


def validate_runtime_policy(policy: RuntimePolicy) -> RuntimePolicy:
    """Fail closed when a resolved runtime policy contains incompatibilities."""

    if policy.issues:
        raise LTMError("Runtime policy validation failed: " + " | ".join(policy.issues))
    return policy


def _resolve_asr(config: PipelineConfig, host: HostCapabilities) -> DeviceDecision:
    backend = str(config.asr.backend)
    requested_device = str(config.asr.device)
    normalized_device = requested_device.strip().lower()
    warnings: list[str] = []
    issues: list[str] = []

    if backend == "mlx-whisper":
        if normalized_device != "auto":
            warnings.append(
                "asr_mlx_device_ignored: MLX uses its native device and does not honor "
                f"asr.device={requested_device!r}."
            )
        if host.is_rosetta:
            issues.append(
                "asr_mlx_rosetta_unsupported: MLX Whisper requires native arm64 Python, "
                "not Rosetta translation."
            )
        elif not host.is_native_apple_silicon:
            issues.append(
                "asr_mlx_requires_native_darwin_arm64: MLX Whisper requires native "
                "Apple Silicon macOS execution."
            )
        if not host.mlx_available:
            issues.append(
                "asr_mlx_package_missing: Python package 'mlx' is unavailable; install "
                "the reviewed MLX extra on native Apple Silicon."
            )
        if not host.mlx_whisper_available:
            issues.append(
                "asr_mlx_whisper_package_missing: Python package 'mlx-whisper' is "
                "unavailable; install the reviewed MLX extra on native Apple Silicon."
            )
        return DeviceDecision(
            role="asr",
            requested_backend=backend,
            resolved_backend=backend,
            requested_device=requested_device,
            resolved_device="mlx",
            explicit_override=True,
            warnings=tuple(warnings),
            issues=tuple(issues),
        )

    if backend != "faster-whisper":
        return DeviceDecision(
            role="asr",
            requested_backend=backend,
            resolved_backend=None,
            requested_device=requested_device,
            resolved_device=None,
            explicit_override=True,
            issues=(
                "asr_backend_unknown: ASR backend must be 'faster-whisper' or "
                f"'mlx-whisper', not {backend!r}.",
            ),
        )

    explicit_override = normalized_device != "auto"
    if normalized_device == "auto":
        resolved_device = "cuda" if host.cuda_available else "cpu"
    elif normalized_device == "cpu":
        resolved_device = "cpu"
    elif normalized_device == "cuda":
        resolved_device = "cuda"
        if not host.cuda_available:
            issues.append(
                "asr_cuda_unavailable: faster-whisper requested CUDA, but CUDA is unavailable."
            )
    elif normalized_device == "mps":
        resolved_device = None
        issues.append(
            "asr_mps_unsupported: faster-whisper/CTranslate2 does not support the "
            "PyTorch MPS device; use CPU or explicit MLX Whisper."
        )
    else:
        resolved_device = None
        issues.append(
            "asr_device_unknown: faster-whisper device must be auto, cpu, or cuda, "
            f"not {requested_device!r}."
        )
    return DeviceDecision(
        role="asr",
        requested_backend=backend,
        resolved_backend=backend,
        requested_device=requested_device,
        resolved_device=resolved_device,
        explicit_override=explicit_override,
        issues=tuple(issues),
    )


def _resolve_alignment(config: PipelineConfig, host: HostCapabilities) -> DeviceDecision:
    requested_backend = str(config.alignment.backend)
    requested_device = config.alignment.device
    if not config.alignment.enabled or requested_backend == "off":
        return DeviceDecision(
            role="alignment",
            requested_backend=requested_backend,
            resolved_backend="off",
            requested_device=requested_device,
            resolved_device=None,
        )
    if requested_backend in {"whisper", "fallback"}:
        return DeviceDecision(
            role="alignment",
            requested_backend=requested_backend,
            resolved_backend="whisper-word-timestamps",
            requested_device=requested_device,
            resolved_device=None,
        )
    if requested_backend not in {"auto", "forced"}:
        return DeviceDecision(
            role="alignment",
            requested_backend=requested_backend,
            resolved_backend=None,
            requested_device=requested_device,
            resolved_device=None,
            issues=(
                "alignment_backend_unknown: alignment backend must be auto, forced, "
                f"whisper, fallback, or off, not {requested_backend!r}.",
            ),
        )

    normalized_device = "auto" if requested_device is None else requested_device.strip().lower()
    explicit_override = normalized_device != "auto"
    warnings: list[str] = []
    issues: list[str] = []
    experimental = False
    if normalized_device == "auto":
        resolved_device = (
            "cpu" if host.is_apple_silicon else ("cuda" if host.cuda_available else "cpu")
        )
    elif normalized_device == "cpu":
        resolved_device = "cpu"
    elif normalized_device == "cuda":
        resolved_device = "cuda"
        if not host.cuda_available:
            issues.append(
                "alignment_cuda_unavailable: WhisperX alignment requested CUDA, "
                "but CUDA is unavailable."
            )
    elif normalized_device == "mps":
        resolved_device = "mps"
        experimental = True
        warnings.append(
            "alignment_mps_experimental: explicit WhisperX MPS alignment is outside "
            "the automatic and production-acceptance device policy."
        )
        if not config.alignment.allow_experimental_mps:
            issues.append(
                "alignment_mps_approval_required: set alignment.allow_experimental_mps=true "
                "only for a separately controlled same-media alignment experiment."
            )
        if host.system != "Darwin" or not host.mps_built or not host.mps_available:
            issues.append(
                "alignment_mps_unavailable: WhisperX alignment requested MPS, but "
                "PyTorch MPS is not built and available on this host."
            )
    else:
        resolved_device = None
        issues.append(
            "alignment_device_unknown: alignment device must be auto, cpu, cuda, or mps, "
            f"not {requested_device!r}."
        )
    return DeviceDecision(
        role="alignment",
        requested_backend=requested_backend,
        resolved_backend="whisperx",
        requested_device=requested_device,
        resolved_device=resolved_device,
        explicit_override=explicit_override,
        experimental=experimental,
        warnings=tuple(warnings),
        issues=tuple(issues),
    )


def _resolve_diarization(config: PipelineConfig, host: HostCapabilities) -> DeviceDecision:
    requested_backend = str(config.diarization.backend)
    requested_device = str(config.diarization.device)
    if not config.diarization.enabled or requested_backend == "off":
        return DeviceDecision(
            role="diarization",
            requested_backend=requested_backend,
            resolved_backend="off",
            requested_device=requested_device,
            resolved_device=None,
        )
    if requested_backend not in {"auto", "pyannote"}:
        return DeviceDecision(
            role="diarization",
            requested_backend=requested_backend,
            resolved_backend=None,
            requested_device=requested_device,
            resolved_device=None,
            issues=(
                "diarization_backend_unknown: diarization backend must be auto, pyannote, "
                f"or off, not {requested_backend!r}.",
            ),
        )

    normalized_device = requested_device.strip().lower()
    explicit_override = normalized_device != "auto"
    warnings: list[str] = []
    issues: list[str] = []
    experimental = False
    if normalized_device == "auto":
        resolved_device = "cuda" if host.cuda_available else "cpu"
    elif normalized_device == "cpu":
        resolved_device = "cpu"
    elif normalized_device == "cuda":
        resolved_device = "cuda"
        if not host.cuda_available:
            issues.append(
                "diarization_cuda_unavailable: pyannote requested CUDA, but CUDA is unavailable."
            )
    elif normalized_device == "mps":
        resolved_device = "mps"
        experimental = True
        warnings.append(
            "diarization_mps_experimental: explicit pyannote MPS diarization requires "
            "separate same-media evidence and is never selected automatically."
        )
        if host.system != "Darwin" or not host.mps_built or not host.mps_available:
            issues.append(
                "diarization_mps_unavailable: pyannote requested MPS, but PyTorch MPS "
                "is not built and available on this host."
            )
    else:
        resolved_device = None
        issues.append(
            "diarization_device_unknown: diarization device must be auto, cpu, cuda, or mps, "
            f"not {requested_device!r}."
        )
    return DeviceDecision(
        role="diarization",
        requested_backend=requested_backend,
        resolved_backend="pyannote",
        requested_device=requested_device,
        resolved_device=resolved_device,
        explicit_override=explicit_override,
        experimental=experimental,
        warnings=tuple(warnings),
        issues=tuple(issues),
    )


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError, ModuleNotFoundError):
        return False


def _torch_capabilities() -> tuple[bool, bool, bool]:
    try:
        import torch

        torch_module: Any = torch
        cuda_available = bool(torch_module.cuda.is_available())
        mps = getattr(getattr(torch_module, "backends", None), "mps", None)
        mps_built = bool(mps is not None and mps.is_built())
        mps_available = bool(mps is not None and mps.is_available())
        return cuda_available, mps_built, mps_available
    except Exception:
        return False, False, False


def _sysctl_value(name: str) -> str | None:
    try:
        completed = subprocess.run(
            ["sysctl", "-n", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _sysctl_positive_int(name: str) -> int | None:
    value = _sysctl_value(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
