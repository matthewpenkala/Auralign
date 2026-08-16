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
    "requirements/macos-arm64-cpu-py311.txt": ("cpu", ("full",), False),
    "requirements/windows-cpu-py311.txt": ("cpu", ("full",), False),
    "requirements/windows-cu128-py311.txt": ("cu128", ("full",), False),
    "requirements/linux-cu126-py311.txt": ("cu126", ("full",), False),
    "requirements/macos-app-arm64-cpu-py311.txt": (
        "cpu",
        ("asr", "mlx", "diarization", "alignment", "speaker-id"),
        True,
    ),
}
MACOS_APP_TARGET = "requirements/macos-app-arm64-cpu-py311.txt"
MACOS_APP_FORBIDDEN_REQUIREMENT_NAMES = frozenset(
    {
        "apple-fm-sdk",
        "build",
        "pyproject-hooks",
        "skops",
        "wtpsplit",
    }
)
SOURCE_HEADLESS_ONLY_REQUIREMENT_NAMES = frozenset({"apple-fm-sdk", "wtpsplit"})
MACOS_APP_FORBIDDEN_NATIVE_MARKERS = (
    "foundation-models-c",
    "libfoundationmodels",
)


def validate_macos_app_requirements(requirements: str) -> None:
    """Reject source/headless-only closures from the standalone app."""
    normalized = requirements.casefold()
    names = {
        line.partition("==")[0].strip().casefold()
        for line in requirements.splitlines()
        if "==" in line and not line.lstrip().startswith("#")
    }
    prohibited_names = sorted(names & MACOS_APP_FORBIDDEN_REQUIREMENT_NAMES)
    prohibited_markers = sorted(
        marker for marker in MACOS_APP_FORBIDDEN_NATIVE_MARKERS if marker in normalized
    )
    prohibited = prohibited_names + prohibited_markers
    if prohibited:
        raise RuntimeError(
            "macOS 14 standalone requirements contain the source/headless-only "
            "Apple cleanup SDK or SaT scorer closure: " + ", ".join(prohibited)
        )


def validate_full_requirements(requirements: str) -> None:
    """Reject explicitly selected source/headless proposal extras from ``full``."""
    names = {
        line.partition("==")[0].strip().casefold()
        for line in requirements.splitlines()
        if "==" in line and not line.lstrip().startswith("#")
    }
    prohibited = sorted(names & SOURCE_HEADLESS_ONLY_REQUIREMENT_NAMES)
    if prohibited:
        raise RuntimeError(
            "generic full requirements contain source/headless-only proposal "
            "dependencies: " + ", ".join(prohibited)
        )


def exported_requirements(
    group: str,
    extras: tuple[str, ...],
    no_default_groups: bool,
) -> str:
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "requirements.txt"
        command = [
            "uv",
            "export",
            "--locked",
            "--no-dev",
        ]
        if no_default_groups:
            command.append("--no-default-groups")
        for extra in extras:
            command.extend(["--extra", extra])
        command.extend(
            [
                "--group",
                group,
                "--no-emit-project",
                "--no-header",
                "--no-annotate",
                "--emit-index-url",
                "--output-file",
                str(output),
            ]
        )
        subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return output.read_text(encoding="utf-8").rstrip() + "\n" + SPDX_FOOTER


def verify_or_write(*, write: bool) -> None:
    rendered_by_contract = {
        contract: exported_requirements(*contract) for contract in set(TARGETS.values())
    }
    drifted: list[str] = []
    for relative, contract in TARGETS.items():
        path = ROOT / relative
        rendered = rendered_by_contract[contract]
        if relative == MACOS_APP_TARGET:
            validate_macos_app_requirements(rendered)
        if contract[1] == ("full",):
            validate_full_requirements(rendered)
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
