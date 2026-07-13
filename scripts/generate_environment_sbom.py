#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Generate a path-redacted CycloneDX inventory from one installed native runtime."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from local_transcript_max.platform_security import atomic_write_text

PLATFORM_CONTRACTS = {
    "darwin-arm64-cpu": ("darwin", {"arm64", "aarch64"}, "cpu"),
    "windows-amd64-cpu": ("win32", {"amd64", "x86_64"}, "cpu"),
    "windows-amd64-cu128": ("win32", {"amd64", "x86_64"}, "cu128"),
    "linux-amd64-cu126": ("linux", {"amd64", "x86_64"}, "cu126"),
}
SHA40 = re.compile(r"^[0-9a-f]{40}$")


def target_runtime(python: Path) -> dict[str, str]:
    completed = subprocess.run(
        [
            str(python),
            "-c",
            "import json, platform, sys; "
            "print(json.dumps({'machine': platform.machine().lower(), "
            "'python': platform.python_version(), 'sys_platform': sys.platform}))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or not all(
        isinstance(payload.get(key), str) for key in ("machine", "python", "sys_platform")
    ):
        raise RuntimeError("target Python returned invalid platform metadata")
    return payload


def _strip_local_references(value: Any) -> None:
    if isinstance(value, dict):
        references = value.get("externalReferences")
        if isinstance(references, list):
            value["externalReferences"] = [
                item
                for item in references
                if not (
                    isinstance(item, dict)
                    and str(item.get("url", "")).casefold().startswith("file:")
                )
            ]
            if not value["externalReferences"]:
                value.pop("externalReferences")
        for item in value.values():
            _strip_local_references(item)
    elif isinstance(value, list):
        for item in value:
            _strip_local_references(item)


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def _normalized_path_text(value: str) -> str:
    return value.casefold().replace("\\", "/")


def bind_environment(
    payload: dict[str, Any],
    *,
    platform_name: str,
    profile: str,
    commit: str,
    runtime: dict[str, str],
) -> dict[str, Any]:
    _strip_local_references(payload)
    metadata = payload.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise RuntimeError("generated SBOM metadata is invalid")
    properties = metadata.setdefault("properties", [])
    if not isinstance(properties, list):
        raise RuntimeError("generated SBOM properties are invalid")
    properties.extend(
        {"name": name, "value": value}
        for name, value in (
            ("auralign:commit", commit),
            ("auralign:platform", platform_name),
            ("auralign:profile", profile),
            ("auralign:python-version", runtime["python"]),
            ("auralign:sbom-kind", "installed-environment"),
        )
    )
    properties.sort(key=lambda item: (str(item.get("name", "")), str(item.get("value", ""))))
    return payload


def generate(*, python: Path, platform_name: str, profile: str, commit: str, output: Path) -> int:
    expected_system, expected_machines, expected_profile = PLATFORM_CONTRACTS[platform_name]
    if profile != expected_profile:
        raise RuntimeError("SBOM profile does not match its platform contract")
    if not SHA40.fullmatch(commit):
        raise RuntimeError("SBOM commit must be a full lowercase commit ID")
    runtime = target_runtime(python)
    if runtime["sys_platform"] != expected_system or runtime["machine"] not in expected_machines:
        raise RuntimeError("target Python does not match the requested native platform")
    if not runtime["python"].startswith("3.11."):
        raise RuntimeError("native release SBOM requires an installed Python 3.11 runtime")

    with tempfile.TemporaryDirectory() as temporary:
        raw = Path(temporary) / "environment.cdx.json"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "cyclonedx_py",
                "environment",
                str(python),
                "--spec-version",
                "1.5",
                "--output-reproducible",
                "--output-format",
                "JSON",
                "--output-file",
                str(raw),
            ],
            check=True,
        )
        payload = json.loads(raw.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("generated environment SBOM is invalid")
    bind_environment(
        payload,
        platform_name=platform_name,
        profile=profile,
        commit=commit,
        runtime=runtime,
    )
    forbidden = tuple(
        _normalized_path_text(value) for value in (str(Path.home()), str(python.parent)) if value
    )
    if any(
        _normalized_path_text(value).startswith("file:")
        or any(path in _normalized_path_text(value) for path in forbidden)
        for value in _iter_strings(payload)
    ):
        raise RuntimeError("generated environment SBOM contains a private local path")
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    atomic_write_text(output, rendered)
    components = payload.get("components")
    count = len(components) if isinstance(components, list) else 0
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORM_CONTRACTS), required=True)
    parser.add_argument("--profile", choices=("cpu", "cu126", "cu128"), required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise RuntimeError("refusing to overwrite an existing native environment SBOM")
    # Do not resolve the final launcher symlink: on POSIX, doing so changes a
    # virtual-environment interpreter into the base interpreter and inventories
    # the wrong environment. abspath still normalizes relative components.
    python = Path(os.path.abspath(args.python.expanduser()))
    if not python.is_file():
        raise RuntimeError("target Python interpreter is missing")
    count = generate(
        python=python,
        platform_name=args.platform,
        profile=args.profile,
        commit=args.commit,
        output=args.output.expanduser(),
    )
    print(
        f"native_environment_sbom_generated=true platform={args.platform} "
        f"profile={args.profile} components={count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
