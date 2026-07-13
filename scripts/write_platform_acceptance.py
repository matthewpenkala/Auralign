#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Write a hash-bound owner attestation after an observed native validation pass."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from assemble_private_release import (
    ACCEPTANCE_CONFIGS,
    CAPSULE_ID,
    ROOT,
    clean_repository,
    repository_model_contract_digest,
    sha256_file,
)
from validate_dependency_sbom import validate_payload as validate_dependency_sbom_payload

from local_transcript_max.platform_security import (
    atomic_write_text,
    ensure_private_directory,
    open_regular_nofollow,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=sorted(ACCEPTANCE_CONFIGS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dependency-sbom", type=Path, required=True)
    parser.add_argument("--owner-attests-pass", action="store_true", required=True)
    args = parser.parse_args()
    if not args.owner_attests_pass:  # pragma: no cover - argparse enforces it
        raise RuntimeError("owner pass attestation is required")
    if not clean_repository():
        raise RuntimeError("platform acceptance requires a clean index and untracked worktree")
    branch = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch != "main":
        raise RuntimeError("platform acceptance must be recorded on sanitized main")
    commit = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise RuntimeError("platform acceptance requires an exact candidate commit")
    profile, config = ACCEPTANCE_CONFIGS[args.platform]
    output_dir = ensure_private_directory(args.output_dir.expanduser().absolute())
    sbom = args.dependency_sbom.expanduser().absolute()
    expected_sbom_name = f"{args.platform}.cdx.json"
    if sbom.parent != output_dir or sbom.name != expected_sbom_name:
        raise RuntimeError("native dependency SBOM must be the expected file in output-dir")
    descriptor = open_regular_nofollow(sbom, os.O_RDONLY)
    try:
        if os.fstat(descriptor).st_nlink != 1:
            raise RuntimeError("native dependency SBOM must not be hard-linked")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            sbom_bytes = handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    sbom_payload = json.loads(sbom_bytes)
    validate_dependency_sbom_payload(
        sbom_payload,
        profile,
        label=sbom.name,
        platform_name=args.platform,
        commit=commit,
    )
    sbom_sha256 = hashlib.sha256(sbom_bytes).hexdigest()
    payload = {
        "schema_version": "auralign-platform-acceptance.v2",
        "platform": args.platform,
        "profile": profile,
        "commit": commit,
        "passed": True,
        "owner_attested": True,
        "completed_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "python_version": "3.11",
        "validator": "scripts/run_trusted_platform_validation.py",
        "validator_sha256": sha256_file(ROOT / "scripts/run_trusted_platform_validation.py"),
        "config": config,
        "config_sha256": sha256_file(ROOT / config),
        "model_contract_sha256": repository_model_contract_digest(),
        "capsule_id": CAPSULE_ID,
        "dependency_sbom": expected_sbom_name,
        "dependency_sbom_sha256": sbom_sha256,
    }
    path = output_dir / f"{args.platform}.json"
    if path.exists() or path.is_symlink():
        raise RuntimeError("refusing to overwrite an existing platform acceptance record")
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"platform_acceptance_recorded=true platform={args.platform} commit={commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
