#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Generate the effective third-party component and asset notice inventory."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "compliance/THIRD_PARTY_COMPONENTS.generated.md"


def load(relative: str) -> dict[str, object]:
    return tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))


def clean(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render() -> str:
    components = load("compliance/components.toml")
    component_defaults = components["defaults"]
    assert isinstance(component_defaults, dict)
    component_rows = components["components"]
    assert isinstance(component_rows, list)
    assets = load("compliance/external_assets.toml")
    asset_defaults = assets["defaults"]
    assert isinstance(asset_defaults, dict)
    asset_rows = assets["assets"]
    assert isinstance(asset_rows, list)

    lines = [
        "<!--",
        "SPDX-FileCopyrightText: 2026 Matthew Penkala",
        "",
        "SPDX-License-" + "Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0",
        "-->",
        "",
        "# Effective Third-Party Component Inventory",
        "",
        "> Generated from the machine-readable compliance registries. Do not edit by hand.",
        "> This inventory records evidence and release disposition; it grants no rights.",
        "",
        "## Declared, Build, CI, And Container Components",
        "",
        "| Identifier | Versions/digest | License | Use | Bundling | Redistribution | Review |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in sorted(component_rows, key=lambda item: item["id"]):
        effective = component_defaults | row
        lines.append(
            "| "
            + " | ".join(
                clean(value)
                for value in (
                    effective["identifier"],
                    ", ".join(effective["versions"]),
                    effective["license_expression"],
                    effective["usage"],
                    effective["bundling_status"],
                    effective["redistribution_status"],
                    effective["review_status"],
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Models And Datasets",
            "",
            "| Kind/key | Immutable revision | License | Acquisition | Runtime | "
            "Redistribution | Product class | Delivery | Pack identity | Review |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in sorted(asset_rows, key=lambda item: (item["kind"], item["key"])):
        effective = asset_defaults | row
        pack_id = effective.get("package_bundle_id", "—")
        pack_version = effective.get("package_bundle_version", "")
        pack_identity = (
            f"{pack_id}@{pack_version}"
            if pack_version
            else pack_id
        )
        lines.append(
            "| "
            + " | ".join(
                clean(value)
                for value in (
                    f"{effective['kind']}:{effective['key']}",
                    effective["revision"],
                    effective["license_expression"],
                    effective["acquisition_status"],
                    effective["runtime_status"],
                    effective["redistribution_status"],
                    effective.get("product_classification", "—"),
                    effective.get("delivery", "—"),
                    pack_identity,
                    effective["review_status"],
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "Unknown, conditional, gated, or blocked rows remain unavailable for release unless",
            "the exact machine-readable record contains the required owner-approved evidence.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render()
    if args.check:
        actual = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if actual != expected:
            print(f"third_party_notice_drift={OUTPUT.relative_to(ROOT)}", file=sys.stderr)
            return 1
        print("third_party_notice_clean=true")
        return 0
    OUTPUT.write_text(expected, encoding="utf-8")
    print(f"generated={OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
