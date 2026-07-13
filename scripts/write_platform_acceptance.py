#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Write a hash-bound owner attestation after an observed native validation pass."""

from __future__ import annotations

import argparse
import datetime as dt
import json
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", choices=sorted(ACCEPTANCE_CONFIGS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
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
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    profile, config = ACCEPTANCE_CONFIGS[args.platform]
    payload = {
        "schema_version": "auralign-platform-acceptance.v1",
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
    }
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = output_dir / f"{args.platform}.json"
    if path.exists() or path.is_symlink():
        raise RuntimeError("refusing to overwrite an existing platform acceptance record")
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print(f"platform_acceptance_recorded=true platform={args.platform} commit={commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
