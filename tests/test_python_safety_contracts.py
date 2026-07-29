# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Focused safety and completeness contracts shared by the CLI and macOS bridge."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

import local_transcript_max.cleanup as cleanup_module
import local_transcript_max.cleanup_policy as cleanup_policy
import local_transcript_max.models as models_module
import local_transcript_max.pipeline as pipeline_module
import local_transcript_max.startup as startup_module
from local_transcript_max.cleanup import (
    CleanupRejectedError,
    _RejectRedirects,
    run_cleanup,
    validate_cleanup_endpoint,
)
from local_transcript_max.config import (
    PipelineConfig,
    diarization_runtime_enabled,
    required_model_requests,
)
from local_transcript_max.doctor import (
    StartupEvidence,
    required_inference_dependencies,
    run_doctor,
)
from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.gui_bridge import (
    MODEL_STATUS_SCHEMA_VERSION,
    ModelStatusCompleteness,
    _selected_model_ids,
    models_payload,
)
from local_transcript_max.pipeline import preflight_required_models
from local_transcript_max.schemas import Transcript
from local_transcript_max.startup import StartupProbe, startup_probe_ids


def test_cleanup_public_validation_api_remains_reexported() -> None:
    assert validate_cleanup_endpoint is cleanup_policy.validate_cleanup_endpoint
    assert CleanupRejectedError is cleanup_policy.CleanupRejectedError


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:11434/v1",
        "https://localhost/v1?model=local",
        "http://[::1]:11434/v1",
    ],
)
def test_cleanup_policy_accepts_local_endpoints_online_and_offline(endpoint: str) -> None:
    validate_cleanup_endpoint(endpoint, allow_remote_cleanup=False)
    validate_cleanup_endpoint(endpoint, allow_remote_cleanup=False, offline=True)


def test_cleanup_policy_requires_explicit_remote_consent_and_offline_still_wins() -> None:
    endpoint = "https://cleanup.example.invalid/v1"
    with pytest.raises(CleanupRejectedError, match="allow-remote-cleanup"):
        validate_cleanup_endpoint(endpoint, allow_remote_cleanup=False)
    validate_cleanup_endpoint(endpoint, allow_remote_cleanup=True)
    with pytest.raises(CleanupRejectedError, match="Offline mode"):
        validate_cleanup_endpoint(
            endpoint,
            allow_remote_cleanup=True,
            offline=True,
        )


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        ("http://user@127.0.0.1:11434/v1", "inline credentials"),
        ("http://:password@127.0.0.1:11434/v1", "inline credentials"),
        ("http://127.0.0.1:11434/v1?access_token=value", "inline credentials"),
        ("http://127.0.0.1:11434/v1?API-KEY=value", "inline credentials"),
        ("http://127.0.0.1:11434/v1#private", "URL fragment"),
        ("ftp://127.0.0.1/v1", "absolute HTTP"),
        ("http://127.0.0.1:not-a-port/v1", "invalid port"),
    ],
)
def test_cleanup_policy_rejects_credential_and_url_ambiguity(
    endpoint: str,
    message: str,
) -> None:
    with pytest.raises(CleanupRejectedError, match=message):
        validate_cleanup_endpoint(endpoint, allow_remote_cleanup=True)


def test_cleanup_config_validation_is_mode_aware_and_performs_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("cleanup policy validation attempted network access")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_network)
    disabled = PipelineConfig.model_validate(
        {
            "offline": True,
            "cleanup": {"mode": "off", "endpoint": "not-a-url"},
        }
    )
    assert disabled.cleanup.mode == "off"

    remote = PipelineConfig.model_validate(
        {
            "cleanup": {
                "mode": "local-llm",
                "endpoint": "https://cleanup.example.invalid/v1",
                "allow_remote_cleanup": True,
            }
        }
    )
    assert remote.cleanup.mode == "local-llm"

    with pytest.raises(ValidationError, match="Offline mode"):
        PipelineConfig.model_validate(
            {
                "offline": True,
                "cleanup": {
                    "mode": "local-llm",
                    "endpoint": "https://cleanup.example.invalid/v1",
                    "allow_remote_cleanup": True,
                },
            }
        )


def test_runtime_cleanup_revalidates_mutated_config_before_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = False

    def unexpected_request(*_args: object, **_kwargs: object) -> str:
        nonlocal requested
        requested = True
        raise AssertionError("unsafe cleanup endpoint reached the request boundary")

    config = PipelineConfig()
    config.cleanup.mode = "local-llm"
    config.cleanup.endpoint = "https://cleanup.example.invalid/v1"
    config.cleanup.allow_remote_cleanup = False
    monkeypatch.setattr(cleanup_module, "request_local_cleanup", unexpected_request)

    with pytest.raises(CleanupRejectedError, match="allow-remote-cleanup"):
        run_cleanup(Transcript(input_path="synthetic.wav"), tmp_path, config)
    assert requested is False


