#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Build, verify, sign, package, and notarize the standalone macOS application.

The shipped product is one scriptless PKG containing one self-contained app.
Models remain outside both artifacts. Release mode fails closed unless the
source tree is clean, both Developer ID identities are supplied, notarization
is accepted, and the exact final package is stapled and reverified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "packaging/macos/runtime-sources.toml"
REQUIREMENTS_PATH = ROOT / "requirements/macos-app-arm64-cpu-py311.txt"
ENTITLEMENTS_PATH = ROOT / "packaging/macos/Auralign.entitlements"
COMPONENT_PLIST_PATH = ROOT / "packaging/macos/component.plist"
DISTRIBUTION_PATH = ROOT / "packaging/macos/Distribution.xml"
INSTALLER_RESOURCES_PATH = ROOT / "packaging/macos/installer-design/Resources"
PROJECT_PATH = ROOT / "macos/Auralign.xcodeproj"
SCHEME = "Auralign"
RUNTIME_DIRECTORY_NAME = "AuralignEngineRuntime"
RUNTIME_BUNDLE_NAME = f"{RUNTIME_DIRECTORY_NAME}.bundle"
RUNTIME_BUNDLE_IDENTIFIER = "com.auralign.studio.engine-runtime"
APP_IDENTIFIER = "com.auralign.studio"
RECEIPT_IDENTIFIER = "com.auralign.studio.pkg.app"
DIAGNOSTIC_CACHE_SCHEMA_VERSION = "auralign-macos-diagnostic-cache.v1"
NORMALIZATION_IMPLEMENTATION_VERSION = "auralign-macho-normalization.v3"
SYSTEM_LIBRARY_PREFIXES = ("/System/Library/", "/usr/lib/")
MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe",
    b"\xca\xfe\xba\xbf",
    b"\xbe\xba\xfe\xca",
    b"\xbf\xba\xfe\xca",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
}
TOKEN_PATTERNS = (
    re.compile(rb"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{24,}"),
    re.compile(rb"(?<![A-Za-z0-9_-])sk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(rb"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}"),
    re.compile(rb"Bearer\s+[A-Za-z0-9._~+/-]{32,}={0,2}"),
)
PRIVATE_KEY_MARKERS = (
    b"BEGIN OPENSSH" + b" PRIVATE KEY",
    b"BEGIN RSA" + b" PRIVATE KEY",
    b"BEGIN EC" + b" PRIVATE KEY",
)
DEBUG_ONLY_MARKERS = (
    b"AURALIGN_ENABLE_UI_TEST_FIXTURES",
    b"--ui-fixture",
    b"/AURALIGN_SYNTHETIC/",
    b"fixture.synthetic-evidence",
    b"fixture.invalid-request",
    b"selected-run-plan",
    b"invalid-hidden-advanced",
    b"settings-locked",
    b"AURALIGN_PROTOTYPE_DIRECTION",
    b"AURALIGN_PROTOTYPE_SCREEN",
    b"AURALIGN_ENABLE_DEBUG_AUTOMATION",
    b"AURALIGN_SNAPSHOT_DIR",
    b"AURALIGN_SNAPSHOT_ON_LAUNCH",
    b"AURALIGN_SNAPSHOT_SECTION",
    b"AURALIGN_SNAPSHOT_APPEARANCE",
    b"AURALIGN_SNAPSHOT_WINDOW_SIZE",
    b"AURALIGN_SNAPSHOT_EXIT_AFTER_CAPTURE",
    b"AURALIGN_SNAPSHOT_WINDOW_ROLE",
    b"AURALIGN_SNAPSHOT_WINDOW_ACTIVITY",
    b"AURALIGN_SNAPSHOT_SIDEBAR",
    b"AURALIGN_SNAPSHOT_NONCE",
    b"AURALIGN_PRESENTATION_REDUCE_TRANSPARENCY",
    b"AURALIGN_PRESENTATION_INCREASE_CONTRAST",
    b"AURALIGN_PRESENTATION_REDUCE_MOTION",
    b"AURALIGN_PRESENTATION_DIFFERENTIATE_WITHOUT_COLOR",
    b"com.auralign.studio.debug.command",
    b"com.auralign.studio.synthetic-fixture.",
)


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )


def output(command: list[str], *, cwd: Path = ROOT) -> str:
    return run(command, cwd=cwd, capture=True).stdout.strip()


def quiet_output(command: list[str], *, cwd: Path = ROOT) -> str:
    """Return checked output without flooding logs for per-object inspection."""
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def no_bytecode_environment(
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    environment = (os.environ if base is None else base).copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def embedded_python_command(executable: Path, *arguments: str) -> list[str]:
    return [str(executable), "-B", *arguments]


def write_embedded_python_launcher(executable: Path, destination: Path) -> Path:
    """Write a temporary interpreter proxy that forces both bytecode controls."""

    require(
        executable.is_file(),
        f"embedded Python executable is missing: {executable}",
    )
    require(
        not destination.exists() and not destination.is_symlink(),
        f"refusing to overwrite embedded Python launcher: {destination}",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        (
            "#!/bin/sh\n"
            "set -eu\n"
            "export PYTHONDONTWRITEBYTECODE=1\n"
            f"exec {shlex.quote(str(executable.absolute()))} -B -I \"$@\"\n"
        ),
        encoding="utf-8",
    )
    destination.chmod(0o700)
    return destination


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def app_runtime_bundle(app: Path) -> Path:
    return app / f"Contents/Frameworks/{RUNTIME_BUNDLE_NAME}"


def app_runtime_root(app: Path) -> Path:
    return app_runtime_bundle(app) / "Contents/Resources/Runtime"


def load_sources() -> dict[str, Any]:
    payload = tomllib.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    require(
        payload.get("schema_version") == "auralign-macos-runtime-sources.v1",
        "unsupported macOS runtime source contract",
    )
    require(payload.get("architecture") == "arm64", "release architecture drift")
    require(payload.get("minimum_macos") == "14.0", "minimum macOS drift")
    return payload


def verify_packaging_contract(sources: dict[str, Any]) -> None:
    distribution = ET.parse(DISTRIBUTION_PATH).getroot()
    package = distribution.find("pkg-ref")
    require(package is not None, "Distribution package reference missing")
    require(package.attrib.get("id") == RECEIPT_IDENTIFIER, "Distribution receipt drift")
    require(
        package.attrib.get("version")
        == f"{sources['product_version']}.{sources['build_number']}",
        "Distribution version drift",
    )
    require(
        (package.text or "").strip() == "Auralign-component.pkg",
        "Distribution component filename drift",
    )
    run(
        [
            sys.executable,
            str(ROOT / "packaging/macos/installer-design/build.py"),
            "--check-only",
        ]
    )


def require_host() -> None:
    require(sys.platform == "darwin", "standalone macOS builds require macOS")
    require(platform.machine() == "arm64", "standalone builds require native arm64")
    for tool in (
        "clang",
        "gpg",
        "install_name_tool",
        "otool",
        "pkg-config",
        "pkgbuild",
        "productbuild",
        "xcodebuild",
        "xcrun",
        "zstd",
    ):
        require(shutil.which(tool) is not None, f"required build tool missing: {tool}")


def require_clean_main() -> str:
    require(output(["git", "branch", "--show-current"]) == "main", "release requires main")
    require(
        not output(["git", "status", "--porcelain=v1", "--untracked-files=all"]),
        "release requires a clean index and worktree",
    )
    revision = output(["git", "rev-parse", "--verify", "HEAD^{commit}"])
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "invalid Git revision")
    return revision


def source_revision(allow_dirty: bool) -> tuple[str, str]:
    revision = (
        output(["git", "rev-parse", "--verify", "HEAD^{commit}"])
        if allow_dirty
        else require_clean_main()
    )
    tree = output(
        [
            "uv",
            "run",
            "--frozen",
            "python",
            "-c",
            (
                "from local_transcript_max.code_provenance import "
                "_source_tree_sha256; from pathlib import Path; "
                "print(_source_tree_sha256(Path.cwd()))"
            ),
        ]
    )
    require(re.fullmatch(r"[0-9a-f]{64}", tree) is not None, "invalid source-tree digest")
    return revision, tree


def toolchain_fingerprint() -> dict[str, str]:
    return {
        "host_macos": platform.mac_ver()[0],
        "host_architecture": platform.machine(),
        "clang": output(["clang", "--version"]),
        "xcode": output(["xcodebuild", "-version"]),
        "macos_sdk_version": output(
            ["xcrun", "--sdk", "macosx", "--show-sdk-version"]
        ),
        "macos_sdk_path": output(["xcrun", "--sdk", "macosx", "--show-sdk-path"]),
        "pkg_config": output(["pkg-config", "--version"]),
    }


def cache_key_inputs(
    *,
    stage: str,
    sources: dict[str, Any],
    toolchain: dict[str, str],
    revision: str | None = None,
    tree_digest: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": DIAGNOSTIC_CACHE_SCHEMA_VERSION,
        "stage": stage,
        "architecture": sources["architecture"],
        "minimum_macos": sources["minimum_macos"],
        "runtime_sources_sha256": sha256(SOURCES_PATH),
        "requirements_sha256": sha256(REQUIREMENTS_PATH),
        "builder_sha256": sha256(Path(__file__).resolve()),
        "normalization_implementation_version": (
            NORMALIZATION_IMPLEMENTATION_VERSION
        ),
        "toolchain": toolchain,
    }
    if revision is not None:
        payload["source_revision"] = revision
    if tree_digest is not None:
        payload["source_tree_sha256"] = tree_digest
    return payload


def fresh_directory(path: Path) -> Path:
    require(not path.exists(), f"refusing to reuse build destination: {path}")
    path.mkdir(parents=True, mode=0o700)
    return path


