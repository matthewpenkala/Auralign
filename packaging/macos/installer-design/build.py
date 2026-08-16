#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Verify Auralign's internalized, deterministic Installer presentation kit."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import html.parser
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
PIXEL_SIZE = (1240, 836)
EXPECTED_TOKENS = {
    "@@PRODUCT_NAME@@",
    "@@APP_PACKAGE_ID@@",
    "@@APP_COMPONENT_PACKAGE@@",
    "@@MODEL_PACKAGE_ID@@",
    "@@MODEL_COMPONENT_PACKAGE@@",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class HTMLValidator(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_html = False
        self.has_body = False

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        self.has_html = self.has_html or tag == "html"
        self.has_body = self.has_body or tag == "body"


def validate_html(path: Path) -> None:
    parser = HTMLValidator()
    parser.feed(path.read_text(encoding="utf-8"))
    require(parser.has_html and parser.has_body, f"invalid Installer HTML: {path}")


def validate_png(path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    require(payload.startswith(b"\x89PNG\r\n\x1a\n"), f"invalid PNG signature: {path}")
    offset = 8
    ihdr: tuple[int, int, int, int, int, int, int] | None = None
    chunks: set[bytes] = set()
    while offset < len(payload):
        require(offset + 12 <= len(payload), f"truncated PNG chunk: {path}")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        chunk_type = payload[offset + 4 : offset + 8]
        chunk_data = payload[offset + 8 : offset + 8 + length]
        checksum = payload[offset + 8 + length : offset + 12 + length]
        require(len(checksum) == 4, f"truncated PNG checksum: {path}")
        expected_crc = struct.unpack(">I", checksum)[0]
        actual_crc = binascii.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        require(actual_crc == expected_crc, f"invalid PNG checksum: {path}")
        chunks.add(chunk_type)
        if chunk_type == b"IHDR":
            require(length == 13, f"invalid PNG IHDR length: {path}")
            ihdr = struct.unpack(">IIBBBBB", chunk_data)
        offset += 12 + length
        if chunk_type == b"IEND":
            break
    require(offset == len(payload), f"trailing PNG bytes: {path}")
    require(ihdr is not None, f"missing PNG IHDR: {path}")
    width, height, depth, color, compression, filtering, interlace = ihdr
    require((width, height) == PIXEL_SIZE, f"unexpected PNG size: {path}")
    require(depth == 8 and color == 2, f"PNG must be 8-bit RGB without alpha: {path}")
    require((compression, filtering, interlace) == (0, 0, 0), f"PNG format drift: {path}")
    require(b"iCCP" in chunks, f"PNG ICC profile missing: {path}")
    return {
        "size": [width, height],
        "mode": "RGB",
        "icc_profile": True,
        "sha256": sha256(path),
    }


def validate_svg(path: Path) -> None:
    root = ET.parse(path).getroot()
    lowered = path.read_text(encoding="utf-8").casefold()
    require("<text" not in lowered, f"Installer background contains SVG text: {path}")
    require("data:image" not in lowered, f"Installer background embeds raster data: {path}")
    require("/users/" not in lowered and "file:" not in lowered, f"absolute SVG asset path: {path}")
    images = root.findall(".//{http://www.w3.org/2000/svg}image")
    require(len(images) == 1, f"unexpected Installer SVG image count: {path}")
    require(
        images[0].get("href") == "../BrandAssets/auralign-styled-symbol-tight.png",
        f"unexpected Installer SVG image source: {path}",
    )
    require(
        {name: images[0].get(name) for name in ("x", "y", "width", "height")}
        == {"x": "-30", "y": "211", "width": "270", "height": "222.14"},
        f"unexpected Installer symbol placement: {path}",
    )
    require(
        'x="8.5" y="204" width="194" height="224"' not in lowered,
        f"superseded Installer artwork card returned: {path}",
    )


def validate_brand_artwork() -> dict[str, object]:
    manifest_path = HERE / "BrandAssets/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == "1.1", "brand manifest schema drift")
    require(manifest.get("placement", {}).get("border_or_card") is False, "brand card drift")
    command = [
        sys.executable,
        "-B",
        str(HERE / "BrandAssets/export_brand_artwork.py"),
    ]
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        env=environment,
        text=True,
    )
    evidence = json.loads(result.stdout)
    require(evidence.get("passed") is True, "brand reproduction verification failed")
    return evidence


def validate_manifest() -> dict[str, object]:
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == "1.2", "installer manifest schema drift")
    files = manifest.get("files")
    require(isinstance(files, dict), "installer manifest files missing")
    for relative, evidence in files.items():
        require(isinstance(relative, str), "installer manifest path invalid")
        require(isinstance(evidence, dict), f"installer manifest evidence invalid: {relative}")
        path = HERE / relative
        require(path.is_file(), f"installer manifest file missing: {relative}")
        require(path.stat().st_size == evidence.get("bytes"), f"installer bytes drift: {relative}")
        require(sha256(path) == evidence.get("sha256"), f"installer hash drift: {relative}")
    source = manifest.get("source_project")
    require(isinstance(source, dict), "installer source project record missing")
    require(
        source.get("commit") == "bd94520ecf106b7502ad04e19e4997dbb74d05dd",
        "installer source commit drift",
    )
    require(source.get("production_artwork_modified") is True, "artwork modification drift")
    return manifest


def validate_distribution_round_trip() -> None:
    require(shutil.which("pkgbuild") is not None, "pkgbuild is unavailable")
    require(shutil.which("productbuild") is not None, "productbuild is unavailable")
    template = (HERE / "Distribution.xml.template").read_text(encoding="utf-8")
    require(
        {token for token in EXPECTED_TOKENS if token in template} == EXPECTED_TOKENS,
        "Distribution template tokens drifted",
    )
    require("<script" not in template.casefold(), "Installer JavaScript is prohibited")
    with tempfile.TemporaryDirectory(prefix="auralign-pkg-design-") as raw:
        temporary = Path(raw)
        app_component = temporary / "Auralign-application-component.pkg"
        model_component = temporary / "Auralign-offline-models-component.pkg"
        package = temporary / "Auralign.pkg"
        distribution = temporary / "Distribution.xml"
        rendered = template
        for token, value in {
            "@@PRODUCT_NAME@@": "Auralign",
            "@@APP_PACKAGE_ID@@": "com.auralign.studio.pkg.design-validation.app",
            "@@APP_COMPONENT_PACKAGE@@": app_component.name,
            "@@MODEL_PACKAGE_ID@@": "com.auralign.studio.pkg.design-validation.models",
            "@@MODEL_COMPONENT_PACKAGE@@": model_component.name,
        }.items():
            rendered = rendered.replace(token, value)
        distribution.write_text(rendered, encoding="utf-8", newline="\n")
        ET.parse(distribution)
        subprocess.run(
            [
                "pkgbuild",
                "--nopayload",
                "--identifier",
                "com.auralign.studio.pkg.design-validation.app",
                "--version",
                "0.3.1.1",
                str(app_component),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "pkgbuild",
                "--nopayload",
                "--identifier",
                "com.auralign.studio.pkg.design-validation.models",
                "--version",
                "2.0.0",
                str(model_component),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "productbuild",
                "--distribution",
                str(distribution),
                "--package-path",
                str(temporary),
                "--resources",
                str(HERE / "Resources"),
                str(package),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        require(package.is_file(), "Installer design round-trip produced no package")


def validate() -> dict[str, object]:
    manifest = validate_manifest()
    ET.parse(HERE / "Distribution.xml.template")
    for path in (
        HERE / "Resources/en.lproj/welcome.html",
        HERE / "Resources/en.lproj/readme.html",
        HERE / "Resources/en.lproj/license.html",
        HERE / "Resources/en.lproj/conclusion.html",
    ):
        validate_html(path)
    pngs = {
        name: validate_png(HERE / "Resources" / name)
        for name in ("background.png", "background-darkAqua.png")
    }
    for path in sorted((HERE / "Sources").glob("*.svg")):
        validate_svg(path)
    brand_artwork = validate_brand_artwork()
    validate_distribution_round_trip()
    return {
        "passed": True,
        "source_commit": manifest["source_project"]["commit"],
        "pngs": pngs,
        "brand_artwork": brand_artwork,
        "distribution_round_trip": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Compatibility flag; this internalized builder is verification-only.",
    )
    parser.parse_args()
    print(json.dumps(validate(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