def test_cleanup_http_redirects_fail_closed() -> None:
    with pytest.raises(CleanupRejectedError, match="redirects are not allowed"):
        _RejectRedirects().redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "http://127.0.0.1:11434/redirected",
        )


@pytest.mark.parametrize(
    ("enabled", "backend", "active"),
    [
        (False, "off", False),
        (False, "auto", False),
        (False, "pyannote", False),
        (True, "off", False),
        (True, "auto", True),
        (True, "pyannote", True),
    ],
)
def test_diarization_required_model_and_startup_cross_product_matches_pipeline(
    enabled: bool,
    backend: str,
    active: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = PipelineConfig()
    config.model_cache = tmp_path / "models"
    config.diarization.enabled = enabled
    config.diarization.backend = backend  # type: ignore[assignment]
    config.alignment.enabled = False
    config.speaker_id.mode = "off"

    required_roles = {role for role, _value in required_model_requests(config)}
    dependencies = required_inference_dependencies(config)
    probe_ids = set(startup_probe_ids(config))
    selected = _selected_model_ids({"config": config.model_dump(mode="json")})

    assert diarization_runtime_enabled(config.diarization) is active
    assert ("diarization" in required_roles) is active
    assert ("pyannote.audio" in dependencies) is active
    assert ("startup:diarization:pyannote" in probe_ids) is active
    assert ("startup:native:torchcodec" in probe_ids) is active
    assert (("diarization", "community-1") in selected) is active

    requested_roles: list[str] = []

    def record_model(
        role: str,
        _model: str,
        _cache: Path,
        *,
        offline: bool,
    ) -> Path:
        assert offline is config.offline
        requested_roles.append(role)
        return tmp_path

    monkeypatch.setattr(pipeline_module, "require_model", record_model)
    monkeypatch.setattr(pipeline_module, "validate_requested_device", lambda _config: None)
    monkeypatch.setattr(pipeline_module, "resolve_device", lambda _device: "cpu")
    monkeypatch.setattr(
        pipeline_module,
        "resolve_compute_type",
        lambda _compute_type, _device: "float32",
    )
    monkeypatch.setattr(
        pipeline_module,
        "validate_compute_type",
        lambda _compute_type, _device: None,
    )

    preflight_required_models(config)
    assert ("diarization" in requested_roles) is active


def test_doctor_startup_evidence_is_versioned_complete_and_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = PipelineConfig()
    config.model_cache = tmp_path / "models"
    config.diarization.backend = "off"
    config.alignment.enabled = False
    config.speaker_id.mode = "off"
    expected = list(startup_probe_ids(config))
    monkeypatch.setattr(
        startup_module,
        "run_startup_probes",
        lambda _config: [
            StartupProbe(name=probe_id, ok=True, message="synthetic startup fixture")
            for probe_id in expected
        ],
    )

    report = run_doctor(config, include_docker=False, startup=True)
    evidence = report.startup_evidence
    completeness = next(
        check for check in report.checks if check.name == "startup:evidence-completeness"
    )

    assert evidence.schema_version == "doctor_startup_evidence.v1"
    assert evidence.requested is True
    assert evidence.complete is True
    assert evidence.expected_ids == expected
    assert evidence.observed_ids == expected
    assert evidence.expected_count == len(expected)
    assert evidence.observed_count == len(expected)
    assert completeness.status == "ok"


def test_doctor_startup_evidence_fails_closed_on_missing_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = PipelineConfig()
    config.model_cache = tmp_path / "models"
    config.diarization.backend = "off"
    config.alignment.enabled = False
    config.speaker_id.mode = "off"
    monkeypatch.setattr(startup_module, "run_startup_probes", lambda _config: [])

    report = run_doctor(config, include_docker=False, startup=True)
    completeness = next(
        check for check in report.checks if check.name == "startup:evidence-completeness"
    )

    assert report.startup_evidence.complete is False
    assert report.startup_evidence.expected_count > 0
    assert report.startup_evidence.observed_count == 0
    assert completeness.status == "error"
    assert report.ok is False


def test_startup_probe_rejects_excluded_faster_whisper_vad_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    faster_whisper = ModuleType("faster_whisper")
    faster_whisper.WhisperModel = object
    utils = ModuleType("faster_whisper.utils")
    utils.get_assets_path = lambda: str(tmp_path)
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    config = PipelineConfig()
    config.asr.vad_filter = True

    with pytest.raises(
        BackendUnavailableError,
        match="standalone runtime intentionally excludes",
    ):
        startup_module._probe_faster_whisper(config, device="cpu")


def test_alignment_startup_probe_uses_shared_inference_only_loader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = PipelineConfig()
    config.model_cache = tmp_path
    config.alignment.language = "en"
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(startup_module, "verify_generated_punkt", lambda _cache: None)
    monkeypatch.setattr(
        startup_module,
        "preflight_forced_alignment",
        lambda _config: "local-alignment-model",
    )

    def fake_loader(**kwargs):
        calls.append(kwargs)
        return object(), {"dictionary": {"a": 1}}

    monkeypatch.setattr(
        startup_module,
        "_load_inference_alignment_model",
        fake_loader,
    )

    message = startup_module._probe_whisperx(config, device="cpu")

    assert calls == [
        {
            "language": "en",
            "device": "cpu",
            "model_name": "local-alignment-model",
            "model_dir": tmp_path,
        }
    ]
    assert "loaded pinned local CTC model" in message


def test_startup_evidence_schema_rejects_unknown_versions_and_false_counts() -> None:
    with pytest.raises(ValidationError):
        StartupEvidence.model_validate(
            {
                "schema_version": "doctor_startup_evidence.v2",
                "requested": False,
                "complete": False,
            }
        )
    with pytest.raises(ValidationError, match="expected_count"):
        StartupEvidence(
            expected_ids=["startup:asr:faster-whisper"],
            expected_count=0,
        )


def test_models_payload_declares_strict_complete_status_coverage(tmp_path: Path) -> None:
    private_cache = tmp_path / "private-model-cache"
    payload = models_payload({"model_cache": str(private_cache)})

    assert payload["schema_version"] == MODEL_STATUS_SCHEMA_VERSION
    assert payload["complete"] is True
    assert payload["expected_ids"] == sorted(payload["expected_ids"])
    assert payload["observed_ids"] == payload["expected_ids"]
    assert payload["expected_count"] == len(payload["expected_ids"])
    assert payload["observed_count"] == len(payload["observed_ids"])
    assert [row["status_id"] for row in payload["models"]] == payload["observed_ids"]
    serialized = json.dumps(payload)
    assert str(private_cache) not in serialized
    assert all("/" not in identity and "?" not in identity for identity in payload["expected_ids"])


def test_models_payload_canonicalizes_status_row_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = list(reversed(models_module.all_statuses(tmp_path / "models")))
    monkeypatch.setattr(models_module, "all_statuses", lambda _cache: statuses)

    payload = models_payload({"model_cache": str(tmp_path / "models")})
    row_ids = [row["status_id"] for row in payload["models"]]

    assert row_ids == sorted(row_ids)
    assert row_ids == payload["observed_ids"]
    assert payload["observed_ids"] == payload["expected_ids"]
    assert payload["complete"] is True


def test_models_payload_reports_missing_status_rows_without_silent_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statuses = models_module.all_statuses(tmp_path / "models")
    assert statuses
    monkeypatch.setattr(models_module, "all_statuses", lambda _cache: statuses[:-1])

    payload = models_payload({"model_cache": str(tmp_path / "models")})

    assert payload["complete"] is False
    assert payload["observed_count"] == payload["expected_count"] - 1
    assert set(payload["observed_ids"]) < set(payload["expected_ids"])


def test_model_status_completeness_schema_is_strict() -> None:
    with pytest.raises(ValidationError):
        ModelStatusCompleteness.model_validate(
            {
                "schema_version": "gui_model_status.v2",
                "complete": True,
                "expected_ids": [],
                "observed_ids": [],
                "expected_count": 0,
                "observed_count": 0,
            }
        )
    with pytest.raises(ValidationError, match="observed_count"):
        ModelStatusCompleteness(
            complete=False,
            expected_ids=["asr:large-v3"],
            observed_ids=[],
            expected_count=1,
            observed_count=1,
        )
    with pytest.raises(ValidationError, match="duplicate"):
        ModelStatusCompleteness(
            complete=True,
            expected_ids=["asr:large-v3", "asr:large-v3"],
            observed_ids=["asr:large-v3", "asr:large-v3"],
            expected_count=2,
            observed_count=2,
        )
    with pytest.raises(ValidationError, match="canonically ordered"):
        ModelStatusCompleteness(
            complete=True,
            expected_ids=["asr:turbo", "asr:large-v3"],
            observed_ids=["asr:turbo", "asr:large-v3"],
            expected_count=2,
            observed_count=2,
        )
    with pytest.raises(ValidationError, match="unexpected identifier"):
        ModelStatusCompleteness(
            complete=False,
            expected_ids=["asr:large-v3"],
            observed_ids=["asr:turbo"],
            expected_count=1,
            observed_count=1,
        )
