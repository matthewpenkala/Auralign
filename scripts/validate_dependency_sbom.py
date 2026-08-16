#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Validate deterministic CycloneDX dependency-graph invariants."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from packaging.markers import Marker

CPU_PROHIBITED = re.compile(r"^(?:triton|nvidia-|.*cuda|.*cudnn|.*nccl)", re.IGNORECASE)
ROOT = Path(__file__).resolve().parents[1]
PLATFORM_PROFILES = {
    "darwin-arm64-cpu-app": "cpu",
    "darwin-arm64-cpu": "cpu",
    "windows-amd64-cpu": "cpu",
    "windows-amd64-cu128": "cu128",
    "linux-amd64-cu126": "cu126",
}
PLATFORM_REQUIREMENTS = {
    "darwin-arm64-cpu-app": "requirements/macos-app-arm64-cpu-py311.txt",
    "darwin-arm64-cpu": "requirements/macos-arm64-cpu-py311.txt",
    "windows-amd64-cpu": "requirements/windows-cpu-py311.txt",
    "windows-amd64-cu128": "requirements/windows-cu128-py311.txt",
    "linux-amd64-cu126": "requirements/linux-cu126-py311.txt",
}
PLATFORM_MARKER_ENVIRONMENTS = {
    "darwin-arm64-cpu-app": {
        "implementation_name": "cpython",
        "os_name": "posix",
        "platform_machine": "arm64",
        "platform_system": "Darwin",
        "platform_python_implementation": "CPython",
        "python_version": "3.11",
        "python_full_version": "3.11.0",
        "sys_platform": "darwin",
    },
    "darwin-arm64-cpu": {
        "implementation_name": "cpython",
        "os_name": "posix",
        "platform_machine": "arm64",
        "platform_system": "Darwin",
        "platform_python_implementation": "CPython",
        "python_version": "3.11",
        "python_full_version": "3.11.0",
        "sys_platform": "darwin",
    },
    "windows-amd64-cpu": {
        "implementation_name": "cpython",
        "os_name": "nt",
        "platform_machine": "AMD64",
        "platform_system": "Windows",
        "platform_python_implementation": "CPython",
        "python_version": "3.11",
        "python_full_version": "3.11.0",
        "sys_platform": "win32",
    },
    "windows-amd64-cu128": {
        "implementation_name": "cpython",
        "os_name": "nt",
        "platform_machine": "AMD64",
        "platform_system": "Windows",
        "platform_python_implementation": "CPython",
        "python_version": "3.11",
        "python_full_version": "3.11.0",
        "sys_platform": "win32",
    },
    "linux-amd64-cu126": {
        "implementation_name": "cpython",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "platform_system": "Linux",
        "platform_python_implementation": "CPython",
        "python_version": "3.11",
        "python_full_version": "3.11.0",
        "sys_platform": "linux",
    },
}
CORE_RUNTIME_PACKAGES = {
    "ctranslate2",
    "faster-whisper",
    "local-transcript-max",
    "pyannote-audio",
    "speechbrain",
    "torch",
    "torchaudio",
    "torchcodec",
    "torchvision",
    "whisperx",
}
REQUIREMENT_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s\\]+)(?:\s*;\s*(.+))?$")
PATCHED_LIGHTNING_REQUIREMENT = (
    "lightning @ https://github.com/Lightning-AI/pytorch-lightning/archive/"
    "d710d689510d50e800f53b3cd773cbca20b1f86f.tar.gz"
)
PATCHED_LIGHTNING_SOURCE_VERSION = "2.6.2"
PATCHED_LIGHTNING_ARCHIVE_HASH = (
    "--hash=sha256:059f9272bf684f391f52c4e175a6a8e0d5cfa3ba4b3a0fde9aa4ff13dd72e11e"
)
PRIVATE_PATH = re.compile(
    r"(?:^|/)(?:users|home)/[^/\s\"',;:)}\]]+(?:/|$|(?=[\s\"',;:)}\]]))",
    re.IGNORECASE,
)
RUNTIME_SOURCES = tomllib.loads(
    (ROOT / "packaging/macos/runtime-sources.toml").read_text(encoding="utf-8")
)
APP_SUPPLEMENTAL_VERSIONS = {
    "cpython": str(RUNTIME_SOURCES["python"]["version"]),
    "python-build-standalone": str(RUNTIME_SOURCES["python"]["release"]),
    "ffmpeg": str(RUNTIME_SOURCES["ffmpeg"]["version"]),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _properties(metadata: dict[str, object]) -> dict[str, str]:
    rows = metadata.get("properties", [])
    require(isinstance(rows, list), "SBOM metadata properties are invalid")
    result: dict[str, str] = {}
    for row in rows:
        require(isinstance(row, dict), "SBOM metadata property is invalid")
        name, value = row.get("name"), row.get("value")
        require(isinstance(name, str) and isinstance(value, str), "SBOM property is invalid")
        require(name not in result, f"duplicate SBOM property: {name}")
        result[name] = value
    return result


def _normalize_package_name(value: object) -> str:
    return re.sub(r"[-_.]+", "-", str(value).casefold())


def _locked_native_versions(platform_name: str) -> dict[str, str]:
    requirements = ROOT / PLATFORM_REQUIREMENTS[platform_name]
    versions = {"local-transcript-max": "0.2.0"}
    lines = requirements.read_text(encoding="utf-8").splitlines()
    for line_number, line in enumerate(lines, 1):
        if not line or line[0].isspace() or line.startswith(("#", "--")):
            continue
        candidate = line.removesuffix("\\").rstrip()
        if candidate == PATCHED_LIGHTNING_REQUIREMENT:
            require(
                line_number < len(lines)
                and lines[line_number].strip() == PATCHED_LIGHTNING_ARCHIVE_HASH,
                f"{requirements}: patched Lightning archive hash drift",
            )
            require(
                "lightning" not in versions,
                f"{requirements}: duplicate package pin: lightning",
            )
            versions["lightning"] = PATCHED_LIGHTNING_SOURCE_VERSION
            continue
        match = REQUIREMENT_PIN.fullmatch(candidate)
        require(match is not None, f"{requirements}: unpinned row at line {line_number}")
        assert match is not None
        marker = match.group(3)
        if marker and not Marker(marker).evaluate(PLATFORM_MARKER_ENVIRONMENTS[platform_name]):
            continue
        name = _normalize_package_name(match.group(1))
        require(name not in versions, f"{requirements}: duplicate package pin: {name}")
        versions[name] = match.group(2)
    require(
        CORE_RUNTIME_PACKAGES <= versions.keys(),
        f"{requirements}: core runtime package contract is incomplete",
    )
    return versions


NATIVE_PACKAGE_VERSIONS = {
    platform_name: _locked_native_versions(platform_name) for platform_name in PLATFORM_PROFILES
}


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def validate_payload(
    payload: object,
    profile: str,
    *,
    label: str,
    platform_name: str | None = None,
    commit: str | None = None,
) -> int:
    require(isinstance(payload, dict), f"{label}: SBOM root is invalid")
    require(payload.get("bomFormat") == "CycloneDX", f"{label}: not CycloneDX")
    standalone_app = platform_name == "darwin-arm64-cpu-app"
    expected_spec = "1.6" if standalone_app else "1.5"
    require(
        payload.get("specVersion") == expected_spec,
        f"{label}: unexpected schema version",
    )
    metadata = payload.get("metadata")
    require(isinstance(metadata, dict), f"{label}: metadata missing")
    environment = platform_name is not None
    root = metadata.get("component")
    if environment:
        require(PLATFORM_PROFILES.get(platform_name) == profile, f"{label}: platform/profile drift")
        properties = _properties(metadata)
        expected_properties = {
            "auralign:platform": platform_name,
            "auralign:profile": profile,
            "auralign:sbom-kind": (
                "standalone-app-runtime" if standalone_app else "installed-environment"
            ),
        }
        if commit is not None:
            expected_properties["auralign:commit"] = commit
        require(
            all(properties.get(key) == value for key, value in expected_properties.items()),
            f"{label}: installed-environment binding drift",
        )
        require(
            properties.get("auralign:python-version", "").startswith("3.11."),
            f"{label}: native SBOM Python drift",
        )
    else:
        require(
            str(payload.get("serialNumber", "")).startswith("urn:uuid:"),
            f"{label}: serial",
        )
        require(
            isinstance(root, dict) and root.get("bom-ref"),
            f"{label}: root component missing",
        )
    components = payload.get("components")
    dependencies = payload.get("dependencies")
    require(isinstance(components, list) and components, f"{label}: components missing")
    require(isinstance(dependencies, list) and dependencies, f"{label}: graph missing")
    require(all(isinstance(item, dict) for item in components), f"{label}: invalid component")
    require(all(isinstance(item, dict) for item in dependencies), f"{label}: invalid dependency")
    component_refs = {item.get("bom-ref") for item in components}
    require(
        None not in component_refs and all(isinstance(value, str) for value in component_refs),
        f"{label}: component reference missing",
    )
    require(len(component_refs) == len(components), f"{label}: duplicate component reference")
    graph_refs = {item.get("ref") for item in dependencies}
    require(
        None not in graph_refs and all(isinstance(value, str) for value in graph_refs),
        f"{label}: dependency reference missing",
    )
    require(len(graph_refs) == len(dependencies), f"{label}: duplicate dependency reference")
    if not environment:
        assert isinstance(root, dict)
        require(root["bom-ref"] in graph_refs, f"{label}: root graph entry missing")
    require(component_refs <= graph_refs, f"{label}: graph omits components")
    valid_refs = set(component_refs)
    if not environment:
        assert isinstance(root, dict)
        valid_refs.add(root["bom-ref"])
    for dependency in dependencies:
        targets = dependency.get("dependsOn", [])
        require(isinstance(targets, list), f"{label}: invalid dependsOn")
        require(set(targets) <= valid_refs, f"{label}: graph contains unknown target")
    if profile == "cpu":
        prohibited = sorted(
            str(item["name"])
            for item in components
            if CPU_PROHIBITED.match(str(item.get("name", "")))
        )
        require(not prohibited, f"{label}: CPU profile contains {prohibited}")
    normalized_names = [_normalize_package_name(item.get("name", "")) for item in components]
    names = set(normalized_names)
    if profile in {"cu126", "cu128"}:
        require("torch" in names, f"{label}: CUDA torch drift")
        if platform_name != "windows-amd64-cu128":
            require("nvidia-cuda-runtime-cu12" in names, f"{label}: CUDA runtime drift")
    if environment:
        require(len(normalized_names) == len(names), f"{label}: duplicate package")
        expected_versions = NATIVE_PACKAGE_VERSIONS[platform_name]
        allowed_versions = dict(expected_versions)
        if standalone_app:
            allowed_versions.update(APP_SUPPLEMENTAL_VERSIONS)
        observed_versions = {
            _normalize_package_name(item.get("name", "")): str(item.get("version", ""))
            for item in components
        }
        missing = sorted(allowed_versions.keys() - names)
        unexpected = sorted(names - allowed_versions.keys())
        require(not missing, f"{label}: native runtime packages missing: {missing}")
        require(not unexpected, f"{label}: unexpected native runtime packages: {unexpected}")
        drifted = sorted(
            name
            for name, version in allowed_versions.items()
            if observed_versions.get(name) != version
        )
        require(
            not drifted,
            f"{label}: native locked package version drift: {drifted}",
        )
        for value in _iter_strings(payload):
            normalized = value.casefold().replace("\\", "/")
            require(not normalized.startswith("file:"), f"{label}: local path leak")
            require(PRIVATE_PATH.search(normalized) is None, f"{label}: private home path leak")
    return len(components)


def validate(
    path: Path,
    profile: str,
    *,
    platform_name: str | None = None,
    commit: str | None = None,
) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    count = validate_payload(
        payload,
        profile,
        label=str(path),
        platform_name=platform_name,
        commit=commit,
    )
    platform_text = f" platform={platform_name}" if platform_name else ""
    print(f"dependency_sbom_valid=true profile={profile}{platform_text} components={count}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--profile", choices=("cpu", "cu126", "cu128"), required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORM_PROFILES))
    parser.add_argument("--commit")
    args = parser.parse_args()
    if args.commit is not None:
        require(args.platform is not None, "--commit requires --platform")
        require(re.fullmatch(r"[0-9a-f]{40}", args.commit) is not None, "invalid commit")
    validate(
        args.path,
        args.profile,
        platform_name=args.platform,
        commit=args.commit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
