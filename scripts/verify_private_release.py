#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Re-open and verify the closed Auralign 0.2.0 private release payload."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from assemble_private_release import COMMON_BUNDLE_FILES, VERSION
from verify_distribution import verify_private_release

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--commit")
    args = parser.parse_args()
    commit = args.commit or subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    release_dir = args.release_dir.expanduser().resolve(strict=True)
    wheel = next(release_dir.glob("*.whl"), None)
    if wheel is None:
        raise RuntimeError("private release wheel is missing")
    verify_private_release(
        release_dir,
        version=VERSION,
        commit=commit,
        native_common_files={Path(name).as_posix() for name in COMMON_BUNDLE_FILES}
        | {wheel.name, "model-manifest.json"},
    )
    print(f"private_release_verified=true commit={commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
