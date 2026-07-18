# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import json
import platform
from pathlib import Path
from typing import Any, cast

import pytest

import local_transcript_max.runtime_policy as runtime_policy_module
from local_transcript_max.config import load_config
from local_transcript_max.errors import LTMError
from local_transcript_max.runtime_policy import (
    HostCapabilities,
    RuntimePolicy,
    collect_host_capabilities,
    resolve_runtime_policy,
    validate_runtime_policy,
)


def host(**updates: Any) -> HostCapabilities:
    values: dict[str, Any] = {
        "system": "Darwin",
        "process_architecture": "arm64",
        "hardware_architecture": "arm64",
        "macos_version": "26.5",
        "is_apple_silicon": True,
        "is_native_arm64": True,
        "is_native_apple_silicon": True,
        "is_rosetta": False,
        "torch_available": True,
        "cuda_available": False,
        "mps_built": True,
        "mps_available": True,
        "mlx_available": True,
        "mlx_whisper_available": True,
    }
    values.update(updates)
    return HostCapabilities.model_validate(values)


def test_native_apple_silicon_resolves_explicit_mlx_and_cpu_torch_roles(
    tmp_path: Path,
) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    config.alignment.backend = "forced"
    config.alignment.device = None

    policy = resolve_runtime_policy(config, host())

    assert policy.valid is True
    assert policy.asr.requested_backend == "mlx-whisper"
    assert policy.asr.resolved_backend == "mlx-whisper"
    assert policy.asr.resolved_device == "mlx"
    assert policy.asr.actual_device is None
    assert policy.alignment.resolved_backend == "whisperx"
    assert policy.alignment.resolved_device == "cpu"
    assert policy.diarization.resolved_device == "cpu"
    assert validate_runtime_policy(policy) is policy


def test_apple_silicon_does_not_automatically_select_mlx(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)

    policy = resolve_runtime_policy(config, host())

    assert policy.asr.resolved_backend == "faster-whisper"
    assert policy.asr.resolved_device == "cpu"
    assert policy.asr.explicit_override is False