def verified_cache_payload(
    cache_root: Path,
    stage: str,
    key_inputs: dict[str, Any],
) -> Path | None:
    key = canonical_json_sha256(key_inputs)
    entry = cache_root / stage / key
    if not entry.exists():
        return None
    require(entry.is_dir() and not entry.is_symlink(), f"invalid cache entry: {entry}")
    manifest_path = entry / "manifest.json"
    payload = entry / "payload"
    require(manifest_path.is_file(), f"incomplete cache manifest: {entry}")
    require(payload.is_dir() and not payload.is_symlink(), f"incomplete cache payload: {entry}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"malformed cache manifest: {entry}") from exc
    require(isinstance(manifest, dict), f"malformed cache manifest: {entry}")
    require(
        manifest.get("schema_version") == DIAGNOSTIC_CACHE_SCHEMA_VERSION,
        f"cache schema drift: {entry}",
    )
    require(manifest.get("stage") == stage, f"cache stage drift: {entry}")
    require(manifest.get("key") == key, f"cache key drift: {entry}")
    require(
        manifest.get("key_inputs") == key_inputs,
        f"cache input manifest drift: {entry}",
    )
    require(
        manifest.get("payload_inventory") == inventory_rows(payload),
        f"cache payload hash or mode drift: {entry}",
    )
    return payload


def publish_cache_payload(
    cache_root: Path,
    stage: str,
    key_inputs: dict[str, Any],
    payload_sources: dict[str, Path],
) -> Path:
    key = canonical_json_sha256(key_inputs)
    stage_root = cache_root / stage
    stage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = verified_cache_payload(cache_root, stage, key_inputs)
    if existing is not None:
        return existing

    temporary = Path(tempfile.mkdtemp(prefix=f".{key}.", dir=stage_root))
    try:
        payload = fresh_directory(temporary / "payload")
        for name, source in sorted(payload_sources.items()):
            require(
                name not in {"", ".", ".."} and "/" not in name,
                f"invalid cache payload name: {name}",
            )
            require(
                source.exists() or source.is_symlink(),
                f"cache payload source missing: {source}",
            )
            destination = payload / name
            if source.is_dir() and not source.is_symlink():
                shutil.copytree(source, destination, symlinks=True)
            else:
                shutil.copy2(source, destination, follow_symlinks=False)
        manifest = {
            "schema_version": DIAGNOSTIC_CACHE_SCHEMA_VERSION,
            "stage": stage,
            "key": key,
            "key_inputs": key_inputs,
            "payload_inventory": inventory_rows(payload),
        }
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        entry = stage_root / key
        require(not entry.exists(), f"ambiguous concurrent cache entry: {entry}")
        os.replace(temporary, entry)
        verified = verified_cache_payload(cache_root, stage, key_inputs)
        require(verified is not None, f"new cache entry did not verify: {entry}")
        assert verified is not None
        return verified
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def restore_cache_item(source: Path, destination: Path) -> None:
    require(
        not destination.exists() and not destination.is_symlink(),
        f"refusing to overwrite cache restore destination: {destination}",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def merge_cached_evidence(source: Path, destination: Path) -> None:
    require(source.is_dir(), f"cached evidence directory missing: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir() and not path.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists() or target.is_symlink():
            source_metadata = path.lstat()
            target_metadata = target.lstat()
            require(
                stat.S_IFMT(source_metadata.st_mode)
                == stat.S_IFMT(target_metadata.st_mode),
                f"cached evidence type conflicts with current evidence: {relative}",
            )
            if path.is_symlink():
                require(
                    os.readlink(path) == os.readlink(target),
                    f"cached evidence link conflicts with current evidence: {relative}",
                )
            else:
                require(
                    sha256(path) == sha256(target),
                    f"cached evidence hash conflicts with current evidence: {relative}",
                )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target, follow_symlinks=False)


def download(
    url: str,
    expected_sha256: str,
    destination: Path,
    *,
    cache_root: Path | None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    key_inputs = {
        "schema_version": DIAGNOSTIC_CACHE_SCHEMA_VERSION,
        "stage": "downloads",
        "url": url,
        "expected_sha256": expected_sha256,
    }
    cached: Path | None = None
    if cache_root is not None:
        payload = verified_cache_payload(cache_root, "downloads", key_inputs)
        if payload is not None:
            cached = payload / "source"
            require(cached.is_file(), f"cached download payload missing: {url}")
            require(sha256(cached) == expected_sha256, f"cached download digest drift: {url}")
    if cached is not None:
        shutil.copy2(cached, destination)
    elif not destination.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "Auralign-release-builder"})
        with urllib.request.urlopen(request, timeout=120) as response:
            require(response.status == 200, f"download failed: {url}")
            temporary = destination.with_suffix(destination.suffix + ".partial")
            require(not temporary.exists(), f"stale partial download exists: {temporary}")
            with temporary.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            temporary.replace(destination)
    require(sha256(destination) == expected_sha256, f"download digest mismatch: {url}")
    if cache_root is not None and cached is None:
        publish_cache_payload(
            cache_root,
            "downloads",
            key_inputs,
            {"source": destination},
        )
    return destination


def verify_ffmpeg_signature(
    source: Path,
    signature: Path,
    key: Path,
    fingerprint: str,
    evidence: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="auralign-gpg-") as temporary:
        home = Path(temporary)
        home.chmod(0o700)
        run(["gpg", "--homedir", str(home), "--batch", "--import", str(key)])
        observed = output(
            ["gpg", "--homedir", str(home), "--batch", "--with-colons", "--fingerprint"]
        )
        require(
            f"fpr:::::::::{fingerprint}:" in observed,
            "FFmpeg signing-key fingerprint mismatch",
        )
        verification = subprocess.run(
            [
                "gpg",
                "--homedir",
                str(home),
                "--batch",
                "--verify",
                str(signature),
                str(source),
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        evidence.write_text(verification.stderr, encoding="utf-8")
        require(verification.returncode == 0, "FFmpeg release signature verification failed")
        require(
            f"using RSA key {fingerprint}" in verification.stderr,
            "FFmpeg signature used an unexpected key",
        )


def macho_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as handle:
            magic = handle.read(4)
        if magic in MACHO_MAGICS:
            result.append(path)
    return sorted(result)


def thin_runtime_to_arm64(root: Path, evidence: Path) -> dict[str, Any]:
    """Normalize universal wheel objects to the release's arm64-only contract."""
    changes: list[dict[str, str | list[str]]] = []
    natives = macho_files(root)
    for path in natives:
        architectures = quiet_output(["lipo", "-archs", str(path)]).split()
        require(bool(architectures), f"native object has no reported architecture: {path}")
        require(
            "arm64" in architectures,
            f"native object has no arm64 slice: {path}",
        )
        for architecture in architectures:
            verification = subprocess.run(
                ["lipo", str(path), "-verify_arch", architecture],
                check=False,
                text=True,
                capture_output=True,
            )
            require(
                verification.returncode == 0,
                f"native object failed pre-thinning {architecture} verification: {path}",
            )
        if architectures == ["arm64"]:
            continue

        original_digest = sha256(path)
        metadata = path.stat()
        temporary = path.with_name(f".{path.name}.auralign-arm64")
        require(not temporary.exists(), f"stale thinning output exists: {temporary}")
        try:
            run(
                [
                    "lipo",
                    str(path),
                    "-thin",
                    "arm64",
                    "-output",
                    str(temporary),
                ]
            )
            os.chmod(temporary, stat.S_IMODE(metadata.st_mode))
            os.utime(
                temporary,
                ns=(metadata.st_atime_ns, metadata.st_mtime_ns),
            )
            require(
                quiet_output(["lipo", "-archs", str(temporary)]).split() == ["arm64"],
                f"thinned object is not arm64-only: {path}",
            )
            subprocess.run(
                ["lipo", str(temporary), "-verify_arch", "arm64"],
                check=True,
                text=True,
                capture_output=True,
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

        changes.append(
            {
                "path": path.relative_to(root).as_posix(),
                "original_architectures": architectures,
                "original_sha256": original_digest,
                "arm64_sha256": sha256(path),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "auralign-native-thinning.v1",
        "target_architecture": "arm64",
        "objects_scanned": len(natives),
        "objects_thinned": len(changes),
        "changes": changes,
    }
    evidence_path = evidence / "native-thinning.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "target_architecture": "arm64",
        "objects_scanned": len(natives),
        "objects_thinned": len(changes),
        "evidence_sha256": sha256(evidence_path),
    }


def minimum_macos(path: Path) -> tuple[int, int]:
    text = quiet_output(["xcrun", "vtool", "-show-build", str(path)])
    match = re.search(r"\bminos\s+(\d+)\.(\d+)", text)
    require(match is not None, f"Mach-O lacks LC_BUILD_VERSION: {path}")
    assert match is not None
    return int(match.group(1)), int(match.group(2))


def install_names(path: Path) -> list[str]:
    rows = quiet_output(["otool", "-D", str(path)]).splitlines()
    header = re.compile(
        rf"^{re.escape(str(path))}(?: \(architecture [^)]+\))?:$"
    )
    names = [
        row.strip()
        for row in rows
        if row.strip() and header.fullmatch(row.strip()) is None
    ]
    return list(dict.fromkeys(names))


def install_name(path: Path) -> str | None:
    names = install_names(path)
    return names[0] if names else None


def linked_libraries(path: Path) -> list[str]:
    rows = quiet_output(["otool", "-L", str(path)]).splitlines()[1:]
    return [row.strip().split(" (", 1)[0] for row in rows if row.strip()]


def rpaths(path: Path) -> list[str]:
    text = quiet_output(["otool", "-l", str(path)])
    return re.findall(r"\n\s*path\s+(\S+)\s+\(offset", text)


def approved_swift_runtime_path(value: str) -> bool:
    path = PurePosixPath(value)
    root = PurePosixPath("/usr/lib/swift")
    return (
        path.is_absolute()
        and path.as_posix() == value
        and "." not in path.parts
        and ".." not in path.parts
        and (path == root or root in path.parents)
    )


def allowed_runtime_rpath(value: str) -> bool:
    return (
        value in {"@executable_path", "@loader_path"}
        or value.startswith(("@executable_path/", "@loader_path/"))
        or approved_swift_runtime_path(value)
    )


def normalize_native_install_names(root: Path, evidence: Path) -> dict[str, Any]:
    """Remove build-machine and wheel-placeholder IDs from bundled libraries."""
    changes: list[dict[str, Any]] = []
    natives = macho_files(root)
    for path in natives:
        originals = install_names(path)
        if not originals or (
            len(originals) == 1 and originals[0].startswith("@")
        ):
            continue
        normalized = f"@rpath/{path.name}"
        original_digest = sha256(path)
        run(["install_name_tool", "-id", normalized, str(path)])
        require(
            install_names(path) == [normalized],
            f"native install-name rewrite failed: {path}",
        )
        changes.append(
            {
                "path": path.relative_to(root).as_posix(),
                "original_install_name": originals[0],
                "original_install_names": originals,
                "normalized_install_name": normalized,
                "original_sha256": original_digest,
                "normalized_sha256": sha256(path),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "auralign-native-install-names.v1",
        "objects_scanned": len(natives),
        "objects_normalized": len(changes),
        "changes": changes,
    }
    evidence_path = evidence / "native-install-names.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "objects_scanned": len(natives),
        "objects_normalized": len(changes),
        "evidence_sha256": sha256(evidence_path),
    }


def normalize_native_rpaths(root: Path, evidence: Path) -> dict[str, Any]:
    """Remove build-host search paths that cannot be used by the final payload."""
    changes: list[dict[str, Any]] = []
    natives = macho_files(root)
    for path in natives:
        invalid = list(
            dict.fromkeys(
                value
                for value in rpaths(path)
                if not allowed_runtime_rpath(value)
            )
        )
        if not invalid:
            continue

        original_digest = sha256(path)
        for value in invalid:
            run(["install_name_tool", "-delete_rpath", value, str(path)])
        remaining = rpaths(path)
        require(
            all(allowed_runtime_rpath(value) for value in remaining),
            f"native RPATH normalization failed: {path}",
        )
        changes.append(
            {
                "path": path.relative_to(root).as_posix(),
                "removed_rpaths": invalid,
                "remaining_rpaths": remaining,
                "original_sha256": original_digest,
                "normalized_sha256": sha256(path),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "auralign-native-rpaths.v1",
        "objects_scanned": len(natives),
        "objects_normalized": len(changes),
        "changes": changes,
    }
    evidence_path = evidence / "native-rpaths.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "objects_scanned": len(natives),
        "objects_normalized": len(changes),
        "evidence_sha256": sha256(evidence_path),
    }


def prune_unbundled_native_backends(root: Path, evidence: Path) -> dict[str, Any]:
    """Remove pinned-wheel backends whose external native closures are not shipped."""
    site_packages = root / "lib/python3.11/site-packages"
    removals = {
        "torchaudio/lib/_torchaudio_sox.so": "optional libsox backend is not bundled",
        "torchaudio/lib/libtorchaudio_sox.so": "optional libsox backend is not bundled",
        "torchcodec/libtorchcodec_core4.dylib": "release bundles FFmpeg 7 only",
        "torchcodec/libtorchcodec_core5.dylib": "release bundles FFmpeg 7 only",
        "torchcodec/libtorchcodec_core6.dylib": "release bundles FFmpeg 7 only",
        "torchcodec/libtorchcodec_custom_ops4.dylib": "release bundles FFmpeg 7 only",
        "torchcodec/libtorchcodec_custom_ops5.dylib": "release bundles FFmpeg 7 only",
        "torchcodec/libtorchcodec_custom_ops6.dylib": "release bundles FFmpeg 7 only",
        "torchcodec/libtorchcodec_pybind_ops4.so": "release bundles FFmpeg 7 only",
        "torchcodec/libtorchcodec_pybind_ops5.so": "release bundles FFmpeg 7 only",
        "torchcodec/libtorchcodec_pybind_ops6.so": "release bundles FFmpeg 7 only",
        "torio/lib/_torio_ffmpeg4.so": "optional torio backend does not support FFmpeg 7",
        "torio/lib/_torio_ffmpeg5.so": "optional torio backend does not support FFmpeg 7",
        "torio/lib/_torio_ffmpeg6.so": "optional torio backend does not support FFmpeg 7",
        "torio/lib/libtorio_ffmpeg4.so": "optional torio backend does not support FFmpeg 7",
        "torio/lib/libtorio_ffmpeg5.so": "optional torio backend does not support FFmpeg 7",
        "torio/lib/libtorio_ffmpeg6.so": "optional torio backend does not support FFmpeg 7",
    }
    rows: list[dict[str, Any]] = []
    for relative, reason in removals.items():
        path = site_packages / relative
        require(path.is_file(), f"expected pinned-wheel backend is missing: {relative}")
        rows.append(
            {
                "path": f"lib/python3.11/site-packages/{relative}",
                "reason": reason,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
        path.unlink()

    payload: dict[str, Any] = {
        "schema_version": "auralign-native-backend-pruning.v1",
        "bundled_ffmpeg_major": 7,
        "removed": rows,
    }
    evidence_path = evidence / "native-backend-pruning.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "bundled_ffmpeg_major": 7,
        "objects_removed": len(rows),
        "evidence_sha256": sha256(evidence_path),
    }


def prune_unreachable_third_party_test_content(
    root: Path,
    evidence: Path,
) -> dict[str, Any]:
    """Remove an unreachable upstream testing module that embeds a CI token."""
    relative = Path("lib/python3.11/site-packages/transformers/testing_utils.py")
    path = root / relative
    require(path.is_file(), f"expected Transformers testing utility is missing: {relative}")
    original = path.read_bytes()
    matches = list(TOKEN_PATTERNS[1].finditer(original))
    require(
        len(matches) == 1,
        "Transformers test-token contract changed; human review is required",
    )
    token = matches[0].group()
    for source in (ROOT / "src").rglob("*.py"):
        require(
            b"transformers.testing_utils" not in source.read_bytes(),
            f"production source reaches Transformers testing utility: {source}",
        )
    path.unlink()
    payload: dict[str, Any] = {
        "schema_version": "auralign-unreachable-test-content-pruning.v1",
        "path": relative.as_posix(),
        "reason": "not reachable from Auralign production entry points",
        "credential_sha256": hashlib.sha256(token).hexdigest(),
        "original_sha256": hashlib.sha256(original).hexdigest(),
        "original_bytes": len(original),
        "result": "removed from copied runtime stage",
    }
    evidence_path = evidence / "unreachable-test-content-pruning.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "files_removed": 1,
        "evidence_sha256": sha256(evidence_path),
    }


def strip_upstream_native_signatures(root: Path, evidence: Path) -> dict[str, Any]:
    """Remove wheel-vendor signatures before deterministic Mach-O normalization."""
    rows: list[dict[str, str]] = []
    natives = macho_files(root)
    for path in natives:
        inspection = subprocess.run(
            ["codesign", "-d", str(path)],
            check=False,
            text=True,
            capture_output=True,
        )
        if inspection.returncode != 0:
            continue
        original_digest = sha256(path)
        subprocess.run(
            ["codesign", "--remove-signature", str(path)],
            check=True,
            text=True,
            capture_output=True,
        )
        verification = subprocess.run(
            ["codesign", "-d", str(path)],
            check=False,
            text=True,
            capture_output=True,
        )
        require(
            verification.returncode != 0,
            f"upstream native signature was not removed: {path}",
        )
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "original_sha256": original_digest,
                "unsigned_sha256": sha256(path),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "auralign-upstream-signature-stripping.v1",
        "objects_scanned": len(natives),
        "signatures_removed": len(rows),
        "changes": rows,
    }
    evidence_path = evidence / "upstream-signature-stripping.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "objects_scanned": len(natives),
        "signatures_removed": len(rows),
        "evidence_sha256": sha256(evidence_path),
    }


def sign_runtime_ad_hoc_for_verification(
    root: Path,
    evidence: Path,
) -> dict[str, Any]:
    """Sign the normalized diagnostic runtime so Apple Silicon can execute it."""
    rows: list[dict[str, str]] = []
    natives = sorted(
        macho_files(root),
        key=lambda path: (-len(path.parts), path.as_posix()),
    )
    for path in natives:
        subprocess.run(
            [
                "codesign",
                "--force",
                "--sign",
                "-",
                "--timestamp=none",
                str(path),
            ],
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            ["codesign", "--verify", "--strict", str(path)],
            check=True,
            text=True,
            capture_output=True,
        )
        metadata = subprocess.run(
            ["codesign", "-d", "--verbose=4", str(path)],
            check=True,
            text=True,
            capture_output=True,
        ).stderr
        cdhash = re.search(r"^CDHash=(\S+)$", metadata, flags=re.MULTILINE)
        require(cdhash is not None, f"ad-hoc CodeDirectory hash missing: {path}")
        assert cdhash is not None
        rows.append(
            {
                "path": path.relative_to(root).as_posix(),
                "signed_sha256": sha256(path),
                "cdhash": cdhash.group(1),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "auralign-diagnostic-adhoc-signing.v1",
        "identity": "ad-hoc",
        "final_distribution_signature": False,
        "objects_signed": len(rows),
        "objects": rows,
    }
    evidence_path = evidence / "diagnostic-adhoc-signing.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "identity": "ad-hoc",
        "objects_signed": len(rows),
        "evidence_sha256": sha256(evidence_path),
    }


def _native_name_index(root: Path, natives: list[Path]) -> dict[str, set[Path]]:
    native_set = {path.resolve(strict=True) for path in natives}
    result: dict[str, set[Path]] = {}
    for path in root.rglob("*"):
        if not (path.is_file() or path.is_symlink()):
            continue
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            continue
        if resolved in native_set:
            result.setdefault(path.name, set()).add(resolved)
    for path in natives:
        identity = install_name(path)
        if identity is not None and identity.startswith("@rpath/"):
            result.setdefault(identity.removeprefix("@rpath/"), set()).add(
                path.resolve(strict=True)
            )
    return result


def _owner_rpath_candidates(root: Path, owner: Path, suffix: str) -> set[Path]:
    candidates: set[Path] = set()
    for value in rpaths(owner):
        base: Path | None = None
        if value == "@loader_path":
            base = owner.parent
        elif value.startswith("@loader_path/"):
            base = owner.parent / value.removeprefix("@loader_path/")
        if base is None:
            continue
        try:
            candidates.add(
                _resolved_inside(
                    root,
                    base / suffix,
                    label="owner RPATH dependency",
                )
            )
        except (FileNotFoundError, RuntimeError):
            continue
    return candidates


def add_unique_loader_rpaths(root: Path, evidence: Path) -> dict[str, Any]:
    """Make uniquely contained @rpath dependencies independent of preload order."""
    root = root.resolve(strict=True)
    natives = macho_files(root)
    by_name = _native_name_index(root, natives)
    additions: dict[Path, dict[str, set[str]]] = {}
    for owner in natives:
        own_install_name = install_name(owner)
        for dependency in linked_libraries(owner):
            if dependency == own_install_name or not dependency.startswith("@rpath/"):
                continue
            suffix = dependency.removeprefix("@rpath/")
            if len(_owner_rpath_candidates(root, owner, suffix)) == 1:
                continue
            candidates = by_name.get(Path(suffix).name, set())
            if len(candidates) != 1:
                continue
            target = next(iter(candidates))
            relative = os.path.relpath(target.parent, owner.parent)
            value = "@loader_path" if relative == "." else f"@loader_path/{relative}"
            detail = additions.setdefault(owner, {})
            detail.setdefault(value, set()).add(dependency)

    rows: list[dict[str, Any]] = []
    for owner, values in sorted(additions.items()):
        original_digest = sha256(owner)
        for value in sorted(values):
            if value not in rpaths(owner):
                run(["install_name_tool", "-add_rpath", value, str(owner)])
        for _value, dependencies in values.items():
            for dependency in dependencies:
                suffix = dependency.removeprefix("@rpath/")
                require(
                    len(_owner_rpath_candidates(root, owner, suffix)) == 1,
                    f"loader-relative RPATH did not resolve {dependency} in {owner}",
                )
        rows.append(
            {
                "path": owner.relative_to(root).as_posix(),
                "added_rpaths": sorted(values),
                "dependencies": {
                    value: sorted(dependencies)
                    for value, dependencies in sorted(values.items())
                },
                "original_sha256": original_digest,
                "normalized_sha256": sha256(owner),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "auralign-native-loader-rpaths.v1",
        "objects_scanned": len(natives),
        "objects_normalized": len(rows),
        "changes": rows,
    }
    evidence_path = evidence / "native-loader-rpaths.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "objects_scanned": len(natives),
        "objects_normalized": len(rows),
        "evidence_sha256": sha256(evidence_path),
    }


def verify_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target = os.readlink(path)
        require(not os.path.isabs(target), f"absolute runtime symlink: {path}")
        resolved = path.resolve(strict=True)
        require(resolved.is_relative_to(root.resolve()), f"escaping runtime symlink: {path}")


def _resolved_inside(root: Path, candidate: Path, *, label: str) -> Path:
    root_resolved = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    require(resolved.is_relative_to(root_resolved), f"{label} escapes payload: {candidate}")
    require(resolved.is_file(), f"{label} is not a file: {candidate}")
    return resolved


def _token_candidate(
    root: Path,
    owner: Path,
    value: str,
    *,
    executable: Path | None = None,
) -> Path | None:
    if value.startswith("@loader_path/"):
        return _resolved_inside(
            root,
            owner.parent / value.removeprefix("@loader_path/"),
            label="loader-relative path",
        )
    if value.startswith("@executable_path/") and executable is not None:
        return _resolved_inside(
            root,
            executable.parent / value.removeprefix("@executable_path/"),
            label="executable-relative path",
        )
    return None


def _runtime_executables(root: Path, natives: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in natives:
        relative = path.relative_to(root).as_posix()
        if relative.startswith("Contents/MacOS/") or "/bin/" in f"/{relative}":
            result.append(path)
    return result


def _approved_system_rpath_candidate(value: str, suffix: str) -> Path | None:
    if not approved_swift_runtime_path(value):
        return None
    suffix_path = PurePosixPath(suffix)
    require(
        not suffix_path.is_absolute()
        and "." not in suffix_path.parts
        and ".." not in suffix_path.parts,
        f"unsafe @rpath dependency suffix: {suffix}",
    )
    if (
        len(suffix_path.parts) != 1
        or re.fullmatch(r"libswift[A-Za-z0-9_+.-]*\.dylib", suffix_path.name)
        is None
    ):
        return None
    candidate = (PurePosixPath(value) / suffix_path).as_posix()
    require(
        approved_swift_runtime_path(candidate),
        f"Apple Swift @rpath expansion escaped approved root: {candidate}",
    )
    # dyld shared-cache libraries need not appear as ordinary filesystem files.
    return Path(candidate)


def _resolve_dependency(
    root: Path,
    owner: Path,
    dependency: str,
    *,
    executables: list[Path],
    rpaths_by_owner: dict[Path, list[str]],
) -> Path:
    if dependency.startswith("@loader_path/"):
        candidate = _token_candidate(root, owner, dependency)
        assert candidate is not None
        return candidate
    if dependency.startswith("@executable_path/"):
        candidates: set[Path] = set()
        for executable in executables:
            try:
                candidate = _token_candidate(
                    root,
                    owner,
                    dependency,
                    executable=executable,
                )
            except (FileNotFoundError, RuntimeError):
                continue
            if candidate is not None:
                candidates.add(candidate)
        require(
            len(candidates) == 1,
            f"executable-relative dependency is unresolved or ambiguous in {owner}: "
            f"{dependency}",
        )
        return next(iter(candidates))
    if dependency.startswith("@rpath/"):
        suffix = dependency.removeprefix("@rpath/")
        candidates: set[Path] = set()
        owners = [owner, *executables]
        for rpath_owner in owners:
            for value in rpaths_by_owner[rpath_owner]:
                base: Path | None = None
                if value == "@loader_path":
                    base = rpath_owner.parent
                elif value.startswith("@loader_path/"):
                    base = rpath_owner.parent / value.removeprefix("@loader_path/")
                elif value == "@executable_path" and rpath_owner in executables:
                    base = rpath_owner.parent
                elif value.startswith("@executable_path/") and rpath_owner in executables:
                    base = rpath_owner.parent / value.removeprefix("@executable_path/")
                system_candidate = _approved_system_rpath_candidate(value, suffix)
                if system_candidate is not None:
                    candidates.add(system_candidate)
                if base is None:
                    continue
                candidate = base / suffix
                try:
                    candidates.add(
                        _resolved_inside(
                            root,
                            candidate,
                            label="rpath dependency",
                        )
                    )
                except (FileNotFoundError, RuntimeError):
                    continue
        require(
            len(candidates) == 1,
            f"rpath dependency is unresolved or ambiguous in {owner}: {dependency}",
        )
        return next(iter(candidates))
    raise RuntimeError(f"non-relocatable native dependency in {owner}: {dependency}")


def verify_native_closure(root: Path, minimum: tuple[int, int] = (14, 0)) -> None:
    verify_symlinks(root)
    natives = macho_files(root)
    require(bool(natives), "runtime contains no native code")
    executables = _runtime_executables(root, natives)
    require(bool(executables), "runtime contains no executable Mach-O")
    rpaths_by_owner = {path: rpaths(path) for path in natives}
    for path in natives:
        own_install_name = install_name(path)
        if own_install_name is not None:
            require(
                own_install_name.startswith(
                    ("@rpath/", "@loader_path/", "@executable_path/")
                ),
                f"non-relocatable native install name in {path}: {own_install_name}",
            )
        architectures = quiet_output(["lipo", "-archs", str(path)]).split()
        require(architectures == ["arm64"], f"native object is not arm64-only: {path}")
        require(minimum_macos(path) <= minimum, f"deployment target exceeds macOS 14: {path}")
        for dependency in linked_libraries(path):
            if dependency == own_install_name:
                continue
            if dependency.startswith(SYSTEM_LIBRARY_PREFIXES):
                continue
            _resolve_dependency(
                root,
                path,
                dependency,
                executables=executables,
                rpaths_by_owner=rpaths_by_owner,
            )
        for value in rpaths_by_owner[path]:
            require(
                allowed_runtime_rpath(value),
                f"non-relocatable LC_RPATH in {path}: {value}",
            )


def verify_payload_safety(root: Path, markers: Iterable[Path]) -> None:
    encoded = [str(path.resolve()).encode() for path in markers]
    encoded.append(str(Path.home()).encode())
    encoded.append(str(Path(tempfile.gettempdir()).resolve()).encode())
    if sys.platform == "darwin":
        xattr_command = ["/usr/bin/xattr"]
        if root.is_dir():
            xattr_command.append("-r")
        xattr_command.append(str(root))
        inspected_attributes = subprocess.run(
            xattr_command,
            check=False,
            capture_output=True,
            text=True,
        )
        require(
            inspected_attributes.returncode == 0,
            f"cannot inspect extended attributes: {root}",
        )
        require(
            "com.apple.quarantine" not in inspected_attributes.stdout,
            f"quarantine metadata in payload: {root}",
        )
    paths = [root] if root.is_file() else root.rglob("*")
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.name if root.is_file() else path.relative_to(root).as_posix()
        lowered = relative.casefold()
        require(
            not any(
                part in {".git", ".venv", "__pycache__", "auraligntests", "auralignuitests"}
                for part in Path(lowered).parts
            ),
            f"development/private path in payload: {relative}",
        )
        with path.open("rb") as handle:
            previous = b""
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                payload = previous + chunk
                for marker in encoded:
                    require(marker not in payload, f"private build path leaked into {path}")
                require(
                    not any(pattern.search(payload) for pattern in TOKEN_PATTERNS),
                    f"token-shaped secret leaked into {path}",
                )
                require(
                    not any(marker in payload for marker in PRIVATE_KEY_MARKERS),
                    f"private key material leaked into {path}",
                )
                require(
                    not any(marker in payload for marker in DEBUG_ONLY_MARKERS),
                    f"Debug-only marker leaked into {path}",
                )
                previous = payload[-8192:]


def normalize_installable_payload_modes(
    root: Path,
    evidence: Path,
) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            continue
        original = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            required = 0o555
        elif stat.S_ISREG(metadata.st_mode):
            required = 0o555 if original & 0o111 else 0o444
        else:
            continue
        normalized = (original | required) & ~0o022
        if normalized == original:
            continue
        path.chmod(normalized)
        changes.append(
            {
                "path": "." if path == root else path.relative_to(root).as_posix(),
                "original_mode": original,
                "normalized_mode": normalized,
            }
        )
    payload = {
        "schema_version": "auralign-installable-payload-modes.v1",
        "changes": changes,
    }
    (evidence / "installable-payload-modes.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_installable_payload_modes(root)
    return payload


def verify_installable_payload_modes(root: Path) -> None:
    defects: list[str] = []
    for path in [root, *sorted(root.rglob("*"))]:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            continue
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISDIR(metadata.st_mode):
            required = 0o555
        elif stat.S_ISREG(metadata.st_mode):
            required = 0o555 if mode & 0o111 else 0o444
        else:
            continue
        if mode & required != required or mode & 0o022:
            relative = "." if path == root else path.relative_to(root).as_posix()
            defects.append(f"{relative} ({mode:#05o})")
    require(
        not defects,
        "non-installable payload modes: " + ", ".join(defects),
    )


def verify_app_topology(app: Path) -> None:
    require(app.name == "Auralign.app", "unexpected application bundle name")
    contents = app / "Contents"
    require(contents.is_dir(), "application Contents directory missing")
    allowed = {
        "Frameworks",
        "Info.plist",
        "MacOS",
        "PkgInfo",
        "Resources",
        "_CodeSignature",
    }
    observed = {path.name for path in contents.iterdir()}
    require(observed <= allowed, f"unexpected application topology: {sorted(observed - allowed)}")
    require((contents / "MacOS/Auralign").is_file(), "application executable missing")
    runtime_bundle = app_runtime_bundle(app)
    require(runtime_bundle.is_dir(), "embedded engine bundle missing")
    with (runtime_bundle / "Contents/Info.plist").open("rb") as handle:
        runtime_info = plistlib.load(handle)
    require(
        runtime_info.get("CFBundleIdentifier") == RUNTIME_BUNDLE_IDENTIFIER,
        "embedded engine bundle identifier drift",
    )
    require(
        runtime_info.get("CFBundlePackageType") == "BNDL",
        "embedded engine bundle package type drift",
    )
    require(
        (app_runtime_root(app) / "bin/python3").is_file(),
        "embedded Python launcher missing",
    )
    require(not (contents / "PlugIns").exists(), "application plug-ins are prohibited")
    verify_installable_payload_modes(app)


def rewrite_ffmpeg_prefix(prefix: Path) -> None:
    candidates = [
        prefix / "bin/ffmpeg",
        prefix / "bin/ffprobe",
        *prefix.glob("lib/*.dylib"),
    ]
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        for dependency in linked_libraries(path):
            is_staged = dependency.startswith(str(prefix / "lib") + "/")
            is_declared_prefix = dependency.startswith(
                (
                    "/lib/libav",
                    "/lib/libswresample",
                    "/lib/libswscale",
                )
            )
            if is_staged or is_declared_prefix:
                run(
                    [
                        "install_name_tool",
                        "-change",
                        dependency,
                        f"@rpath/{Path(dependency).name}",
                        str(path),
                    ]
                )
        if path.suffix == ".dylib":
            run(["install_name_tool", "-id", f"@rpath/{path.name}", str(path)])
            if "@loader_path" not in rpaths(path):
                run(
                    [
                        "install_name_tool",
                        "-add_rpath",
                        "@loader_path",
                        str(path),
                    ]
                )
    for executable in (prefix / "bin/ffmpeg", prefix / "bin/ffprobe"):
        if "@executable_path/../lib" not in rpaths(executable):
            run(
                [
                    "install_name_tool",
                    "-add_rpath",
                    "@executable_path/../lib",
                    str(executable),
                ]
            )


def copy_ffmpeg_runtime(prefix: Path, runtime: Path) -> None:
    library = runtime / "lib"
    binary = runtime / "bin"
    for path in prefix.glob("lib/*.dylib"):
        destination = library / path.name
        if destination.exists() or destination.is_symlink():
            destination.unlink()
        if path.is_symlink():
            destination.symlink_to(os.readlink(path))
        else:
            shutil.copy2(path, destination)
    for name in ("ffmpeg", "ffprobe"):
        shutil.copy2(prefix / "bin" / name, binary / name)
        (binary / name).chmod(0o755)


def rewrite_pyav_extensions(runtime: Path, prefix: Path) -> None:
    package = runtime / "lib/python3.11/site-packages/av"
    require(package.is_dir(), "source-built PyAV package missing")
    require(not (package / ".dylibs").exists(), "prebuilt PyAV codec closure remains")
    for path in package.rglob("*.so"):
        for dependency in linked_libraries(path):
            is_staged = dependency.startswith(str(prefix / "lib") + "/")
            is_declared_prefix = dependency.startswith(
                (
                    "/lib/libav",
                    "/lib/libswresample",
                    "/lib/libswscale",
                )
            )
            if is_staged or is_declared_prefix:
                run(
                    [
                        "install_name_tool",
                        "-change",
                        dependency,
                        f"@rpath/{Path(dependency).name}",
                        str(path),
                    ]
                )
        relative_library = os.path.relpath(runtime / "lib", path.parent)
        pyav_rpath = f"@loader_path/{relative_library}"
        if pyav_rpath not in rpaths(path):
            run(
                [
                    "install_name_tool",
                    "-add_rpath",
                    pyav_rpath,
                    str(path),
                ]
            )


def prune_runtime(runtime: Path) -> None:
    keep = {"python3", "python3.11", "ffmpeg", "ffprobe"}
    for path in (runtime / "bin").iterdir():
        if path.name not in keep:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    for path in runtime.rglob("__pycache__"):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
    for path in runtime.rglob("*.pyc"):
        path.unlink()
    for path in runtime.rglob("direct_url.json"):
        path.unlink()
    site_packages = runtime / "lib/python3.11/site-packages"
    for pattern in ("pip", "pip-*.dist-info"):
        for path in site_packages.glob(pattern):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    ensurepip = runtime / "lib/python3.11/ensurepip"
    if ensurepip.is_dir():
        shutil.rmtree(ensurepip)
    for relative in (
        "lib/itcl4.3.5",
        "lib/thread3.0.4",
        "lib/tcl9",
        "lib/tcl9.0",
        "lib/tk9.0",
        "lib/libtcl9.0.dylib",
        "lib/libtcl9tk9.0.dylib",
        "lib/python3.11/idlelib",
        "lib/python3.11/tkinter",
        "lib/python3.11/turtledemo",
    ):
        path = runtime / relative
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
    for path in (runtime / "lib/python3.11/lib-dynload").glob("_tkinter*.so"):
        path.unlink()


def inventory_rows(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            rows.append(
                {
                    "path": relative,
                    "type": "symlink",
                    "target": os.readlink(path),
                    "mode": stat.S_IMODE(metadata.st_mode),
                }
            )
        elif stat.S_ISDIR(metadata.st_mode):
            rows.append(
                {
                    "path": relative,
                    "type": "directory",
                    "mode": stat.S_IMODE(metadata.st_mode),
                }
            )
        elif stat.S_ISREG(metadata.st_mode):
            rows.append(
                {
                    "path": relative,
                    "type": "file",
                    "bytes": metadata.st_size,
                    "sha256": sha256(path),
                    "mode": stat.S_IMODE(metadata.st_mode),
                }
            )
    return rows


def write_inventory(root: Path, output_path: Path, *, schema_version: str) -> None:
    rows = inventory_rows(root)
    output_path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "files": rows,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def runtime_inventory(runtime: Path, output_path: Path) -> None:
    write_inventory(
        runtime,
        output_path,
        schema_version="auralign-macos-runtime-manifest.v1",
    )


def runtime_smoke(runtime: Path, *, label: str) -> dict[str, str]:
    python = runtime / "bin/python3"
    ffmpeg = runtime / "bin/ffmpeg"
    ffprobe = runtime / "bin/ffprobe"
    for path in (python, ffmpeg, ffprobe):
        require(path.is_file(), f"{label}: required runtime executable missing: {path.name}")
    with tempfile.TemporaryDirectory(prefix="auralign-runtime-smoke-") as raw:
        private = Path(raw)
        home = private / "home"
        cache = private / "cache"
        models = private / "models"
        temporary = private / "tmp"
        for directory in (home, cache, models, temporary):
            directory.mkdir(mode=0o700)
        environment = {
            "HOME": str(home),
            "PATH": f"{runtime / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "LTM_OFFLINE": "1",
            "LTM_MODEL_CACHE": str(models),
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DO_NOT_TRACK": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "TMPDIR": str(temporary),
            "XDG_CACHE_HOME": str(cache),
            "HF_HOME": str(cache / "HuggingFace"),
        }
        native = run(
            embedded_python_command(
                python,
                "-I",
                "-c",
                (
                    "import av,torch;"
                    "from local_transcript_max.native_runtime import "
                    "probe_torchcodec,reviewed_media_executable;"
                    "print('torch='+torch.__version__);"
                    "print('av='+av.__version__);"
                    "print('torchcodec='+probe_torchcodec());"
                    "print('ffmpeg='+reviewed_media_executable('ffmpeg'))"
                ),
            ),
            cwd=home,
            env=environment,
            capture=True,
        )
        reachability = run(
            embedded_python_command(
                python,
                "-I",
                "-c",
                (
                    "import sys;"
                    "import local_transcript_max.cli;"
                    "import local_transcript_max.gui_bridge;"
                    "import local_transcript_max.pipeline;"
                    "import local_transcript_max.startup;"
                    "print('transformers.testing_utils' in sys.modules)"
                ),
            ),
            cwd=home,
            env=environment,
            capture=True,
        )
        require(
            reachability.stdout.strip() == "False",
            f"{label}: production entry point reached Transformers testing utility",
        )
        ffmpeg_version = run(
            [str(ffmpeg), "-version"],
            cwd=home,
            env=environment,
            capture=True,
        ).stdout.splitlines()[0]
        ffprobe_version = run(
            [str(ffprobe), "-version"],
            cwd=home,
            env=environment,
            capture=True,
        ).stdout.splitlines()[0]
        handshake = run(
            embedded_python_command(
                python,
                "-I",
                "-m",
                "local_transcript_max.gui_bridge",
                "handshake",
            ),
            cwd=home,
            env=environment,
            capture=True,
        )
        rows = [line for line in handshake.stdout.splitlines() if line.strip()]
        require(len(rows) == 1, f"{label}: bridge handshake output was not singular")
        event = json.loads(rows[0])
        require(event.get("event") == "handshake", f"{label}: bridge handshake event drift")
        payload = event.get("payload")
        require(isinstance(payload, dict), f"{label}: bridge handshake payload missing")
        require(
            payload.get("protocol") == "auralign_gui_bridge.v1",
            f"{label}: bridge protocol drift",
        )
        require(
            payload.get("package_version") == "0.2.0",
            f"{label}: bridge package version drift",
        )
        return {
            "label": label,
            "native": native.stdout.strip(),
            "ffmpeg": ffmpeg_version,
            "ffprobe": ffprobe_version,
            "bridge_protocol": str(payload["protocol"]),
            "python": str(payload.get("python", "")),
            "transformers_testing_utils_reachable": reachability.stdout.strip(),
        }


def write_runtime_sbom(runtime: Path, evidence: Path, sources: dict[str, Any]) -> Path:
    destination = evidence / (
        f"Auralign-{sources['product_version']}-{sources['build_number']}"
        "-macos-arm64.cdx.json"
    )
    with tempfile.TemporaryDirectory(prefix="auralign-sbom-python-") as raw:
        launcher = write_embedded_python_launcher(
            runtime / "bin/python3",
            Path(raw) / "python",
        )
        run(
            [
                "uv",
                "run",
                "--frozen",
                "cyclonedx-py",
                "environment",
                str(launcher),
                "--gather-license-texts",
                "--output-reproducible",
                "--short-PURLs",
                "--spec-version",
                "1.6",
                "--output-format",
                "JSON",
                "--output-file",
                str(destination),
                "--validate",
            ],
            env=no_bytecode_environment(),
        )
    payload = json.loads(destination.read_text(encoding="utf-8"))
    components = payload.get("components")
    require(isinstance(components, list), "runtime SBOM has no components")
    license_overrides = {
        "pyannote-audio": "MIT",
        "pyannote-core": "MIT",
        "pyannote-database": "MIT",
        "pyannote-metrics": "MIT",
        "pyannote-pipeline": "MIT",
        "pyannoteai-sdk": "MIT",
        "sentencepiece": "Apache-2.0",
        "torchcodec": "BSD-3-Clause",
    }
    for component in components:
        name = component.get("name")
        if name in license_overrides and not component.get("licenses"):
            component["licenses"] = [
                {
                    "license": {
                        "acknowledgement": "declared",
                        "id": license_overrides[str(name)],
                    }
                }
            ]
        references = component.get("externalReferences")
        if isinstance(references, list):
            component["externalReferences"] = [
                reference
                for reference in references
                if not str(reference.get("url", "")).startswith("file:")
            ]
    missing = sorted(
        str(component.get("name"))
        for component in components
        if not component.get("licenses")
    )
    require(not missing, f"runtime SBOM components lack license evidence: {missing}")
    package_components = list(components)
    installed_rows = json.loads(
        (runtime / "installed-distributions.json").read_text(encoding="utf-8")
    )
    installed_versions = {
        re.sub(r"[-_.]+", "-", str(row["name"]).casefold()): str(row["version"])
        for row in installed_rows
    }
    sbom_versions = {
        re.sub(r"[-_.]+", "-", str(row["name"]).casefold()): str(row["version"])
        for row in package_components
    }
    require(
        sbom_versions == installed_versions,
        "runtime SBOM does not exactly match installed Python distributions",
    )
    components.extend(
        [
            {
                "bom-ref": f"cpython=={sources['python']['version']}",
                "type": "framework",
                "name": "CPython",
                "version": sources["python"]["version"],
                "hashes": [
                    {
                        "alg": "SHA-256",
                        "content": sources["python"]["sha256"],
                    }
                ],
                "licenses": [
                    {"license": {"id": expression}}
                    for expression in sources["python"]["runtime_license_expressions"]
                ],
                "purl": f"pkg:generic/cpython@{sources['python']['version']}",
            },
            {
                "bom-ref": (
                    "python-build-standalone=="
                    f"{sources['python']['release']}+{sources['python']['source_revision']}"
                ),
                "type": "application",
                "name": "python-build-standalone",
                "version": sources["python"]["release"],
                "hashes": [
                    {
                        "alg": "SHA-256",
                        "content": sources["python"]["metadata_sha256"],
                    }
                ],
                "licenses": [
                    {
                        "license": {
                            "id": sources["python"]["project_license_expression"]
                        }
                    }
                ],
            },
            {
                "bom-ref": f"ffmpeg=={sources['ffmpeg']['version']}",
                "type": "application",
                "name": "FFmpeg",
                "version": sources["ffmpeg"]["version"],
                "hashes": [
                    {
                        "alg": "SHA-256",
                        "content": sources["ffmpeg"]["sha256"],
                    }
                ],
                "licenses": [
                    {
                        "license": {
                            "id": sources["ffmpeg"]["license_expression"]
                        }
                    }
                ],
                "purl": f"pkg:generic/ffmpeg@{sources['ffmpeg']['version']}",
            },
        ]
    )
    metadata = payload.setdefault("metadata", {})
    require(isinstance(metadata, dict), "runtime SBOM metadata is invalid")
    properties = metadata.setdefault("properties", [])
    require(isinstance(properties, list), "runtime SBOM properties are invalid")
    build_info = json.loads(
        (runtime / "release-build.json").read_text(encoding="utf-8")
    )
    properties.extend(
        {"name": name, "value": value}
        for name, value in (
            ("auralign:commit", str(build_info["source_revision"])),
            ("auralign:platform", "darwin-arm64-cpu-app"),
            ("auralign:profile", "cpu"),
            ("auralign:python-version", str(sources["python"]["version"])),
            ("auralign:sbom-kind", "standalone-app-runtime"),
        )
    )
    properties.sort(
        key=lambda item: (str(item.get("name", "")), str(item.get("value", "")))
    )
    dependencies = payload.setdefault("dependencies", [])
    require(isinstance(dependencies, list), "runtime SBOM dependency graph is invalid")
    graph_refs = {
        str(item.get("ref"))
        for item in dependencies
        if isinstance(item, dict) and item.get("ref")
    }
    for component in components:
        reference = component.get("bom-ref")
        require(isinstance(reference, str) and reference, "runtime SBOM reference missing")
        if reference not in graph_refs:
            dependencies.append({"ref": reference, "dependsOn": []})
            graph_refs.add(reference)
    destination.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    from validate_dependency_sbom import validate_payload

    validate_payload(
        payload,
        "cpu",
        label=str(destination),
        platform_name="darwin-arm64-cpu-app",
        commit=str(build_info["source_revision"]),
    )
    verify_payload_safety(destination, (ROOT, runtime))
    return destination


def install_hash_locked_pure_python_sdists(
    work: Path,
    runtime: Path,
    downloads: Path,
    wheelhouse: Path,
    evidence: Path,
    sources: dict[str, Any],
) -> list[dict[str, str]]:
    contracts = sources.get("pure_python_sdist_builds", [])
    require(isinstance(contracts, list), "pure-Python sdist build contract is invalid")
    if not contracts:
        return []

    python_environment = no_bytecode_environment()
    build_root = work / "pure-python-build-environment"
    run(
        embedded_python_command(
            runtime / "bin/python3",
            "-m",
            "venv",
            str(build_root),
        ),
        env=python_environment,
    )
    build_python = build_root / "bin/python"
    run(
        embedded_python_command(
            build_python,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(downloads),
            "setuptools==83.0.0",
            "wheel==0.46.3",
            "packaging==26.2",
        ),
        env=python_environment,
    )
    build_environment = no_bytecode_environment()
    build_environment["PYTHONHASHSEED"] = "0"
    build_environment["SOURCE_DATE_EPOCH"] = "0"
    records: list[dict[str, str]] = []
    for contract in contracts:
        require(isinstance(contract, dict), "pure-Python sdist entry is invalid")
        name = str(contract.get("name", ""))
        version = str(contract.get("version", ""))
        asset = str(contract.get("asset", ""))
        expected_sha256 = str(contract.get("sha256", ""))
        require(name and version and asset, "pure-Python sdist identity is incomplete")
        source_archive = wheelhouse / asset
        require(source_archive.is_file(), f"pure-Python sdist missing: {asset}")
        require(
            sha256(source_archive) == expected_sha256,
            f"pure-Python sdist hash drift: {asset}",
        )

        first = fresh_directory(work / f"{name}-wheel-first")
        second = fresh_directory(work / f"{name}-wheel-second")
        for destination in (first, second):
            run(
                embedded_python_command(
                    build_python,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(destination),
                    str(source_archive),
                ),
                env=build_environment,
            )
        first_wheels = list(first.glob("*.whl"))
        second_wheels = list(second.glob("*.whl"))
        require(
            len(first_wheels) == 1 and len(second_wheels) == 1,
            f"pure-Python wheel output is ambiguous: {name}",
        )
        built_wheel = first_wheels[0]
        second_wheel = second_wheels[0]
        require(
            built_wheel.name == second_wheel.name
            and sha256(built_wheel) == sha256(second_wheel),
            f"pure-Python wheel build is not deterministic: {name}",
        )
        run(
            embedded_python_command(
                runtime / "bin/python3",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--force-reinstall",
                str(built_wheel),
            ),
            env=python_environment,
        )
        records.append(
            {
                "name": name,
                "version": version,
                "source_asset": asset,
                "source_sha256": expected_sha256,
                "built_wheel": built_wheel.name,
                "built_wheel_sha256": sha256(built_wheel),
                "build_setuptools": "83.0.0",
                "build_wheel": "0.46.3",
                "build_packaging": "26.2",
            }
        )
    (evidence / "pure-python-sdist-builds.json").write_text(
        json.dumps(records, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


def build_runtime(
    work: Path,
    sources: dict[str, Any],
    *,
    allow_dirty: bool,
    cache_root: Path | None,
) -> Path:
    revision, tree_digest = source_revision(allow_dirty)
    toolchain = toolchain_fingerprint()
    native_cache_inputs = cache_key_inputs(
        stage="native",
        sources=sources,
        toolchain=toolchain,
    )
    runtime_cache_inputs = cache_key_inputs(
        stage="runtime",
        sources=sources,
        toolchain=toolchain,
        revision=revision,
        tree_digest=tree_digest,
    )
    downloads = fresh_directory(work / "downloads")
    evidence = fresh_directory(work / "evidence")

    python = sources["python"]
    ffmpeg = sources["ffmpeg"]
    pyav = sources["pyav"]
    python_archive = download(
        python["url"],
        python["sha256"],
        downloads / python["asset"],
        cache_root=cache_root,
    )
    python_metadata_archive = download(
        python["metadata_url"],
        python["metadata_sha256"],
        downloads / python["metadata_asset"],
        cache_root=cache_root,
    )
    ffmpeg_archive = download(
        ffmpeg["url"],
        ffmpeg["sha256"],
        downloads / ffmpeg["asset"],
        cache_root=cache_root,
    )
    ffmpeg_signature = download(
        ffmpeg["signature_url"],
        ffmpeg["signature_sha256"],
        downloads / f"{ffmpeg['asset']}.asc",
        cache_root=cache_root,
    )
    ffmpeg_key = download(
        ffmpeg["signing_key_url"],
        ffmpeg["signing_key_sha256"],
        downloads / "ffmpeg-release-signing-key.asc",
        cache_root=cache_root,
    )
    pyav_archive = download(
        pyav["url"],
        pyav["sha256"],
        downloads / pyav["asset"],
        cache_root=cache_root,
    )
    verify_ffmpeg_signature(
        ffmpeg_archive,
        ffmpeg_signature,
        ffmpeg_key,
        ffmpeg["signing_key_fingerprint"],
        evidence / "ffmpeg-signature.txt",
    )
    for requirement in sources["build_requirements"]:
        download(
            requirement["url"],
            requirement["sha256"],
            downloads / Path(requirement["url"]).name,
            cache_root=cache_root,
        )

    if cache_root is not None:
        cached_runtime = verified_cache_payload(
            cache_root,
            "runtime",
            runtime_cache_inputs,
        )
        if cached_runtime is not None:
            extraction = fresh_directory(work / "python-extraction")
            runtime = extraction / "python"
            restore_cache_item(cached_runtime / "runtime", runtime)
            merge_cached_evidence(cached_runtime / "evidence", evidence)
            build_info = json.loads(
                (runtime / "release-build.json").read_text(encoding="utf-8")
            )
            require(
                build_info.get("source_revision") == revision
                and build_info.get("source_tree_sha256") == tree_digest,
                "cached runtime source provenance drift",
            )
            prune_runtime(runtime)
            verify_native_closure(runtime)
            verify_payload_safety(runtime, (ROOT, work))
            smoke = runtime_smoke(runtime, label="restored-diagnostic-cache-runtime")
            cache_use = {
                "schema_version": "auralign-diagnostic-cache-use.v1",
                "stage": "runtime",
                "key": canonical_json_sha256(runtime_cache_inputs),
                "authoritative_release_evidence": False,
                "smoke": smoke,
            }
            (evidence / "diagnostic-cache-use.json").write_text(
                json.dumps(cache_use, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            return runtime

    source_root = fresh_directory(work / "sources")
    wheelhouse = fresh_directory(work / "wheelhouse")
    extraction = fresh_directory(work / "python-extraction")
    run(["tar", "-xzf", str(python_archive), "-C", str(extraction)])
    runtime = extraction / "python"
    require((runtime / "bin/python3").is_file(), "Python runtime extraction failed")
    python_environment = no_bytecode_environment()
    require(
        run(
            embedded_python_command(
                runtime / "bin/python3",
                "-c",
                "import platform; print(platform.machine())",
            ),
            env=python_environment,
            capture=True,
        ).stdout.strip()
        == "arm64",
        "embedded Python is not arm64",
    )
    require(minimum_macos(runtime / "bin/python3") <= (14, 0), "Python target too new")

    metadata_root = fresh_directory(work / "python-metadata")
    run(
        [
            "tar",
            "--zstd",
            "-xf",
            str(python_metadata_archive),
            "-C",
            str(metadata_root),
            "python/PYTHON.json",
            "python/licenses",
        ]
    )
    python_metadata = json.loads(
        (metadata_root / "python/PYTHON.json").read_text(encoding="utf-8")
    )
    require(
        python_metadata.get("python_version") == python["version"],
        "python-build-standalone metadata version drift",
    )
    require(
        python_metadata.get("target_triple") == "aarch64-apple-darwin",
        "python-build-standalone target drift",
    )
    require(
        python_metadata.get("build_options") == python["build_options"],
        "python-build-standalone build-options drift",
    )
    require(
        python_metadata.get("apple_sdk_deployment_target")
        == python["apple_sdk_deployment_target"],
        "python-build-standalone deployment-target drift",
    )
    require(
        python_metadata.get("licenses") == python["runtime_license_expressions"],
        "python-build-standalone runtime-license drift",
    )

    download_requirements = embedded_python_command(
        runtime / "bin/python3",
        "-m",
        "pip",
        "download",
        "--require-hashes",
        "--only-binary=:all:",
    )
    for contract in sources.get("pure_python_sdist_builds", []):
        download_requirements.extend(["--no-binary", str(contract["name"])])
    download_requirements.extend(
        ["--dest", str(wheelhouse), "-r", str(REQUIREMENTS_PATH)]
    )
    run(download_requirements, env=python_environment)
    pure_python_sdist_builds = install_hash_locked_pure_python_sdists(
        work,
        runtime,
        downloads,
        wheelhouse,
        evidence,
        sources,
    )
    run(
        embedded_python_command(
            runtime / "bin/python3",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "-r",
            str(REQUIREMENTS_PATH),
        ),
        env=python_environment,
    )

    project_wheels = fresh_directory(work / "project-wheel")
    run(["uv", "build", "--wheel", "--out-dir", str(project_wheels)])
    run(
        embedded_python_command(
            runtime / "bin/python3",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(project_wheels),
            "local-transcript-max==0.2.0",
        ),
        env=python_environment,
    )

    ffmpeg_source = fresh_directory(source_root / f"ffmpeg-{ffmpeg['version']}")
    run(
        [
            "tar",
            "-xJf",
            str(ffmpeg_archive),
            "--strip-components=1",
            "-C",
            str(ffmpeg_source),
        ]
    )
    configure = [
        "./configure",
        "--prefix=/",
        "--cc=clang",
        "--arch=arm64",
        "--target-os=darwin",
        "--enable-cross-compile",
        *ffmpeg["configure"],
        "--extra-cflags=-mmacosx-version-min=14.0",
        (
            "--extra-ldflags=-mmacosx-version-min=14.0 "
            "-Wl,-headerpad_max_install_names"
        ),
    ]
    build_environment = no_bytecode_environment()
    build_environment["MACOSX_DEPLOYMENT_TARGET"] = "14.0"
    cached_native = (
        verified_cache_payload(cache_root, "native", native_cache_inputs)
        if cache_root is not None
        else None
    )
    ffmpeg_prefix = work / "ffmpeg-prefix"
    pyav_wheels = work / "pyav-wheel"
    if cached_native is not None:
        restore_cache_item(cached_native / "ffmpeg-prefix", ffmpeg_prefix)
        restore_cache_item(cached_native / "pyav-wheel", pyav_wheels)
        merge_cached_evidence(cached_native / "evidence", evidence)
    else:
        fresh_directory(ffmpeg_prefix)
        configured = run(
            configure,
            cwd=ffmpeg_source,
            env=build_environment,
            capture=True,
        )
        (evidence / "ffmpeg-configure.txt").write_text(
            configured.stdout + configured.stderr,
            encoding="utf-8",
        )
        run(
            ["make", f"-j{os.cpu_count() or 1}"],
            cwd=ffmpeg_source,
            env=build_environment,
        )
        install_environment = build_environment.copy()
        install_environment["DESTDIR"] = str(ffmpeg_prefix)
        run(["make", "install"], cwd=ffmpeg_source, env=install_environment)
        rewrite_ffmpeg_prefix(ffmpeg_prefix)

    version_text = output([str(ffmpeg_prefix / "bin/ffmpeg"), "-version"])
    require("--disable-gpl" in version_text, "FFmpeg GPL disable flag missing")
    require("--disable-nonfree" in version_text, "FFmpeg nonfree disable flag missing")
    require("--disable-version3" in version_text, "FFmpeg version3 disable flag missing")
    require("--disable-network" in version_text, "FFmpeg network disable flag missing")
    require("--enable-gpl" not in version_text, "FFmpeg GPL code enabled")
    require("--enable-nonfree" not in version_text, "FFmpeg nonfree code enabled")
    if cached_native is None:
        (evidence / "ffmpeg-version.txt").write_text(
            version_text + "\n",
            encoding="utf-8",
        )
        build_environment_root = work / "pyav-build-environment"
        run(
            embedded_python_command(
                runtime / "bin/python3",
                "-m",
                "venv",
                str(build_environment_root),
            ),
            env=python_environment,
        )
        build_python = build_environment_root / "bin/python"
        run(
            embedded_python_command(
                build_python,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                str(downloads),
                "Cython==3.1.3",
                "setuptools==83.0.0",
                "wheel==0.46.3",
                "packaging==26.2",
            ),
            env=python_environment,
        )
        fresh_directory(pyav_wheels)
        pyav_environment = build_environment.copy()
        pyav_environment["PKG_CONFIG_PATH"] = str(ffmpeg_prefix / "lib/pkgconfig")
        pyav_environment["PKG_CONFIG_SYSROOT_DIR"] = str(ffmpeg_prefix)
        pyav_environment["CFLAGS"] = "-mmacosx-version-min=14.0"
        pyav_environment["LDFLAGS"] = "-mmacosx-version-min=14.0"
        run(
            embedded_python_command(
                build_python,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--wheel-dir",
                str(pyav_wheels),
                str(pyav_archive),
            ),
            env=pyav_environment,
        )
        if cache_root is not None:
            publish_cache_payload(
                cache_root,
                "native",
                native_cache_inputs,
                {
                    "ffmpeg-prefix": ffmpeg_prefix,
                    "pyav-wheel": pyav_wheels,
                    "evidence": evidence,
                },
            )
    else:
        cache_use = {
            "schema_version": "auralign-diagnostic-cache-use.v1",
            "stage": "native",
            "key": canonical_json_sha256(native_cache_inputs),
            "authoritative_release_evidence": False,
        }
        (evidence / "diagnostic-native-cache-use.json").write_text(
            json.dumps(cache_use, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    built_pyav_candidates = list(pyav_wheels.glob("av-15.1.0-*.whl"))
    require(
        len(built_pyav_candidates) == 1,
        "PyAV native stage output is missing or ambiguous",
    )
    built_pyav = built_pyav_candidates[0]
    shutil.copy2(built_pyav, wheelhouse / built_pyav.name)
    run(
        embedded_python_command(
            runtime / "bin/python3",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(pyav_wheels),
            "--force-reinstall",
            "--no-deps",
            "av==15.1.0",
        ),
        env=python_environment,
    )
    rewrite_pyav_extensions(runtime, ffmpeg_prefix)
    copy_ffmpeg_runtime(ffmpeg_prefix, runtime)
    prune_runtime(runtime)
    unreachable_test_content_pruning = prune_unreachable_third_party_test_content(
        runtime,
        evidence,
    )
    native_backend_pruning = prune_unbundled_native_backends(runtime, evidence)
    upstream_signature_stripping = strip_upstream_native_signatures(runtime, evidence)
    native_install_names = normalize_native_install_names(runtime, evidence)
    native_rpaths = normalize_native_rpaths(runtime, evidence)
    native_loader_rpaths = add_unique_loader_rpaths(runtime, evidence)
    native_thinning = thin_runtime_to_arm64(runtime, evidence)
    diagnostic_ad_hoc_signing = sign_runtime_ad_hoc_for_verification(
        runtime,
        evidence,
    )

    legal = runtime / "Legal"
    legal.mkdir()
    shutil.copy2(ffmpeg_source / "COPYING.LGPLv2.1", legal / "FFmpeg-LGPL-2.1.txt")
    shutil.copy2(ffmpeg_source / "LICENSE.md", legal / "FFmpeg-LICENSE.md")
    with tarfile.open(pyav_archive, "r:gz") as archive:
        member = archive.getmember("av-15.1.0/LICENSE.txt")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise RuntimeError("PyAV license missing")
        (legal / "PyAV-BSD-3-Clause.txt").write_bytes(extracted.read())
    shutil.copy2(runtime / "lib/python3.11/LICENSE.txt", legal / "CPython-PSF.txt")
    shutil.copy2(
        metadata_root / "python/PYTHON.json",
        legal / "python-build-standalone-PYTHON.json",
    )
    shutil.copytree(
        metadata_root / "python/licenses",
        legal / "python-build-standalone-licenses",
    )
    shutil.copy2(ffmpeg_archive, legal / ffmpeg_archive.name)
    shutil.copy2(evidence / "ffmpeg-configure.txt", legal / "FFmpeg-configure.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", legal / "Auralign-notices.md")
    shutil.copy2(
        ROOT / "compliance/THIRD_PARTY_COMPONENTS.generated.md",
        legal / "Auralign-components.md",
    )
    installable_payload_modes = normalize_installable_payload_modes(runtime, evidence)

    build_info = {
        "schema_version": "auralign-embedded-build.v1",
        "product_version": sources["product_version"],
        "build_number": sources["build_number"],
        "source_revision": revision,
        "source_tree_sha256": tree_digest,
        "architecture": sources["architecture"],
        "minimum_macos": sources["minimum_macos"],
        "python": python,
        "ffmpeg": ffmpeg,
        "pyav": {
            **pyav,
            "built_wheel_sha256": sha256(built_pyav),
        },
        "pure_python_sdist_builds": pure_python_sdist_builds,
        "unreachable_test_content_pruning": unreachable_test_content_pruning,
        "native_backend_pruning": native_backend_pruning,
        "upstream_signature_stripping": upstream_signature_stripping,
        "native_install_names": native_install_names,
        "native_rpaths": native_rpaths,
        "native_loader_rpaths": native_loader_rpaths,
        "native_thinning": native_thinning,
        "diagnostic_ad_hoc_signing": diagnostic_ad_hoc_signing,
        "installable_payload_modes": installable_payload_modes,
        "requirements_sha256": sha256(REQUIREMENTS_PATH),
        "diagnostic_cache": {
            "enabled": cache_root is not None,
            "native_cache_hit": cached_native is not None,
            "runtime_cache_hit": False,
            "authoritative_release_evidence": False,
        },
        "models_included": False,
    }
    (runtime / "release-build.json").write_text(
        json.dumps(build_info, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    installed = run(
        embedded_python_command(
            runtime / "bin/python3",
            "-I",
            "-c",
            (
                "import importlib.metadata as m,json;"
                "print(json.dumps(sorted("
                "({'name':d.metadata['Name'],'version':d.version} for d in m.distributions()),"
                "key=lambda x:(x['name'].lower(),x['version']))))"
            ),
        ),
        env=python_environment,
        capture=True,
    ).stdout.strip()
    (runtime / "installed-distributions.json").write_text(
        json.dumps(json.loads(installed), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    write_runtime_sbom(runtime, evidence, sources)
    prune_runtime(runtime)
    verify_installable_payload_modes(runtime)
    smoke = runtime_smoke(runtime, label="constructed-runtime")
    (evidence / "runtime-smoke.json").write_text(
        json.dumps(smoke, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    prune_runtime(runtime)
    verify_native_closure(runtime)
    verify_payload_safety(runtime, (ROOT, work))
    runtime_inventory(runtime, evidence / "runtime-manifest.json")
    if cache_root is not None:
        publish_cache_payload(
            cache_root,
            "runtime",
            runtime_cache_inputs,
            {
                "runtime": runtime,
                "evidence": evidence,
            },
        )
    return runtime


def copy_app_with_runtime(work: Path, runtime: Path, sources: dict[str, Any]) -> Path:
    derived = fresh_directory(work / "DerivedData")
    run(
        [
            "xcodebuild",
            "-project",
            str(PROJECT_PATH),
            "-scheme",
            SCHEME,
            "-configuration",
            "Release",
            "-derivedDataPath",
            str(derived),
            "-destination",
            "platform=macOS,arch=arm64",
            "CODE_SIGNING_ALLOWED=NO",
            "build",
        ]
    )
    built = derived / "Build/Products/Release/Auralign.app"
    require(built.is_dir(), "Xcode Release app missing")
    app = work / "Auralign.app"
    shutil.copytree(built, app, symlinks=True)
    runtime_bundle = app_runtime_bundle(app)
    runtime_resources = runtime_bundle / "Contents/Resources"
    runtime_resources.mkdir(parents=True)
    destination = app_runtime_root(app)
    shutil.copytree(runtime, destination, symlinks=True)
    runtime_info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleIdentifier": RUNTIME_BUNDLE_IDENTIFIER,
        "CFBundleName": RUNTIME_DIRECTORY_NAME,
        "CFBundlePackageType": "BNDL",
        "CFBundleShortVersionString": sources["product_version"],
        "CFBundleSupportedPlatforms": ["MacOSX"],
        "CFBundleVersion": sources["build_number"],
        "LSMinimumSystemVersion": "14.0",
    }
    with (runtime_bundle / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump(runtime_info, handle, sort_keys=True)
    info_path = app / "Contents/Info.plist"
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    require(
        info.get("CFBundleShortVersionString") == sources["product_version"],
        "app version drift",
    )
    require(info.get("CFBundleVersion") == sources["build_number"], "app build drift")
    require(info.get("LSMinimumSystemVersion") == "14.0", "app minimum macOS drift")
    info["AuralignRuntimeLayout"] = "signed-engine-resource-bundle-v1"
    with info_path.open("wb") as handle:
        plistlib.dump(info, handle, sort_keys=True)
    verify_app_topology(app)
    verify_native_closure(app)
    verify_payload_safety(app, (ROOT, work))
    return app


def identity_team_id(identity: str, prefix: str) -> str:
    require(identity.startswith(prefix), f"identity must begin with {prefix}")
    match = re.search(r"\(([A-Z0-9]{10})\)\s*$", identity)
    require(match is not None, f"identity has no valid Team ID: {identity}")
    assert match is not None
    return match.group(1)


def codesign_metadata(path: Path) -> dict[str, str]:
    result = subprocess.run(
        ["codesign", "-d", "--verbose=4", str(path)],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    require(result.returncode == 0, f"codesign metadata unavailable: {path}")
    values: dict[str, str] = {}
    authorities: list[str] = []
    for line in result.stderr.splitlines():
        if line.startswith("Authority="):
            authorities.append(line.removeprefix("Authority="))
        elif "=" in line:
            key, value = line.split("=", 1)
            if key in {"Identifier", "TeamIdentifier", "CDHash"}:
                values[key] = value
    values["Authorities"] = " | ".join(authorities)
    return values


def verify_app_signatures(
    app: Path,
    *,
    expected_team: str | None,
    structure_only: bool,
) -> dict[str, str]:
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    runtime_bundle = app_runtime_bundle(app)
    for path in [*macho_files(app), runtime_bundle, app]:
        verification = subprocess.run(
            ["codesign", "--verify", "--strict", "--verbose=2", str(path)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        require(
            verification.returncode == 0,
            f"nested signature verification failed: {path}\n{verification.stderr}",
        )
        metadata = codesign_metadata(path)
        if not structure_only:
            require(
                metadata.get("TeamIdentifier") == expected_team,
                f"signed Team ID drift: {path}",
            )
            require(
                "Developer ID Application:" in metadata.get("Authorities", ""),
                f"non-Developer-ID nested signature: {path}",
            )
    outer = codesign_metadata(app)
    require(outer.get("Identifier") == APP_IDENTIFIER, "signed app identifier drift")
    require(
        codesign_metadata(runtime_bundle).get("Identifier")
        == RUNTIME_BUNDLE_IDENTIFIER,
        "signed engine bundle identifier drift",
    )
    return outer


def sign_app(
    app: Path,
    identity: str,
    *,
    structure_only: bool,
    expected_team: str | None,
) -> dict[str, str]:
    timestamp = [] if structure_only else ["--timestamp"]
    hardened_runtime = [] if structure_only else ["--options", "runtime"]
    main_executable = app / "Contents/MacOS/Auralign"
    for path in sorted(
        macho_files(app),
        key=lambda candidate: len(candidate.relative_to(app).parts),
        reverse=True,
    ):
        if path == main_executable:
            continue
        command = [
            "codesign",
            "--force",
            *hardened_runtime,
            *timestamp,
            "--sign",
            identity,
            str(path),
        ]
        signature = subprocess.run(
            command,
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        require(
            signature.returncode == 0,
            f"nested code signing failed: {path}\n{signature.stderr}",
        )
    runtime_bundle = app_runtime_bundle(app)
    run(
        [
            "codesign",
            "--force",
            *hardened_runtime,
            *timestamp,
            "--sign",
            identity,
            str(runtime_bundle),
        ]
    )
    run(
        [
            "codesign",
            "--force",
            *hardened_runtime,
            *timestamp,
            "--entitlements",
            str(ENTITLEMENTS_PATH),
            "--sign",
            identity,
            str(app),
        ]
    )
    return verify_app_signatures(
        app,
        expected_team=expected_team,
        structure_only=structure_only,
    )


def verify_expanded_package(
    package: Path,
    destination: Path,
    *,
    signed_app: Path,
    sources: dict[str, Any],
) -> Path:
    require(not destination.exists(), f"refusing to reuse expansion destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    expanded = destination
    run(["pkgutil", "--expand-full", str(package), str(expanded)])
    installer_script_directories = [
        candidate
        for candidate in (
            expanded / "Scripts",
            *(component / "Scripts" for component in sorted(expanded.glob("*.pkg"))),
        )
        if candidate.exists()
    ]
    require(
        not installer_script_directories,
        "installer scripts are prohibited: "
        + ", ".join(str(path) for path in installer_script_directories),
    )
    component = expanded / "Auralign-component.pkg"
    payload_app = component / "Payload/Applications/Auralign.app"
    require(payload_app.is_dir(), "expanded package payload is missing Auralign.app")
    package_info = component / "PackageInfo"
    require(package_info.is_file(), "expanded component PackageInfo is missing")
    package_root = ET.parse(package_info).getroot()
    expected_version = f"{sources['product_version']}.{sources['build_number']}"
    require(
        package_root.attrib.get("identifier") == RECEIPT_IDENTIFIER,
        "component receipt identifier drift",
    )
    require(
        package_root.attrib.get("version") == expected_version,
        "component receipt version drift",
    )
    require(
        inventory_rows(payload_app) == inventory_rows(signed_app),
        "expanded package application bytes or modes differ from signed app",
    )
    expanded_resources = expanded / "Resources"
    for relative in (
        Path("background.png"),
        Path("background-darkAqua.png"),
        Path("en.lproj/welcome.html"),
        Path("en.lproj/conclusion.html"),
    ):
        expected = INSTALLER_RESOURCES_PATH / relative
        observed = expanded_resources / relative
        require(observed.is_file(), f"installer presentation resource missing: {relative}")
        require(
            sha256(observed) == sha256(expected),
            f"installer presentation resource drift: {relative}",
        )
    verify_app_topology(payload_app)
    verify_native_closure(payload_app)
    verify_payload_safety(payload_app, (ROOT, package.parent))
    return payload_app


def build_package(
    work: Path,
    app: Path,
    sources: dict[str, Any],
    installer_identity: str | None,
    *,
    structure_only: bool,
) -> Path:
    root = fresh_directory(work / "pkgroot")
    applications = root / "Applications"
    applications.mkdir()
    shutil.copytree(app, applications / "Auralign.app", symlinks=True)
    component = work / "Auralign-component.pkg"
    version = f"{sources['product_version']}.{sources['build_number']}"
    run(
        [
            "pkgbuild",
            "--root",
            str(root),
            "--component-plist",
            str(COMPONENT_PLIST_PATH),
            "--identifier",
            RECEIPT_IDENTIFIER,
            "--version",
            version,
            "--install-location",
            "/",
            "--ownership",
            "recommended",
            str(component),
        ]
    )
    name = (
        f"Auralign-{sources['product_version']}-{sources['build_number']}"
        "-macos-arm64"
        + (".UNSIGNED-TEST.pkg" if structure_only else ".pkg")
    )
    package = work / name
    command = [
        "productbuild",
        "--distribution",
        str(DISTRIBUTION_PATH),
        "--package-path",
        str(work),
        "--resources",
        str(INSTALLER_RESOURCES_PATH),
    ]
    if not structure_only:
        require(bool(installer_identity), "Developer ID Installer identity is required")
        command.extend(["--sign", str(installer_identity), "--timestamp"])
    command.append(str(package))
    run(command)
    expanded_app = verify_expanded_package(
        package,
        work / "expanded-pkg",
        signed_app=app,
        sources=sources,
    )
    smoke = runtime_smoke(
        app_runtime_root(expanded_app),
        label="expanded-package-runtime",
    )
    (work / "evidence/package-runtime-smoke.json").write_text(
        json.dumps(smoke, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return package


def notarize(package: Path, profile: str, evidence: Path) -> None:
    submission = run(
        [
            "xcrun",
            "notarytool",
            "submit",
            str(package),
            "--keychain-profile",
            profile,
            "--wait",
            "--timeout",
            "1h",
            "--output-format",
            "json",
        ],
        capture=True,
    )
    result = json.loads(submission.stdout)
    (evidence / "notarization-submit.json").write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    require(result.get("status") == "Accepted", "Apple notarization was not accepted")
    identifier = result.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise RuntimeError("notarization ID missing")
    log = run(
        [
            "xcrun",
            "notarytool",
            "log",
            identifier,
            "--keychain-profile",
            profile,
        ],
        capture=True,
    )
    (evidence / "notarization-log.json").write_text(log.stdout, encoding="utf-8")
    run(["xcrun", "stapler", "staple", "-v", str(package)])
    run(["xcrun", "stapler", "validate", "-v", str(package)])


def verify_final_package(
    package: Path,
    *,
    app: Path,
    sources: dict[str, Any],
    expected_team: str | None,
    structure_only: bool,
) -> None:
    if structure_only:
        print("structure_only=true final_release=false")
        return
    signature = run(["pkgutil", "--check-signature", str(package)], capture=True)
    signature_text = signature.stdout + signature.stderr
    require(
        "Developer ID Installer" in signature_text,
        "installer signature is not Developer ID",
    )
    require(
        expected_team is not None and expected_team in signature_text,
        "installer signature Team ID drift",
    )
    run(["spctl", "--assess", "--type", "install", "--verbose=4", str(package)])
    run(["xcrun", "stapler", "validate", "-v", str(package)])
    payload_files = run(
        ["pkgutil", "--payload-files", str(package)],
        capture=True,
    ).stdout.splitlines()
    require(
        any(
            path.removeprefix("./").rstrip("/") == "Applications/Auralign.app"
            for path in payload_files
        ),
        "final package payload listing omits Auralign.app",
    )
    payload_app = verify_expanded_package(
        package,
        package.parent / "expanded-final-pkg",
        signed_app=app,
        sources=sources,
    )
    verify_app_signatures(
        payload_app,
        expected_team=expected_team,
        structure_only=False,
    )
    smoke = runtime_smoke(
        app_runtime_root(payload_app),
        label="final-stapled-package-runtime",
    )
    (package.parent / "evidence/final-package-runtime-smoke.json").write_text(
        json.dumps(smoke, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def write_release_manifest(
    work: Path,
    runtime: Path,
    app: Path,
    package: Path,
    sources: dict[str, Any],
    *,
    structure_only: bool,
    app_signature: dict[str, str],
    expected_team: str | None,
) -> None:
    runtime_manifest = work / "runtime-manifest.json"
    write_inventory(
        app_runtime_root(app),
        runtime_manifest,
        schema_version="auralign-macos-signed-runtime-manifest.v1",
    )
    signed_app_manifest = work / "signed-app-manifest.json"
    shutil.copy2(
        work / "evidence/signed-app-manifest.json",
        signed_app_manifest,
    )
    runtime_sbom = work / (
        f"Auralign-{sources['product_version']}-{sources['build_number']}"
        "-macos-arm64.cdx.json"
    )
    shutil.copy2(
        work / "evidence" / runtime_sbom.name,
        runtime_sbom,
    )
    ffmpeg_source = work / sources["ffmpeg"]["asset"]
    shutil.copy2(work / "downloads" / sources["ffmpeg"]["asset"], ffmpeg_source)
    build_info = json.loads((runtime / "release-build.json").read_text(encoding="utf-8"))
    payload = {
        "schema_version": "auralign-macos-pkg-release.v1",
        "release_ready": not structure_only,
        "product_version": sources["product_version"],
        "build_number": sources["build_number"],
        "source_revision": build_info["source_revision"],
        "source_tree_sha256": build_info["source_tree_sha256"],
        "package": {
            "name": package.name,
            "bytes": package.stat().st_size,
            "sha256": sha256(package),
        },
        "app": {
            "identifier": APP_IDENTIFIER,
            "runtime_bundle": RUNTIME_BUNDLE_NAME,
            "runtime_bundle_identifier": RUNTIME_BUNDLE_IDENTIFIER,
            "runtime_relative_path": (
                f"Contents/Frameworks/{RUNTIME_BUNDLE_NAME}/"
                "Contents/Resources/Runtime"
            ),
            "main_executable_sha256": sha256(app / "Contents/MacOS/Auralign"),
            "code_directory_hash": app_signature.get("CDHash"),
            "team_identifier": app_signature.get("TeamIdentifier"),
        },
        "runtime_build_info_sha256": sha256(runtime / "release-build.json"),
        "runtime_manifest": {
            "name": runtime_manifest.name,
            "bytes": runtime_manifest.stat().st_size,
            "sha256": sha256(runtime_manifest),
        },
        "signed_app_manifest": {
            "name": signed_app_manifest.name,
            "bytes": signed_app_manifest.stat().st_size,
            "sha256": sha256(signed_app_manifest),
        },
        "runtime_sbom": {
            "name": runtime_sbom.name,
            "bytes": runtime_sbom.stat().st_size,
            "sha256": sha256(runtime_sbom),
        },
        "ffmpeg_corresponding_source": {
            "name": ffmpeg_source.name,
            "bytes": ffmpeg_source.stat().st_size,
            "sha256": sha256(ffmpeg_source),
        },
        "models_included": False,
        "expected_team_identifier": expected_team,
        "installer_design": {
            "source_commit": "bd94520ecf106b7502ad04e19e4997dbb74d05dd",
            "background_sha256": sha256(
                INSTALLER_RESOURCES_PATH / "background.png"
            ),
            "dark_aqua_background_sha256": sha256(
                INSTALLER_RESOURCES_PATH / "background-darkAqua.png"
            ),
        },
    }
    release_manifest = work / "macos-pkg-release.json"
    release_manifest.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    release_files = [
        package,
        ffmpeg_source,
        runtime_sbom,
        runtime_manifest,
        signed_app_manifest,
        release_manifest,
    ]
    (work / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in release_files),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=ROOT / "build/macos-standalone",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home()
        / "Library/Caches/com.auralign.studio/release-builder-diagnostics",
        help=(
            "Hash-verified diagnostic download/native/runtime cache. "
            "Never used by --no-cache release builds."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable every diagnostic cache for an authoritative clean build.",
    )
    parser.add_argument("--developer-application-identity")
    parser.add_argument("--developer-installer-identity")
    parser.add_argument("--notary-profile")
    parser.add_argument(
        "--structure-only",
        action="store_true",
        help="Build an ad-hoc signed, unmistakably non-release test package.",
    )
    args = parser.parse_args()
    require_host()
    sources = load_sources()
    verify_packaging_contract(sources)
    work = fresh_directory(args.work_dir.expanduser().resolve())
    if not args.structure_only:
        require(args.no_cache, "Developer ID release builds require --no-cache")
    cache_root = None if args.no_cache else args.cache_dir.expanduser().resolve()
    runtime = build_runtime(
        work,
        sources,
        allow_dirty=args.structure_only,
        cache_root=cache_root,
    )
    app = copy_app_with_runtime(work, runtime, sources)
    expected_team: str | None = None
    if args.structure_only:
        app_signature = sign_app(
            app,
            "-",
            structure_only=True,
            expected_team=None,
        )
    else:
        require(
            bool(args.developer_application_identity),
            "Developer ID Application identity is required",
        )
        require(
            bool(args.developer_installer_identity),
            "Developer ID Installer identity is required",
        )
        require(bool(args.notary_profile), "notary Keychain profile is required")
        application_team = identity_team_id(
            str(args.developer_application_identity),
            "Developer ID Application:",
        )
        installer_team = identity_team_id(
            str(args.developer_installer_identity),
            "Developer ID Installer:",
        )
        require(application_team == installer_team, "Developer ID Team IDs do not match")
        expected_team = application_team
        app_signature = sign_app(
            app,
            str(args.developer_application_identity),
            structure_only=False,
            expected_team=expected_team,
        )
    verify_app_topology(app)
    verify_native_closure(app)
    verify_payload_safety(app, (ROOT, work))
    write_inventory(
        app,
        work / "evidence/signed-app-manifest.json",
        schema_version="auralign-macos-signed-app-manifest.v1",
    )
    signed_smoke = runtime_smoke(
        app_runtime_root(app),
        label="signed-app-runtime",
    )
    (work / "evidence/signed-app-runtime-smoke.json").write_text(
        json.dumps(signed_smoke, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_app_signatures(
        app,
        expected_team=expected_team,
        structure_only=args.structure_only,
    )
    package = build_package(
        work,
        app,
        sources,
        args.developer_installer_identity,
        structure_only=args.structure_only,
    )
    evidence = work / "evidence"
    if not args.structure_only:
        notarize(package, args.notary_profile, evidence)
    verify_final_package(
        package,
        app=app,
        sources=sources,
        expected_team=expected_team,
        structure_only=args.structure_only,
    )
    write_release_manifest(
        work,
        runtime,
        app,
        package,
        sources,
        structure_only=args.structure_only,
        app_signature=app_signature,
        expected_team=expected_team,
    )
    print(f"package={package}")
    print(f"sha256={sha256(package)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
