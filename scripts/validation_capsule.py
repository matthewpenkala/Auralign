#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Build or verify the owner-local, fixed real-media validation capsule."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SOURCE_SIZE = 331_159_995
SOURCE_SHA256 = "f34144917f29d8435b4bd34cc5942bb0dd5f58505d4e30c6add6a8cdc372a6ad"
CANARY_START = 600.0
CANARY_DURATION = 600.0
EXPECTED_SPEAKERS = 2
ENROLLMENT_INTERVALS = {
    "enrollment-speaker-00.wav": ((1205.210, 1221.612), (363.080, 375.601), (0.031, 10.932)),
    "enrollment-speaker-01.wav": ((133.259, 161.643), (1238.251, 1261.218)),
}
CAPSULE_FILES = (
    "canary-10m.mp4",
    "enrollment-speaker-00.wav",
    "enrollment-speaker-01.wav",
    "validation-capsule.json",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def ffprobe(path: Path) -> dict[str, Any]:
    completed = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size:stream=index,codec_type,codec_name,width,height,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("ffprobe returned an invalid payload")
    return payload


def normalized_media_metadata(path: Path) -> dict[str, Any]:
    payload = ffprobe(path)
    streams = payload.get("streams")
    format_row = payload.get("format")
    if not isinstance(streams, list) or not isinstance(format_row, dict):
        raise RuntimeError("ffprobe media metadata is incomplete")
    return {
        "duration_seconds": round(float(format_row["duration"]), 6),
        "size_bytes": int(format_row["size"]),
        "streams": streams,
    }


def build(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve(strict=True)
    if source.stat().st_size != SOURCE_SIZE or sha256_file(source) != SOURCE_SHA256:
        raise RuntimeError("source media does not match the approved size and SHA-256")
    destination = destination.expanduser().resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)

    canary = destination / "canary-10m.mp4"
    canary_command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-ss",
        "00:10:00.000",
        "-t",
        "600.000",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "+genpts",
        "-movflags",
        "+faststart",
        str(canary),
    ]
    run(canary_command)

    for filename, intervals in ENROLLMENT_INTERVALS.items():
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        for start, end in intervals:
            command.extend(["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(source)])
        filters = []
        labels = []
        for index in range(len(intervals)):
            label = f"a{index}"
            labels.append(f"[{label}]")
            filters.append(
                f"[{index}:a:0]aresample=16000,aformat=sample_fmts=s16:channel_layouts=mono[{label}]"
            )
        filters.append(f"{''.join(labels)}concat=n={len(intervals)}:v=0:a=1[out]")
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[out]",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(destination / filename),
            ]
        )
        run(command)

    ffmpeg_version = run(["ffmpeg", "-version"]).stdout.splitlines()
    manifest = {
        "schema_version": "auralign-validation-capsule.v1",
        "source": {"size_bytes": SOURCE_SIZE, "sha256": SOURCE_SHA256},
        "canary": {
            "file": canary.name,
            "start_seconds": CANARY_START,
            "end_seconds": CANARY_START + CANARY_DURATION,
            "expected_speaker_count": EXPECTED_SPEAKERS,
            "sha256": sha256_file(canary),
            "media": normalized_media_metadata(canary),
        },
        "enrollments": [
            {
                "file": filename,
                "intervals_seconds": [[start, end] for start, end in intervals],
                "sha256": sha256_file(destination / filename),
                "media": normalized_media_metadata(destination / filename),
            }
            for filename, intervals in ENROLLMENT_INTERVALS.items()
        ],
        "tooling": {
            "ffmpeg_version": ffmpeg_version[0] if ffmpeg_version else "unknown",
            "ffmpeg_build": ffmpeg_version[1] if len(ffmpeg_version) > 1 else "unknown",
            "canary_command": (
                "ffmpeg -hide_banner -loglevel error -i {SOURCE} -ss 00:10:00.000 "
                "-t 600.000 -map 0:v:0 -map 0:a:0 -c copy -avoid_negative_ts "
                "make_zero -fflags +genpts -movflags +faststart canary-10m.mp4"
            ),
        },
    }
    manifest_path = destination / "validation-capsule.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sums = "".join(f"{sha256_file(destination / name)}  {name}\n" for name in CAPSULE_FILES)
    (destination / "SHA256SUMS").write_text(sums, encoding="utf-8")
    for path in destination.iterdir():
        path.chmod(0o600)
    verify(destination)


def verify(destination: Path) -> None:
    destination = destination.expanduser().resolve(strict=True)
    allowed = {*CAPSULE_FILES, "SHA256SUMS"}
    actual = {path.name for path in destination.iterdir() if path.is_file()}
    if actual != allowed or any(path.is_symlink() for path in destination.iterdir()):
        raise RuntimeError("capsule file allowlist or link policy failed")
    sums: dict[str, str] = {}
    for line in (destination / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, filename = line.split("  ", maxsplit=1)
        if filename in sums or filename not in CAPSULE_FILES:
            raise RuntimeError("SHA256SUMS contains an unexpected or duplicate entry")
        sums[filename] = digest
    if set(sums) != set(CAPSULE_FILES):
        raise RuntimeError("SHA256SUMS is incomplete")
    for filename, expected in sums.items():
        if sha256_file(destination / filename) != expected:
            raise RuntimeError("capsule SHA-256 verification failed")
    manifest = json.loads((destination / "validation-capsule.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "auralign-validation-capsule.v1":
        raise RuntimeError("capsule manifest schema is unsupported")
    if manifest.get("source", {}).get("sha256") != SOURCE_SHA256:
        raise RuntimeError("capsule source identity is invalid")
    if manifest.get("canary", {}).get("expected_speaker_count") != EXPECTED_SPEAKERS:
        raise RuntimeError("capsule speaker-count contract is invalid")
    duration = float(manifest.get("canary", {}).get("media", {}).get("duration_seconds", 0))
    if not 599.9 <= duration <= 600.1:
        raise RuntimeError("capsule duration is outside the fixed ten-minute tolerance")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--source", type=Path, required=True)
    build_parser.add_argument("--destination", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--capsule-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        build(args.source, args.destination)
    else:
        verify(args.capsule_dir)
    print("validation_capsule_ok=true")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"validation_capsule_ok=false reason={type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
