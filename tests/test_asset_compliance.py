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


def presentation_registry() -> dict[tuple[str, str], dict[str, Any]]:
    records = load_toml("external_assets.toml")["presentation_assets"]
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
    required_model_release = set(policy["requirements"]["required_model_release_fields"])
    for record in records.values():
        assert required <= record.keys()
        if record["kind"] == "model":
            assert required_model_release <= record.keys()
        assert IMMUTABLE_REVISION.fullmatch(str(record["revision"]))
        assert str(record["source_url"]).startswith("https://")
        assert str(record["terms_url"]).startswith("https://")
        assert str(record["license_evidence_url"]).startswith("https://")


def test_sat_presentation_assets_are_exact_local_and_outside_product_manifest() -> None:
    records = presentation_registry()

    assert set(records) == {
        ("presentation-model", "presentation-sat-3l-sm"),
        ("presentation-tokenizer", "presentation-sat-xlm-roberta-base-tokenizer"),
    }
    for record in records.values():
        assert IMMUTABLE_REVISION.fullmatch(str(record["revision"]))
        assert record["license_expression"] == "MIT"
        assert record["acquisition_status"] == (
            "manual_preprovision_only_no_auralign_download_path"
        )
        assert record["runtime_status"] == (
            "approved_source_headless_local_proposal_signal_exact_files_required"
        )
        assert record["product_classification"] == ("source-headless-production-proposal-only")
        assert record["runtime_locality"] == "offline_local_only_no_runtime_acquisition"
        assert record["standalone_disposition"] == "excluded_from_macos14_app_and_pkg"
        assert str(record["review_status"]).endswith("bounded_live_smoke")
        digests = _contract(record, "file_sha256", integer=False)
        sizes = _contract(record, "file_size", integer=True)
        assert set(digests) == set(sizes)
        assert all(re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in digests.values())
        assert all(isinstance(value, int) and value > 0 for value in sizes.values())


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
        assert record["acquisition_status"] == entry.acquisition_status
        assert record["runtime_status"] == entry.runtime_status
        assert record["redistribution_status"] == entry.redistribution_status
        assert record["review_status"] == entry.review_status
        assert record["reviewed_at"] == entry.reviewed_at
        assert record["product_classification"] == entry.product_classification
        assert record["backend_compatibility"] == entry.backend_compatibility
        assert record["modification_status"] == entry.modification_status
        assert record["package_bundle_id"] == entry.package_bundle_id
        assert record["package_bundle_version"] == entry.package_bundle_version
        assert record["package_relative_path"] == entry.package_relative_path
        assert record["delivery"] == entry.delivery
        assert entry.production_selectable


def _contract(record: dict[str, Any], field: str, *, integer: bool) -> dict[str, str | int]:
    result: dict[str, str | int] = {}
    for item in record[field]:
        relative, raw_value = str(item).split("=", 1)
        assert relative not in result
        result[relative] = int(raw_value) if integer else raw_value
    return result


def test_model_registry_binds_complete_delivery_and_file_contracts() -> None:
    records = registry()
    assert len(MODEL_MANIFEST) == 19
    for key, entry in MODEL_MANIFEST.items():
        record = records[("model", key)]
        assert _contract(record, "macos_0_3_0_file_sha256", integer=False) == (
            entry.expected_digest_map
        )
        assert _contract(record, "macos_0_3_0_file_size", integer=True) == (entry.expected_size_map)
        assert set(entry.runtime_files) == set(entry.expected_digest_map)
        assert set(entry.runtime_files) == set(entry.expected_size_map)
        assert record["macos_0_3_0_owner_decision"].startswith("Matthew Penkala, 2026-07-25")
        expected_status = {
            "system-component": "bundled_bootstrap_component",
            "native-download": "native_download_pack",
        }[entry.delivery]
        assert record["macos_0_3_0_product_status"] == expected_status


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
        assert str(record["redistribution_status"]).startswith("blocked")
        assert str(record["acquisition_status"]).startswith(("blocked", "manual_only"))
        if kind == "model":
            assert str(record["runtime_status"]).startswith("blocked")


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
    dataset_records = {row["key"]: row for row in dataset_audit_payload(tmp_path)["datasets"]}

    assert model_records["large-v3"]["revision"] == MODEL_MANIFEST["large-v3"].revision
    assert model_records["large-v3"]["acquisition_status"] == "approved"
    assert model_records["large-v3"]["runtime_status"] == "approved_local"
    assert model_records["mlx-large-v3-turbo"]["license_expression"] == "MIT"
    assert model_records["mlx-large-v3-turbo"]["redistribution_status"].startswith("permitted_")
    assert model_records["mlx-large-v3-turbo"]["production_selectable"] is True
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
    assert (
        "revision\tlicense\tcompliance_review\tacquisition\truntime\tredistribution"
        in models.stdout
    )
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
