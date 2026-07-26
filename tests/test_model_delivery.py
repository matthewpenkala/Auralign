# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from local_transcript_max.config import MODEL_MANIFEST
from local_transcript_max.model_delivery import (
    ALLOWED_REDIRECT_HOST_SUFFIXES,
    BOOTSTRAP_MODEL_KEYS,
    MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
    NORMAL_PRESET_MODELS,
    ModelDeliveryCatalogError,
    dump_model_delivery_catalog,
    model_delivery_catalog,
    model_delivery_catalog_sha256,
    validate_model_delivery_catalog,
    write_model_delivery_catalog,
)
from local_transcript_max.model_pack import model_pack_contract, model_pack_sbom


def test_catalog_closes_all_nineteen_models_and_normal_presets() -> None:
    payload = model_delivery_catalog()
    records = {record["key"]: record for record in payload["models"]}

    assert payload["schema_version"] == MODEL_DELIVERY_CATALOG_SCHEMA_VERSION
    assert set(records) == set(MODEL_MANIFEST)
    assert payload["normal_presets"] == NORMAL_PRESET_MODELS
    assert payload["bootstrap"]["model_keys"] == list(BOOTSTRAP_MODEL_KEYS)
    assert payload["pack_contract"] == model_pack_contract()
    assert payload["allowed_redirect_host_suffixes"] == list(
        ALLOWED_REDIRECT_HOST_SUFFIXES
    )
    assert "hf.co" not in payload["allowed_redirect_host_suffixes"]
    assert "us.aws.cdn.hf.co" in payload["allowed_redirect_host_suffixes"]
    assert "cas-server.xethub.hf.co" in payload["allowed_redirect_host_suffixes"]
    assert sum(record["delivery"] == "system-component" for record in records.values()) == 3
    assert sum(record["delivery"] == "native-download" for record in records.values()) == 16

    for key, entry in MODEL_MANIFEST.items():
        record = records[key]
        assert record["revision"] == entry.revision
        assert record["pack_id"] == entry.package_bundle_id
        assert record["pack_version"] == entry.package_bundle_version
        assert record["pack_relative_path"] == entry.package_relative_path
        assert record["aliases"] == list(entry.aliases)
        assert record["directory_aliases"] == list(entry.directory_aliases)
        assert record["acquisition_status"] == entry.acquisition_status
        assert record["runtime_status"] == entry.runtime_status
        assert record["reviewed_at"] == entry.reviewed_at
        assert record["product_classification"] == entry.product_classification
        assert record["backend_compatibility"] == entry.backend_compatibility
        if entry.delivery == "native-download":
            assert record["pack_sbom"] == model_pack_sbom(entry)
        else:
            assert "pack_sbom" not in record
        assert record["total_size"] == sum(entry.expected_size_map.values())
        assert [file["path"] for file in record["files"]] == list(entry.runtime_files)
        assert {file["path"]: file["size"] for file in record["files"]} == (
            entry.expected_size_map
        )
        assert {file["path"]: file["sha256"] for file in record["files"]} == (
            entry.expected_digest_map
        )
        assert record["display_name"]
        assert record["description"]
        assert record["accessibility_label"]
        if entry.delivery == "native-download":
            assert all(
                f"/resolve/{entry.revision}/" in file["url"] for file in record["files"]
            )
        else:
            assert all("url" not in file for file in record["files"])


def test_catalog_is_deterministic_and_source_resource_can_be_regenerated(
    tmp_path: Path,
) -> None:
    first = dump_model_delivery_catalog()
    second = dump_model_delivery_catalog()
    assert first == second
    assert len(model_delivery_catalog_sha256()) == 64

    output = tmp_path / "model_delivery_catalog.json"
    assert write_model_delivery_catalog(output) == output
    assert output.read_text(encoding="utf-8") == first


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["models"][0].__setitem__("revision", "main"),
        lambda payload: payload["models"][0]["files"][0].__setitem__("size", 0),
        lambda payload: payload["models"][0]["files"][0].__setitem__("sha256", "bad"),
        lambda payload: payload["models"][0]["files"][0].__setitem__("path", "../escape"),
        lambda payload: payload["models"][0]["files"][0].__setitem__(
            "url", "http://example.com/model"
        ),
        lambda payload: payload["models"][0].__setitem__("pack_version", "latest"),
        lambda payload: payload["models"][0].__setitem__("pack_id", "com.example.model"),
        lambda payload: payload["models"][0].__setitem__("description", ""),
        lambda payload: payload["models"].append(copy.deepcopy(payload["models"][0])),
    ],
)
def test_catalog_mutations_fail_closed(mutation) -> None:
    payload = model_delivery_catalog()
    mutation(payload)
    with pytest.raises(ModelDeliveryCatalogError):
        validate_model_delivery_catalog(payload)


def test_catalog_destination_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("unrelated", encoding="utf-8")
    link = tmp_path / "catalog.json"
    link.symlink_to(target)
    with pytest.raises(ModelDeliveryCatalogError, match="symbolic link"):
        write_model_delivery_catalog(link)
