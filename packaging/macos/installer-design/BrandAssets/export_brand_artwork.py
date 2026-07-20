#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Reproduce and verify Auralign's curated transparent Installer symbol."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageCms

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "auralign-styled-symbol.svg"
OUTPUT = HERE / "auralign-styled-symbol-tight.png"
SOURCE_SHA256 = "8ed79fd4430b534862a90d224fd03c6aac8e1fae32c580b6716146c27b8167b7"
OUTPUT_SHA256 = "6f556cd43c2c30f3658490da933e1e345a52320f223af47b3de192cae1f63963"
OUTPUT_BYTES = 453_041
SOURCE_ALPHA_BOUNDS = (145, 306, 1903, 1741)
TIGHT_CROP = (113, 274, 1935, 1773)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def srgb_profile_bytes() -> bytes:
    """Return an sRGB profile with the source project's normalized ICC date."""

    profile = bytearray(
        ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    )
    if len(profile) < 40 or profile[36:40] != b"acsp":
        raise RuntimeError("LittleCMS returned an invalid sRGB ICC profile")
    profile[24:36] = struct.pack(">6H", 2026, 7, 19, 12, 0, 0)
    return bytes(profile)


def render_tight(destination: Path) -> dict[str, object]:
    if sha256(SOURCE) != SOURCE_SHA256:
        raise RuntimeError("curated brand SVG hash drift")
    resvg = shutil.which("resvg")
    if resvg is None:
        raise RuntimeError("resvg is required to reproduce the brand artwork")
    with tempfile.TemporaryDirectory(prefix="auralign-brand-artwork-") as raw:
        rendered = Path(raw) / "symbol-2048.png"
        subprocess.run(
            [
                resvg,
                "--quiet",
                "--width",
                "2048",
                "--height",
                "2048",
                str(SOURCE),
                str(rendered),
            ],
            check=True,
        )
        with Image.open(rendered) as image:
            rgba = image.convert("RGBA")
            bounds = rgba.getchannel("A").getbbox()
            if bounds != SOURCE_ALPHA_BOUNDS:
                raise RuntimeError(f"brand alpha bounds drift: {bounds}")
            tight = rgba.crop(TIGHT_CROP)
            tight.save(
                destination,
                format="PNG",
                compress_level=9,
                optimize=False,
                icc_profile=srgb_profile_bytes(),
            )
    digest = sha256(destination)
    size = destination.stat().st_size
    if (digest, size) != (OUTPUT_SHA256, OUTPUT_BYTES):
        raise RuntimeError(
            "brand raster reproducibility drift: "
            f"sha256={digest}, bytes={size}"
        )
    return {
        "bytes": size,
        "icc_profile": True,
        "mode": "RGBA",
        "pixel_size": [1822, 1499],
        "sha256": digest,
        "source_alpha_bounds": list(SOURCE_ALPHA_BOUNDS),
        "tight_crop": list(TIGHT_CROP),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--write",
        action="store_true",
        help="Replace the checked-in raster after exact reproduction succeeds.",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="auralign-brand-check-") as raw:
        candidate = Path(raw) / OUTPUT.name
        evidence = render_tight(candidate)
        if args.write:
            candidate.replace(OUTPUT)
    if not OUTPUT.is_file() or sha256(OUTPUT) != OUTPUT_SHA256:
        raise RuntimeError("checked-in brand raster is missing or has drifted")
    print(json.dumps({"passed": True, "output": evidence}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
