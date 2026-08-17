# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from local_transcript_max.config import MODEL_MANIFEST
from local_transcript_max.model_bundle import (
    MODEL_BUNDLE_IDENTIFIER,
    MODEL_BUNDLE_VERSION,
)
from local_transcript_max.model_delivery import (
    MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
    MODEL_DELIVERY_CATALOG_VERSION,
    NORMAL_PRESET_MODELS,
    model_delivery_catalog_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


product_sbom = _load_script("validate_product_sbom")
dependency_sbom = product_sbom.dependency_sbom
standalone_builder = _load_script("build_macos_standalone")

BUNDLED_KEYS = ("community-1", "en", "ecapa")
PRODUCT_REF = "product:com.auralign.studio@0.3.2"


def _model_digest(key: str) -> str:
    entry = MODEL_MANIFEST[key]
    encoded = json.dumps(
        {
            "files": entry.expected_digest_map,
            "model_id": entry.model_id,
            "revision": entry.revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _model_component(key: str) -> dict[str, Any]:
    entry = MODEL_MANIFEST[key]
    assert entry.revision is not None
    publisher, model_name = entry.model_id.split("/", 1)
    digest = _model_digest(key)
    return {
        "bom-ref": f"model:{entry.model_id}@{entry.revision}",
        "type": "machine-learning-model",
        "group": publisher,
        "name": model_name,
        "version": entry.revision,
        "description": entry.attribution,
        "hashes": [{"alg": "SHA-256", "content": digest}],
        "licenses": [
            {
                "license": {
                    "acknowledgement": "declared",
                    "id": entry.license_expression,
                }
            }
        ],
        "externalReferences": [
            {"type": "website", "url": entry.source_url},
            {"type": "license", "url": entry.terms_url},
        ],
        "properties": [
            {"name": "auralign:model-key", "value": key},
            {"name": "auralign:model-role", "value": entry.role},
            {
                "name": "auralign:model-gated-acquisition",
                "value": str(entry.gated).lower(),
            },
            {
                "name": "auralign:model-bundle-id",
                "value": MODEL_BUNDLE_IDENTIFIER,
            },
            {
                "name": "auralign:model-bundle-version",
                "value": MODEL_BUNDLE_VERSION,
            },
            {
                "name": "auralign:model-file-manifest-sha256",
                "value": digest,
            },
        ],
    }


def _complete_product_sbom() -> dict[str, Any]:
    versions = dict(dependency_sbom.NATIVE_PACKAGE_VERSIONS[product_sbom.PRODUCT_PLATFORM])
    versions.update(dependency_sbom.APP_SUPPLEMENTAL_VERSIONS)
    runtime_components = [
        {
            "bom-ref": f"runtime:{name}@{version}",
            "type": (
                "framework"
                if name == "cpython"
                else "application"
                if name in {"ffmpeg", "python-build-standalone"}
                else "library"
            ),
            "name": name,
            "version": version,
        }
        for name, version in sorted(versions.items())
    ]
    model_components = [_model_component(key) for key in BUNDLED_KEYS]
    model_refs = [component["bom-ref"] for component in model_components]
    runtime_ref = next(
        component["bom-ref"]
        for component in runtime_components
        if component["name"] == "local-transcript-max"
    )
    dependencies = [
        {
            "ref": component["bom-ref"],
            "dependsOn": model_refs if component["bom-ref"] == runtime_ref else [],
        }
        for component in runtime_components
    ]
    dependencies.extend(
        {"ref": component["bom-ref"], "dependsOn": []} for component in model_components
    )
    dependencies.append({"ref": PRODUCT_REF, "dependsOn": [runtime_ref]})
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.6.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:00000000-0000-4000-8000-000000000001",
        "version": 1,
        "metadata": {
            "component": {
                "bom-ref": PRODUCT_REF,
                "type": "application",
                "name": "Auralign",
                "version": "0.3.2",
            },
            "properties": [
                {"name": "auralign:commit", "value": "a" * 40},
                {
                    "name": "auralign:model-bundle-id",
                    "value": MODEL_BUNDLE_IDENTIFIER,
                },
                {
                    "name": "auralign:model-bundle-version",
                    "value": MODEL_BUNDLE_VERSION,
                },
                {
                    "name": "auralign:native-model-pack-catalog-schema",
                    "value": MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
                },
                {
                    "name": "auralign:native-model-pack-catalog-version",
                    "value": MODEL_DELIVERY_CATALOG_VERSION,
                },
                {
                    "name": "auralign:native-model-pack-catalog-sha256",
                    "value": model_delivery_catalog_sha256(),
                },
                {"name": "auralign:native-model-pack-count", "value": "16"},
                {
                    "name": "auralign:native-model-pack-normal-presets",
                    "value": json.dumps(
                        NORMAL_PRESET_MODELS,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
                {
                    "name": "auralign:native-model-pack-sbom-contract",
                    "value": "CycloneDX-1.6-per-pack",
                },
                {"name": "auralign:platform", "value": "darwin-arm64-cpu-app"},
                {"name": "auralign:profile", "value": "cpu"},
                {"name": "auralign:python-version", "value": "3.11.14"},
                {"name": "auralign:sbom-kind", "value": "complete-product"},
            ],
        },
        "components": [*runtime_components, *model_components],
        "dependencies": dependencies,
    }


def _metadata_properties(payload: dict[str, Any]) -> list[dict[str, str]]:
    return payload["metadata"]["properties"]


def _property(rows: list[dict[str, str]], name: str) -> dict[str, str]:
    return next(row for row in rows if row["name"] == name)


def _model(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return next(
        component
        for component in payload["components"]
        if component.get("type") == "machine-learning-model"
        and _property(component["properties"], "auralign:model-key")["value"] == key
    )


def _runtime(payload: dict[str, Any]) -> dict[str, Any]:
    return next(
        component
        for component in payload["components"]
        if component.get("name") == "local-transcript-max"
    )


def _dependency(payload: dict[str, Any], reference: str) -> dict[str, Any]:
    return next(row for row in payload["dependencies"] if row["ref"] == reference)


def test_complete_product_sbom_accepts_exact_runtime_and_packaged_model_contract() -> None:
    payload = _complete_product_sbom()

    assert product_sbom.validate_payload(payload) == len(payload["components"])


def test_standalone_builder_augmentation_emits_valid_complete_product_sbom(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _complete_product_sbom()
    model_refs = {
        component["bom-ref"]
        for component in payload["components"]
        if component.get("type") == "machine-learning-model"
    }
    payload["components"] = [
        component
        for component in payload["components"]
        if component.get("bom-ref") not in model_refs
    ]
    for component in payload["components"]:
        component["licenses"] = [
            {
                "license": {
                    "acknowledgement": "declared",
                    "id": "MIT",
                }
            }
        ]
    payload["dependencies"] = [
        {
            **row,
            "dependsOn": [target for target in row["dependsOn"] if target not in model_refs],
        }
        for row in payload["dependencies"]
        if row["ref"] not in model_refs
    ]
    payload["metadata"]["properties"] = [
        {
            **row,
            "value": (
                "standalone-app-runtime" if row["name"] == "auralign:sbom-kind" else row["value"]
            ),
        }
        for row in payload["metadata"]["properties"]
        if row["name"]
        not in {
            "auralign:model-bundle-id",
            "auralign:model-bundle-version",
        }
    ]
    sbom = tmp_path / "runtime.cdx.json"
    sbom.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    model_bundle = tmp_path / MODEL_BUNDLE_IDENTIFIER
    digest_by_path: dict[Path, str] = {}
    for key in BUNDLED_KEYS:
        entry = MODEL_MANIFEST[key]
        model_directory = model_bundle / "models" / entry.directory_name
        for relative, expected_digest in entry.expected_digest_map.items():
            path = model_directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
            digest_by_path[path] = expected_digest

    original_sha256 = standalone_builder.sha256
    monkeypatch.setattr(
        standalone_builder,
        "verify_model_bundle",
        lambda _path: {"state": "verified"},
    )
    monkeypatch.setattr(
        standalone_builder,
        "sha256",
        lambda path: digest_by_path.get(Path(path), original_sha256(Path(path))),
    )

    standalone_builder.augment_product_sbom_with_models(sbom, model_bundle)

    completed = json.loads(sbom.read_text(encoding="utf-8"))
    assert product_sbom.validate_payload(completed) == len(completed["components"])
    assert (
        _property(
            _metadata_properties(completed),
            "auralign:sbom-kind",
        )["value"]
        == "complete-product"
    )
    runtime_dependencies = _dependency(completed, _runtime(completed)["bom-ref"])
    assert model_refs == set(runtime_dependencies["dependsOn"]) & model_refs


def test_complete_product_sbom_accepts_runtime_component_as_product_root() -> None:
    payload = _complete_product_sbom()
    payload["metadata"].pop("component")
    payload["dependencies"].remove(_dependency(payload, PRODUCT_REF))

    assert product_sbom.validate_payload(payload) == len(payload["components"])


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("standalone-app-runtime", "must be complete-product"),
        ("standalone-product-with-offline-models", "must be complete-product"),
    ],
)
def test_complete_product_sbom_rejects_wrong_kind(value: str, message: str) -> None:
    payload = _complete_product_sbom()
    _property(_metadata_properties(payload), "auralign:sbom-kind")["value"] = value

    with pytest.raises(product_sbom.ProductSBOMError, match=message):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_duplicate_top_level_kind() -> None:
    payload = _complete_product_sbom()
    _metadata_properties(payload).append(
        {"name": "auralign:sbom-kind", "value": "complete-product"}
    )

    with pytest.raises(product_sbom.ProductSBOMError, match="duplicate property"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_schema_invalid_payload() -> None:
    payload = _complete_product_sbom()
    payload["components"][0]["type"] = "not-a-cyclonedx-component-type"

    with pytest.raises(product_sbom.ProductSBOMError, match="CycloneDX 1.6 schema"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_missing_model() -> None:
    payload = _complete_product_sbom()
    removed = _model(payload, "en")
    removed_ref = removed["bom-ref"]
    payload["components"].remove(removed)
    payload["dependencies"].remove(_dependency(payload, removed_ref))
    _dependency(payload, _runtime(payload)["bom-ref"])["dependsOn"].remove(removed_ref)

    with pytest.raises(product_sbom.ProductSBOMError, match="exactly 3 packaged model"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_extra_model() -> None:
    payload = _complete_product_sbom()
    extra = copy.deepcopy(_model(payload, "en"))
    extra_ref = "model:example/extra@0000000000000000000000000000000000000000"
    extra["bom-ref"] = extra_ref
    _property(extra["properties"], "auralign:model-key")["value"] = "extra"
    payload["components"].append(extra)
    payload["dependencies"].append({"ref": extra_ref, "dependsOn": []})
    _dependency(payload, _runtime(payload)["bom-ref"])["dependsOn"].append(extra_ref)

    with pytest.raises(product_sbom.ProductSBOMError, match="exactly 3 packaged model"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_duplicate_model_key() -> None:
    payload = _complete_product_sbom()
    _property(
        _model(payload, "en")["properties"],
        "auralign:model-key",
    )["value"] = "community-1"

    with pytest.raises(product_sbom.ProductSBOMError, match="duplicate model component key"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_duplicate_component_reference() -> None:
    payload = _complete_product_sbom()
    _model(payload, "en")["bom-ref"] = _model(payload, "ecapa")["bom-ref"]

    with pytest.raises(product_sbom.ProductSBOMError, match="duplicate component reference"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_tampered_model_reference() -> None:
    payload = _complete_product_sbom()
    component = _model(payload, "en")
    original = component["bom-ref"]
    tampered = f"{original}-tampered"
    component["bom-ref"] = tampered
    _dependency(payload, original)["ref"] = tampered
    runtime_dependencies = _dependency(payload, _runtime(payload)["bom-ref"])["dependsOn"]
    runtime_dependencies[runtime_dependencies.index(original)] = tampered

    with pytest.raises(product_sbom.ProductSBOMError, match="model reference drift"):
        product_sbom.validate_payload(payload)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("digest", "model digest contract drift"),
        ("file-manifest", "model file-manifest digest drift"),
        ("license", "model license drift"),
        ("revision", "model revision drift"),
        ("bundle-id", "model bundle ID drift"),
        ("bundle-version", "model bundle version drift"),
    ],
)
def test_complete_product_sbom_rejects_tampered_model_contract(
    field: str,
    message: str,
) -> None:
    payload = _complete_product_sbom()
    component = _model(payload, "community-1")
    if field == "digest":
        component["hashes"][0]["content"] = "0" * 64
    elif field == "file-manifest":
        _property(
            component["properties"],
            "auralign:model-file-manifest-sha256",
        )["value"] = "0" * 64
    elif field == "license":
        component["licenses"][0]["license"]["id"] = "MIT"
    elif field == "revision":
        component["version"] = "0" * 40
    elif field == "bundle-id":
        _property(
            component["properties"],
            "auralign:model-bundle-id",
        )["value"] = "com.example.wrong"
    else:
        _property(
            component["properties"],
            "auralign:model-bundle-version",
        )["value"] = "999.0.0"

    with pytest.raises(product_sbom.ProductSBOMError, match=message):
        product_sbom.validate_payload(payload)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "auralign:model-bundle-id",
            "com.example.wrong",
            "top-level model bundle ID drift",
        ),
        (
            "auralign:model-bundle-version",
            "999.0.0",
            "top-level model bundle version drift",
        ),
    ],
)
def test_complete_product_sbom_rejects_tampered_bundle_metadata(
    name: str,
    value: str,
    message: str,
) -> None:
    payload = _complete_product_sbom()
    _property(_metadata_properties(payload), name)["value"] = value

    with pytest.raises(product_sbom.ProductSBOMError, match=message):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_unlinked_model() -> None:
    payload = _complete_product_sbom()
    model_ref = _model(payload, "ecapa")["bom-ref"]
    _dependency(payload, _runtime(payload)["bom-ref"])["dependsOn"].remove(model_ref)

    with pytest.raises(product_sbom.ProductSBOMError, match="not linked to every packaged"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_missing_graph_row() -> None:
    payload = _complete_product_sbom()
    model_ref = _model(payload, "ecapa")["bom-ref"]
    payload["dependencies"].remove(_dependency(payload, model_ref))

    with pytest.raises(product_sbom.ProductSBOMError, match="does not exactly cover"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_duplicate_graph_row() -> None:
    payload = _complete_product_sbom()
    payload["dependencies"].append(copy.deepcopy(payload["dependencies"][0]))

    with pytest.raises(
        product_sbom.ProductSBOMError,
        match="CycloneDX 1.6 schema|duplicate dependency reference",
    ):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_unknown_graph_target() -> None:
    payload = _complete_product_sbom()
    _dependency(payload, _runtime(payload)["bom-ref"])["dependsOn"].append("unknown-ref")

    with pytest.raises(product_sbom.ProductSBOMError, match="unknown target"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_product_root_not_linked_to_runtime() -> None:
    payload = _complete_product_sbom()
    _dependency(payload, PRODUCT_REF)["dependsOn"] = []

    with pytest.raises(product_sbom.ProductSBOMError, match="product root is not linked"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_private_path() -> None:
    payload = _complete_product_sbom()
    _model(payload, "community-1")["description"] = (
        "/" + "Users/example/private/model-cache/Systran--faster-whisper-large-v3"
    )

    with pytest.raises(product_sbom.ProductSBOMError, match="private local path"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_accepts_human_readable_file_label() -> None:
    product_sbom._validate_privacy(
        {"label": "declared license file: LICENSE"},
        label="fixture",
    )


@pytest.mark.parametrize(
    "uri",
    (
        "file:///opt/local-model-cache",
        "<file:///opt/local-model-cache>",
        "[file:///opt/local-model-cache]",
        "URL:file:///opt/local-model-cache",
    ),
)
def test_complete_product_sbom_rejects_local_file_uri(uri: str) -> None:
    with pytest.raises(product_sbom.ProductSBOMError, match="local file URI"):
        product_sbom._validate_privacy(
            {"url": uri},
            label="fixture",
        )


def test_runtime_sbom_normalization_makes_leaf_dependencies_explicit() -> None:
    payload = _complete_product_sbom()
    leaf = next(row for row in payload["dependencies"] if not row["dependsOn"])
    leaf.pop("dependsOn")

    dependencies = standalone_builder.normalize_cyclonedx_dependency_graph(payload)

    assert leaf["dependsOn"] == []
    assert dependencies is payload["dependencies"]
    assert product_sbom.validate_payload(payload) == len(payload["components"])


def test_complete_product_sbom_preserves_runtime_subset_validation() -> None:
    payload = _complete_product_sbom()
    torch_component = next(
        component for component in payload["components"] if component.get("name") == "torch"
    )
    torch_component["version"] = "999.0.0"

    with pytest.raises(product_sbom.ProductSBOMError, match="runtime subset is invalid"):
        product_sbom.validate_payload(payload)


def test_complete_product_sbom_rejects_duplicate_json_object_key(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.cdx.json"
    path.write_text(
        '{"bomFormat":"CycloneDX","bomFormat":"CycloneDX","specVersion":"1.6"}',
        encoding="utf-8",
    )

    with pytest.raises(product_sbom.ProductSBOMError, match="duplicate JSON key"):
        product_sbom.validate(path)
