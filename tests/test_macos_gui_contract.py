# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Repository-level contracts for the native macOS control surface."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACOS = ROOT / "macos"
APP = MACOS / "Auralign"
PROJECT = MACOS / "Auralign.xcodeproj" / "project.pbxproj"
APP_ICON = APP / "Assets.xcassets" / "AppIcon.appiconset"
ICON_COMPOSER_DOCUMENT = APP / "audio-waveform-icon.icon"
ASSET_PROVENANCE = MACOS / "ASSET_PROVENANCE.md"

ICON_PACKAGE_HASHES = {
    "Assets/01-structural-frame.svg": (
        "474b1a6dc3be5d547826267f7dca8d134552e67f71a1631c174083950eb374dc"
    ),
    "Assets/02-inner-panel-and-axis.svg": (
        "676e7349fa826f3142b03d9190e43850d951833672668ab4264dc34ab550b95d"
    ),
    "Assets/03-waveform-base.svg": (
        "c322281b1e303cf68109d4128920b97acdac506e4831dadc6b13cfc72dfa168b"
    ),
    "Assets/04-waveform-detail.svg": (
        "49f176abb8663e62cdbcbdec85b434f62771d2ad7e0f4c0db224adc0952524f7"
    ),
    "Assets/05-inactive-rails.svg": (
        "dd16d4d3f46953b00dffe454b3eb4584a047e69f099f15e2d6db957949aa9549"
    ),
    "Assets/06-selected-rail.svg": (
        "2210f306afa73f7ff8feafd9c8e9d1b3d8f6419b6cef4ec4bb29e20d5654a25d"
    ),
    "Assets/07-inactive-controls.svg": (
        "4f593bbbed3db64ee1bcf0b32bb34600c2ae41391329e7e7adb41f6359892673"
    ),
    "Assets/08-selected-controls.svg": (
        "416a5b107f056c5de202775afe6470fecc60990a636f20d06d96082319a141d4"
    ),
    "icon.json": "e3e1589694b7f168fd04074b09b4d9a06620e4ccc34bd68e17ece5f64ddea4a3",
}
ICON_PACKAGE_SNAPSHOT = (
    "69b2088ccbe96a0c88fae867c775c6957f2ffe897b513667b44d216a0f292862"
)
FALLBACK_HASHES = {
    "Contents.json": (
        "13b1c7461192863c9f3ab3ceab313acd6f6531898360983495bf13d48dfc967f"
    ),
    "icon_16x16.png": (
        "e48b4d8566ba5cf441667051fa8ceb902ab7eac76db6f07fa90daa38b7ce95be"
    ),
    "icon_16x16@2x.png": (
        "2e641facf28743785c7adb5d901c5956b37a52e62e936bd77bec797e25031df1"
    ),
    "icon_32x32.png": (
        "2e641facf28743785c7adb5d901c5956b37a52e62e936bd77bec797e25031df1"
    ),
    "icon_32x32@2x.png": (
        "f2cd0f5f25fc246e6aeb118368eee7570117cb8e70b543e69e84b25a15fef372"
    ),
    "icon_128x128.png": (
        "547c49c67aa4c98afc1f45a62f473a20e2f5d3460f7d5b6f182d3e30fad153ff"
    ),
    "icon_128x128@2x.png": (
        "3f5ac5475098bd134754520db487656b8a35bf49bf83d009cecfdb71ff39fc9d"
    ),
    "icon_256x256.png": (
        "3f5ac5475098bd134754520db487656b8a35bf49bf83d009cecfdb71ff39fc9d"
    ),
    "icon_256x256@2x.png": (
        "1f4162a7e08b835b82a6ccc9f684aa634f8b8182c36548619559d3747dcf8473"
    ),
    "icon_512x512.png": (
        "1f4162a7e08b835b82a6ccc9f684aa634f8b8182c36548619559d3747dcf8473"
    ),
    "icon_512x512@2x.png": (
        "2c1b5ed43a0ae83cb43582bbe34cef1071e44cf257ef5b41edb0480a21dc1e4a"
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_quarantine(path: Path) -> bool:
    if sys.platform != "darwin":
        return False
    return (
        subprocess.run(
            ["/usr/bin/xattr", "-p", "com.apple.quarantine", path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _png_metadata(path: Path) -> tuple[int, int, int, int]:
    """Return width, height, bit depth, and color type from a valid PNG IHDR."""

    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"{path} is not a complete PNG")
    chunk_length = struct.unpack(">I", data[8:12])[0]
    chunk_type = data[12:16]
    if chunk_length != 13 or chunk_type != b"IHDR":
        raise AssertionError(f"{path} does not begin with a canonical PNG IHDR")
    chunk_data = data[16:29]
    expected_crc = struct.unpack(">I", data[29:33])[0]
    actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise AssertionError(f"{path} has an invalid PNG IHDR checksum")
    width, height, bit_depth, color_type, compression, filtering, interlace = (
        struct.unpack(">IIBBBBB", chunk_data)
    )
    if (compression, filtering, interlace) != (0, 0, 0):
        raise AssertionError(f"{path} uses an unexpected PNG encoding")
    return width, height, bit_depth, color_type


def test_release_build_is_arm64_hardened_path_free_and_fixture_free() -> None:
    project = PROJECT.read_text(encoding="utf-8")
    info = (APP / "Info.plist").read_text(encoding="utf-8")

    assert project.count("ARCHS = arm64;") == 2
    assert 'AURALIGN_ENGINE_ROOT = "";' in project
    assert "ENABLE_HARDENED_RUNTIME = YES;" in project
    assert (
        project.count(
            'ASSETCATALOG_COMPILER_APPICON_NAME = "audio-waveform-icon";'
        )
        == 2
    )
    assert project.count("ASSETCATALOG_COMPILER_STANDALONE_ICON_BEHAVIOR = all;") == 2
    assert project.count("ENABLE_PREVIEWS = YES;") == 1
    assert project.count("ENABLE_PREVIEWS = NO;") == 1
    assert project.count("CODE_SIGN_INJECT_BASE_ENTITLEMENTS = NO;") == 1
    assert "<key>LSMultipleInstancesProhibited</key>" in info
    assert "/Users/" not in project
    assert "/Users/" not in info
    assert "ASSETCATALOG_COMPILER_APPICON_NAME = AppIcon;" not in project
    assert not list(APP_ICON.glob("AppIcon-*.png"))


def test_passive_global_status_is_not_rendered_as_a_toolbar_control() -> None:
    main_view = (APP / "Views" / "MainView.swift").read_text(encoding="utf-8")

    assert "ToolbarItem(placement: .status)" not in main_view
    assert "GlobalStatusFooter(model: model)" in main_view
    assert "ToolbarItem(placement: .primaryAction)" in main_view
    assert "AccessibilityIdentifier.primaryActionStatus" in main_view


def test_passive_status_component_never_looks_like_an_input_capsule() -> None:
    visual_system = (
        APP / "Views" / "AuralignVisualSystem.swift"
    ).read_text(encoding="utf-8")
    status_component = visual_system.split(
        "struct AuralignStatusBadge: View {", maxsplit=1
    )[1].split(
        "struct AuralignStatusBanner: View {", maxsplit=1
    )[0]

    assert "Label(text, systemImage:" in status_component
    assert "Capsule()" not in status_component
    assert ".background(" not in status_component
    assert ".overlay" not in status_component


def test_genuine_icon_composer_document_matches_authoritative_snapshot() -> None:
    root_metadata = ICON_COMPOSER_DOCUMENT.lstat()
    assert stat.S_ISDIR(root_metadata.st_mode)
    assert not stat.S_ISLNK(root_metadata.st_mode)

    directories: list[str] = []
    entries: list[dict[str, int | str]] = []
    for path in sorted(ICON_COMPOSER_DOCUMENT.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(ICON_COMPOSER_DOCUMENT).as_posix()
        assert not stat.S_ISLNK(metadata.st_mode), relative
        assert not _has_quarantine(path), relative
        if stat.S_ISDIR(metadata.st_mode):
            directories.append(relative)
            continue
        assert stat.S_ISREG(metadata.st_mode), relative
        if os.name != "nt":
            assert stat.S_IMODE(metadata.st_mode) == 0o644, relative
        entries.append(
            {
                "bytes": metadata.st_size,
                # Git records only executable/non-executable. Normalize the
                # reviewed non-executable source mode so the immutable package
                # digest is stable on Windows checkouts as well as POSIX hosts.
                "mode": 0o644,
                "path": relative,
                "sha256": _sha256(path),
            }
        )

    assert directories == ["Assets"]
    assert len(entries) == 9
    assert sum(int(entry["bytes"]) for entry in entries) == 29_869
    assert {str(entry["path"]): str(entry["sha256"]) for entry in entries} == (
        ICON_PACKAGE_HASHES
    )
    canonical = json.dumps(
        {"directories": directories, "files": entries},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert hashlib.sha256(canonical).hexdigest() == ICON_PACKAGE_SNAPSHOT


def test_app_icon_fallback_has_exact_hashes_slots_and_pixel_sizes() -> None:
    assert {
        path.name for path in APP_ICON.iterdir() if path.is_file()
    } == set(FALLBACK_HASHES)
    for filename, expected_hash in FALLBACK_HASHES.items():
        path = APP_ICON / filename
        metadata = path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        assert not stat.S_ISLNK(metadata.st_mode)
        assert not _has_quarantine(path)
        assert _sha256(path) == expected_hash

    contents = json.loads((APP_ICON / "Contents.json").read_text(encoding="utf-8"))
    declared: dict[str, int] = {}
    for item in contents["images"]:
        logical_size = int(item["size"].split("x", maxsplit=1)[0])
        scale = int(item["scale"].removesuffix("x"))
        declared[item["filename"]] = logical_size * scale

    assert declared == {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, pixels in declared.items():
        width, height, bit_depth, color_type = _png_metadata(APP_ICON / filename)
        assert (width, height) == (pixels, pixels)
        assert bit_depth == 8
        assert color_type == 2  # Truecolor RGB, with no alpha channel.


def test_icon_provenance_is_immutable_and_repository_relative() -> None:
    provenance = ASSET_PROVENANCE.read_text(encoding="utf-8")
    for value in (
        "ae319eb89650b9c8b0ce4ce5bac5e8adf2a0bb48",
        "1f8db9502713133f8d2963be8a6009e9ab6b593bb81ddc3e33b9e10210a0e5d6",
        ICON_PACKAGE_SNAPSHOT,
        FALLBACK_HASHES["Contents.json"],
        "24a627e0bec4201d19f24c37422fddafda48d7cd450807ff523ffe3f9dded3af",
    ):
        assert value in provenance
    assert "/Users/" not in provenance
    assert "exports/icon-composer/audio-waveform-icon.icon" in provenance
    assert "exports/xcode/AppIcon.xcassets/AppIcon.appiconset" in provenance


def test_macos_gui_runbook_and_local_gate_are_indexed_and_safe() -> None:
    docs_index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "macos-gui.md").read_text(encoding="utf-8")
    gate = (ROOT / "scripts" / "check_macos_gui.sh").read_text(encoding="utf-8")

    assert "macos-gui.md" in docs_index
    assert "docs/macos-gui.md" in readme
    assert "doctor --startup" in runbook
    assert "pass `CODE_SIGNING_ALLOWED=NO` to a UI-test run" in runbook
    assert "/tmp/auralign-macos-derived" not in runbook
    assert "Sign to Run Locally" in runbook
    assert "Gatekeeper" in runbook

    assert "CODE_SIGNING_ALLOWED=NO" not in gate
    assert "Library/Developer/Xcode/DerivedData/AuralignLocalGate" in gate
    assert "GateEvidence" in gate
    assert ".AuralignLocalGate.lock" in gate
    assert "mktemp" not in gate
    assert 'rm -rf "$derived_data"' not in gate
    assert "-jobs 1" in gate
    assert "-parallel-testing-enabled NO" in gate
    assert "-resultBundlePath" in gate
    assert "signed-tests-summary.json" in gate
    assert "signed-tests-tree.json" in gate
    assert "minimumTestCount" in gate
    assert "runtimeWarnings" in gate
    assert "unexpectedRuntimeWarnings" in gate
    assert "User-interactive quality-of-service" in gate
    assert "build-warnings.json" in gate
    assert "ps -axo pid=,args=" in gate
    assert "source-state-before.json" in gate
    assert "source-state-after.json" in gate
    assert "repository source state changed" in gate
    assert "LAST_ATTEMPT" in gate
    assert "LATEST_PASS" in gate
    assert "after-tests" in gate
    assert "after-analyze" in gate
    assert "after-release-build" in gate
    assert "postflight" in gate
    assert "AuralignEngineRoot" in gate
    assert "codesign --verify --deep --strict" in gate
    assert "com.apple.security.get-task-allow" in gate
    assert "com.apple.security.cs.disable-library-validation" in gate
    assert "com.apple.security.cs.allow-unsigned-executable-memory" in gate
    assert "com.apple.security.cs.dynamic-codesigning" in gate
    assert "com.apple.security.application-groups" in gate
    assert "exact empty entitlement dictionary" in gate
    assert "com.auralign.studio" in gate
    assert "LSMinimumSystemVersion" in gate
    assert "CFBundlePackageType" in gate
    assert "CFBundleShortVersionString" in gate
    assert "CFBundleVersion" in gate
    assert "CFBundleIconName" in gate
    assert "CFBundleIconFile" in gate
    assert "audio-waveform-icon.icns" in gate
    assert "assetutil" in gate
    assert "iconutil" in gate
    assert "config_manifest.json" in gate
    assert "FieldCatalog.json" in gate
    assert "release-file-allowlist.diff" in gate
    assert "find -P" in gate
    assert "com.apple.quarantine" in gate
    assert "otool -L" in gate
    assert "XCRemoteSwiftPackageReference" in gate
    assert "single-instance-contender" in gate
    assert "AURALIGN_ENABLE_UI_TEST_FIXTURES" in gate
    assert "AURALIGN_PROTOTYPE_DIRECTION" in gate
    assert "AURALIGN_ENABLE_DEBUG_AUTOMATION" in gate
    assert "AURALIGN_SNAPSHOT_DIR" in gate
    assert "AURALIGN_SNAPSHOT_NONCE" in gate
    assert "AURALIGN_SNAPSHOT_WINDOW_ROLE" in gate
    assert "AURALIGN_SNAPSHOT_WINDOW_ACTIVITY" in gate
    assert "AURALIGN_SNAPSHOT_SIDEBAR" in gate
    assert "AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY" in gate
    assert "AURALIGN_PRESENTATION_INCREASE_CONTRAST" in gate
    assert "AURALIGN_PRESENTATION_REDUCE_MOTION" in gate
    assert "AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR" in gate
    assert "com.auralign.studio.debug.command" in gate
    assert "com.auralign.studio.synthetic-fixture." in gate
    assert "selected-run-plan" in gate
    assert "invalid-hidden-advanced" in gate
    assert "settings-locked" in gate
    assert "/AURALIGN_SYNTHETIC/" in gate
    assert "Auralign.debug.dylib" in gate
    assert "__preview.dylib" in gate

    project = PROJECT.read_text(encoding="utf-8")
    for marker in (
        "XCRemoteSwiftPackageReference",
        "XCLocalSwiftPackageReference",
        "packageReferences",
    ):
        assert marker not in project
    assert "DEPLOYMENT_POSTPROCESSING = YES;" in project
    assert "STRIP_INSTALLED_PRODUCT = YES;" in project
    assert "STRIP_STYLE = all;" in project