def test_rosetta_rejects_mlx_without_fallback(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    rosetta = host(
        process_architecture="x86_64",
        is_native_arm64=False,
        is_native_apple_silicon=False,
        is_rosetta=True,
    )

    policy = resolve_runtime_policy(config, rosetta)

    assert policy.asr.resolved_backend == "mlx-whisper"
    assert policy.asr.resolved_device == "mlx"
    assert policy.asr.fallback_reason is None
    assert any(issue.startswith("asr_mlx_rosetta_unsupported:") for issue in policy.issues)
    with pytest.raises(LTMError, match="asr_mlx_rosetta_unsupported"):
        validate_runtime_policy(policy)


def test_mlx_requires_packages_and_native_darwin_arm64(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    linux = host(
        system="Linux",
        process_architecture="x86_64",
        hardware_architecture="x86_64",
        macos_version=None,
        is_apple_silicon=False,
        is_native_arm64=False,
        is_native_apple_silicon=False,
        mps_built=False,
        mps_available=False,
        mlx_available=False,
        mlx_whisper_available=False,
    )

    policy = resolve_runtime_policy(config, linux)

    assert policy.asr.resolved_backend == "mlx-whisper"
    assert policy.asr.fallback_reason is None
    assert any(issue.startswith("asr_mlx_requires_native_darwin_arm64:") for issue in policy.issues)
    assert any(issue.startswith("asr_mlx_package_missing:") for issue in policy.issues)
    assert any(issue.startswith("asr_mlx_whisper_package_missing:") for issue in policy.issues)


@pytest.mark.parametrize(
    ("system", "architecture", "cuda_available", "expected_device"),
    [
        ("Darwin", "x86_64", False, "cpu"),
        ("Linux", "x86_64", False, "cpu"),
        ("Linux", "x86_64", True, "cuda"),
    ],
)
def test_portable_auto_device_resolution(
    tmp_path: Path,
    system: str,
    architecture: str,
    cuda_available: bool,
    expected_device: str,
) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    capabilities = host(
        system=system,
        process_architecture=architecture,
        hardware_architecture=architecture,
        macos_version="26.5" if system == "Darwin" else None,
        is_apple_silicon=False,
        is_native_arm64=False,
        is_native_apple_silicon=False,
        mps_built=False,
        mps_available=False,
        cuda_available=cuda_available,
    )

    policy = resolve_runtime_policy(config, capabilities)

    assert policy.asr.resolved_device == expected_device
    assert policy.alignment.resolved_device == expected_device
    assert policy.diarization.resolved_device == expected_device


def test_alignment_resolution_never_inherits_asr_device_on_apple_silicon(
    tmp_path: Path,
) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    config.asr.device = cast(Any, "mps")
    config.alignment.backend = "forced"
    config.alignment.device = None

    policy = resolve_runtime_policy(config, host())

    assert policy.asr.resolved_device == "mlx"
    assert policy.alignment.requested_device is None
    assert policy.alignment.resolved_device == "cpu"
    assert any(warning.startswith("asr_mlx_device_ignored:") for warning in policy.warnings)


@pytest.mark.parametrize("requested", ["mps", "metal", "cuda:0"])
def test_faster_whisper_rejects_mps_and_unknown_devices(
    tmp_path: Path,
    requested: str,
) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.device = cast(Any, requested)

    policy = resolve_runtime_policy(config, host())

    assert policy.valid is False
    assert policy.asr.resolved_device is None
    expected_code = "asr_mps_unsupported:" if requested == "mps" else "asr_device_unknown:"
    assert any(issue.startswith(expected_code) for issue in policy.issues)


def test_explicit_unavailable_mps_is_experimental_and_rejected(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    config.alignment.device = "mps"
    config.diarization.device = "mps"
    no_mps = host(mps_built=False, mps_available=False)

    policy = resolve_runtime_policy(config, no_mps)

    assert policy.alignment.experimental is True
    assert policy.alignment.explicit_override is True
    assert policy.alignment.resolved_device == "mps"
    assert policy.diarization.experimental is True
    assert policy.diarization.explicit_override is True
    assert policy.diarization.resolved_device == "mps"
    assert any(issue.startswith("alignment_mps_unavailable:") for issue in policy.issues)
    assert any(issue.startswith("diarization_mps_unavailable:") for issue in policy.issues)
    with pytest.raises(LTMError, match="alignment_mps_unavailable"):
        validate_runtime_policy(policy)


def test_explicit_available_alignment_mps_requires_separate_approval(
    tmp_path: Path,
) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    config.alignment.device = "mps"
    config.diarization.device = "mps"

    policy = resolve_runtime_policy(config, host())

    assert policy.valid is False
    assert any(
        issue.startswith("alignment_mps_approval_required:")
        for issue in policy.issues
    )

    config.alignment.allow_experimental_mps = True
    policy = resolve_runtime_policy(config, host())

    assert policy.valid is True
    assert policy.alignment.experimental is True
    assert policy.diarization.experimental is True
    assert len(policy.warnings) == 2
    assert validate_runtime_policy(policy) is policy


@pytest.mark.parametrize(
    ("role", "value", "issue_prefix"),
    [
        ("alignment", "neural-engine", "alignment_device_unknown:"),
        ("diarization", "neural-engine", "diarization_device_unknown:"),
    ],
)
def test_unknown_torch_device_is_rejected(
    tmp_path: Path,
    role: str,
    value: str,
    issue_prefix: str,
) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    if role == "alignment":
        config.alignment.device = cast(Any, value)
    else:
        config.diarization.device = cast(Any, value)

    policy = resolve_runtime_policy(config, host())

    assert any(issue.startswith(issue_prefix) for issue in policy.issues)


def test_policy_serialization_round_trip_preserves_requested_resolved_actual_fields(
    tmp_path: Path,
) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    policy = resolve_runtime_policy(config, host())

    payload = policy.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True)
    restored = RuntimePolicy.model_validate_json(encoded)

    assert restored == policy
    assert payload["schema_version"] == "runtime_policy.v1"
    assert payload["asr"]["requested_device"] == "auto"
    assert payload["asr"]["resolved_device"] == "cpu"
    assert payload["asr"]["actual_device"] is None
    assert payload["alignment"]["requested_device"] is None
    assert payload["alignment"]["resolved_device"] == "cpu"


def test_collect_host_capabilities_detects_native_and_rosetta_apple_silicon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        platform,
        "mac_ver",
        lambda: ("26.5", ("", "", ""), ""),
    )
    sysctl_values = {
        "hw.optional.arm64": "1",
        "sysctl.proc_translated": "0",
        "hw.model": "Mac15,9",
        "machdep.cpu.brand_string": "Apple M3 Max",
        "hw.memsize": "68719476736",
        "hw.physicalcpu": "12",
        "hw.logicalcpu": "12",
    }
    requested_sysctls: list[str] = []

    def sysctl_value(name: str) -> str | None:
        requested_sysctls.append(name)
        return sysctl_values.get(name)

    monkeypatch.setattr(
        runtime_policy_module,
        "_sysctl_value",
        sysctl_value,
    )
    monkeypatch.setattr(
        runtime_policy_module,
        "_module_available",
        lambda name: name in {"torch", "mlx", "mlx_whisper"},
    )
    monkeypatch.setattr(
        runtime_policy_module,
        "_torch_capabilities",
        lambda: (False, True, True),
    )

    native = collect_host_capabilities()
    assert native.is_apple_silicon is True
    assert native.is_native_arm64 is True
    assert native.is_native_apple_silicon is True
    assert native.is_rosetta is False
    assert native.mac_hardware_model == "Mac15,9"
    assert native.mac_chip == "Apple M3 Max"
    assert native.physical_memory_bytes == 68_719_476_736
    assert native.physical_cpu_count == 12
    assert native.logical_cpu_count == 12
    assert native.mps_built is True
    assert native.mps_available is True
    assert native.mlx_available is True
    assert native.mlx_whisper_available is True
    assert all("serial" not in name.lower() for name in requested_sysctls)
    assert all(
        "serial" not in field.lower()
        for field in native.model_dump(mode="json")
    )

    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    sysctl_values["sysctl.proc_translated"] = "1"
    translated = collect_host_capabilities()
    assert translated.hardware_architecture == "arm64"
    assert translated.is_apple_silicon is True
    assert translated.is_native_arm64 is False
    assert translated.is_native_apple_silicon is False
    assert translated.is_rosetta is True


def test_host_hardware_profile_is_optional_and_skips_sysctl_off_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        runtime_policy_module,
        "_sysctl_value",
        lambda name: pytest.fail(f"unexpected sysctl query: {name}"),
    )
    monkeypatch.setattr(runtime_policy_module, "_module_available", lambda _name: False)

    capabilities = collect_host_capabilities()

    assert capabilities.mac_hardware_model is None
    assert capabilities.mac_chip is None
    assert capabilities.physical_memory_bytes is None
    assert capabilities.physical_cpu_count is None
    assert capabilities.logical_cpu_count is None
