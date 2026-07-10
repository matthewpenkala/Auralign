# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Small shared utilities."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

from .errors import CommandExecutionError
from .platform_security import atomic_write_text as secure_atomic_write_text
from .redaction import redact_text


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_repo_dir(model_id: str) -> str:
    return model_id.replace("/", "--")


def write_json(path: Path, data: Any) -> None:
    atomic_write_json(path, data)


def atomic_write_json(path: Path, data: Any) -> None:
    encoded = json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n"
    secure_atomic_write_text(path, encoded)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def which(name: str) -> str | None:
    return shutil.which(name)


def run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, text=True, capture_output=True, check=check)
    except FileNotFoundError as exc:
        executable = args[0] if args else "command"
        raise CommandExecutionError(
            f"Required executable not found: {executable}. Install it and make sure it is on PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = redact_text(exc.stderr or "")
        stdout = redact_text(exc.stdout or "")
        detail = stderr.strip() or stdout.strip() or f"exit code {exc.returncode}"
        command = " ".join(args[:3])
        raise CommandExecutionError(f"Command failed: {command}. {detail}") from exc


def atomic_text_write(path: Path, text: str) -> None:
    secure_atomic_write_text(path, text)


@contextmanager
def atomic_text_writer(path: Path, *, newline: str | None = None) -> Iterator[TextIO]:
    """Yield a private same-directory writer and atomically replace on success."""
    handle = io.StringIO(newline=newline)
    yield handle
    secure_atomic_write_text(path, handle.getvalue())
