# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Doctor checks for local tooling, dependencies, and model-cache readiness."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import platform
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .alignment.forced_alignment import preflight_forced_alignment
from .asr.faster_whisper_backend import (
    resolve_compute_type,
    resolve_cpu_threads,
    resolve_device,
    resolve_num_workers,
    supported_compute_types,
)
from .asr.mlx_whisper_backend import mlx_compute_type, mlx_model_status
from .config import (
    PipelineConfig,
    default_hf_home,
    default_hf_hub_cache,
    resolve_model_entry,
)
from .datasets import DEFAULT_DATASET_PROFILE, dataset_budget_plan, default_dataset_cache
from .errors import BackendUnavailableError
from .models import shareable_model_path, status_for
from .redaction import privacy_safe_text
from .utils import run_command, which

MIN_SUPPORTED_PYTHON = (3, 11)
MAX_SUPPORTED_PYTHON = (3, 13)

# Keep this list aligned with project.dependencies in pyproject.toml. These are
# required even when none of the optional inference backends are installed.
BASE_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("huggingface-hub", "huggingface_hub"),
    ("jiwer", "jiwer"),
    ("pydantic", "pydantic"),
    ("pyyaml", "yaml"),
    ("typer", "typer"),
)

OPTIONAL_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("faster-whisper", "faster_whisper"),
    ("mlx-whisper", "mlx_whisper"),
    ("mlx", "mlx"),
    ("pyannote.audio", "pyannote.audio"),
    ("torch", "torch"),
    ("torchaudio", "torchaudio"),
    ("torchcodec", "torchcodec"),
    ("ctranslate2", "ctranslate2"),
    ("whisperx", "whisperx"),
    ("speechbrain", "speechbrain"),
)


def required_inference_dependencies(config: PipelineConfig) -> frozenset[str]:
    """Return distributions required by the configured runtime path."""
    required: set[str] = set()
    if config.asr.backend == "faster-whisper":
        required.update({"faster-whisper", "ctranslate2"})
    elif config.asr.backend == "mlx-whisper":
        required.update({"mlx-whisper", "mlx"})
    if config.diarization.enabled and config.diarization.backend in {"auto", "pyannote"}:
        required.update({"pyannote.audio", "torch", "torchaudio"})
    if config.alignment.enabled and config.alignment.backend == "forced":
        required.update({"whisperx", "torch", "torchaudio"})
    if config.speaker_id.mode == "auto":
        required.update({"speechbrain", "torch", "torchaudio"})
    return frozenset(required)


class DoctorCheck(BaseModel):
    name: str
    status: str
    message: str


class DoctorReport(BaseModel):
    ok: bool
    offline: bool
    checks: list[DoctorCheck] = Field(default_factory=list)

    def add(self, name: str, status: str, message: str) -> None:
        self.checks.append(DoctorCheck(name=name, status=status, message=message))


def _version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def _python_supported(version: tuple[int, int]) -> bool:
    return MIN_SUPPORTED_PYTHON <= version < MAX_SUPPORTED_PYTHON


def _python_package_check(package: str, module: str, *, required: bool) -> DoctorCheck:
    available = _module_available(module)
    version = _version(package)
    status = "ok" if available else ("error" if required else "warning")
    if available and version is not None:
        message = version
    elif available:
        message = "installed; distribution version metadata unavailable"
    elif version is not None:
        message = f"not importable; distribution version={version}"
    else:
        message = "not installed"
    return DoctorCheck(name=f"python_package:{package}", status=status, message=message)


