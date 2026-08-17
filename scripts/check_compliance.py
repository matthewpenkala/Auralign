#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fail-closed repository and packaging compliance checks."""

from __future__ import annotations

import hashlib
import re
import sys
import tomllib
from pathlib import Path

from local_transcript_max.config import MODEL_MANIFEST
from local_transcript_max.datasets import DATASET_MANIFEST
from local_transcript_max.model_bundle import MODEL_BUNDLE_NLTK_VERSION

ROOT = Path(__file__).resolve().parents[1]
PROJECT_LICENSE = "LicenseRef-PolyForm-Internal-Use-1.0.0"
LICENSE_SHA256 = "1ba74ee07fe40289275d57fd6a7206d350560e8246fb661e510c7ccd70fe883a"
SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def read_toml(relative: str) -> dict[str, object]:
    return tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))


def parse_model_file_contract(
    row: dict[str, object],
    field: str,
    *,
    parse_value: type[str] | type[int],
) -> dict[str, str] | dict[str, int]:
    raw_items = row.get(field)
    require(isinstance(raw_items, list), f"model {field} is missing: {row.get('key')}")
    parsed: dict[str, str] | dict[str, int] = {}
    for raw_item in raw_items:
        require(isinstance(raw_item, str) and "=" in raw_item, f"invalid {field} item")
        relative, raw_value = raw_item.split("=", 1)
        require(
            bool(relative) and not relative.startswith("/") and ".." not in Path(relative).parts,
            f"unsafe model contract path: {relative}",
        )
        require(relative not in parsed, f"duplicate model contract path: {relative}")
        if parse_value is int:
            require(raw_value.isdecimal(), f"invalid model byte count: {raw_item}")
            value: str | int = int(raw_value)
            require(value > 0, f"non-positive model byte count: {raw_item}")
        else:
            require(SHA256.fullmatch(raw_value) is not None, f"invalid model digest: {raw_item}")
            value = raw_value
        parsed[relative] = value
    return parsed


def verify_legal_files() -> None:
    required = [
        "LICENSE",
        "LICENSES/LicenseRef-PolyForm-Internal-Use-1.0.0.txt",
        "NOTICE",
        "THIRD_PARTY_NOTICES.md",
        "compliance/THIRD_PARTY_COMPONENTS.generated.md",
        "REUSE.toml",
        "CONTRIBUTING.md",
        "SECURITY.md",
    ]
    for relative in required:
        path = ROOT / relative
        require(
            path.is_file() and not path.is_symlink(),
            f"missing or unsafe legal file: {relative}",
        )
    license_text = (ROOT / "LICENSE").read_bytes()
    reuse_license = (ROOT / "LICENSES/LicenseRef-PolyForm-Internal-Use-1.0.0.txt").read_bytes()
    require(license_text == reuse_license, "root and REUSE license texts differ")
    require(hashlib.sha256(license_text).hexdigest() == LICENSE_SHA256, "license text drift")
    require(
        license_text.startswith(b"Required Notice: Copyright 2026 Matthew Penkala\n"),
        "PolyForm required notice is missing or changed",
    )


