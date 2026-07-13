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
TOKEN_PATTERNS = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"hf_[A-Za-z0-9]{24,}"),
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
)
PRIVATE_KEY_MARKERS = (
    b"BEGIN OPENSSH" + b" PRIVATE KEY",
    b"BEGIN RSA" + b" PRIVATE KEY",
    b"BEGIN EC" + b" PRIVATE KEY",
)


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
        lowered = {part.casefold() for part in path.parts}
        basename = path.name.casefold()
        if lowered.intersection(PROHIBITED_PARTS):
            raise RuntimeError(f"reachable private path: {name}")
        if basename in PROHIBITED_BASENAMES or path.suffix.casefold() in PROHIBITED_SUFFIXES:
            raise RuntimeError(f"reachable prohibited file: {name}")
        if size >= MAX_BLOB_BYTES:
            raise RuntimeError(f"reachable blob is at least 10 MiB: {name}")
        if object_id in scanned:
            continue
        scanned.add(object_id)
        data = blob_bytes(object_id)
        if any(marker in data for marker in PRIVATE_KEY_MARKERS) or any(
            pattern.search(data) for pattern in TOKEN_PATTERNS
        ):
            raise RuntimeError(f"reachable blob contains a secret pattern: {name}")


def main() -> int:
    verify_history()
    print("reachable_history_ok=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
