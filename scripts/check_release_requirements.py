#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Regenerate or verify the lock-derived native release requirements."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPDX_FOOTER = (
    "# SPDX-FileCopyrightText: 2026 Matthew Penkala\n"
    "# SPDX-License-"
    "Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0\n"
)
TARGETS = {
    "requirements/macos-arm64-cpu-py311.txt": "cpu",
    "requirements/windows-cpu-py311.txt": "cpu",
    "requirements/windows-cu128-py311.txt": "cu128",
    "requirements/linux-cu126-py311.txt": "cu126",
}


def exported_requirements(group: str) -> str:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--extra",
                "full",
                "--group",
                group,
                "--no-dev",
                "--no-emit-project",
                "--no-header",
                "--no-annotate",
                "--emit-index-url",
                "--output-file",
                str(output),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return output.read_text(encoding="utf-8").rstrip() + "\n" + SPDX_FOOTER


def verify_or_write(*, write: bool) -> None:
    rendered_by_group = {group: exported_requirements(group) for group in set(TARGETS.values())}
    drifted: list[str] = []
    for relative, group in TARGETS.items():
        path = ROOT / relative
        rendered = rendered_by_group[group]
        if write:
            path.write_text(rendered, encoding="utf-8")
        elif not path.is_file() or path.read_text(encoding="utf-8") != rendered:
            drifted.append(relative)
    if drifted:
        raise RuntimeError(
            "lock-derived release requirements drifted: "
            + ", ".join(sorted(drifted))
            + "; regenerate with scripts/check_release_requirements.py --write"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    verify_or_write(write=args.write)
    print(f"release_requirements_ok=true mode={'write' if args.write else 'check'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
