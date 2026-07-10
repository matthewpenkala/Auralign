#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Assemble the closed Auralign 0.2.0 private release after native acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from local_transcript_max.config import resolve_model_entry
from local_transcript_max.models import model_manifest_digest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
ACCEPTANCE_PLATFORMS = ("darwin-arm64-cpu", "windows-amd64-cpu", "windows-amd64-cu128")
MODEL_SELECTIONS = (
    ("asr", "large-v3"),
    ("diarization", "community-1"),
    ("alignment", "en"),
    ("speaker-embedding", "ecapa"),
)
COMMON_BUNDLE_FILES = (
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "compliance/THIRD_PARTY_COMPONENTS.generated.md",
    "docs/release/v0.2.0-private-runbook.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()


def verify_acceptance(acceptance_dir: Path, commit: str) -> None:
    for platform_name in ACCEPTANCE_PLATFORMS:
        path = acceptance_dir / f"{platform_name}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "schema_version": "auralign-platform-acceptance.v1",
            "platform": platform_name,
            "commit": commit,
            "passed": True,
        }
        if not isinstance(payload, dict) or any(
            payload.get(key) != value for key, value in expected.items()
        ):
            raise RuntimeError(f"platform acceptance is missing or invalid: {platform_name}")


def model_manifest() -> dict[str, Any]:
    models = []
    for role, key in MODEL_SELECTIONS:
        entry = resolve_model_entry(role, key)
        if entry is None:
            raise RuntimeError(f"model manifest selection is missing: {role}:{key}")
        if set(entry.expected_digest_map) != set(entry.runtime_files):
            raise RuntimeError(f"model digest contract is incomplete: {role}:{key}")
        models.append(
            {
                "role": role,
                "key": key,
                "model_id": entry.model_id,
                "revision": entry.revision,
                "runtime_files": entry.expected_digest_map,
                "repository_manifest_digest": model_manifest_digest(entry),
                "gated": entry.gated,
                "terms_url": entry.terms_url,
                "redistribution_status": entry.redistribution_status,
            }
        )
    return {"schema_version": "auralign-release-model-manifest.v1", "models": models}


def add_common(stage: Path, wheel: Path, model_manifest_path: Path) -> None:
    shutil.copy2(wheel, stage / wheel.name)
    shutil.copy2(model_manifest_path, stage / model_manifest_path.name)
    for relative in COMMON_BUNDLE_FILES:
        source = ROOT / relative
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def build_windows_bundle(output: Path, wheel: Path, model_manifest_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / f"Auralign-{VERSION}-windows-amd64"
        stage.mkdir()
        add_common(stage, wheel, model_manifest_path)
        for relative in (
            "install-windows.ps1",
            "bootstrap.ps1",
            "examples/config.strict-windows-amd64-cpu.yaml",
            "examples/config.strict-windows-amd64-cu128.yaml",
            "requirements/windows-cpu-py311.txt",
            "requirements/windows-cu128-py311.txt",
        ):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(stage.parent))


def build_macos_bundle(output: Path, wheel: Path, model_manifest_path: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / f"Auralign-{VERSION}-darwin-arm64"
        stage.mkdir()
        add_common(stage, wheel, model_manifest_path)
        for relative in (
            "install-macos.sh",
            "bootstrap.sh",
            "examples/config.strict-macos-arm64-cpu.yaml",
            "requirements/macos-arm64-cpu-py311.txt",
        ):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
        with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.add(stage, arcname=stage.name, recursive=True)


def build_sboms(output: Path, build_dir: Path) -> None:
    sbom_dir = build_dir / "release-sboms"
    sbom_dir.mkdir(parents=True)
    for name, requirement in (
        ("macos-arm64-cpu", "requirements/macos-arm64-cpu-py311.txt"),
        ("windows-amd64-cpu", "requirements/windows-cpu-py311.txt"),
        ("windows-amd64-cu128", "requirements/windows-cu128-py311.txt"),
        ("linux-amd64-cu126", "requirements/linux-cu126-py311.txt"),
    ):
        run(
            [
                "uv",
                "run",
                "--frozen",
                "cyclonedx-py",
                "requirements",
                requirement,
                "--pyproject",
                "pyproject.toml",
                "--output-reproducible",
                "--output-file",
                str(sbom_dir / f"{name}.cdx.json"),
            ]
        )
    for source in sorted((build_dir / "sbom").glob("*.spdx.json")):
        shutil.copy2(source, sbom_dir / source.name)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(sbom_dir.iterdir()):
            archive.write(path, path.name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/private-release-v0.2.0")
    args = parser.parse_args()
    commit = git_head()
    verify_acceptance(args.acceptance_dir.expanduser().resolve(strict=True), commit)
    if subprocess.run(["git", "diff", "--quiet"], cwd=ROOT).returncode != 0:
        raise RuntimeError("release assembly requires a clean tracked worktree")
    manifest_payload = model_manifest()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise RuntimeError("release output directory already exists")
    output_dir.mkdir(mode=0o700, parents=True)
    build_dir = ROOT / "build"
    run(["uv", "build", "--out-dir", str(output_dir)])
    run(
        [
            "uv",
            "run",
            "--frozen",
            "python",
            "scripts/verify_distribution.py",
            "--dist-dir",
            str(output_dir),
            "--sbom-dir",
            str(build_dir / "sbom"),
        ]
    )
    wheel = next(output_dir.glob("*.whl"))
    model_path = output_dir / "model-manifest.json"
    model_path.write_text(
        json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    build_windows_bundle(
        output_dir / f"Auralign-{VERSION}-windows-amd64.zip", wheel, model_path
    )
    build_macos_bundle(
        output_dir / f"Auralign-{VERSION}-darwin-arm64.tar.gz", wheel, model_path
    )
    build_sboms(output_dir / f"Auralign-{VERSION}-sboms.zip", build_dir)
    payload_files = sorted(path for path in output_dir.iterdir() if path.is_file())
    release_manifest = {
        "schema_version": "auralign-private-release.v1",
        "version": VERSION,
        "commit": commit,
        "repository_visibility": "private",
        "artifacts": [
            {"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}
            for path in payload_files
        ],
    }
    manifest_path = output_dir / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(release_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksum_files = [*payload_files, manifest_path]
    (output_dir / "SHA256SUMS").write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in checksum_files),
        encoding="utf-8",
    )
    print(f"private_release_assembled=true commit={commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
