#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Assemble the closed Auralign 0.2.0 private release after native acceptance."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from validate_dependency_sbom import validate as validate_dependency_sbom
from verify_distribution import verify_private_release

from local_transcript_max.config import resolve_model_entry
from local_transcript_max.models import model_manifest_digest
from local_transcript_max.platform_security import (
    atomic_write_text,
    ensure_private_directory,
    ensure_private_file,
)

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
CAPSULE_ID = "auralign-validation-capsule.v1:5a652951669c"
ACCEPTANCE_PLATFORMS = ("darwin-arm64-cpu", "windows-amd64-cpu", "windows-amd64-cu128")
MODEL_SELECTIONS = (
    ("asr", "large-v3"),
    ("diarization", "community-1"),
    ("alignment", "en"),
    ("speaker-embedding", "ecapa"),
)
COMMON_BUNDLE_FILES = (
    "LICENSE",
    "LICENSES/LicenseRef-PolyForm-Internal-Use-1.0.0.txt",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "compliance/THIRD_PARTY_COMPONENTS.generated.md",
    "docs/release/v0.2.0-private-runbook.md",
)
ACCEPTANCE_CONFIGS = {
    "darwin-arm64-cpu": ("cpu", "examples/config.strict-macos-arm64-cpu.yaml"),
    "windows-amd64-cpu": ("cpu", "examples/config.strict-windows-amd64-cpu.yaml"),
    "windows-amd64-cu128": ("cu128", "examples/config.strict-windows-amd64-cu128.yaml"),
}
NATIVE_SBOMS = {
    "darwin-arm64-cpu": "cpu",
    "windows-amd64-cpu": "cpu",
    "windows-amd64-cu128": "cu128",
    "linux-amd64-cu126": "cu126",
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()


def repository_model_contract_digest() -> str:
    encoded = json.dumps(model_manifest(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_acceptance(acceptance_dir: Path, commit: str) -> None:
    validator_digest = sha256_file(ROOT / "scripts/run_trusted_platform_validation.py")
    contract_digest = repository_model_contract_digest()
    for platform_name in ACCEPTANCE_PLATFORMS:
        path = ensure_private_file(acceptance_dir / f"{platform_name}.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        profile, config_relative = ACCEPTANCE_CONFIGS[platform_name]
        sbom_name = f"{platform_name}.cdx.json"
        sbom_path = acceptance_dir / sbom_name
        try:
            sbom_path = ensure_private_file(sbom_path)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(
                f"native dependency SBOM is missing or unsafe: {platform_name}"
            ) from exc
        validate_dependency_sbom(
            sbom_path,
            profile,
            platform_name=platform_name,
            commit=commit,
        )
        expected = {
            "schema_version": "auralign-platform-acceptance.v2",
            "platform": platform_name,
            "profile": profile,
            "commit": commit,
            "passed": True,
            "owner_attested": True,
            "python_version": "3.11",
            "validator": "scripts/run_trusted_platform_validation.py",
            "validator_sha256": validator_digest,
            "config": config_relative,
            "config_sha256": sha256_file(ROOT / config_relative),
            "model_contract_sha256": contract_digest,
            "capsule_id": CAPSULE_ID,
            "dependency_sbom": sbom_name,
            "dependency_sbom_sha256": sha256_file(sbom_path),
        }
        if not isinstance(payload, dict) or any(
            payload.get(key) != value for key, value in expected.items()
        ):
            raise RuntimeError(f"platform acceptance is missing or invalid: {platform_name}")
        if set(payload) != {*expected, "completed_at"}:
            raise RuntimeError(f"platform acceptance contains unexpected fields: {platform_name}")
        try:
            completed = dt.datetime.fromisoformat(
                str(payload["completed_at"]).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise RuntimeError(
                f"platform acceptance timestamp is invalid: {platform_name}"
            ) from exc
        if completed.tzinfo is None:
            raise RuntimeError(f"platform acceptance timestamp lacks a timezone: {platform_name}")
    linux_name = "linux-amd64-cu126"
    linux_sbom = acceptance_dir / f"{linux_name}.cdx.json"
    try:
        linux_sbom = ensure_private_file(linux_sbom)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("retained Linux dependency SBOM is missing or unsafe") from exc
    validate_dependency_sbom(
        linux_sbom,
        "cu126",
        platform_name=linux_name,
        commit=commit,
    )


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


def _archive_mode(path: Path) -> int:
    return 0o755 if path.stat().st_mode & 0o111 else 0o644


def write_deterministic_zip(output: Path, files: list[tuple[Path, str]]) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in sorted(files, key=lambda row: row[1]):
            info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100000 | 0o644) << 16
            archive.writestr(info, path.read_bytes())


def write_deterministic_tar_gz(output: Path, root: Path) -> None:
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(
                    candidate for candidate in root.rglob("*") if candidate.is_file()
                ):
                    info = tarfile.TarInfo(f"{root.name}/{path.relative_to(root).as_posix()}")
                    info.size = path.stat().st_size
                    info.mode = _archive_mode(path)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)


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
        write_deterministic_zip(
            output,
            [
                (path, path.relative_to(stage.parent).as_posix())
                for path in stage.rglob("*")
                if path.is_file()
            ],
        )


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
        write_deterministic_tar_gz(output, stage)


def build_sboms(output: Path, build_dir: Path, acceptance_dir: Path) -> None:
    sbom_dir = build_dir
    run(["uv", "run", "--frozen", "reuse", "spdx", "-o", str(sbom_dir / "source.spdx")])
    commit = git_head()
    for name, profile in NATIVE_SBOMS.items():
        source = acceptance_dir / f"{name}.cdx.json"
        validate_dependency_sbom(
            source,
            profile,
            platform_name=name,
            commit=commit,
        )
        shutil.copy2(source, sbom_dir / source.name)
    write_deterministic_zip(
        output,
        [(path, path.name) for path in sbom_dir.iterdir() if path.is_file()],
    )


def clean_repository() -> bool:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return not completed.stdout.strip()


def run_preassembly_gates() -> None:
    commands = (
        ["uv", "lock", "--check"],
        ["uv", "run", "--frozen", "python", "scripts/check_release_requirements.py"],
        ["uv", "run", "--frozen", "pytest", "-q"],
        ["uv", "run", "--frozen", "ruff", "check", "."],
        ["uv", "run", "--frozen", "mypy", "src/local_transcript_max"],
        ["uv", "run", "--frozen", "reuse", "lint"],
        ["uv", "run", "--frozen", "python", "scripts/check_compliance.py"],
        ["uv", "run", "--frozen", "python", "scripts/generate_third_party_notices.py", "--check"],
        ["uv", "run", "--frozen", "python", "scripts/check_reachable_history.py"],
    )
    for command in commands:
        run(list(command))
    with tempfile.TemporaryDirectory() as temporary:
        staging = Path(temporary)
        exported = staging / "cpu-requirements.txt"
        audit_input = staging / "cpu-audit-requirements.txt"
        audit = staging / "pip-audit.json"
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--extra",
                "full",
                "--group",
                "cpu",
                "--no-dev",
                "--no-emit-project",
                "--output-file",
                str(exported),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        run(
            [
                "uv",
                "run",
                "--frozen",
                "python",
                "scripts/prepare_vulnerability_audit_input.py",
                str(exported),
                str(audit_input),
            ]
        )
        completed = subprocess.run(
            [
                "uv",
                "run",
                "--frozen",
                "pip-audit",
                "-r",
                str(audit_input),
                "--disable-pip",
                "--no-deps",
                "--strict",
                "--progress-spinner",
                "off",
                "-f",
                "json",
                "-o",
                str(audit),
            ],
            cwd=ROOT,
            check=False,
        )
        if completed.returncode not in {0, 1}:
            raise RuntimeError("vulnerability audit execution failed")
        run(
            [
                "uv",
                "run",
                "--frozen",
                "python",
                "scripts/check_vulnerability_audit.py",
                str(audit),
            ]
        )


def smoke_install(wheel: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        venv = Path(temporary) / ".venv"
        run(["uv", "venv", "--python", "3.11", str(venv)])
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        ltm = venv / ("Scripts/ltm.exe" if os.name == "nt" else "bin/ltm")
        run(["uv", "pip", "install", "--python", str(python), str(wheel)])
        run(
            [
                str(python),
                "-c",
                "from importlib.metadata import version; import local_transcript_max; "
                "assert local_transcript_max.__version__ == version('local-transcript-max')",
            ]
        )
        run([str(ltm), "--help"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acceptance-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/private-release-v0.2.0")
    args = parser.parse_args()
    commit = git_head()
    if not clean_repository():
        raise RuntimeError("release assembly requires a clean index and untracked worktree")
    manifest_payload = model_manifest()
    run_preassembly_gates()
    if not clean_repository():
        raise RuntimeError("release gates changed the repository worktree")
    acceptance_dir = ensure_private_directory(args.acceptance_dir.expanduser().absolute())
    verify_acceptance(acceptance_dir, commit)
    output_dir = args.output_dir.absolute()
    if output_dir.exists():
        raise RuntimeError("release output directory already exists")
    output_dir = ensure_private_directory(output_dir)
    with tempfile.TemporaryDirectory() as temporary:
        sbom_dir = Path(temporary) / "sboms"
        sbom_dir.mkdir()
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
                str(sbom_dir),
            ]
        )
        wheel = next(output_dir.glob("*.whl"))
        smoke_install(wheel)
        model_path = output_dir / "model-manifest.json"
        atomic_write_text(
            model_path,
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
        )
        build_windows_bundle(
            output_dir / f"Auralign-{VERSION}-windows-amd64.zip", wheel, model_path
        )
        build_macos_bundle(
            output_dir / f"Auralign-{VERSION}-darwin-arm64.tar.gz", wheel, model_path
        )
        build_sboms(
            output_dir / f"Auralign-{VERSION}-sboms.zip",
            sbom_dir,
            acceptance_dir,
        )
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
    atomic_write_text(
        manifest_path,
        json.dumps(release_manifest, indent=2, sort_keys=True) + "\n",
    )
    checksum_files = [*payload_files, manifest_path]
    atomic_write_text(
        output_dir / "SHA256SUMS",
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in checksum_files),
    )
    for artifact in output_dir.iterdir():
        if artifact.is_file():
            ensure_private_file(artifact)
    verify_private_release(
        output_dir,
        version=VERSION,
        commit=commit,
        native_common_files={Path(name).as_posix() for name in COMMON_BUNDLE_FILES}
        | {wheel.name, model_path.name},
    )
    print(f"private_release_assembled=true commit={commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
