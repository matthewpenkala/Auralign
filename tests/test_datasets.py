# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from local_transcript_max.cli import app
from local_transcript_max.datasets import (
    DATASET_MANIFEST,
    DatasetPolicyError,
    cloud_plan,
    cloud_sync,
    dataset_audit_payload,
    dataset_budget_plan,
    pull_sample_datasets,
)
from local_transcript_max.utils import read_json


def test_runtime_samples_marks_large_corpora_cloud_only(tmp_path: Path) -> None:
    payload = dataset_audit_payload(tmp_path)
    records = {row["key"]: row for row in payload["datasets"]}
    assert records["librispeech-dummy"]["classification"] == "sample"
    assert records["librispeech-full"]["classification"] == "cloud_only"
    assert records["ami-full"]["classification"] == "cloud_only"
    assert records["voxceleb-full"]["classification"] == "cloud_only"
    assert records["librispeech-full"]["runtime_required"] is False


def test_default_dataset_plan_stays_under_65gb(tmp_path: Path) -> None:
    plan = dataset_budget_plan(tmp_path, max_local_gb=65, min_free_gb=0)
    assert plan["ok"] is True
    assert plan["projected_local_gb"] < 65
    assert plan["cloud_only_dataset_estimate_gb"] == 898.0
    assert "librispeech-dummy" not in plan["local_downloads"]
    assert "librispeech-dummy" in plan["blocked_local_downloads"]
    assert "voxceleb-full" in plan["cloud_only"]


def test_over_budget_plan_fails_unless_overridden(tmp_path: Path) -> None:
    blocked = dataset_budget_plan(tmp_path, max_local_gb=1, min_free_gb=0)
    assert blocked["ok"] is False
    assert blocked["blockers"]
    allowed = dataset_budget_plan(
        tmp_path,
        max_local_gb=1,
        min_free_gb=0,
        allow_over_budget=True,
    )
    assert allowed["ok"] is True
    assert allowed["blockers"]


def test_dataset_plan_checks_model_cache_volume_when_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_free_gb(path: Path) -> float:
        return 10.0 if path.name == "models" else 200.0

    monkeypatch.setattr("local_transcript_max.datasets._free_gb", fake_free_gb)
    monkeypatch.setattr("local_transcript_max.datasets._same_volume", lambda first, second: False)
    plan = dataset_budget_plan(
        tmp_path / "datasets",
        model_cache_dir=tmp_path / "models",
        max_local_gb=65,
        min_free_gb=5,
    )
    assert plan["ok"] is False
    assert "model-cache free space" in " ".join(plan["blockers"])


def test_dataset_plan_cli_exits_nonzero_when_over_budget(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "datasets",
            "plan",
            "--cache-dir",
            str(tmp_path),
            "--max-local-gb",
            "1",
            "--min-free-gb",
            "0",
        ],
    )
    assert result.exit_code == 1
    assert "exceeds budget" in result.stdout


def test_dataset_plan_cli_uses_ltm_model_cache_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_cache = tmp_path / "custom-models"
    monkeypatch.setenv("LTM_MODEL_CACHE", str(model_cache))
    result = CliRunner().invoke(
        app,
        [
            "datasets",
            "plan",
            "--cache-dir",
            str(tmp_path / "datasets"),
            "--max-local-gb",
            "65",
            "--min-free-gb",
            "0",
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["model_cache"] == str(model_cache)


def test_pull_samples_blocks_unverified_dummy_without_network_and_writes_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_snapshot_download(**kwargs: Any) -> str:
        raise AssertionError(f"blocked sample must not be downloaded: {kwargs}")

    monkeypatch.setattr("local_transcript_max.datasets.snapshot_download", fake_snapshot_download)
    result = pull_sample_datasets(tmp_path)
    assert result["results"][0]["status"] == "blocked"
    assert "blocked_missing_publisher_license" in result["results"][0]["reason"]
    assert (tmp_path / "dataset_manifest.json").exists()
    assert not (
        tmp_path / "samples" / DATASET_MANIFEST["librispeech-dummy"].directory_name
    ).exists()


def test_dataset_manifest_redacts_token_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "hf_" + "fake_secret_value_for_unit_test_only"
    monkeypatch.setenv("HF_TOKEN", secret)
    pull_result = pull_sample_datasets(tmp_path, max_sample_gb=0)
    text = (tmp_path / "dataset_manifest.json").read_text(encoding="utf-8")
    assert secret not in text
    assert pull_result["results"][0]["status"] == "blocked"
    stub = read_json(tmp_path / "gated_stub.json")
    assert "blocked_missing_publisher_license" in stub[0]["reason"]


def test_cloud_plan_generates_rclone_commands_without_tokens(tmp_path: Path) -> None:
    plan = cloud_plan(tmp_path, remote="gdrive:auralign-datasets")
    output = str(plan)
    assert "rclone copy" in output
    assert "ProgramComputer/voxceleb" in output
    assert "hf_" not in output
    assert plan["requires_explicit_bulk_transfer_approval"] is True
    assert {item["key"] for item in plan["plans"]} == {"librispeech-full", "ami-full"}
    assert {item["key"] for item in plan["blocked"]} == {"voxceleb-full"}
    assert all("ProgramComputer/voxceleb" not in item["hydrate_command"] for item in plan["plans"])


def test_cloud_plan_rejects_inline_credentials(tmp_path: Path) -> None:
    with pytest.raises(DatasetPolicyError, match="inline credentials"):
        cloud_plan(tmp_path, remote="https://token@example.invalid/bucket")


def test_cloud_sync_refuses_without_remote_and_approval(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["datasets", "cloud-sync", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "without --yes-i-understand-bulk-transfer" in result.stderr

    with pytest.raises(DatasetPolicyError, match="without --yes-i-understand-bulk-transfer"):
        cloud_sync(tmp_path, remote="gdrive:auralign-datasets")


def test_cloud_plan_cli_requires_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LTM_DATASET_MIRROR_REMOTE", raising=False)
    result = CliRunner().invoke(app, ["datasets", "cloud-plan", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "LTM_DATASET_MIRROR_REMOTE" in result.stderr
