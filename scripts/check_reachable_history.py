#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Fail closed on sensitive paths, secrets, or large blobs in reachable Git history."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_BLOB_BYTES = 10 * 1024 * 1024
PROHIBITED_PARTS = {
    ".git",
    ".hf-cache",
    ".huggingface",
    ".venv",
    "datasets",
    "embeddings",
    "models",
    "outputs",
    "profiles",
    "transcripts",
}
PROHIBITED_BASENAMES = {
    ".env",
    ".ltm-model.json",
    "model_audit.json",
    "validation-capsule.json",
}
PROHIBITED_SUFFIXES = {
    ".aac",
    ".aif",
    ".aiff",
    ".avi",
    ".bin",
    ".ckpt",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mxf",
    ".npz",
    ".ogg",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
    ".wav",
    ".webm",
    ".wma",
}
REVIEWED_SWIFT_MODELS_DIRECTORY = PurePosixPath("macos/Auralign/Models")
REVIEWED_PRIVATE_KEY_LITERAL_BLOBS = {
    # A Codex checkpoint of the macOS Release scanner. It contains the three
    # literal private-key sentinels that scanner searches for, no token-shaped
    # value, and no key material. The immutable object ID keeps this exception
    # narrower than a path-level content exemption.
    "39a39501ec422d15cd23e4ca4ce37d2e3f817bfb",
    # Two later Codex checkpoints of the standalone PKG builder contained the
    # same three scanner sentinels, no token-shaped value, and no key material.
    # The live builder now composes the sentinels so future blobs need no
    # exception; these immutable objects remain reachable through checkpoint
    # refs and are reviewed by exact object ID only.
    "fc61c95264a7edbf1ac6a2537d84edca79f1d6c4",
    "e3c3a8c4ffa37794e1f54f294f6d93c4cdceb087",
}
TOKEN_PATTERNS = (
    re.compile(rb"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{24,}"),
    re.compile(rb"(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(rb"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}"),
)
PRIVATE_KEY_MARKERS = (
    b"BEGIN OPENSSH" + b" PRIVATE KEY",
    b"BEGIN RSA" + b" PRIVATE KEY",
    b"BEGIN EC" + b" PRIVATE KEY",
)


def is_reviewed_swift_models_source(path: PurePosixPath) -> bool:
    """Return whether ``path`` is a direct Swift source in the native namespace."""

    return path.parent == REVIEWED_SWIFT_MODELS_DIRECTORY and path.suffix == ".swift"


def has_prohibited_path_component(path: PurePosixPath) -> bool:
    """Return whether a path uses a private-artifact directory name.

    ``macos/Auralign/Models`` is the native app's Swift source namespace, not a
    model cache. Permit only direct ``.swift`` files in that exact directory;
    nested content, weight files, and every other prohibited component remain
    fail-closed.
    """

    lowered = {part.casefold() for part in path.parts}
    return bool(lowered.intersection(PROHIBITED_PARTS)) and (
        not is_reviewed_swift_models_source(path)
    )


def has_unreviewed_secret_shape(object_id: str, data: bytes) -> bool:
    """Detect credentials while permitting one immutable scanner-source blob."""

    if any(pattern.search(data) for pattern in TOKEN_PATTERNS):
        return True
    if not any(marker in data for marker in PRIVATE_KEY_MARKERS):
        return False
    return object_id not in REVIEWED_PRIVATE_KEY_LITERAL_BLOBS


def reachable_objects() -> list[tuple[str, str]]:
    completed = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    rows: list[tuple[str, str]] = []
    for line in completed.stdout.splitlines():
        object_id, separator, path = line.partition(" ")
        if separator:
            rows.append((object_id, path))
    return rows


def object_metadata(object_ids: list[str]) -> dict[str, tuple[str, int]]:
    completed = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        cwd=ROOT,
        input="".join(f"{object_id}\n" for object_id in object_ids),
        check=True,
        capture_output=True,
        text=True,
    )
    result: dict[str, tuple[str, int]] = {}
    for line in completed.stdout.splitlines():
        object_id, object_type, size = line.split()
        result[object_id] = (object_type, int(size))
    return result


def blob_bytes(object_id: str) -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", object_id], cwd=ROOT, check=True, capture_output=True
    ).stdout


def verify_history() -> None:
    rows = reachable_objects()
    metadata = object_metadata([object_id for object_id, _path in rows])
    scanned: set[str] = set()
    for object_id, name in rows:
        object_type, size = metadata[object_id]
        if object_type != "blob":
            continue
        path = PurePosixPath(name)
        basename = path.name.casefold()
        if has_prohibited_path_component(path):
            raise RuntimeError(f"reachable private path: {name}")
        if basename in PROHIBITED_BASENAMES or path.suffix.casefold() in PROHIBITED_SUFFIXES:
            raise RuntimeError(f"reachable prohibited file: {name}")
        if size >= MAX_BLOB_BYTES:
            raise RuntimeError(f"reachable blob is at least 10 MiB: {name}")
        if object_id in scanned:
            continue
        scanned.add(object_id)
        data = blob_bytes(object_id)
        if has_unreviewed_secret_shape(object_id, data):
            raise RuntimeError(f"reachable blob contains a secret pattern: {name}")


def main() -> int:
    verify_history()
    print("reachable_history_ok=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
