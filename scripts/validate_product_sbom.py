#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Validate the complete-product CycloneDX SBOM for the private macOS product."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Final, cast

import validate_dependency_sbom as dependency_sbom
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator

from local_transcript_max.config import MODEL_MANIFEST, ModelEntry
from local_transcript_max.model_bundle import (
    BOOTSTRAP_MODEL_KEYS,
    MODEL_BUNDLE_IDENTIFIER,
    MODEL_BUNDLE_VERSION,
)
from local_transcript_max.model_delivery import (
    MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
    MODEL_DELIVERY_CATALOG_VERSION,
    NORMAL_PRESET_MODELS,
    model_delivery_catalog_sha256,
)

PACKAGED_MODEL_KEYS: Final = BOOTSTRAP_MODEL_KEYS
PRODUCT_SBOM_KIND: Final = "complete-product"
PRODUCT_PLATFORM: Final = "darwin-arm64-cpu-app"
PRODUCT_PROFILE: Final = "cpu"
RUNTIME_COMPONENT_NAME: Final = "local-transcript-max"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_PRIVATE_PATH = re.compile(
    r"(?:^|[\s\"'`(=])(?:"
    r"/" r"Users/[^/\s]+(?:/|$)|"
    r"/" r"home/[^/\s]+(?:/|$)|"
    r"/(?:private/)?tmp(?:/|$)|"
    r"/(?:private/)?var/folders(?:/|$)|"
    r"~/|"
    r"[A-Za-z]:[\\/]"
    r")",
    re.IGNORECASE,
)
_FILE_URI = re.compile(r"file:/", re.IGNORECASE)
_MODEL_PROPERTY_NAMES: Final = {
    "auralign:model-key",
    "auralign:model-role",
    "auralign:model-gated-acquisition",
    "auralign:model-bundle-id",
    "auralign:model-bundle-version",
    "auralign:model-file-manifest-sha256",
}
_CYCLONEDX_VALIDATOR = JsonStrictValidator(SchemaVersion.V1_6)