def verify_project_metadata() -> None:
    data = read_toml("pyproject.toml")
    project = data["project"]
    assert isinstance(project, dict)
    require(project.get("license") == PROJECT_LICENSE, "wrong project license expression")
    require("Private :: Do Not Upload" in project.get("classifiers", []), "PyPI guard missing")
    authors = project.get("authors", [])
    require(authors == [{"name": "Matthew Penkala"}], "unexpected project author metadata")
    license_files = set(project.get("license-files", []))
    require(
        {
            "LICENSE",
            "LICENSES/*",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "compliance/THIRD_PARTY_COMPONENTS.generated.md",
        }
        <= license_files,
        "package license files are incomplete",
    )
    optional_dependencies = project.get("optional-dependencies")
    require(
        isinstance(optional_dependencies, dict),
        "project optional dependencies are missing",
    )
    presentation_sat = optional_dependencies.get("presentation-sat")
    require(
        presentation_sat == ["wtpsplit==2.2.1"],
        "presentation-sat must remain an exact source/headless-only dependency",
    )
    full_requirements = optional_dependencies.get("full")
    require(isinstance(full_requirements, list), "full extra is missing")
    full_names = {requirement_name(item) for item in full_requirements}
    require(
        not {"apple-fm-sdk", "wtpsplit"}.intersection(full_names),
        "source/headless-only proposal dependencies entered the full extra",
    )
    policy = read_toml("compliance/policy.toml")
    release_authorizations = policy.get("release_authorizations")
    require(
        isinstance(release_authorizations, dict),
        "release authorizations are missing",
    )
    historical_release = release_authorizations.get("macos_0_3_0")
    require(
        isinstance(historical_release, dict)
        and str(historical_release.get("runtime_asset_contract", "")).startswith("NLTK 3.9.4;"),
        "accepted 0.3.0 Punkt contract drift",
    )
    successor_release = release_authorizations.get("macos_0_3_1")
    require(
        isinstance(successor_release, dict)
        and successor_release.get("status") == "owner_cleared_project_release_decision"
        and successor_release.get("approved_by") == "Matthew Penkala"
        and successor_release.get("approved_at") == "2026-08-16"
        and successor_release.get("visibility") == "private_only"
        and successor_release.get("inherits_exact_model_contract_from") == "macos_0_3_0"
        and str(successor_release.get("runtime_asset_contract", "")).startswith("NLTK 3.10.3;")
        and successor_release.get("component_root")
        == "/Library/Application Support/com.auralign.studio/Models/"
        "com.auralign.studio.models.bootstrap.v2"
        and successor_release.get("component_version") == "2.0.0"
        and successor_release.get("component_receipt")
        == "com.auralign.studio.pkg.models.bootstrap-v2",
        "authorized 0.3.1 successor release contract drift",
    )
    current_release = release_authorizations.get("macos_0_3_2")
    require(
        isinstance(current_release, dict)
        and current_release.get("status") == "owner_cleared_project_release_decision"
        and current_release.get("approved_by") == "Matthew Penkala"
        and current_release.get("approved_at") == "2026-08-16"
        and current_release.get("visibility") == "private_only"
        and current_release.get("inherits_exact_model_contract_from") == "macos_0_3_1"
        and current_release.get("model_keys") == successor_release.get("model_keys")
        and current_release.get("normal_preset_asr") == successor_release.get("normal_preset_asr")
        and current_release.get("bootstrap_model_keys")
        == successor_release.get("bootstrap_model_keys")
        and current_release.get("native_download_model_keys")
        == successor_release.get("native_download_model_keys")
        and current_release.get("runtime_asset_contract")
        == successor_release.get("runtime_asset_contract")
        and current_release.get("component_root") == successor_release.get("component_root")
        and current_release.get("component_version") == "2.0.0"
        and current_release.get("component_receipt")
        == "com.auralign.studio.pkg.models.bootstrap-v2",
        "authorized 0.3.2 successor release contract drift",
    )
    development_contracts = policy.get("development_contracts")
    require(
        isinstance(development_contracts, dict),
        "development contracts are missing",
    )
    punkt_contract = development_contracts.get("nltk_punkt_successor")
    require(
        isinstance(punkt_contract, dict)
        and punkt_contract.get("status") == "release_successor_macos_0_3_1"
        and punkt_contract.get("nltk_version") == MODEL_BUNDLE_NLTK_VERSION
        and punkt_contract.get("runtime_asset_key") == "nltk-punkt-tab-english-corpus-free"
        and punkt_contract.get("empty_file_sha256")
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        and punkt_contract.get("marker_size") == 547
        and punkt_contract.get("marker_sha256")
        == "2cf9fc3a66d8fb081b5d99201c4382b7ece315747c7ad4593e9d5ca8656ac65e"
        and punkt_contract.get("historical_release_effect")
        == "none; accepted 0.3.0 facts and artifacts remain immutable",
        "successor NLTK Punkt development contract drift",
    )