def run_doctor(config: PipelineConfig, *, include_docker: bool = True) -> DoctorReport:
    report = DoctorReport(ok=True, offline=config.offline)

    python_version = (sys.version_info.major, sys.version_info.minor)
    py_ok = _python_supported(python_version)
    report.add(
        "python",
        "ok" if py_ok else "error",
        f"{platform.python_version()} at {sys.executable}; supported=>=3.11,<3.13",
    )
    if not py_ok:
        report.ok = False

    for binary in ("ffmpeg", "ffprobe"):
        path = which(binary)
        status = "ok" if path else "error"
        report.add(binary, status, path or f"{binary} not found on PATH")
        if not path:
            report.ok = False

    for name, check_path in (
        ("model_cache", config.model_cache.expanduser()),
        ("hf_home", default_hf_home()),
        ("hf_hub_cache", default_hf_hub_cache()),
    ):
        status, message = path_status(
            check_path,
            create_ok=name in {"model_cache", "hf_home"},
        )
        report.add(name, status, message)
        if status == "error":
            report.ok = False
    token_state = "present" if (os.getenv("LTM_HF_TOKEN") or os.getenv("HF_TOKEN")) else "missing"
    report.add("huggingface_token", "ok" if token_state == "present" else "warning", token_state)
    report.add("offline_mode", "ok" if config.offline else "info", str(config.offline))
    report.add(
        "host",
        "info",
        f"{platform.system()} {platform.release()} machine={platform.machine()}",
    )
    private_artifact_warning = private_benchmark_artifact_warning(Path.cwd())
    if private_artifact_warning:
        report.add("private_benchmark_artifacts", "warning", private_artifact_warning)
    dataset_plan = dataset_budget_plan(
        default_dataset_cache(),
        model_cache_dir=config.model_cache,
        profile=DEFAULT_DATASET_PROFILE,
    )
    dataset_status = "ok" if dataset_plan["ok"] else "warning"
    report.add(
        "datasets",
        dataset_status,
        "profile=runtime-samples "
        f"projected_local_gb={dataset_plan['projected_local_gb']} "
        f"cloud_only_gb={dataset_plan['cloud_only_dataset_estimate_gb']} "
        "full_corpora_are_optional_non_runtime",
    )

    for package, module in BASE_DEPENDENCIES:
        check = _python_package_check(package, module, required=True)
        report.checks.append(check)
        if check.status == "error":
            report.ok = False
    required_inference = required_inference_dependencies(config)
    for package, module in OPTIONAL_DEPENDENCIES:
        check = _python_package_check(package, module, required=package in required_inference)
        report.checks.append(check)
        if check.status == "error":
            report.ok = False

    cuda_message = "torch not installed"
    if _module_available("torch"):
        try:
            import torch

            torch_module: Any = torch
            cuda_message = f"cuda_available={torch_module.cuda.is_available()}"
            if torch_module.cuda.is_available():
                cuda_message += f" device={torch_module.cuda.get_device_name(0)}"
            mps = getattr(torch_module.backends, "mps", None)
            if mps is not None:
                cuda_message += f" mps_available={mps.is_available()}"
        except Exception as exc:  # pragma: no cover - defensive import guard
            cuda_message = f"torch import failed: {exc}"
    report.add("cuda", "info", cuda_message)

    if config.asr.backend == "mlx-whisper":
        model_status = mlx_model_status(config)
        level = "ok" if model_status.present else "error"
        report.add(
            "asr_device_compute",
            level,
            "backend=mlx-whisper "
            f"resolved_device=mlx compute_type={mlx_compute_type(config)} "
            f"mlx_fp16={config.asr.mlx_fp16} "
            f"model_path={model_status.path} "
            f"model_complete={model_status.present} "
            "runtime_downloads_disabled=true",
        )
        if not model_status.present:
            report.ok = False
    else:
        asr_device = resolve_device(config.asr.device)
        asr_compute_type = resolve_compute_type(config.asr.compute_type, asr_device)
        asr_cpu_threads = resolve_cpu_threads(config.asr.cpu_threads, asr_device)
        asr_num_workers = resolve_num_workers(config.asr.num_workers)
        supported = supported_compute_types(asr_device)
        if supported is None:
            report.add(
                "asr_device_compute",
                "warning",
                f"resolved_device={asr_device} compute_type={asr_compute_type} support=unknown",
            )
        else:
            status = "ok" if asr_compute_type in supported else "error"
            report.add(
                "asr_device_compute",
                status,
                "requested_device="
                f"{config.asr.device} resolved_device={asr_device} "
                f"compute_type={asr_compute_type} "
                f"cpu_threads={thread_value(config.asr.cpu_threads)} "
                f"effective_cpu_threads={thread_value(asr_cpu_threads)} "
                f"num_workers={thread_value(config.asr.num_workers)} "
                f"effective_num_workers={thread_value(asr_num_workers)} "
                f"batched={config.asr.batched} "
                f"batch_size={thread_value(config.asr.batch_size if config.asr.batched else None)} "
                f"supported={','.join(sorted(supported))}",
            )
            if status == "error":
                report.ok = False

    if _module_available("torchcodec"):
        report.add(
            "pyannote_decoder_strategy",
            "info",
            "pyannote backend preloads canonical WAV with torchaudio "
            "to avoid hidden decoder calls.",
        )

    if config.alignment.enabled and config.alignment.backend == "forced":
        try:
            alignment_path = preflight_forced_alignment(config)
        except BackendUnavailableError as exc:
            report.add("forced_alignment_readiness", "error", str(exc))
            report.ok = False
        else:
            report.add("forced_alignment_readiness", "ok", alignment_path)

    model_checks: list[tuple[str, str | None]] = []
    if config.asr.backend == "mlx-whisper":
        model_checks.append(("asr-mlx", config.asr.mlx_model))
    else:
        model_checks.append(("asr", config.asr.model))
    model_checks.append(
        ("diarization", config.diarization.model if config.diarization.enabled else None)
    )
    if config.alignment.enabled and config.alignment.backend == "forced":
        model_checks.append(("alignment", config.alignment.model or "auto"))
    for role, model_value in model_checks:
        if not model_value:
            continue
        entry = resolve_model_entry(role, model_value)
        if entry is None:
            report.add(f"model:{role}", "error", f"unknown model '{model_value}'")
            report.ok = False
            continue
        model_status = (
            mlx_model_status(config) if role == "asr-mlx" else status_for(entry, config.model_cache)
        )
        if model_status.present:
            report.add(
                f"model:{role}:{entry.key}",
                "ok",
                shareable_model_path(model_status.path, config.model_cache),
            )
        else:
            report.add(
                f"model:{role}:{entry.key}",
                "error",
                model_status.problem_summary(config.model_cache, shareable=True),
            )
            report.ok = False

    if include_docker:
        docker_path = which("docker")
        if docker_path:
            report.add("docker_cli", "ok", docker_path)
            try:
                completed = run_command(["docker", "compose", "version"], check=False)
                status = "ok" if completed.returncode == 0 else "warning"
                message = (completed.stdout or completed.stderr).strip()
                report.add("docker_compose", status, message or "compose unavailable")
            except Exception as exc:  # pragma: no cover - host-specific
                report.add("docker_compose", "warning", str(exc))
        else:
            report.add("docker_cli", "warning", "docker not found")

    return report