class ProductSBOMError(RuntimeError):
    """Raised when a complete-product SBOM fails closed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProductSBOMError(message)


def _as_mapping(value: object, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProductSBOMError(message)
    return cast(dict[str, Any], value)


def _as_mapping_list(value: object, message: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ProductSBOMError(message)
    return cast(list[dict[str, Any]], value)


def _required_string(value: object, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProductSBOMError(message)
    return value


def _as_string_list(value: object, message: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ProductSBOMError(message)
    return cast(list[str], value)


def _properties(container: Mapping[str, Any], *, context: str) -> dict[str, str]:
    rows = _as_mapping_list(
        container.get("properties"),
        f"{context}: properties are missing or invalid",
    )
    properties: dict[str, str] = {}
    for row in rows:
        name = _required_string(row.get("name"), f"{context}: property name is invalid")
        value = _required_string(row.get("value"), f"{context}: property value is invalid")
        require(name not in properties, f"{context}: duplicate property: {name}")
        properties[name] = value
    return properties


def _iter_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_strings(child)


def _validate_privacy(payload: object, *, label: str) -> None:
    private_roots = {
        str(Path.home()).casefold().replace("\\", "/").rstrip("/"),
        str(Path(__file__).resolve().parents[1]).casefold().replace("\\", "/").rstrip("/"),
    }
    for value in _iter_strings(payload):
        normalized = value.casefold().replace("\\", "/")
        require(
            _FILE_URI.search(normalized) is None,
            f"{label}: SBOM contains a local file URI",
        )
        require(
            _PRIVATE_PATH.search(value) is None,
            f"{label}: SBOM contains a private local path",
        )
        require(
            not any(root and root in normalized for root in private_roots),
            f"{label}: SBOM contains a private repository or home path",
        )


def _validate_cyclonedx_schema(payload: dict[str, Any], *, label: str) -> None:
    require(
        payload.get("bomFormat") == "CycloneDX",
        f"{label}: SBOM is not CycloneDX",
    )
    require(
        payload.get("specVersion") == "1.6",
        f"{label}: complete-product SBOM must use CycloneDX 1.6",
    )
    try:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        validation_error = _CYCLONEDX_VALIDATOR.validate_str(encoded)
    except (TypeError, ValueError) as exc:
        raise ProductSBOMError(
            f"{label}: complete-product SBOM cannot be schema validated"
        ) from exc
    require(
        validation_error is None,
        f"{label}: complete-product SBOM fails the CycloneDX 1.6 schema",
    )


def _canonical_model_digest(entry: ModelEntry) -> str:
    require(
        isinstance(entry.revision, str) and _REVISION.fullmatch(entry.revision) is not None,
        f"model authority has no immutable revision: {entry.key}",
    )
    expected_files = entry.expected_digest_map
    require(
        set(expected_files) == set(entry.runtime_files) and bool(expected_files),
        f"model authority has an incomplete digest contract: {entry.key}",
    )
    require(
        all(_SHA256.fullmatch(digest) is not None for digest in expected_files.values()),
        f"model authority has an invalid SHA-256 contract: {entry.key}",
    )
    encoded = json.dumps(
        {
            "files": expected_files,
            "model_id": entry.model_id,
            "revision": entry.revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _license_id(component: Mapping[str, Any], *, key: str, label: str) -> str:
    rows = _as_mapping_list(
        component.get("licenses"),
        f"{label}: model license list is invalid: {key}",
    )
    require(len(rows) == 1, f"{label}: model must have exactly one license: {key}")
    license_row = _as_mapping(
        rows[0].get("license"),
        f"{label}: model license record is invalid: {key}",
    )
    return _required_string(
        license_row.get("id"),
        f"{label}: model license ID is missing: {key}",
    )


def _model_hash(component: Mapping[str, Any], *, key: str, label: str) -> str:
    rows = _as_mapping_list(
        component.get("hashes"),
        f"{label}: model hash list is invalid: {key}",
    )
    require(len(rows) == 1, f"{label}: model must have exactly one digest: {key}")
    algorithm = rows[0].get("alg")
    require(algorithm == "SHA-256", f"{label}: model SHA-256 algorithm is invalid: {key}")
    return _required_string(
        rows[0].get("content"),
        f"{label}: model SHA-256 content is invalid: {key}",
    )


def _external_references(
    component: Mapping[str, Any],
    *,
    key: str,
    label: str,
) -> list[tuple[str, str]]:
    rows = _as_mapping_list(
        component.get("externalReferences"),
        f"{label}: model external references are invalid: {key}",
    )
    references: list[tuple[str, str]] = []
    for row in rows:
        reference_type = _required_string(
            row.get("type"),
            f"{label}: model external reference type is invalid: {key}",
        )
        url = _required_string(
            row.get("url"),
            f"{label}: model external reference URL is invalid: {key}",
        )
        references.append((reference_type, url))
    return references


def _validate_model_component(
    component: dict[str, Any],
    *,
    key: str,
    metadata_properties: Mapping[str, str],
    label: str,
) -> str:
    entry = MODEL_MANIFEST[key]
    require(entry.key == key, f"{label}: model manifest key drift: {key}")
    require(
        entry.product_classification == "production" and entry.blocker is None,
        f"{label}: model is not product-cleared: {key}",
    )
    require(
        entry.package_bundle_id == MODEL_BUNDLE_IDENTIFIER,
        f"{label}: model package bundle authority drift: {key}",
    )
    expected_digest = _canonical_model_digest(entry)
    assert entry.revision is not None
    expected_reference = f"model:{entry.model_id}@{entry.revision}"
    publisher, model_name = entry.model_id.split("/", 1)
    properties = _properties(component, context=f"{label}: model {key}")

    require(
        component.get("type") == "machine-learning-model",
        f"{label}: model component type drift: {key}",
    )
    require(
        component.get("bom-ref") == expected_reference,
        f"{label}: model reference drift: {key}",
    )
    require(
        component.get("group") == publisher and component.get("name") == model_name,
        f"{label}: model identity drift: {key}",
    )
    require(
        component.get("version") == entry.revision,
        f"{label}: model revision drift: {key}",
    )
    require(
        component.get("description") == entry.attribution,
        f"{label}: model attribution drift: {key}",
    )
    require(
        _license_id(component, key=key, label=label) == entry.license_expression,
        f"{label}: model license drift: {key}",
    )
    require(
        _model_hash(component, key=key, label=label) == expected_digest,
        f"{label}: model digest contract drift: {key}",
    )
    require(
        properties.get("auralign:model-key") == key
        and properties.get("auralign:model-role") == entry.role
        and properties.get("auralign:model-gated-acquisition") == str(entry.gated).lower(),
        f"{label}: model authority properties drift: {key}",
    )
    require(
        properties.get("auralign:model-bundle-id")
        == entry.package_bundle_id
        == metadata_properties["auralign:model-bundle-id"],
        f"{label}: model bundle ID drift: {key}",
    )
    component_bundle_version = properties.get("auralign:model-bundle-version")
    require(
        component_bundle_version in {None, MODEL_BUNDLE_VERSION},
        f"{label}: model bundle version drift: {key}",
    )
    require(
        properties.get("auralign:model-file-manifest-sha256") == expected_digest,
        f"{label}: model file-manifest digest drift: {key}",
    )
    require(
        sorted(_external_references(component, key=key, label=label))
        == sorted(
            [
                ("license", entry.terms_url),
                ("website", entry.source_url),
            ]
        ),
        f"{label}: model source or terms reference drift: {key}",
    )
    return expected_reference


def _normalize_name(value: object) -> str:
    return re.sub(r"[-_.]+", "-", str(value).casefold())


def _validate_graph(
    payload: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any],
    components: list[dict[str, Any]],
    component_refs: set[str],
    model_refs: set[str],
    label: str,
) -> None:
    dependency_rows = _as_mapping_list(
        payload.get("dependencies"),
        f"{label}: dependency graph is missing or invalid",
    )
    graph: dict[str, tuple[str, ...]] = {}
    for row in dependency_rows:
        reference = _required_string(
            row.get("ref"),
            f"{label}: dependency graph reference is invalid",
        )
        typed_targets = _as_string_list(
            row.get("dependsOn"),
            f"{label}: dependency target is invalid: {reference}",
        )
        require(
            len(typed_targets) == len(set(typed_targets)),
            f"{label}: duplicate dependency target: {reference}",
        )
        require(reference not in graph, f"{label}: duplicate dependency reference: {reference}")
        require(reference not in typed_targets, f"{label}: self dependency: {reference}")
        graph[reference] = tuple(typed_targets)

    runtime_components = [
        component
        for component in components
        if _normalize_name(component.get("name")) == RUNTIME_COMPONENT_NAME
    ]
    require(
        len(runtime_components) == 1,
        f"{label}: complete product must contain one local-transcript-max runtime",
    )
    runtime_ref = _required_string(
        runtime_components[0].get("bom-ref"),
        f"{label}: runtime component reference is missing",
    )

    metadata_component = metadata.get("component")
    if metadata_component is None:
        product_ref = runtime_ref
    else:
        root_component = _as_mapping(
            metadata_component,
            f"{label}: metadata root component is invalid",
        )
        product_ref = _required_string(
            root_component.get("bom-ref"),
            f"{label}: metadata root component reference is missing",
        )

    valid_refs = set(component_refs)
    valid_refs.add(product_ref)
    require(
        set(graph) == valid_refs,
        f"{label}: dependency graph does not exactly cover product components",
    )
    for reference, targets in graph.items():
        require(
            set(targets) <= valid_refs,
            f"{label}: dependency graph contains an unknown target: {reference}",
        )
    require(
        model_refs <= set(graph[runtime_ref]),
        f"{label}: runtime root is not linked to every packaged model component",
    )
    if product_ref != runtime_ref:
        require(
            runtime_ref in graph[product_ref],
            f"{label}: product root is not linked to the runtime root",
        )
    for model_ref in model_refs:
        require(
            graph[model_ref] == (),
            f"{label}: model component has unexpected dependencies: {model_ref}",
        )


def _validate_runtime_subset(
    payload: dict[str, Any],
    *,
    model_refs: set[str],
    metadata_properties: Mapping[str, str],
    label: str,
) -> None:
    runtime_payload = copy.deepcopy(payload)
    runtime_components = _as_mapping_list(
        runtime_payload.get("components"),
        f"{label}: runtime component list is invalid",
    )
    runtime_components = [
        component for component in runtime_components if component.get("bom-ref") not in model_refs
    ]
    runtime_refs = {cast(str, component["bom-ref"]) for component in runtime_components}
    runtime_payload["components"] = runtime_components

    runtime_dependencies = _as_mapping_list(
        runtime_payload.get("dependencies"),
        f"{label}: runtime dependency graph is invalid",
    )
    runtime_payload["dependencies"] = [
        {
            **row,
            "dependsOn": [
                target
                for target in cast(list[object], row.get("dependsOn", []))
                if target in runtime_refs
            ],
        }
        for row in runtime_dependencies
        if row.get("ref") in runtime_refs
    ]

    runtime_metadata = _as_mapping(
        runtime_payload.get("metadata"),
        f"{label}: runtime metadata is invalid",
    )
    runtime_properties = _as_mapping_list(
        runtime_metadata.get("properties"),
        f"{label}: runtime metadata properties are invalid",
    )
    runtime_metadata["properties"] = [
        {
            **row,
            "value": (
                "standalone-app-runtime"
                if row.get("name") == "auralign:sbom-kind"
                else row.get("value")
            ),
        }
        for row in runtime_properties
        if row.get("name")
        not in {
            "auralign:model-bundle-id",
            "auralign:model-bundle-version",
        }
    ]

    try:
        dependency_sbom.validate_payload(
            runtime_payload,
            PRODUCT_PROFILE,
            label=f"{label}: runtime subset",
            platform_name=PRODUCT_PLATFORM,
            commit=metadata_properties["auralign:commit"],
        )
    except RuntimeError as exc:
        raise ProductSBOMError(f"{label}: complete-product runtime subset is invalid") from exc


def validate_payload(payload: object, *, label: str = "product SBOM") -> int:
    """Validate one decoded complete-product SBOM and return its component count."""

    root = _as_mapping(payload, f"{label}: SBOM root is invalid")
    _validate_privacy(root, label=label)
    _validate_cyclonedx_schema(root, label=label)

    metadata = _as_mapping(root.get("metadata"), f"{label}: metadata is missing or invalid")
    metadata_properties = _properties(metadata, context=f"{label}: metadata")
    require(
        metadata_properties.get("auralign:sbom-kind") == PRODUCT_SBOM_KIND,
        f"{label}: top-level auralign:sbom-kind must be complete-product",
    )
    require(
        metadata_properties.get("auralign:model-bundle-id") == MODEL_BUNDLE_IDENTIFIER,
        f"{label}: top-level model bundle ID drift",
    )
    require(
        metadata_properties.get("auralign:model-bundle-version") == MODEL_BUNDLE_VERSION,
        f"{label}: top-level model bundle version drift",
    )
    require(
        metadata_properties.get("auralign:native-model-pack-catalog-schema")
        == MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
        f"{label}: native model-pack catalog schema drift",
    )
    require(
        metadata_properties.get("auralign:native-model-pack-catalog-version")
        == MODEL_DELIVERY_CATALOG_VERSION,
        f"{label}: native model-pack catalog version drift",
    )
    require(
        metadata_properties.get("auralign:native-model-pack-catalog-sha256")
        == model_delivery_catalog_sha256(),
        f"{label}: native model-pack catalog digest drift",
    )
    require(
        metadata_properties.get("auralign:native-model-pack-count")
        == str(
            sum(
                entry.delivery == "native-download"
                for entry in MODEL_MANIFEST.values()
            )
        ),
        f"{label}: native model-pack count drift",
    )
    require(
        metadata_properties.get("auralign:native-model-pack-normal-presets")
        == json.dumps(NORMAL_PRESET_MODELS, sort_keys=True, separators=(",", ":")),
        f"{label}: native model-pack preset contract drift",
    )
    require(
        metadata_properties.get("auralign:native-model-pack-sbom-contract")
        == "CycloneDX-1.6-per-pack",
        f"{label}: native model-pack SBOM contract drift",
    )
    commit = metadata_properties.get("auralign:commit", "")
    require(
        _REVISION.fullmatch(commit) is not None,
        f"{label}: top-level source commit is not immutable",
    )

    components = _as_mapping_list(
        root.get("components"),
        f"{label}: component list is missing or invalid",
    )
    require(bool(components), f"{label}: component list is empty")
    component_refs: set[str] = set()
    model_components: list[dict[str, Any]] = []
    for component in components:
        reference = _required_string(
            component.get("bom-ref"),
            f"{label}: component reference is missing",
        )
        require(
            reference not in component_refs,
            f"{label}: duplicate component reference: {reference}",
        )
        component_refs.add(reference)
        if component.get("type") == "machine-learning-model":
            model_components.append(component)
        else:
            if reference.startswith("model:"):
                raise ProductSBOMError(
                    f"{label}: model reference is disguised as a non-model component"
                )
            component_properties = component.get("properties")
            if component_properties is not None:
                observed = _properties(component, context=f"{label}: component {reference}")
                require(
                    not (_MODEL_PROPERTY_NAMES & observed.keys()),
                    f"{label}: non-model component contains model authority properties",
                )

    require(
        len(model_components) == len(PACKAGED_MODEL_KEYS),
        (
            f"{label}: complete product must contain exactly "
            f"{len(PACKAGED_MODEL_KEYS)} packaged model components"
        ),
    )
    by_key: dict[str, dict[str, Any]] = {}
    for component in model_components:
        properties = _properties(component, context=f"{label}: model component")
        key = _required_string(
            properties.get("auralign:model-key"),
            f"{label}: model component key is missing",
        )
        require(
            key in PACKAGED_MODEL_KEYS,
            f"{label}: unexpected model component key: {key}",
        )
        require(key not in by_key, f"{label}: duplicate model component key: {key}")
        by_key[key] = component
    require(
        set(by_key) == set(PACKAGED_MODEL_KEYS),
        f"{label}: packaged model component set is incomplete",
    )

    model_refs = {
        _validate_model_component(
            by_key[key],
            key=key,
            metadata_properties=metadata_properties,
            label=label,
        )
        for key in PACKAGED_MODEL_KEYS
    }
    require(
        len(model_refs) == len(PACKAGED_MODEL_KEYS),
        f"{label}: duplicate packaged model references",
    )
    _validate_graph(
        root,
        metadata=metadata,
        components=components,
        component_refs=component_refs,
        model_refs=model_refs,
        label=label,
    )
    _validate_runtime_subset(
        root,
        model_refs=model_refs,
        metadata_properties=metadata_properties,
        label=label,
    )
    return len(components)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProductSBOMError(f"product SBOM contains duplicate JSON key: {key}")
        result[key] = value
    return result


def validate(path: Path) -> int:
    """Load and validate a complete-product SBOM from disk."""

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductSBOMError("product SBOM cannot be read as canonical JSON") from exc
    count = validate_payload(payload, label=str(path))
    print(
        "product_sbom_valid=true "
        f"kind={PRODUCT_SBOM_KIND} components={count} models={len(PACKAGED_MODEL_KEYS)}"
    )
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    validate(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