def verify_asset_registry() -> None:
    data = read_toml("compliance/external_assets.toml")
    policy = read_toml("compliance/policy.toml")
    requirements = policy.get("requirements")
    require(isinstance(requirements, dict), "external asset requirements are missing")
    model_release_fields = requirements.get("required_model_release_fields")
    require(
        isinstance(model_release_fields, list)
        and all(isinstance(field, str) and field for field in model_release_fields),
        "required model release fields are missing",
    )
    required_model_release_fields = set(model_release_fields)
    rows = data.get("assets")
    require(isinstance(rows, list), "external asset registry has no assets")
    presentation_rows = data.get("presentation_assets")
    require(
        isinstance(presentation_rows, list),
        "external asset registry has no presentation assets",
    )
    defaults = data.get("defaults")
    require(isinstance(defaults, dict), "external asset registry has no defaults")
    required_effective = {
        "bundling_status",
        "commercial_use_status",
        "notice_obligations",
        "privacy_classification",
        "reviewer",
        "source_offer_obligations",
    }
    for row in rows:
        effective = defaults | row
        require(
            required_effective <= effective.keys(),
            f"asset governance fields missing: {row.get('key')}",
        )
    indexed = {(row["kind"], row["key"]): row for row in rows}
    require(len(indexed) == len(rows), "duplicate external asset registry key")
    require(len(MODEL_MANIFEST) + len(DATASET_MANIFEST) == len(rows), "asset count drift")

    presentation_indexed = {(row["kind"], row["key"]): row for row in presentation_rows}
    require(
        len(presentation_indexed) == len(presentation_rows),
        "duplicate presentation asset registry key",
    )
    expected_presentation = {
        ("presentation-model", "presentation-sat-3l-sm"): (
            "segment-any-text/sat-3l-sm",
            "137da054051ad9f1eac42025f758db4ac9f22535",
            {
                "config.json": "d61498bc239f773126ee794446cdcad88822967e0b02ae7bd070f725c98be791",
                "model_optimized.onnx": (
                    "8573277b4dbea9c5fb1b4cfd8c21e5aa628069ac8258d1342ba664e1b64ada6d"
                ),
            },
            {"config.json": 965, "model_optimized.onnx": 427756189},
        ),
        ("presentation-tokenizer", "presentation-sat-xlm-roberta-base-tokenizer"): (
            "facebookAI/xlm-roberta-base",
            "e73636d4f797dec63c3081bb6ed5c7b0bb3f2089",
            {
                "config.json": "d66ed8cd4f2a93b358c245e50736fa389ed4f35c0bae7aad0b32abb20c62b579",
                "sentencepiece.bpe.model": (
                    "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865"
                ),
                "tokenizer.json": (
                    "a898ea75433890f6610f4e470b8ebeb0c21dce5c8dd61f892eb09eb5919d2e2c"
                ),
                "tokenizer_config.json": (
                    "994f46754c5bf4014f1aa92d34b1374319c3a6b3f702105cd5b742beaecd18ce"
                ),
            },
            {
                "config.json": 615,
                "sentencepiece.bpe.model": 5069051,
                "tokenizer.json": 9096718,
                "tokenizer_config.json": 25,
            },
        ),
    }
    require(
        set(presentation_indexed) == set(expected_presentation),
        "presentation asset registry drift",
    )
    for registry_key, (identifier, revision, digests, sizes) in expected_presentation.items():
        row = presentation_indexed[registry_key]
        require(
            row.get("identifier") == identifier,
            f"presentation asset identifier drift: {registry_key[1]}",
        )
        require(
            row.get("revision") == revision,
            f"presentation asset revision drift: {registry_key[1]}",
        )
        require(
            row.get("license_expression") == "MIT",
            f"presentation asset license drift: {registry_key[1]}",
        )
        require(
            row.get("runtime_status")
            == "approved_source_headless_local_proposal_signal_exact_files_required",
            f"presentation asset runtime disposition drift: {registry_key[1]}",
        )
        require(
            row.get("product_classification") == "source-headless-production-proposal-only",
            f"presentation asset product classification drift: {registry_key[1]}",
        )
        require(
            row.get("standalone_disposition") == "excluded_from_macos14_app_and_pkg",
            f"presentation asset standalone disposition drift: {registry_key[1]}",
        )
        require(
            row.get("runtime_locality") == "offline_local_only_no_runtime_acquisition",
            f"presentation asset locality drift: {registry_key[1]}",
        )
        require(
            parse_model_file_contract(row, "file_sha256", parse_value=str) == digests,
            f"presentation asset digest contract drift: {registry_key[1]}",
        )
        require(
            parse_model_file_contract(row, "file_size", parse_value=int) == sizes,
            f"presentation asset size contract drift: {registry_key[1]}",
        )

    for key, entry in MODEL_MANIFEST.items():
        row = indexed.get(("model", key))
        require(row is not None, f"model missing from registry: {key}")
        require(
            required_model_release_fields <= row.keys(),
            f"model release governance fields missing: {key}",
        )
        require(
            SHA40.fullmatch(entry.revision or "") is not None,
            f"model revision not pinned: {key}",
        )
        require(row["revision"] == entry.revision, f"model revision drift: {key}")
        require(row["identifier"] == entry.model_id, f"model identifier drift: {key}")
        require(
            row["license_expression"] == entry.license_expression,
            f"model license drift: {key}",
        )
        require(row["source_url"] == entry.source_url, f"model source URL drift: {key}")
        require(row["terms_url"] == entry.terms_url, f"model terms URL drift: {key}")
        require(
            row["acquisition_status"] == entry.acquisition_status,
            f"model acquisition status drift: {key}",
        )
        require(
            row["runtime_status"] == entry.runtime_status,
            f"model runtime status drift: {key}",
        )
        require(
            row["redistribution_status"] == entry.redistribution_status,
            f"model redistribution status drift: {key}",
        )
        require(row["review_status"] == entry.review_status, f"model review drift: {key}")
        require(
            row["product_classification"] == entry.product_classification,
            f"model product classification drift: {key}",
        )
        require(
            row["backend_compatibility"] == entry.backend_compatibility,
            f"model backend compatibility drift: {key}",
        )
        require(
            row["modification_status"] == entry.modification_status,
            f"model modification status drift: {key}",
        )
        require(
            row["package_bundle_id"] == entry.package_bundle_id,
            f"model package bundle ID drift: {key}",
        )
        require(
            row["package_bundle_version"] == entry.package_bundle_version,
            f"model package bundle version drift: {key}",
        )
        require(
            row["package_relative_path"] == entry.package_relative_path,
            f"model package path drift: {key}",
        )
        require(row["delivery"] == entry.delivery, f"model delivery drift: {key}")
        require(
            parse_model_file_contract(
                row,
                "macos_0_3_0_file_sha256",
                parse_value=str,
            )
            == entry.expected_digest_map,
            f"model digest contract drift: {key}",
        )
        require(
            parse_model_file_contract(
                row,
                "macos_0_3_0_file_size",
                parse_value=int,
            )
            == entry.expected_size_map,
            f"model size contract drift: {key}",
        )
        expected_product_status = {
            "system-component": "bundled_bootstrap_component",
            "native-download": "native_download_pack",
        }.get(entry.delivery)
        require(
            row["macos_0_3_0_product_status"] == expected_product_status,
            f"model product delivery disposition drift: {key}",
        )
        owner_decision = row.get("macos_0_3_0_owner_decision")
        require(
            isinstance(owner_decision, str)
            and "Matthew Penkala" in owner_decision
            and "2026-07-25" in owner_decision
            and "not legal opinion" in owner_decision,
            f"model owner clearance record missing: {key}",
        )
        require(entry.production_selectable, f"model remains code-wired-only: {key}")
        if entry.license_expression == "NOASSERTION":
            require(
                entry.redistribution_status.startswith("blocked"),
                f"unlicensed model not blocked: {key}",
            )
            require(not entry.pullable, f"unlicensed model acquisition enabled: {key}")

    for key, entry in DATASET_MANIFEST.items():
        row = indexed.get(("dataset", key))
        require(row is not None, f"dataset missing from registry: {key}")
        require(SHA40.fullmatch(entry.revision) is not None, f"dataset revision not pinned: {key}")
        require(row["revision"] == entry.revision, f"dataset revision drift: {key}")
        require(
            row["license_expression"] == entry.license_expression,
            f"dataset license drift: {key}",
        )
        require(row["review_status"] == entry.review_status, f"dataset review drift: {key}")
        if entry.license_expression == "NOASSERTION":
            require(
                not entry.acquisition_approved,
                f"unlicensed dataset acquisition enabled: {key}",
            )
        if key == "voxceleb-full":
            require(not entry.mirror_approved, "VoxCeleb mirror must remain blocked")