def path_status(path: Path, *, create_ok: bool) -> tuple[str, str]:
    if path.exists():
        if not path.is_dir():
            return "error", f"{path} exists but is not a directory"
        try:
            test_file = path / ".ltm_write_test"
            test_file.write_text("", encoding="utf-8")
            test_file.unlink(missing_ok=True)
        except OSError as exc:
            return "error", f"{path} is not writable: {exc}"
        return "ok", str(path)
    if create_ok:
        return "warning", f"{path} does not exist yet; it will be created when needed"
    return "warning", f"{path} does not exist"


def thread_value(value: int | None) -> str:
    return "unset" if value is None else str(value)


def private_benchmark_artifact_warning(repo_root: Path) -> str | None:
    if not (repo_root / ".git").exists():
        return None
    artifact_dirs = ["benchmark_runs", "benchmark_report", "benchmark_reports"]
    issues: list[str] = []
    for name in artifact_dirs:
        path = repo_root / name
        if not path.exists():
            continue
        ignored = git_check_ignore(repo_root, name)
        status = git_status_path(repo_root, name)
        if status:
            issues.append(f"{name} is visible to git status")
        elif not ignored:
            issues.append(f"{name} exists but is not git-ignored")
    if not issues:
        return None
    return (
        "; ".join(issues)
        + "; keep private generated benchmark artifacts local, ignored, and unstaged"
    )


def git_check_ignore(repo_root: Path, path: str) -> bool:
    completed = run_command(
        ["git", "-C", str(repo_root), "check-ignore", "-q", "--", path],
        check=False,
    )
    return completed.returncode == 0


def git_status_path(repo_root: Path, path: str) -> str:
    completed = run_command(
        ["git", "-C", str(repo_root), "status", "--short", "--", path],
        check=False,
    )
    return completed.stdout.strip()


def format_doctor(report: DoctorReport) -> str:
    lines = [f"offline_mode={report.offline}", ""]
    for check in report.checks:
        lines.append(f"[{check.status}] {check.name}: {privacy_safe_text(check.message)}")
    lines.append("")
    lines.append("doctor_ok=true" if report.ok else "doctor_ok=false")
    return "\n".join(lines)
