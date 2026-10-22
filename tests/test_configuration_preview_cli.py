# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcript_max.cli import app
from local_transcript_max.config import load_config
from local_transcript_max.configuration_evidence import configuration_evidence


def test_preview_uses_real_cli_precedence_without_running_or_creating_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "not-probed.wav"
    media.write_bytes(b"not-media")
    config_path = tmp_path / "job.yaml"
    config_path.write_text(
        "asr:\n  device: cuda\n  initial_prompt: private names\n", encoding="utf-8"
    )
    monkeypatch.setenv("LTM_ASR_DEVICE", "cpu")
    monkeypatch.setenv("LTM_MODEL_LIFECYCLE", "persistent")
    monkeypatch.setenv("LTM_HF_TOKEN", "hf_" + "private" * 10)
    monkeypatch.setenv("LTM_CLEANUP_ENDPOINT", "https://operator:secret@example.invalid/v1")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("configuration preview invoked the processing pipeline")

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", forbidden)
    output = tmp_path / "absent-output"
    result = CliRunner().invoke(app, [
        "transcribe", str(media), "--config", str(config_path), "--out", str(output),
        "--model-lifecycle", "per-job", "--print-effective-config",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["source_configuration"]["asr"]["device"] == "cpu"
    assert payload["source_configuration"]["runtime"]["model_lifecycle"] == "per-job"
    assert "LTM_ASR_DEVICE" in payload["present_environment_overrides"]
    assert "LTM_HF_TOKEN" not in payload["present_environment_overrides"]
    effective = load_config(config_file=config_path)
    effective.runtime.model_lifecycle = "per-job"
    assert payload["source_config_sha256"] == configuration_evidence(effective)[1]
    assert not payload["inference_performed"]
    assert not payload["models_verified"]
    assert not payload["media_probed"]
    for private in ("private names", "operator", "secret", str(tmp_path), "hf_private"):
        assert private not in result.stdout
    assert not output.exists()


def test_preview_rejects_invalid_effective_config_without_writing(tmp_path: Path) -> None:
    media = tmp_path / "input.wav"
    media.write_bytes(b"placeholder")
    output = tmp_path / "absent"
    result = CliRunner().invoke(app, [
        "transcribe", str(media), "--out", str(output), "--alignment-device", "invalid",
        "--print-effective-config",
    ])
    assert result.exit_code != 0
    assert not output.exists()