def requirement_name(requirement: str) -> str:
    match = re.match(r"[A-Za-z0-9_.-]+", requirement)
    require(match is not None, f"invalid requirement: {requirement}")
    assert match is not None
    return re.sub(r"[-_.]+", "-", match.group().lower())


def verify_component_registry() -> None:
    registry = read_toml("compliance/components.toml")
    defaults = registry.get("defaults")
    rows = registry.get("components")
    require(isinstance(defaults, dict), "component registry has no defaults")
    require(isinstance(rows, list), "component registry has no components")
    policy = read_toml("compliance/policy.toml")
    classes = policy.get("license_classes")
    require(isinstance(classes, dict), "license policy classes missing")
    recognized = set().union(*(set(value) for value in classes.values()))
    required = {
        "bundling_status",
        "category",
        "commercial_use_status",
        "id",
        "identifier",
        "license_expression",
        "notice_obligations",
        "privacy_classification",
        "redistribution_status",
        "review_date",
        "review_status",
        "reviewer",
        "source_offer_obligations",
        "source_url",
        "terms_url",
        "usage",
        "versions",
    }
    indexed: dict[str, dict[str, object]] = {}
    for row in rows:
        effective = defaults | row
        missing = required.difference(effective)
        require(not missing, f"component fields missing for {row.get('id')}: {sorted(missing)}")
        raw_identifier = str(effective["identifier"])
        identifier = (
            requirement_name(raw_identifier)
            if str(effective["category"]).startswith("python-")
            else raw_identifier.lower()
        )
        require(identifier not in indexed, f"duplicate component identifier: {identifier}")
        indexed[identifier] = effective
        expression = str(effective["license_expression"])
        tokens = set(re.findall(r"LicenseRef-[A-Za-z0-9.-]+|[A-Za-z0-9.-]+", expression))
        tokens.difference_update({"AND", "OR", "WITH"})
        require(tokens <= recognized, f"unclassified component license: {expression}")
        if tokens.intersection(
            set(classes["conditionally_allowed"])
            | set(classes["human_review_required"])
            | set(classes["prohibited_without_owner_evidence"])
        ):
            disposition = str(effective["redistribution_status"])
            require(
                "blocked" in disposition or "conditional" in disposition,
                f"conditional component lacks release block: {effective['id']}",
            )

    pyproject = read_toml("pyproject.toml")
    project = pyproject["project"]
    assert isinstance(project, dict)
    requirements = list(project["dependencies"])
    for values in project["optional-dependencies"].values():
        requirements.extend(values)
    for values in pyproject["dependency-groups"].values():
        requirements.extend(values)
    requirements.extend(pyproject["build-system"]["requires"])
    direct = {requirement_name(item) for item in requirements}
    missing_components = direct.difference(indexed)
    require(not missing_components, f"direct components unregistered: {sorted(missing_components)}")

    lock = read_toml("uv.lock")
    locked: dict[str, set[str]] = {}
    for package in lock["package"]:
        name = requirement_name(package["name"])
        locked.setdefault(name, set()).add(package["version"])
    for name in direct.difference({"hatchling"}):
        versions = set(indexed[name]["versions"])
        require(locked.get(name, set()) <= versions, f"component version drift: {name}")


