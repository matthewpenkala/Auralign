# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Privacy-safe binding for the exact source tree executing a local job."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SHA40 = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def current_code_revision(repo_root: Path | None = None) -> str:
    """Return the Git commit plus a deterministic executable-source tree hash."""

    if repo_root is None:
        embedded = _embedded_code_revision()
        if embedded is not None:
            return embedded
    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    commit = _git_commit(root) or "unversioned"
    return f"{commit}+source-sha256:{_source_tree_sha256(root)}"


def _embedded_code_revision() -> str | None:
    """Read the immutable build binding from a standalone runtime, if present."""

    executable = Path(sys.executable).resolve()
    if len(executable.parents) < 2:
        return None
    record = executable.parents[1] / "release-build.json"
    if not record.is_file() or record.is_symlink():
        return None
    try:
        payload = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("embedded Auralign build record is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        "auralign-embedded-build.v1"
    ):
        raise RuntimeError("embedded Auralign build record has an unsupported schema")
    revision = payload.get("source_revision")
    source_digest = payload.get("source_tree_sha256")
    if (
        not isinstance(revision, str)
        or SHA40.fullmatch(revision) is None
        or not isinstance(source_digest, str)
        or SHA256.fullmatch(source_digest) is None
    ):
        raise RuntimeError("embedded Auralign build record has invalid source identity")
    return f"{revision}+source-sha256:{source_digest}"


def _git_commit(root: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else None


def _source_tree_sha256(root: Path) -> str:
    candidates = [
        path
        for path in (root / "src" / "local_transcript_max").rglob("*")
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts
    ]
    candidates.extend(
        path
        for path in (root / "pyproject.toml", root / "uv.lock")
        if path.is_file() and not path.is_symlink()
    )
    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()
