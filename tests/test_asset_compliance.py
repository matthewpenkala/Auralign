# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from local_transcript_max.cli import app
from local_transcript_max.config import MODEL_MANIFEST, resolve_model_entry
from local_transcript_max.datasets import DATASET_MANIFEST, cloud_plan, dataset_audit_payload
from local_transcript_max.errors import ModelCacheError
from local_transcript_max.models import audit_payload, pull_model

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPLIANCE_ROOT = REPO_ROOT / "compliance"
IMMUTABLE_REVISION = re.compile(r"[0-9a-f]{40}")


def load_toml(name: str) -> dict[str, Any]:
    with (COMPLIANCE_ROOT / name).open("rb") as handle:
        return tomllib.load(handle)


def registry() -> dict[tuple[str, str], dict[str, Any]]:
    records = load_toml("external_assets.toml")["assets"]
    indexed = {(str(record["kind"]), str(record["key"])): record for record in records}
    assert len(indexed) == len(records)
    return indexed


def test_external_asset_registry_is_complete_and_policy_fields_are_present() -> None:
    policy = load_toml("policy.toml")
    records = registry()
    expected = {
        *(("model", key) for key in MODEL_MANIFEST),
        *(("dataset", key) for key in DATASET_MANIFEST),
    }

    assert set(records) == expected
    required = set(policy["requirements"]["required_registry_fields"])
    for record in records.values():
        assert required <= record.keys()
        assert IMMUTABLE_REVISION.fullmatch(str(record["revision"]))
        assert str(record["source_url"]).startswith("https://")
        assert str(record["terms_url"]).startswith("https://")
        assert str(record["license_evidence_url"]).startswith("https://")


def test_runtime_model_manifest_matches_external_asset_registry() -> None:
    records = registry()
    for key, entry in MODEL_MANIFEST.items():
        record = records[("model", key)]
        assert record["identifier"] == entry.model_id
        assert record["revision"] == entry.revision
        assert record["license_expression"] == entry.license_expression
        assert record["source_url"] == entry.source_url
        assert record["terms_url"] == entry.terms_url
        assert record["attribution"] == entry.attribution
        assert record["redistribution_status"] == entry.redistribution_status
        assert record["review_status"] == entry.review_status
        assert record["reviewed_at"] == entry.reviewed_at


def test_runtime_dataset_manifest_matches_external_asset_registry() -> None:
    records = registry()
    for key, entry in DATASET_MANIFEST.items():
        record = records[("dataset", key)]
        assert record["identifier"] == entry.repo_id
        assert record["revision"] == entry.revision
        assert record["license_expression"] == entry.license_expression
        assert record["source_url"] == entry.source_url
        assert record["terms_url"] == entry.terms_url
        assert record["attribution"] == entry.attribution
        assert record["redistribution_status"] == entry.redistribution_status
        assert record["review_status"] == entry.review_status
        assert record["reviewed_at"] == entry.reviewed_at


def test_unverified_assets_fail_closed_without_overrides() -> None:
    records = registry()
    overrides = load_toml("overrides.toml")

    assert overrides["overrides"] == []
    for (kind, _key), record in records.items():
        if record["license_expression"] != "NOASSERTION":
            continue
        assert record["redistribution_status"] == "blocked"
        assert record["acquisition_status"] in {"blocked", "manual_only"}
        if kind == "model":
            assert record["runtime_status"] == "local_evaluation_only"


def test_sensitive_dataset_policy_blocks_dummy_acquisition_and_voxceleb_mirroring(
    tmp_path: Path,
) -> None:
    dummy = DATASET_MANIFEST["librispeech-dummy"]
    voxceleb = DATASET_MANIFEST["voxceleb-full"]
    plan = cloud_plan(tmp_path, remote="gdrive:auralign-datasets")

    assert dummy.acquisition_approved is False
    assert dummy.license_expression == "NOASSERTION"
    assert voxceleb.mirror_approved is False
    assert voxceleb.privacy_classification == "biometric_identifiable_voice_high_risk"
    assert "voxceleb-full" not in {item["key"] for item in plan["plans"]}
    assert "voxceleb-full" in {item["key"] for item in plan["blocked"]}


def test_model_revision_override_cannot_bypass_approved_pin(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None

    with pytest.raises(ModelCacheError, match="Refusing unapproved revision override"):
        pull_model(entry, tmp_path, revision="0" * 40)


def test_audit_payloads_expose_compliance_decisions(tmp_path: Path) -> None:
    model_records = {row["key"]: row for row in audit_payload(tmp_path)["models"]}
    dataset_records = {
        row["key"]: row for row in dataset_audit_payload(tmp_path)["datasets"]
    }

    assert model_records["large-v3"]["revision"] == MODEL_MANIFEST["large-v3"].revision
    assert model_records["mlx-large-v3-turbo"]["license_expression"] == "NOASSERTION"
    assert model_records["mlx-large-v3-turbo"]["redistribution_status"] == "blocked"
    assert dataset_records["librispeech-dummy"]["acquisition_approved"] is False
    assert dataset_records["voxceleb-full"]["mirror_approved"] is False


def test_list_commands_expose_revision_license_and_review_status(tmp_path: Path) -> None:
    runner = CliRunner()
    models = runner.invoke(app, ["models", "list", "--cache-dir", str(tmp_path / "models")])
    datasets = runner.invoke(
        app,
        ["datasets", "list", "--cache-dir", str(tmp_path / "datasets")],
    )

    assert models.exit_code == 0
    assert "revision\tlicense\tcompliance_review\tredistribution" in models.stdout
    assert MODEL_MANIFEST["large-v3"].revision in models.stdout
    assert datasets.exit_code == 0
    assert "revision\tlicense\tacquisition_approved\tmirror_approved" in datasets.stdout
    assert DATASET_MANIFEST["voxceleb-full"].review_status in datasets.stdout


def test_pull_samples_cli_fails_closed_for_unverified_fixture(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["datasets", "pull-samples", "--cache-dir", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "blocked hf-internal-testing/librispeech_asr_dummy" in result.stdout
    assert "blocked_missing_publisher_license" in result.stdout