def verify_workflow_pins_and_container_status() -> None:
    workflow_paths = sorted((ROOT / ".github/workflows").glob("*.y*ml"))
    require(bool(workflow_paths), "no GitHub workflows found")
    workflows = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in workflow_paths
    }
    uses: list[str] = []
    for relative, workflow in workflows.items():
        current_uses = re.findall(r"uses:\s*([^\s#]+)", workflow)
        uses.extend(current_uses)
        for use in current_uses:
            require(
                re.search(r"@[0-9a-f]{40}$", use) is not None,
                f"workflow action is not pinned in {relative}: {use}",
            )
    components = read_toml("compliance/components.toml")["components"]
    action_records = {
        row["identifier"]: set(row["versions"])
        for row in components
        if row["category"] == "github-action"
    }
    for use in uses:
        identifier, revision = use.rsplit("@", 1)
        require(revision in action_records.get(identifier, set()), f"action registry drift: {use}")
    forbidden_release_authority = (
        "docker push",
        "id-token: write",
        "packages: write",
        "pypa/gh-action-pypi-publish",
        "twine upload",
        "uv publish",
    )
    for marker in forbidden_release_authority:
        for relative, workflow in workflows.items():
            require(
                marker not in workflow,
                f"workflow contains release authority in {relative}: {marker}",
            )
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    require(
        "internal-only-not-cleared-for-redistribution" in dockerfile,
        "container status missing",
    )
    require("ACCELERATOR_GROUP" in dockerfile, "container accelerator profile is not explicit")
    require("@sha256:" in dockerfile, "container base images are not digest pinned")
    require("ffmpeg=${FFMPEG_VERSION}" in dockerfile, "container FFmpeg package is not pinned")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    require(
        "CA_CERTIFICATES_VERSION: 20260601~24.04.1" in compose,
        "Ubuntu CUDA ca-certificates pin missing",
    )
    require(
        "FFMPEG_VERSION: 7:6.1.1-3ubuntu5" in compose,
        "Ubuntu CUDA FFmpeg pin missing",
    )
    require(
        (ROOT / "compliance/ffmpeg-bookworm-buildconf.txt").is_file(),
        "FFmpeg build configuration inventory missing",
    )


def main() -> int:
    try:
        verify_legal_files()
        verify_project_metadata()
        verify_asset_registry()
        verify_component_registry()
        verify_workflow_pins_and_container_status()
    except (AssertionError, KeyError, OSError, RuntimeError, TypeError) as exc:
        print(f"compliance_error={exc}", file=sys.stderr)
        return 1
    print("compliance_ok=true")
    print(f"models={len(MODEL_MANIFEST)} datasets={len(DATASET_MANIFEST)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
