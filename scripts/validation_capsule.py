#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Build or verify the owner-local, fixed real-media validation capsule."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
EXPECTED_FILE_SHA256 = {
    "canary-10m.mp4": "2548fbdf27053f38950553745e74e060d2560e6ed90f60d1739174c71999bd0a",
    "enrollment-speaker-00.wav": (
        "aca11e7f86bae81889d5bce4f6320ed5b7d18d6e6b33fd3409c8ab62bbc86b73"
    ),
    "enrollment-speaker-01.wav": (
        "1bdb9ffbcd0870e8a4b009837fd75e1d10dc21e88818e4ec209de9454a6f9db7"
    ),
    "validation-capsule.json": (
        "5a652951669ce3993a6dfb2c1d0102967515430beaf54ba73bf4ae6e9db237f4"
    ),
}
EXPECTED_MEDIA = {
    "canary-10m.mp4": {
        "duration_seconds": 600.013008,
        "size_bytes": 141_995_238,
        "streams": [
            {
                "codec_name": "h264",
                "codec_type": "video",
                "height": 2234,
                "index": 0,
                "width": 3456,
            },
            {
                "channels": 2,
                "codec_name": "aac",
                "codec_type": "audio",
                "index": 1,
                "sample_rate": "48000",
            },
        ],
    },
    "enrollment-speaker-00.wav": {
        "duration_seconds": 39.824,
        "size_bytes": 1_274_446,
        "streams": [
            {
                "channels": 1,
                "codec_name": "pcm_s16le",
                "codec_type": "audio",
                "index": 0,
                "sample_rate": "16000",
            }
        ],
    },
    "enrollment-speaker-01.wav": {
        "duration_seconds": 51.351,
        "size_bytes": 1_643_310,
        "streams": [
            {
                "channels": 1,
                "codec_name": "pcm_s16le",
                "codec_type": "audio",
                "index": 0,
                "sample_rate": "16000",
            }
        ],
    },
}
EXPECTED_CANARY_COMMAND = (
    "ffmpeg -hide_banner -loglevel error -i {SOURCE} -ss 00:10:00.000 "
    "-t 600.000 -map 0:v:0 -map 0:a:0 -c copy -avoid_negative_ts "
    "make_zero -fflags +genpts -movflags +faststart canary-10m.mp4"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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
            "canary_command": EXPECTED_CANARY_COMMAND,
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
    sums_text = (destination / "SHA256SUMS").read_text(encoding="utf-8")
    expected_sums_text = "".join(
        f"{digest}  {filename}\n" for filename, digest in EXPECTED_FILE_SHA256.items()
    )
    if sums_text != expected_sums_text:
        raise RuntimeError("SHA256SUMS does not exactly identify the fixed approved capsule")
    for line in sums_text.splitlines():
        try:
            digest, filename = line.split("  ", maxsplit=1)
        except ValueError as exc:
            raise RuntimeError("SHA256SUMS contains a malformed entry") from exc
        if (
            filename in sums
            or filename not in CAPSULE_FILES
            or SHA256_PATTERN.fullmatch(digest) is None
        ):
            raise RuntimeError("SHA256SUMS contains an unexpected or duplicate entry")
        sums[filename] = digest
    if sums != EXPECTED_FILE_SHA256:
        raise RuntimeError("SHA256SUMS does not identify the fixed approved capsule")
    for filename, expected in sums.items():
        if sha256_file(destination / filename) != expected:
            raise RuntimeError("capsule SHA-256 verification failed")
    manifest = json.loads((destination / "validation-capsule.json").read_text(encoding="utf-8"))
    _validate_manifest(manifest)
    for filename in ("canary-10m.mp4", *ENROLLMENT_INTERVALS):
        if normalized_media_metadata(destination / filename) != EXPECTED_MEDIA[filename]:
            raise RuntimeError("capsule ffprobe media contract failed")


def _validate_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "source",
        "canary",
        "enrollments",
        "tooling",
    }:
        raise RuntimeError("capsule manifest shape is invalid")
    if manifest["schema_version"] != "auralign-validation-capsule.v1":
        raise RuntimeError("capsule manifest schema is unsupported")
    if manifest["source"] != {"size_bytes": SOURCE_SIZE, "sha256": SOURCE_SHA256}:
        raise RuntimeError("capsule source identity is invalid")
    canary = manifest["canary"]
    expected_canary = {
        "file": "canary-10m.mp4",
        "start_seconds": CANARY_START,
        "end_seconds": CANARY_START + CANARY_DURATION,
        "expected_speaker_count": EXPECTED_SPEAKERS,
        "sha256": EXPECTED_FILE_SHA256["canary-10m.mp4"],
        "media": EXPECTED_MEDIA["canary-10m.mp4"],
    }
    if canary != expected_canary:
        raise RuntimeError("capsule canary contract is invalid")
    enrollments = manifest["enrollments"]
    expected_enrollments = [
        {
            "file": filename,
            "intervals_seconds": [[start, end] for start, end in intervals],
            "sha256": EXPECTED_FILE_SHA256[filename],
            "media": EXPECTED_MEDIA[filename],
        }
        for filename, intervals in ENROLLMENT_INTERVALS.items()
    ]
    if enrollments != expected_enrollments:
        raise RuntimeError("capsule enrollment contract is invalid")
    tooling = manifest["tooling"]
    if not isinstance(tooling, dict) or set(tooling) != {
        "ffmpeg_version",
        "ffmpeg_build",
        "canary_command",
    }:
        raise RuntimeError("capsule tooling contract is invalid")
    if (
        not isinstance(tooling["ffmpeg_version"], str)
        or not tooling["ffmpeg_version"].startswith("ffmpeg version ")
        or not isinstance(tooling["ffmpeg_build"], str)
        or not tooling["ffmpeg_build"].startswith("built with ")
        or tooling["canary_command"] != EXPECTED_CANARY_COMMAND
    ):
        raise RuntimeError("capsule tooling contract is invalid")


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
