# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import shutil
import subprocess
from pathlib import Path

import pytest


def test_checkout_preserves_exact_text_and_binary_bytes_with_autocrlf_true(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("real Git is required for checkout-byte regression")
    source = tmp_path / "source"
    source.mkdir()
    rules = Path(__file__).resolve().parents[1] / ".gitattributes"
    (source / ".gitattributes").write_bytes(rules.read_bytes())
    text = b"Synthetic exact-hash license fixture.\nSecond line.\n"
    binary = b"\x00\x01\r\n\xff\n"
    (source / "LICENSE").write_bytes(text)
    (source / "resource.bin").write_bytes(binary)

    def run(*args: str) -> None:
        subprocess.run(
            [git, *args], cwd=source, check=True, capture_output=True, timeout=30,
        )

    run("init")
    run("-c", "core.autocrlf=true", "add", ".gitattributes", "LICENSE", "resource.bin")
    run("-c", "user.name=Auralign test fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "-m", "Synthetic checkout-byte regression fixture")
    checkout = tmp_path / "checkout"
    run("-c", "core.autocrlf=true", "clone", "--no-hardlinks", str(source), str(checkout))
    assert (checkout / "LICENSE").read_bytes() == text
    assert (checkout / "resource.bin").read_bytes() == binary
