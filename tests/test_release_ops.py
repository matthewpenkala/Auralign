# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import re
import tomllib
from importlib import metadata
from pathlib import Path

import yaml
from pytest import MonkeyPatch

import local_transcript_max
from local_transcript_max.config import load_config, resolve_model_entry
from local_transcript_max.doctor import (
    BASE_DEPENDENCIES,
    DoctorReport,
    _python_package_check,
    _python_supported,
    format_doctor,
    required_inference_dependencies,
    run_doctor,
)
from local_transcript_max.errors import BackendUnavailableError

REPO_ROOT = Path(__file__).resolve().parents[1]


def _normalized_requirement_name(requirement: str) -> str:
    name = re.split(r"[<>=!~;\[]", requirement, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def test_runtime_version_comes_from_distribution_metadata() -> None:
    assert local_transcript_max.__version__ == metadata.version("local-transcript-max")


def test_runtime_version_has_source_checkout_fallback(monkeypatch: MonkeyPatch) -> None:
    def missing_distribution(_name: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(local_transcript_max._metadata, "version", missing_distribution)

    assert local_transcript_max._resolve_version() == "0+unknown"


def test_doctor_python_support_matches_package_contract() -> None:
    assert not _python_supported((3, 10))
    assert _python_supported((3, 11))
    assert _python_supported((3, 12))
    assert not _python_supported((3, 13))


def test_doctor_checks_every_base_dependency() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_dependencies = {
        _normalized_requirement_name(requirement)
        for requirement in pyproject["project"]["dependencies"]
    }
    doctor_dependencies = {
        _normalized_requirement_name(distribution) for distribution, _module in BASE_DEPENDENCIES
    }

    assert doctor_dependencies == project_dependencies


def test_missing_required_dependency_is_an_error(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("local_transcript_max.doctor._module_available", lambda _module: False)
    monkeypatch.setattr("local_transcript_max.doctor._version", lambda _package: None)

    check = _python_package_check("jiwer", "jiwer", required=True)

    assert check.status == "error"
    assert check.message == "not installed"


def test_broken_required_dependency_reports_installed_metadata(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr("local_transcript_max.doctor._module_available", lambda _module: False)
    monkeypatch.setattr("local_transcript_max.doctor._version", lambda _package: "4.0")

    check = _python_package_check("jiwer", "jiwer", required=True)

    assert check.status == "error"
    assert check.message == "not importable; distribution version=4.0"


def test_doctor_requires_packages_for_the_active_runtime_path(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    config = load_config(cwd=tmp_path)
    unavailable_modules = {
        "faster_whisper",
        "ctranslate2",
        "pyannote.audio",
        "torch",
        "torchaudio",
        "speechbrain",
        "whisperx",
    }
    monkeypatch.setattr(
        "local_transcript_max.doctor._module_available",
        lambda module: module not in unavailable_modules,
    )
    monkeypatch.setattr("local_transcript_max.doctor._version", lambda _package: None)

    report = run_doctor(config, include_docker=False)
    checks = {check.name: check.status for check in report.checks}

    assert required_inference_dependencies(config) == frozenset(
        {
            "faster-whisper",
            "ctranslate2",
            "pyannote.audio",
            "speechbrain",
            "torch",
            "torchaudio",
        }
    )
    assert checks["python_package:faster-whisper"] == "error"
    assert checks["python_package:ctranslate2"] == "error"
    assert checks["python_package:pyannote.audio"] == "error"
    assert checks["python_package:speechbrain"] == "error"
    assert checks["python_package:whisperx"] == "warning"
    assert report.ok is False


def test_doctor_requires_strict_alignment_and_mlx_packages_for_configured_backends(
    tmp_path: Path,
) -> None:
    strict = load_config(cwd=tmp_path)
    strict.alignment.backend = "forced"
    assert {"whisperx", "torch", "torchaudio"}.issubset(required_inference_dependencies(strict))

    mlx = load_config(cwd=tmp_path)
    mlx.asr.backend = "mlx-whisper"
    mlx.diarization.enabled = False
    mlx.alignment.enabled = False
    mlx.speaker_id.mode = "off"
    assert required_inference_dependencies(mlx) == frozenset({"mlx-whisper", "mlx"})

    speaker_id = load_config(cwd=tmp_path)
    speaker_id.diarization.enabled = False
    speaker_id.alignment.enabled = False
    speaker_id.speaker_id.mode = "auto"
    assert {"speechbrain", "torch", "torchaudio"}.issubset(
        required_inference_dependencies(speaker_id)
    )


def test_strict_offline_example_selects_all_four_required_model_lanes() -> None:
    strict_path = REPO_ROOT / "examples" / "config.strict-offline.yaml"
    strict = yaml.safe_load(strict_path.read_text(encoding="utf-8"))

    assert strict["offline"] is True
    assert strict["asr"]["backend"] == "faster-whisper"
    assert strict["asr"]["model"] == "large-v3"
    assert strict["diarization"]["backend"] == "pyannote"
    assert strict["diarization"]["model"] == "community-1"
    assert strict["diarization"]["enabled"] is True
    assert strict["alignment"]["backend"] == "forced"
    assert strict["alignment"]["model"] == "en"
    assert strict["alignment"]["language"] == "en"
    assert strict["alignment"]["enabled"] is True
    assert strict["speaker_id"]["mode"] == "auto"
    assert strict["speaker_id"]["model"] == "ecapa"


def test_model_runbook_contains_exact_strict_manifest_pointers() -> None:
    runbook = (REPO_ROOT / "docs" / "models.md").read_text(encoding="utf-8")
    for role, key in (
        ("asr", "large-v3"),
        ("diarization", "community-1"),
        ("alignment", "en"),
    ):
        entry = resolve_model_entry(role, key)
        assert entry is not None
        assert entry.model_id in runbook
        assert entry.revision in runbook
        assert entry.directory_name in runbook
        assert entry.source_url in runbook


def test_model_linkage_is_discoverable_to_humans_and_ai_agents() -> None:
    agent_rules = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    docs_index = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    runbook = (REPO_ROOT / "docs" / "models.md").read_text(encoding="utf-8")
    normalized_runbook = " ".join(runbook.split())

    for authority in (
        "docs/models.md",
        "src/local_transcript_max/config.py",
        "compliance/external_assets.toml",
        "examples/config.strict-offline.yaml",
    ):
        assert authority in agent_rules

    assert "Human And AI-Agent Entry Point" in agent_rules
    assert "Do not download models merely" in agent_rules
    assert "Source-repository access is not model acquisition" in agent_rules
    assert "LTM_OFFLINE=1" in agent_rules
    assert "models verify" in agent_rules
    assert (
        "--asr large-v3 --diarization community-1 --alignment en --speaker-id ecapa" in agent_rules
    )
    assert "human and AI-agent runbook" in docs_index
    assert "Canonical Runbook For Humans And AI Agents" in runbook
    assert (
        "synthetic tests prove the contract, not real transcription readiness" in normalized_runbook
    )


def test_doctor_marks_strict_forced_alignment_preflight_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"

    def unavailable(_config):
        raise BackendUnavailableError("forced alignment unavailable")

    monkeypatch.setattr("local_transcript_max.doctor.preflight_forced_alignment", unavailable)
    report = run_doctor(config, include_docker=False)

    check = next(item for item in report.checks if item.name == "forced_alignment_readiness")
    assert check.status == "error"
    assert check.message == "forced alignment unavailable"
    assert report.ok is False


def test_doctor_fails_when_active_model_lanes_are_missing_even_online(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.offline = False

    report = run_doctor(config, include_docker=False)
    checks = {check.name: check.status for check in report.checks}

    assert checks["model:asr:large-v3"] == "error"
    assert checks["model:diarization:community-1"] == "error"
    assert report.ok is False


def test_formatted_doctor_output_redacts_absolute_local_paths() -> None:
    report = DoctorReport(ok=False, offline=True)
    report.add("model:asr:large-v3", "error", "missing at /private/example/models")

    formatted = format_doctor(report)

    assert "/private/example" not in formatted
    assert "<local-path>" in formatted


def test_ci_lint_and_type_checks_reuse_the_matrix_python() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")

    assert not re.search(r"\buv run(?! --python)", workflow)
    assert (
        "GyanD/codexffmpeg/releases/download/$env:FFMPEG_VERSION/"
        "ffmpeg-$env:FFMPEG_VERSION-essentials_build.zip"
    ) in workflow
    assert "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec" in workflow
    assert "Get-FileHash" in workflow
    assert "Expand-Archive" in workflow
    assert "8\\.1\\.2-essentials_build-www\\.gyan\\.dev" in workflow
    assert "ffprobe -version" in workflow
    assert "scripts/prepare_vulnerability_audit_input.py" in workflow
    assert "pip-audit -r build/cpu-audit-requirements.txt" in workflow


def test_container_contract_is_pinned_non_root_and_uses_installed_cli() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG UV_IMAGE=ghcr.io/astral-sh/uv:" in dockerfile
    assert "@sha256:" in dockerfile
    assert "FROM ${UV_IMAGE} AS uv" in dockerfile
    assert "ARG ACCELERATOR_GROUP=cpu" in dockerfile
    assert "ARG FFMPEG_VERSION=7:5.1.9-0+deb12u1" in dockerfile
    assert "ffmpeg=${FFMPEG_VERSION}" in dockerfile
    assert (
        'io.auralign.distribution-status="internal-only-not-cleared-for-redistribution"'
        in dockerfile
    )
    assert 'org.opencontainers.image.licenses="NOASSERTION"' in dockerfile
    assert 'io.auralign.project-license="LicenseRef-PolyForm-Internal-Use-1.0.0"' in dockerfile
    assert "COPY --chown=ltm:ltm LICENSE NOTICE THIRD_PARTY_NOTICES.md" in dockerfile
    assert "curl -LsSf" not in dockerfile
    assert "USER ltm" in dockerfile
    assert 'ENTRYPOINT ["/app/.venv/bin/ltm"]' in dockerfile


def test_compose_server_has_healthcheck_and_private_writable_storage() -> None:
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    server = compose["services"]["ltm-server"]
    gpu_args = compose["services"]["ltm-gpu"]["build"]["args"]
    assert compose["services"]["ltm-cpu"]["build"]["args"]["ACCELERATOR_GROUP"] == "cpu"
    assert gpu_args["ACCELERATOR_GROUP"] == "cu126"
    assert gpu_args["CA_CERTIFICATES_VERSION"] == "20260601~24.04.1"
    assert gpu_args["FFMPEG_VERSION"] == "7:6.1.1-3ubuntu5"
    assert server["build"]["args"]["ACCELERATOR_GROUP"] == "cpu"

    assert all(service["platform"] == "linux/amd64" for service in compose["services"].values())
    assert server["ports"] == ["127.0.0.1:8765:8765"]
    assert server["healthcheck"]["test"][:3] == [
        "CMD",
        "/app/.venv/bin/python",
        "-c",
    ]
    assert "./outputs:/output" in server["volumes"]
    assert "./embeddings:/speaker-profiles" in server["volumes"]
    assert server["environment"]["LTM_API_JOB_STORE"] == "/output/api/.job-store"
    assert server["environment"]["LTM_API_NETWORK_BOUND"] == "1"
    assert server["environment"]["LTM_API_ALLOWED_INPUT_ROOTS"] == "/input"
    assert server["environment"]["LTM_API_ALLOWED_OUTPUT_ROOTS"] == "/output"
    assert server["environment"]["LTM_SPEAKER_PROFILE_DIR"] == "/speaker-profiles"
