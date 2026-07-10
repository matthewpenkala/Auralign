#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Validate deterministic CycloneDX dependency-graph invariants."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

CPU_PROHIBITED = re.compile(r"^(?:triton|nvidia-|.*cuda|.*cudnn|.*nccl)", re.IGNORECASE)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate(path: Path, profile: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(payload.get("bomFormat") == "CycloneDX", f"{path}: not CycloneDX")
    require(payload.get("specVersion") == "1.5", f"{path}: unexpected schema version")
    require(str(payload.get("serialNumber", "")).startswith("urn:uuid:"), f"{path}: serial")
    metadata = payload.get("metadata")
    require(isinstance(metadata, dict), f"{path}: metadata missing")
    root = metadata.get("component")
    require(isinstance(root, dict) and root.get("bom-ref"), f"{path}: root component missing")
    components = payload.get("components")
    dependencies = payload.get("dependencies")
    require(isinstance(components, list) and components, f"{path}: components missing")
    require(isinstance(dependencies, list) and dependencies, f"{path}: graph missing")
    component_refs = {item.get("bom-ref") for item in components}
    require(None not in component_refs, f"{path}: component reference missing")
    require(len(component_refs) == len(components), f"{path}: duplicate component reference")
    graph_refs = {item.get("ref") for item in dependencies}
    require(None not in graph_refs, f"{path}: dependency reference missing")
    require(root["bom-ref"] in graph_refs, f"{path}: root graph entry missing")
    require(component_refs <= graph_refs, f"{path}: graph omits components")
    valid_refs = component_refs | {root["bom-ref"]}
    for dependency in dependencies:
        targets = dependency.get("dependsOn", [])
        require(isinstance(targets, list), f"{path}: invalid dependsOn")
        require(set(targets) <= valid_refs, f"{path}: graph contains unknown target")
    if profile == "cpu":
        prohibited = sorted(
            str(item["name"])
            for item in components
            if CPU_PROHIBITED.match(str(item.get("name", "")))
        )
        require(not prohibited, f"{path}: CPU profile contains {prohibited}")
    if profile in {"cu126", "cu128"}:
        names = {str(item.get("name", "")) for item in components}
        require("torch" in names and "nvidia-cuda-runtime-cu12" in names, f"{path}: CUDA drift")
    print(f"dependency_sbom_valid=true profile={profile} components={len(components)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--profile", choices=("cpu", "cu126", "cu128"), required=True)
    args = parser.parse_args()
    validate(args.path, args.profile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
