#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Build, verify, sign, package, and notarize the standalone macOS product.

The shipped product is one scriptless PKG containing an independently versioned
self-contained application component and exact verified bootstrap-model
component. Cleared ASR models that exceed release-host asset limits are
delivered later by the signed native app through exact verified per-user packs.
Release mode fails closed unless the source tree is clean, both Developer ID
identities are supplied, notarization is accepted, and the exact final package
is stapled and reverified.
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
from collections import Counter
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any, cast

from local_transcript_max.config import MODEL_MANIFEST
from local_transcript_max.model_bundle import (
    BOOTSTRAP_MODEL_KEYS,
    MODEL_BUNDLE_COMPLETION_FILENAME,
    MODEL_BUNDLE_IDENTIFIER,
    MODEL_BUNDLE_LICENSE_FILENAMES,
    MODEL_BUNDLE_MANIFEST_FILENAME,
    MODEL_BUNDLE_PROVENANCE_FILENAME,
    MODEL_BUNDLE_VERIFICATION_FILENAME,
    MODEL_BUNDLE_VERSION,
    assemble_model_bundle,
    model_bundle_notices_bytes,
    verify_model_bundle,
)
from local_transcript_max.model_delivery import (
    MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
    MODEL_DELIVERY_CATALOG_VERSION,
    NORMAL_PRESET_MODELS,
    dump_model_delivery_catalog,
    model_delivery_catalog_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "packaging/macos/runtime-sources.toml"
REQUIREMENTS_PATH = ROOT / "requirements/macos-app-arm64-cpu-py311.txt"
ENTITLEMENTS_PATH = ROOT / "packaging/macos/Auralign.entitlements"
RUNTIME_PYTHON_ENTITLEMENTS_PATH = ROOT / "packaging/macos/AuralignEnginePython.entitlements"
RUNTIME_PYTHON_ENTITLEMENTS = {
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
}
COMPONENT_PLIST_PATH = ROOT / "packaging/macos/component.plist"
DISTRIBUTION_PATH = ROOT / "packaging/macos/Distribution.xml"
INSTALLER_RESOURCES_PATH = ROOT / "packaging/macos/installer-design/Resources"
PROJECT_PATH = ROOT / "macos/Auralign.xcodeproj"
MODEL_DELIVERY_CATALOG_PATH = ROOT / "macos/Auralign/Resources/model_delivery_catalog.json"
SCHEME = "Auralign"
RUNTIME_DIRECTORY_NAME = "AuralignEngineRuntime"
RUNTIME_BUNDLE_NAME = f"{RUNTIME_DIRECTORY_NAME}.bundle"
RUNTIME_BUNDLE_IDENTIFIER = "com.auralign.studio.engine-runtime"
APP_IDENTIFIER = "com.auralign.studio"
APP_RECEIPT_IDENTIFIER = "com.auralign.studio.pkg.app"
MODEL_RECEIPT_IDENTIFIER = "com.auralign.studio.pkg.models.bootstrap-v2"
APP_COMPONENT_FILENAME = "Auralign-application-component.pkg"
MODEL_COMPONENT_FILENAME = "Auralign-offline-models-component.pkg"
GITHUB_RELEASE_ASSET_MAX_BYTES = 2 * 1024**3
MODEL_INSTALL_PARENT = Path("Library/Application Support/com.auralign.studio/Models")
INSTALLER_RESOURCE_RELATIVE_PATHS = (
    Path("background.png"),
    Path("background-darkAqua.png"),
    Path("en.lproj/welcome.html"),
    Path("en.lproj/readme.html"),
    Path("en.lproj/license.html"),
    Path("en.lproj/conclusion.html"),
)
DIAGNOSTIC_CACHE_SCHEMA_VERSION = "auralign-macos-diagnostic-cache.v1"
DIAGNOSTIC_CACHE_DIRECTORY_MODE = 0o555
DIAGNOSTIC_CACHE_FILE_MODE = 0o444
DIAGNOSTIC_CACHE_EXECUTABLE_MODE = 0o555
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

PURE_PYTHON_SDIST_DETERMINISTIC_PATCHES: dict[str, dict[str, str]] = {
    "lightning-aggregate-manifest-v1": {
        "target": "setup.py",
        "original_sha256": ("c6b0de9403b41b0b6cccc7e42c393157a4dd9d97564cb68c80a9ee64cd37dc1c"),
        "patched_sha256": ("a68478757d3fd1bd96ff88a6a32c3c42c86efe7ca7587746454516660b7d9976"),
        "old": "return os.path.join(directory, os.urandom(24).hex())",
        "new": ('return os.path.join(_PATH_ROOT, "AURALIGN_AGGREGATE_MANIFEST.in")'),
    },
    "lightning-redundant-dependency-v1": {
        "target": "src/lightning/__setup__.py",
        "original_sha256": ("a5a8f70f220173a68daced5022063fcef09ca6fbd8d595597ac13e62cc2f5e3d"),
        "patched_sha256": ("6ef3990bbbb7a14f7606f3475af740048cdf1f8f0aebacd67877d5b3b7d18b78"),
        "old": 'install_requires += ["pytorch-lightning"]',
        "new": (
            'install_requires = [req for req in install_requires if req != "pytorch-lightning"]'
        ),
    },
}


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    capture: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=capture,
        timeout=timeout,
    )


def output(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
) -> str:
    return run(command, cwd=cwd, env=env, capture=True).stdout.strip()


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
            f'exec {shlex.quote(str(executable.absolute()))} -B -I "$@"\n'
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
    verify_distribution_contract(
        DISTRIBUTION_PATH,
        sources=sources,
        productbuild_normalized=False,
    )
    run(
        [
            sys.executable,
            "-B",
            str(ROOT / "packaging/macos/installer-design/build.py"),
            "--check-only",
        ],
        env=no_bytecode_environment(),
    )
    verify_installer_resources(INSTALLER_RESOURCES_PATH)


def verify_distribution_contract(
    path: Path,
    *,
    sources: dict[str, Any],
    productbuild_normalized: bool,
) -> None:
    """Verify the exact reviewed Installer semantics.

    ``productbuild`` rewrites insignificant XML formatting, prefixes embedded
    component references with ``#``, adds numeric size attributes, and appends
    one empty ``bundle-version`` record per component. Those four documented
    transformations are accepted for an expanded product archive; every
    security-, presentation-, choice-, receipt-, and version-bearing field
    remains exact and unexpected nodes or attributes fail closed.
    """

    root = ET.parse(path).getroot()
    require(
        root.tag == "installer-gui-script" and root.attrib == {"minSpecVersion": "2"},
        "Distribution root contract drift",
    )
    expected_counts = Counter(
        {
            "title": 1,
            "organization": 1,
            "domains": 1,
            "options": 1,
            "allowed-os-versions": 1,
            "background": 1,
            "background-darkAqua": 1,
            "welcome": 1,
            "readme": 1,
            "license": 1,
            "conclusion": 1,
            "choices-outline": 1,
            "choice": 3,
            "pkg-ref": 4 if productbuild_normalized else 2,
        }
    )
    require(
        Counter(child.tag for child in root) == expected_counts,
        "Distribution top-level element set drift",
    )

    def one(tag: str) -> ET.Element:
        matches = root.findall(tag)
        require(len(matches) == 1, f"Distribution {tag} multiplicity drift")
        return matches[0]

    title = one("title")
    organization = one("organization")
    require(
        title.attrib == {} and (title.text or "").strip() == "Auralign",
        "Distribution title drift",
    )
    require(
        organization.attrib == {} and (organization.text or "").strip() == APP_IDENTIFIER,
        "Distribution organization drift",
    )
    require(
        one("domains").attrib
        == {
            "enable_localSystem": "true",
            "enable_currentUserHome": "false",
            "enable_anywhere": "false",
        },
        "Distribution install-domain contract drift",
    )
    require(
        one("options").attrib
        == {
            "customize": "never",
            "require-scripts": "false",
            "rootVolumeOnly": "true",
            "hostArchitectures": "arm64",
        },
        "Distribution options contract drift",
    )
    allowed = one("allowed-os-versions")
    require(allowed.attrib == {}, "Distribution allowed-OS attributes drift")
    os_versions = list(allowed)
    require(
        len(os_versions) == 1
        and os_versions[0].tag == "os-version"
        and os_versions[0].attrib == {"min": "14.0"}
        and len(os_versions[0]) == 0,
        "Distribution minimum macOS contract drift",
    )

    presentation = {
        "background": {
            "file": "background.png",
            "alignment": "bottomleft",
            "scaling": "tofit",
        },
        "background-darkAqua": {
            "file": "background-darkAqua.png",
            "alignment": "bottomleft",
            "scaling": "tofit",
        },
        "welcome": {"file": "welcome.html", "mime-type": "text/html"},
        "readme": {"file": "readme.html", "mime-type": "text/html"},
        "license": {"file": "license.html", "mime-type": "text/html"},
        "conclusion": {"file": "conclusion.html", "mime-type": "text/html"},
    }
    for tag, attributes in presentation.items():
        element = one(tag)
        require(
            element.attrib == attributes and len(element) == 0,
            f"Distribution {tag} presentation contract drift",
        )

    outline = one("choices-outline")
    outline_lines = list(outline)
    require(
        outline.attrib == {}
        and len(outline_lines) == 1
        and outline_lines[0].tag == "line"
        and outline_lines[0].attrib == {"choice": "default"},
        "Distribution choices outline drift",
    )
    component_lines = list(outline_lines[0])
    require(
        [line.tag for line in component_lines] == ["line", "line"]
        and [line.attrib for line in component_lines]
        == [
            {"choice": APP_RECEIPT_IDENTIFIER},
            {"choice": MODEL_RECEIPT_IDENTIFIER},
        ]
        and all(len(line) == 0 for line in component_lines),
        "Distribution component choice order drift",
    )

    choices = {choice.attrib.get("id"): choice for choice in root.findall("choice")}
    require(
        set(choices)
        == {
            "default",
            APP_RECEIPT_IDENTIFIER,
            MODEL_RECEIPT_IDENTIFIER,
        },
        "Distribution choice set drift",
    )
    require(
        choices["default"].attrib == {"id": "default"} and len(choices["default"]) == 0,
        "Distribution default choice drift",
    )
    for identifier in (APP_RECEIPT_IDENTIFIER, MODEL_RECEIPT_IDENTIFIER):
        choice = choices[identifier]
        references = list(choice)
        require(
            choice.attrib == {"id": identifier, "visible": "false"}
            and len(references) == 1
            and references[0].tag == "pkg-ref"
            and references[0].attrib == {"id": identifier}
            and len(references[0]) == 0,
            f"Distribution choice contract drift: {identifier}",
        )

    package_contracts = {
        APP_RECEIPT_IDENTIFIER: (
            f"{sources['product_version']}.{sources['build_number']}",
            APP_COMPONENT_FILENAME,
        ),
        MODEL_RECEIPT_IDENTIFIER: (
            MODEL_BUNDLE_VERSION,
            MODEL_COMPONENT_FILENAME,
        ),
    }
    primary_packages: dict[str, ET.Element] = {}
    generated_packages: dict[str, ET.Element] = {}
    for package in root.findall("pkg-ref"):
        identifier = package.attrib.get("id")
        require(
            identifier in package_contracts,
            "Distribution contains an unexpected component reference",
        )
        if "version" in package.attrib:
            require(identifier not in primary_packages, "duplicate Distribution component")
            primary_packages[identifier] = package
        else:
            require(identifier not in generated_packages, "duplicate generated component record")
            generated_packages[identifier] = package
    require(
        set(primary_packages) == set(package_contracts),
        "Distribution primary component receipt set drift",
    )
    require(
        set(generated_packages) == (set(package_contracts) if productbuild_normalized else set()),
        "Distribution productbuild component metadata set drift",
    )
    for identifier, (version, filename) in package_contracts.items():
        package = primary_packages[identifier]
        expected_attributes = {
            "id": identifier,
            "version": version,
            "onConclusion": "none",
        }
        expected_text = filename
        if productbuild_normalized:
            expected_attributes |= {
                "installKBytes": package.attrib.get("installKBytes", ""),
                "updateKBytes": package.attrib.get("updateKBytes", ""),
            }
            require(
                expected_attributes["installKBytes"].isdigit()
                and expected_attributes["updateKBytes"].isdigit(),
                f"Distribution component size metadata is invalid: {identifier}",
            )
            expected_text = f"#{filename}"
        require(
            package.attrib == expected_attributes
            and (package.text or "").strip() == expected_text
            and len(package) == 0,
            f"Distribution primary component contract drift: {identifier}",
        )
        if productbuild_normalized:
            generated = generated_packages[identifier]
            generated_children = list(generated)
            bundle_version = generated_children[0] if len(generated_children) == 1 else None
            require(
                generated.attrib == {"id": identifier}
                and not (generated.text or "").strip()
                and len(generated_children) == 1
                and bundle_version is not None
                and bundle_version.tag == "bundle-version"
                and bundle_version.attrib == {}
                and not (bundle_version.text or "").strip(),
                f"Distribution generated component metadata drift: {identifier}",
            )
            bundle_records = list(bundle_version)
            expected_bundle_records = (
                [
                    {
                        "CFBundleShortVersionString": sources["product_version"],
                        "CFBundleVersion": sources["build_number"],
                        "id": APP_IDENTIFIER,
                        "path": "Applications/Auralign.app",
                    }
                ]
                if identifier == APP_RECEIPT_IDENTIFIER
                else []
            )
            require(
                [record.tag for record in bundle_records]
                == ["bundle"] * len(expected_bundle_records)
                and [record.attrib for record in bundle_records] == expected_bundle_records
                and all(
                    not (record.text or "").strip() and len(record) == 0
                    for record in bundle_records
                ),
                f"Distribution generated component metadata drift: {identifier}",
            )


def verify_installer_resources(resources: Path) -> None:
    expected_files = {path.as_posix() for path in INSTALLER_RESOURCE_RELATIVE_PATHS}
    expected_directories = {"en.lproj"}
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for candidate in resources.rglob("*"):
        relative = candidate.relative_to(resources).as_posix()
        metadata = candidate.lstat()
        require(
            not stat.S_ISLNK(metadata.st_mode),
            f"installer presentation resource must not be a symbolic link: {relative}",
        )
        if stat.S_ISDIR(metadata.st_mode):
            actual_directories.add(relative)
        elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
            actual_files.add(relative)
        else:
            raise RuntimeError(f"installer presentation resource is irregular: {relative}")
    require(
        actual_directories == expected_directories and actual_files == expected_files,
        "installer presentation resource allowlist drift",
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
    remote_revision = output(["git", "rev-parse", "--verify", "refs/remotes/origin/main^{commit}"])
    require(
        re.fullmatch(r"[0-9a-f]{40}", remote_revision) is not None,
        "invalid origin/main Git revision",
    )
    require(
        revision == remote_revision,
        "release requires local main and fetched origin/main to match exactly",
    )
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
            "-B",
            "-c",
            (
                "from local_transcript_max.code_provenance import "
                "_source_tree_sha256; from pathlib import Path; "
                "print(_source_tree_sha256(Path.cwd()))"
            ),
        ],
        env=no_bytecode_environment(),
    )
    require(re.fullmatch(r"[0-9a-f]{64}", tree) is not None, "invalid source-tree digest")
    return revision, tree


def toolchain_fingerprint() -> dict[str, str]:
    return {
        "host_macos": platform.mac_ver()[0],
        "host_architecture": platform.machine(),
        "clang": output(["clang", "--version"]),
        "xcode": output(["xcodebuild", "-version"]),
        "macos_sdk_version": output(["xcrun", "--sdk", "macosx", "--show-sdk-version"]),
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
        "normalization_implementation_version": (NORMALIZATION_IMPLEMENTATION_VERSION),
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
    verify_diagnostic_cache_seal(entry, payload, manifest_path)
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


def seal_diagnostic_cache_payload(payload: Path) -> None:
    """Seal one private cache payload while preserving executable file bits."""

    root_metadata = payload.lstat()
    require(
        stat.S_ISDIR(root_metadata.st_mode) and not stat.S_ISLNK(root_metadata.st_mode),
        f"diagnostic cache payload root is unsafe: {payload}",
    )
    owner = (root_metadata.st_uid, root_metadata.st_gid)
    device = root_metadata.st_dev
    directories = [payload]
    for path in sorted(payload.rglob("*")):
        metadata = path.lstat()
        require(
            metadata.st_dev == device and (metadata.st_uid, metadata.st_gid) == owner,
            f"diagnostic cache payload ownership or filesystem drift: {path}",
        )
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            directories.append(path)
            continue
        require(
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_nlink == 1,
            f"diagnostic cache payload contains a linked or irregular entry: {path}",
        )
        path.chmod(
            DIAGNOSTIC_CACHE_EXECUTABLE_MODE
            if stat.S_IMODE(metadata.st_mode) & 0o111
            else DIAGNOSTIC_CACHE_FILE_MODE
        )
    for directory in sorted(
        directories,
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        directory.chmod(DIAGNOSTIC_CACHE_DIRECTORY_MODE)


def verify_diagnostic_cache_seal(
    entry: Path,
    payload: Path,
    manifest_path: Path,
) -> None:
    """Verify one published cache entry is exact, read-only, and self-contained."""

    require(
        {child.name for child in entry.iterdir()} == {"manifest.json", "payload"},
        f"diagnostic cache entry contains unexpected paths: {entry}",
    )
    entry_metadata = entry.lstat()
    require(
        stat.S_ISDIR(entry_metadata.st_mode)
        and not stat.S_ISLNK(entry_metadata.st_mode)
        and stat.S_IMODE(entry_metadata.st_mode) == DIAGNOSTIC_CACHE_DIRECTORY_MODE,
        f"diagnostic cache entry is not sealed: {entry}",
    )
    owner = (entry_metadata.st_uid, entry_metadata.st_gid)
    device = entry_metadata.st_dev
    for path in [payload, manifest_path, *sorted(payload.rglob("*"))]:
        metadata = path.lstat()
        require(
            metadata.st_dev == device and (metadata.st_uid, metadata.st_gid) == owner,
            f"diagnostic cache entry ownership or filesystem drift: {path}",
        )
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            require(
                stat.S_IMODE(metadata.st_mode) == DIAGNOSTIC_CACHE_DIRECTORY_MODE,
                f"diagnostic cache directory is mutable: {path}",
            )
            continue
        require(
            stat.S_ISREG(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_nlink == 1,
            f"diagnostic cache entry contains a linked or irregular file: {path}",
        )
        expected_modes = (
            {DIAGNOSTIC_CACHE_FILE_MODE}
            if path == manifest_path
            else {
                DIAGNOSTIC_CACHE_FILE_MODE,
                DIAGNOSTIC_CACHE_EXECUTABLE_MODE,
            }
        )
        require(
            stat.S_IMODE(metadata.st_mode) in expected_modes,
            f"diagnostic cache file is mutable or has an invalid mode: {path}",
        )


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
        seal_diagnostic_cache_payload(payload)
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
        (temporary / "manifest.json").chmod(DIAGNOSTIC_CACHE_FILE_MODE)
        temporary.chmod(DIAGNOSTIC_CACHE_DIRECTORY_MODE)
        entry = stage_root / key
        require(not entry.exists(), f"ambiguous concurrent cache entry: {entry}")
        os.replace(temporary, entry)
        verified = verified_cache_payload(cache_root, stage, key_inputs)
        require(verified is not None, f"new cache entry did not verify: {entry}")
        assert verified is not None
        return verified
    finally:
        if temporary.exists():
            for path in sorted(
                temporary.rglob("*"),
                key=lambda candidate: len(candidate.parts),
                reverse=True,
            ):
                try:
                    path.chmod(
                        0o700 if path.is_dir() and not path.is_symlink() else 0o600,
                        follow_symlinks=False,
                    )
                except OSError:
                    pass
            temporary.chmod(0o700)
            shutil.rmtree(temporary)


def restore_cache_item(source: Path, destination: Path) -> None:
    require(
        not destination.exists() and not destination.is_symlink(),
        f"refusing to overwrite cache restore destination: {destination}",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_inventory_before = inventory_rows(source) if source.is_dir() else None
    source_mode_before = stat.S_IMODE(source.lstat().st_mode)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)
    require(
        source_mode_before == stat.S_IMODE(source.lstat().st_mode),
        f"cache source mode changed during restore: {source}",
    )
    if source_inventory_before is not None:
        require(
            source_inventory_before == inventory_rows(source)
            and source_inventory_before == inventory_rows(destination),
            f"cache source changed during restore: {source}",
        )
    else:
        require(
            sha256(source) == sha256(destination),
            f"cache source changed during restore: {source}",
        )


def merge_cached_evidence(source: Path, destination: Path) -> None:
    require(source.is_dir(), f"cached evidence directory missing: {source}")
    source_inventory = inventory_rows(source)
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
                stat.S_IFMT(source_metadata.st_mode) == stat.S_IFMT(target_metadata.st_mode),
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
    require(
        source_inventory == inventory_rows(source),
        f"cached evidence changed during restore: {source}",
    )


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
    header = re.compile(rf"^{re.escape(str(path))}(?: \(architecture [^)]+\))?:$")
    names = [row.strip() for row in rows if row.strip() and header.fullmatch(row.strip()) is None]
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
        if not originals or (len(originals) == 1 and originals[0].startswith("@")):
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
            dict.fromkeys(value for value in rpaths(path) if not allowed_runtime_rpath(value))
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


# Auralign 0.3.x includes the pinned MLX dependency closure. The remediated
# runtime also upgrades NLTK to 3.10.3 and builds Lightning without its
# redundant ``pytorch-lightning`` metadata dependency. Compared with the
# original 0.3.x inventory, that removes the ``lightning_fabric`` and
# ``pytorch_lightning`` compatibility-test roots while adding the reviewed
# NLTK 3.10.3 security-test files. These exact roots and bytes remain
# unreachable from Auralign production entry points and are pruned below.
REVIEWED_TEST_PRUNE_ROOT_COUNT = 211
REVIEWED_TEST_PRUNE_ROOTS_SHA256 = (
    "8d05d81fcfaa963d5ea7e88668888f4ac0255e4af15392d7259ca608637eb6b3"
)
REVIEWED_TEST_PRUNE_FILE_COUNT = 4837
REVIEWED_TEST_PRUNE_BYTES = 67_291_439
REVIEWED_TEST_PRUNE_TREE_SHA256 = "55bb8e8ddf6ef06e8d17d9992f29e2f83e3e41d3c64c36e6ed6d9cd004d7c551"
REVIEWED_SAMPLE_DATA_FILES = {
    "lib/python3.11/site-packages/networkx/generators/atlas.dat.gz": {
        "bytes": 8887,
        "sha256": ("73fc416df0164923607751cb759f4ae81deb5f6550bf25be59c86de3b747e41d"),
    },
}
REVIEWED_SAMPLE_DATA_DIRECTORIES = {
    "lib/python3.11/site-packages/pyannote/audio/sample": {
        "files": 4,
        "bytes": 964_146,
        "tree_sha256": ("0fdef21086d733f355b888e1a2fecafa146f0f99d1547afab833a7d1acffc6ea"),
    },
}
REVIEWED_DEVELOPMENT_CONTENT_DIRECTORIES = {
    "lib/python3.11/site-packages/docs": {
        "files": 1,
        "bytes": 5_813,
        "tree_sha256": ("a805e8aea0d6bc27a95494b3931f4b41f5accb358570828a4a5613a554af2460"),
    },
    "lib/python3.11/site-packages/lightning/pytorch/demos": {
        "files": 5,
        "bytes": 31_143,
        "tree_sha256": ("19be71252d240636adfcbe2cbf986ba75a77951e6fb9f98dae8062fc9f7163dd"),
    },
    "lib/python3.11/site-packages/numpy/random/_examples": {
        "files": 7,
        "bytes": 14_434,
        "tree_sha256": ("465a05403b9264579a764a487e2ab35a78d8771ccc61d5f58c134a1d8abc0086"),
    },
    "lib/python3.11/site-packages/recipes": {
        "files": 352,
        "bytes": 4_532_024,
        "tree_sha256": ("47826d9e6f73b64969324679436a45e2e57f733b4bb1ae0aef66c08c3faf0a38"),
    },
    "lib/python3.11/site-packages/sympy/benchmarks": {
        "files": 4,
        "bytes": 17_080,
        "tree_sha256": ("ff2b32088c104959ed3773f2f15f18e5364cb9ed68f1dd206004931964e3a89c"),
    },
    "lib/python3.11/site-packages/sympy/core/benchmarks": {
        "files": 7,
        "bytes": 2_499,
        "tree_sha256": ("c2403cb54d1535b070ff0497c4093416dcf14a381ee41e62adb4e2a3d89057f9"),
    },
    "lib/python3.11/site-packages/sympy/functions/elementary/benchmarks": {
        "files": 2,
        "bytes": 185,
        "tree_sha256": ("e2e1fead6b76aedf606354e32e6389d1630990a594821bd7451d2375146535df"),
    },
    "lib/python3.11/site-packages/sympy/functions/special/benchmarks": {
        "files": 2,
        "bytes": 164,
        "tree_sha256": ("87bd6ce80c266454e6ddf5d51a2578741e9f38267eaca0b9d5741cc7d05b75d5"),
    },
    "lib/python3.11/site-packages/sympy/integrals/benchmarks": {
        "files": 3,
        "bytes": 701,
        "tree_sha256": ("3339026111448b63fb84f71d5134cdf9f34ba97f60535364bb6da10d2ab3aa90"),
    },
    "lib/python3.11/site-packages/sympy/matrices/benchmarks": {
        "files": 2,
        "bytes": 306,
        "tree_sha256": ("c758c949709d86a9ccc5e788e868355f01a65c5bc43b5ce2c7564bc673770568"),
    },
    "lib/python3.11/site-packages/sympy/parsing/autolev/test-examples": {
        "files": 33,
        "bytes": 31_963,
        "tree_sha256": ("8be46fc38432e12034115defe433d63b9f825abed75bba944d6b0f950e6890d1"),
    },
    "lib/python3.11/site-packages/sympy/polys/benchmarks": {
        "files": 4,
        "bytes": 449_083,
        "tree_sha256": ("9a816b10ee139187864b3768537a3ce07c6fa10f04ce85cc4631af3bb897822f"),
    },
    "lib/python3.11/site-packages/sympy/series/benchmarks": {
        "files": 3,
        "bytes": 380,
        "tree_sha256": ("f87895fceea4f798bbaaca262f07a076effc3e3fb59888ba74b85901ec6bd4cd"),
    },
    "lib/python3.11/site-packages/sympy/solvers/benchmarks": {
        "files": 2,
        "bytes": 288,
        "tree_sha256": ("607a9dfbd31a60c8a76c7d61b90dd24d39a7b8008afe57e3bb9ae5e20263923b"),
    },
    "lib/python3.11/site-packages/templates": {
        "files": 19,
        "bytes": 127_741,
        "tree_sha256": ("5c00636f53d7ab7386224af7fdee1a7c0d81aa04487e1df37be972016c351c5a"),
    },
    "lib/python3.11/site-packages/tools": {
        "files": 5,
        "bytes": 40_934,
        "tree_sha256": ("75f32ce602e78469d4ec97554ef5685a9ded1c78c486013754004c52f33210a1"),
    },
}
RETAINED_TESTING_API_DIRECTORIES = {
    "lib/python3.11/site-packages/numpy/testing",
    "lib/python3.11/site-packages/sympy/testing",
    "lib/python3.11/site-packages/torch/_numpy/testing",
    "lib/python3.11/site-packages/torch/testing",
}


def prune_unreachable_third_party_test_content(
    root: Path,
    evidence: Path,
    *,
    enforce_reviewed_contract: bool = True,
) -> dict[str, Any]:
    """Remove reviewed upstream test-only content from the copied runtime."""
    transformers_relative = Path("lib/python3.11/site-packages/transformers/testing_utils.py")
    transformers_path = root / transformers_relative
    require(
        transformers_path.is_file(),
        f"expected Transformers testing utility is missing: {transformers_relative}",
    )
    transformers_original = transformers_path.read_bytes()
    matches = list(TOKEN_PATTERNS[1].finditer(transformers_original))
    require(
        len(matches) == 1,
        "Transformers test-token contract changed; human review is required",
    )
    token = matches[0].group()
    for source in (ROOT / "src").rglob("*.py"):
        payload = source.read_bytes()
        require(
            b"transformers.testing_utils" not in payload,
            f"production source reaches Transformers testing utility: {source}",
        )
        require(
            b"joblib.test" not in payload,
            f"production source reaches joblib test content: {source}",
        )

    site_packages = root / "lib/python3.11/site-packages"
    require(site_packages.is_dir(), "runtime site-packages directory is missing")
    candidate_names = {"sample_data", "test", "testdata", "testing", "tests"}
    candidates = sorted(
        (
            path
            for path in site_packages.rglob("*")
            if path.is_dir()
            and path.name.casefold() in candidate_names
            and path.relative_to(root).as_posix() not in RETAINED_TESTING_API_DIRECTORIES
        ),
        key=lambda path: (len(path.parts), path.as_posix()),
    )
    selected: list[Path] = []
    for candidate in candidates:
        if not any(parent in candidate.parents for parent in selected):
            selected.append(candidate)
    for relative in (
        Path("lib/python3.11/site-packages/sklearn/datasets/data"),
        Path("lib/python3.11/site-packages/sklearn/datasets/descr"),
        Path("lib/python3.11/site-packages/sklearn/datasets/images"),
    ):
        candidate = root / relative
        if candidate.is_dir() and not any(parent in candidate.parents for parent in selected):
            selected.append(candidate)
    selected.sort(key=lambda path: path.relative_to(root).as_posix())
    joblib_path = site_packages / "joblib/test"
    require(joblib_path in selected, "joblib test-content prune target is missing")
    selected_paths = [target.relative_to(root).as_posix() for target in selected]

    test_files: list[dict[str, Any]] = []
    for target in selected:
        require(
            not target.is_symlink(),
            f"test-content symlink requires human review: {target}",
        )
        for candidate in sorted(target.rglob("*")):
            require(
                not candidate.is_symlink(),
                f"test-content symlink requires human review: {candidate}",
            )
            if candidate.is_file():
                test_files.append(
                    {
                        "path": candidate.relative_to(root).as_posix(),
                        "bytes": candidate.stat().st_size,
                        "sha256": sha256(candidate),
                    }
                )
    require(test_files, "test-content inventory is unexpectedly empty")
    if enforce_reviewed_contract:
        require(
            len(selected_paths) == REVIEWED_TEST_PRUNE_ROOT_COUNT,
            "reviewed test-content root count drift",
        )
        require(
            canonical_json_sha256(selected_paths) == REVIEWED_TEST_PRUNE_ROOTS_SHA256,
            "reviewed test-content root set drift",
        )
        require(
            len(test_files) == REVIEWED_TEST_PRUNE_FILE_COUNT,
            "reviewed test-content file count drift",
        )
        require(
            sum(int(row["bytes"]) for row in test_files) == REVIEWED_TEST_PRUNE_BYTES,
            "reviewed test-content byte count drift",
        )
        require(
            canonical_json_sha256(test_files) == REVIEWED_TEST_PRUNE_TREE_SHA256,
            "reviewed test-content inventory drift",
        )

    sample_files: list[dict[str, Any]] = []
    for relative_string, expected in REVIEWED_SAMPLE_DATA_FILES.items():
        relative = Path(relative_string)
        candidate = root / relative
        require(
            candidate.is_file() and not candidate.is_symlink(),
            f"reviewed sample-data prune target is missing: {relative}",
        )
        row = {
            "path": relative.as_posix(),
            "bytes": candidate.stat().st_size,
            "sha256": sha256(candidate),
        }
        if enforce_reviewed_contract:
            require(
                row["bytes"] == expected["bytes"] and row["sha256"] == expected["sha256"],
                f"reviewed sample-data content drift: {relative}",
            )
        sample_files.append(row)
    sample_directory_files: list[dict[str, Any]] = []
    sample_directories: list[Path] = []
    for relative_string, expected in REVIEWED_SAMPLE_DATA_DIRECTORIES.items():
        relative = Path(relative_string)
        directory = root / relative
        require(
            directory.is_dir() and not directory.is_symlink(),
            f"reviewed sample-data directory is missing: {relative}",
        )
        sample_directories.append(directory)
        rows: list[dict[str, Any]] = []
        for candidate in sorted(directory.rglob("*")):
            require(
                not candidate.is_symlink(),
                f"sample-data symlink requires human review: {candidate}",
            )
            if candidate.is_file():
                rows.append(
                    {
                        "path": candidate.relative_to(root).as_posix(),
                        "bytes": candidate.stat().st_size,
                        "sha256": sha256(candidate),
                    }
                )
        if enforce_reviewed_contract:
            require(
                len(rows) == expected["files"],
                f"reviewed sample-data file count drift: {relative}",
            )
            require(
                sum(int(row["bytes"]) for row in rows) == expected["bytes"],
                f"reviewed sample-data byte count drift: {relative}",
            )
            require(
                canonical_json_sha256(rows) == expected["tree_sha256"],
                f"reviewed sample-data inventory drift: {relative}",
            )
        sample_directory_files.extend(rows)
    development_directory_files: list[dict[str, Any]] = []
    development_directories: list[Path] = []
    for relative_string, expected in REVIEWED_DEVELOPMENT_CONTENT_DIRECTORIES.items():
        relative = Path(relative_string)
        directory = root / relative
        require(
            directory.is_dir() and not directory.is_symlink(),
            f"reviewed development-content directory is missing: {relative}",
        )
        development_directories.append(directory)
        rows = []
        for candidate in sorted(directory.rglob("*")):
            require(
                not candidate.is_symlink(),
                f"development-content symlink requires human review: {candidate}",
            )
            if candidate.is_file():
                rows.append(
                    {
                        "path": candidate.relative_to(root).as_posix(),
                        "bytes": candidate.stat().st_size,
                        "sha256": sha256(candidate),
                    }
                )
        if enforce_reviewed_contract:
            require(
                len(rows) == expected["files"],
                f"reviewed development-content file count drift: {relative}",
            )
            require(
                sum(int(row["bytes"]) for row in rows) == expected["bytes"],
                f"reviewed development-content byte count drift: {relative}",
            )
            require(
                canonical_json_sha256(rows) == expected["tree_sha256"],
                f"reviewed development-content inventory drift: {relative}",
            )
        development_directory_files.extend(rows)
    for source in (site_packages / "joblib").rglob("*.py"):
        if joblib_path in source.parents:
            continue
        require(
            b"joblib.test" not in source.read_bytes(),
            f"joblib production module reaches bundled test content: {source}",
        )

    transformers_path.unlink()
    for target in selected:
        shutil.rmtree(target)
    for directory in sample_directories:
        shutil.rmtree(directory)
    for directory in development_directories:
        shutil.rmtree(directory)
    for row in sample_files:
        (root / row["path"]).unlink()
    verify_unreachable_third_party_test_content_absent(root)
    transformers_result = {
        "path": transformers_relative.as_posix(),
        "exists": False,
    }
    test_results = [
        {
            "path": target.relative_to(root).as_posix(),
            "exists": False,
        }
        for target in selected
    ]
    sample_results = [
        {
            "path": row["path"],
            "exists": False,
        }
        for row in sample_files
    ]
    sample_directory_results = [
        {
            "path": directory.relative_to(root).as_posix(),
            "exists": False,
        }
        for directory in sample_directories
    ]
    development_directory_results = [
        {
            "path": directory.relative_to(root).as_posix(),
            "exists": False,
        }
        for directory in development_directories
    ]
    payload: dict[str, Any] = {
        "schema_version": "auralign-unreachable-test-content-pruning.v3",
        "scope": "copied runtime stage only",
        "source_wheels_archives_and_repository_untouched": True,
        "credential_sha256": hashlib.sha256(token).hexdigest(),
        "removed": [
            {
                "path": transformers_relative.as_posix(),
                "reason": "not reachable from Auralign production entry points",
                "original_sha256": hashlib.sha256(transformers_original).hexdigest(),
                "original_bytes": len(transformers_original),
                "result": "absent",
                "result_state_sha256": canonical_json_sha256(transformers_result),
            },
            {
                "paths": [target.relative_to(root).as_posix() for target in selected],
                "reason": (
                    "upstream test modules, compatibility fixtures, and sample "
                    "datasets are not "
                    "reachable from Auralign production entry points"
                ),
                "original_tree_sha256": canonical_json_sha256(test_files),
                "original_directories": len(selected),
                "original_files": len(test_files),
                "original_bytes": sum(int(row["bytes"]) for row in test_files),
                "files": test_files,
                "result": "absent",
                "result_state_sha256": canonical_json_sha256(test_results),
            },
            {
                "paths": [row["path"] for row in sample_files],
                "reason": (
                    "upstream public sample corpus is not reachable from "
                    "Auralign production entry points"
                ),
                "original_tree_sha256": canonical_json_sha256(sample_files),
                "original_files": len(sample_files),
                "original_bytes": sum(int(row["bytes"]) for row in sample_files),
                "files": sample_files,
                "result": "absent",
                "result_state_sha256": canonical_json_sha256(sample_results),
            },
            {
                "paths": [
                    directory.relative_to(root).as_posix() for directory in sample_directories
                ],
                "reason": (
                    "upstream media/example fixture is not reachable from "
                    "Auralign production entry points"
                ),
                "original_tree_sha256": canonical_json_sha256(sample_directory_files),
                "original_files": len(sample_directory_files),
                "original_bytes": sum(int(row["bytes"]) for row in sample_directory_files),
                "files": sample_directory_files,
                "result": "absent",
                "result_state_sha256": canonical_json_sha256(sample_directory_results),
            },
            {
                "paths": [
                    directory.relative_to(root).as_posix() for directory in development_directories
                ],
                "reason": (
                    "upstream documentation, recipes, demos, templates, "
                    "tools, benchmarks, and test examples are not reachable "
                    "from Auralign production entry points"
                ),
                "original_tree_sha256": canonical_json_sha256(development_directory_files),
                "original_files": len(development_directory_files),
                "original_bytes": sum(int(row["bytes"]) for row in development_directory_files),
                "files": development_directory_files,
                "result": "absent",
                "result_state_sha256": canonical_json_sha256(development_directory_results),
            },
        ],
    }
    evidence_path = evidence / "unreachable-test-content-pruning.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "directories_removed": (
            len(selected) + len(sample_directories) + len(development_directories)
        ),
        "files_removed": (
            1
            + len(test_files)
            + len(sample_files)
            + len(sample_directory_files)
            + len(development_directory_files)
        ),
        "evidence_sha256": sha256(evidence_path),
    }


def verify_unreachable_third_party_test_content_absent(root: Path) -> None:
    site_packages = root / "lib/python3.11/site-packages"
    forbidden_paths = (
        Path("lib/python3.11/site-packages/transformers/testing_utils.py"),
        Path("lib/python3.11/site-packages/networkx/generators/atlas.dat.gz"),
        Path("lib/python3.11/site-packages/pyannote/audio/sample"),
        Path("lib/python3.11/site-packages/sklearn/datasets/data"),
        Path("lib/python3.11/site-packages/sklearn/datasets/descr"),
        Path("lib/python3.11/site-packages/sklearn/datasets/images"),
        *(Path(relative) for relative in REVIEWED_DEVELOPMENT_CONTENT_DIRECTORIES),
    )
    for relative in forbidden_paths:
        path = root / relative
        require(
            not path.exists() and not path.is_symlink(),
            f"unreachable third-party test content remains: {relative}",
        )
    forbidden_directories = sorted(
        path.relative_to(root).as_posix()
        for path in site_packages.rglob("*")
        if path.is_dir()
        and path.name.casefold() in {"sample_data", "test", "testdata", "testing", "tests"}
        and path.relative_to(root).as_posix() not in RETAINED_TESTING_API_DIRECTORIES
    )
    require(
        not forbidden_directories,
        "unreachable third-party test directories remain: " + ", ".join(forbidden_directories),
    )


APPROVED_RUNTIME_DATA = {
    "lib/python3.11/lib2to3/Grammar3.11.15.final.0.pickle": {
        "bytes": 15_313,
        "sha256": ("97c8ed74d091fcfd23498029bb819c29d096c3dcb1326edee5dfb0591ade2e4b"),
        "classification": "CPython lib2to3 grammar data",
    },
    "lib/python3.11/lib2to3/PatternGrammar3.11.15.final.0.pickle": {
        "bytes": 1_225,
        "sha256": ("36ee934395b9209737b13893ddaff05fad8e239c2fdfac29d401d3fceeb30768"),
        "classification": "CPython lib2to3 pattern grammar data",
    },
    "lib/python3.11/site-packages/scipy/stats/_sobol_direction_numbers.npz": {
        "bytes": 589_334,
        "sha256": ("4859931147d42ce465b8605cb277f957d98b839d03194fdf06579357906d193b"),
        "classification": "SciPy Sobol direction-number data",
    },
    "lib/python3.11/site-packages/whisperx/assets/mel_filters.npz": {
        "bytes": 4_271,
        "sha256": ("7450ae70723a5ef9d341e3cee628c7cb0177f36ce42c44b7ed2bf3325f0f6d4c"),
        "classification": "WhisperX mel-filter numerical data",
    },
    "lib/python3.11/site-packages/mlx_whisper/assets/mel_filters.npz": {
        "bytes": 4_271,
        "sha256": ("7450ae70723a5ef9d341e3cee628c7cb0177f36ce42c44b7ed2bf3325f0f6d4c"),
        "classification": "MLX Whisper mel-filter numerical data",
    },
    "lib/python3.11/site-packages/sentencepiece/package_data/nfkc.bin": {
        "bytes": 240_008,
        "sha256": ("52f11028ff8a7df3e009d94a8b6a54d4a8a17132efb4ccc1c9a0a41e432bd91e"),
        "classification": "SentencePiece normalization data",
    },
    "lib/python3.11/site-packages/sentencepiece/package_data/nfkc_cf.bin": {
        "bytes": 247_028,
        "sha256": ("60833ec11201446659c3549c183b18f024c4007628cc6b3a4e91ae007697b826"),
        "classification": "SentencePiece normalization data",
    },
    "lib/python3.11/site-packages/sentencepiece/package_data/nmt_nfkc.bin": {
        "bytes": 240_007,
        "sha256": ("79372c41389c2b9b29bc171017ab5400e352debd686b02670a42bec709015074"),
        "classification": "SentencePiece normalization data",
    },
    "lib/python3.11/site-packages/sentencepiece/package_data/nmt_nfkc_cf.bin": {
        "bytes": 247_027,
        "sha256": ("22c292c76f503795f30c85d79d30a7f572fff4f49e00392d2d60f4f93e941a1e"),
        "classification": "SentencePiece normalization data",
    },
}
APPROVED_RUNTIME_ARCHIVES = {
    "lib/python3.11/site-packages/dateutil/zoneinfo/dateutil-zoneinfo.tar.gz": {
        "bytes": 156_400,
        "sha256": ("d3ea52e7b6e968de0d884df1288193596fa95b803db4f92a18279a7398004475"),
        "classification": "python-dateutil timezone data",
    },
}
APPROVED_RUNTIME_LEGAL_ARCHIVES = {
    "Legal/ffmpeg-7.1.5.tar.xz": {
        "bytes": 11_050_340,
        "sha256": ("de668509caf9e35e3cd162473441fdb29538c6d96ed080292b3cf9e6fc5d558f"),
        "classification": "FFmpeg LGPL corresponding source",
    },
}
APPROVED_RUNTIME_PATH_CONFIGURATION = {
    "lib/python3.11/site-packages/distutils-precedence.pth": {
        "bytes": 151,
        "sha256": ("2638ce9e2500e572a5e0de7faed6661eb569d1b696fcba07b0dd223da5f5d224"),
        "classification": "setuptools distutils path-configuration hook",
    },
}
REVIEWED_MODEL_PRUNE_FILE_COUNT = 10
REVIEWED_MODEL_PRUNE_WEIGHT_COUNT = 9
REVIEWED_MODEL_PRUNE_BYTES = 19_002_009
REVIEWED_MODEL_PRUNE_TREE_SHA256 = (
    "45a98bce78625742c9b2f837c213212c0b2e2b3566afcc50154e7ed878e787aa"
)


def _is_model_weight_path(relative: Path) -> bool:
    if relative == Path("lib/python3.11/site-packages/distutils-precedence.pth"):
        return False
    return (
        relative.suffix.casefold()
        in {
            ".bin",
            ".ckpt",
            ".gguf",
            ".h5",
            ".hdf5",
            ".mar",
            ".mlmodel",
            ".onnx",
            ".pb",
            ".pt",
            ".pth",
            ".safetensors",
            ".tflite",
        }
        or relative.suffix.casefold() == ".mlpackage"
    )


def verify_unbundled_model_assets_absent(
    root: Path,
    *,
    enforce_retained_data_contract: bool = True,
) -> list[dict[str, Any]]:
    site_packages = root / "lib/python3.11/site-packages"
    require(site_packages.is_dir(), "runtime site-packages directory is missing")
    approved_binary_data = {
        relative for relative in APPROVED_RUNTIME_DATA if Path(relative).suffix.casefold() == ".bin"
    }
    prohibited = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if (path.is_file() or path.is_dir())
        and _is_model_weight_path(path.relative_to(root))
        and path.relative_to(root).as_posix() not in approved_binary_data
    )
    require(
        not prohibited,
        "bundled model-weight paths remain: " + ", ".join(prohibited),
    )
    if not enforce_retained_data_contract:
        return []

    approved_data = dict(APPROVED_RUNTIME_DATA)
    observed_serialized_data = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".bin", ".npy", ".npz", ".pickle", ".pkl"}
    }
    require(
        observed_serialized_data == set(approved_data),
        "serialized runtime data inventory drift: "
        + ", ".join(sorted(observed_serialized_data.symmetric_difference(approved_data))),
    )
    approved_archives = dict(APPROVED_RUNTIME_ARCHIVES)
    if (root / "Legal").is_dir():
        approved_archives.update(APPROVED_RUNTIME_LEGAL_ARCHIVES)
    observed_archives = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path.name.casefold().endswith((".7z", ".bz2", ".gz", ".tar", ".tgz", ".xz", ".zip"))
    }
    require(
        observed_archives == set(approved_archives),
        "embedded runtime archive inventory drift: "
        + ", ".join(sorted(observed_archives.symmetric_difference(approved_archives))),
    )
    retained: list[dict[str, Any]] = []
    for relative, expected in sorted(
        {
            **approved_data,
            **approved_archives,
            **APPROVED_RUNTIME_PATH_CONFIGURATION,
        }.items()
    ):
        path = root / relative
        observed = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "classification": expected["classification"],
        }
        require(
            observed["bytes"] == expected["bytes"] and observed["sha256"] == expected["sha256"],
            f"approved runtime data content drift: {relative}",
        )
        retained.append(observed)
    return retained


def prune_unbundled_model_assets(
    root: Path,
    evidence: Path,
    *,
    enforce_retained_data_contract: bool = True,
) -> dict[str, Any]:
    """Remove upstream model weights that are outside Auralign's model manifest."""
    for source in (ROOT / "src").rglob("*.py"):
        payload = source.read_bytes()
        for marker in (
            b"onnxruntime.datasets",
            b"torchmetrics.functional.image.dists_models",
            b"torchmetrics.functional.image.lpips_models",
            b"whisperx.vads",
        ):
            require(
                marker not in payload,
                f"production source reaches unbundled upstream model asset: {source}",
            )
    faster_whisper_backend = (
        ROOT / "src/local_transcript_max/asr/faster_whisper_backend.py"
    ).read_bytes()
    require(
        b"require_faster_whisper_vad_asset" in faster_whisper_backend
        and b"silero_vad_v6.onnx" in faster_whisper_backend
        and b"standalone runtime intentionally excludes" in faster_whisper_backend,
        "faster-whisper VAD model exclusion is not fail-closed",
    )
    targets = (
        Path("lib/python3.11/site-packages/faster_whisper/assets/silero_vad_v6.onnx"),
        Path("lib/python3.11/site-packages/onnxruntime/datasets"),
        Path("lib/python3.11/site-packages/torchmetrics/functional/image/dists_models"),
        Path("lib/python3.11/site-packages/torchmetrics/functional/image/lpips_models"),
        Path("lib/python3.11/site-packages/whisperx/assets/pytorch_model.bin"),
    )
    files: list[dict[str, Any]] = []
    for relative in targets:
        target = root / relative
        require(
            target.is_file() or target.is_dir(),
            f"expected upstream model asset is missing: {relative}",
        )
        require(
            not target.is_symlink(),
            f"upstream model-asset symlink requires human review: {relative}",
        )
        candidates = [target] if target.is_file() else sorted(target.rglob("*"))
        for candidate in candidates:
            require(
                not candidate.is_symlink(),
                f"upstream model-asset symlink requires human review: {candidate}",
            )
            if candidate.is_file():
                files.append(
                    {
                        "path": candidate.relative_to(root).as_posix(),
                        "bytes": candidate.stat().st_size,
                        "sha256": sha256(candidate),
                        "model_weight": _is_model_weight_path(candidate.relative_to(root)),
                    }
                )
    weight_files = [row for row in files if row["model_weight"]]
    require(
        len(weight_files) == REVIEWED_MODEL_PRUNE_WEIGHT_COUNT,
        "upstream bundled-model contract changed; human review is required",
    )
    if enforce_retained_data_contract:
        require(
            len(files) == REVIEWED_MODEL_PRUNE_FILE_COUNT,
            "reviewed bundled-model file count drift",
        )
        require(
            sum(int(row["bytes"]) for row in files) == REVIEWED_MODEL_PRUNE_BYTES,
            "reviewed bundled-model byte count drift",
        )
        require(
            canonical_json_sha256(files) == REVIEWED_MODEL_PRUNE_TREE_SHA256,
            "reviewed bundled-model inventory drift",
        )
    for relative in targets:
        target = root / relative
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    retained_data = verify_unbundled_model_assets_absent(
        root,
        enforce_retained_data_contract=enforce_retained_data_contract,
    )
    result_states = [
        {
            "path": relative.as_posix(),
            "exists": False,
        }
        for relative in targets
    ]
    payload: dict[str, Any] = {
        "schema_version": "auralign-unbundled-model-assets.v1",
        "scope": "copied runtime stage only",
        "source_wheels_archives_and_repository_untouched": True,
        "reason": (
            "model weights remain outside the signed app runtime; the separate "
            "bootstrap component and exact verified native packs alone may "
            "establish runtime readiness"
        ),
        "original_tree_sha256": canonical_json_sha256(files),
        "original_files": len(files),
        "original_bytes": sum(int(row["bytes"]) for row in files),
        "model_weight_files": len(weight_files),
        "files": files,
        "result": "absent",
        "result_state_sha256": canonical_json_sha256(result_states),
        "retained_non_model_data": retained_data,
    }
    evidence_path = evidence / "unbundled-model-assets.json"
    evidence_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "files_removed": len(files),
        "model_weight_files_removed": len(weight_files),
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
                    value: sorted(dependencies) for value, dependencies in sorted(values.items())
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
        or re.fullmatch(r"libswift[A-Za-z0-9_+.-]*\.dylib", suffix_path.name) is None
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
            f"executable-relative dependency is unresolved or ambiguous in {owner}: {dependency}",
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
                own_install_name.startswith(("@rpath/", "@loader_path/", "@executable_path/")),
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
    expected_catalog = dump_model_delivery_catalog().encode("utf-8")
    require(
        MODEL_DELIVERY_CATALOG_PATH.read_bytes() == expected_catalog,
        "source model-delivery catalog drift",
    )
    require(
        (contents / "Resources/model_delivery_catalog.json").read_bytes() == expected_catalog,
        "signed application model-delivery catalog drift",
    )
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
    verify_unreachable_third_party_test_content_absent(app_runtime_root(app))
    verify_unbundled_model_assets_absent(app_runtime_root(app))
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


def verify_no_python_cache_artifacts(root: Path) -> None:
    offenders = [
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.name == "__pycache__" or path.suffix == ".pyc"
    ]
    require(
        not offenders,
        "embedded-Python probe left bytecode residue: " + ", ".join(offenders[:10]),
    )


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
    verify_unreachable_third_party_test_content_absent(runtime)
    verify_unbundled_model_assets_absent(runtime)
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
        joblib = run(
            embedded_python_command(
                python,
                "-I",
                "-c",
                (
                    "import joblib,json,os,sys,tempfile;"
                    "handle=tempfile.NamedTemporaryFile(delete=False);"
                    "path=handle.name;handle.close();"
                    "joblib.dump({'ok':True},path);"
                    "value=joblib.load(path);os.unlink(path);"
                    "print(json.dumps({"
                    "'version':joblib.__version__,"
                    "'round_trip':value=={'ok':True},"
                    "'test_reachable':any("
                    "name=='joblib.test' or name.startswith('joblib.test.') "
                    "for name in sys.modules)}))"
                ),
            ),
            cwd=home,
            env=environment,
            capture=True,
        )
        joblib_result = json.loads(joblib.stdout)
        require(
            joblib_result.get("round_trip") is True,
            f"{label}: joblib round-trip smoke failed",
        )
        require(
            joblib_result.get("test_reachable") is False,
            f"{label}: production joblib smoke reached bundled test content",
        )
        production_dependencies = run(
            embedded_python_command(
                python,
                "-I",
                "-c",
                (
                    "import importlib,json,numba;"
                    "jit_result=int(numba.njit(cache=False)("
                    "lambda value:value+1)(1));"
                    "names=('faster_whisper','mlx.core','mlx_whisper','onnxruntime',"
                    "'pyannote.audio',"
                    "'speechbrain.inference.speaker','torchaudio','torchmetrics',"
                    "'whisperx.alignment');"
                    "modules={name:importlib.import_module(name) for name in names};"
                    "print(json.dumps({'numba_jit':jit_result,'versions':"
                    "{name:getattr(module,'__version__','imported') "
                    "for name,module in modules.items()}},sort_keys=True))"
                ),
            ),
            cwd=home,
            env=environment,
            capture=True,
        )
        production_dependency_result = json.loads(production_dependencies.stdout)
        require(
            production_dependency_result.get("numba_jit") == 2,
            f"{label}: production Numba JIT smoke failed",
        )
        production_dependency_versions = production_dependency_result.get("versions")
        require(
            isinstance(production_dependency_versions, dict),
            f"{label}: production dependency version evidence is invalid",
        )
        require(
            set(production_dependency_versions)
            == {
                "faster_whisper",
                "mlx.core",
                "mlx_whisper",
                "onnxruntime",
                "pyannote.audio",
                "speechbrain.inference.speaker",
                "torchaudio",
                "torchmetrics",
                "whisperx.alignment",
            },
            f"{label}: production dependency import smoke drift",
        )
        retained_data = run(
            embedded_python_command(
                python,
                "-I",
                "-c",
                (
                    "import importlib,json,numpy as np,sentencepiece,whisperx\n"
                    "from dateutil.zoneinfo import get_zonefile_instance\n"
                    "from lib2to3.refactor import RefactoringTool\n"
                    "from pathlib import Path\n"
                    "from scipy.stats import qmc\n"
                    "from local_transcript_max.asr.faster_whisper_backend "
                    "import require_faster_whisper_vad_asset\n"
                    "from local_transcript_max.errors import "
                    "BackendUnavailableError\n"
                    "mel_path=Path(whisperx.__file__).parent/"
                    "'assets/mel_filters.npz'\n"
                    "mel=np.load(mel_path)['mel_80']\n"
                    "sobol=qmc.Sobol(d=2,scramble=False).random_base2(m=2)\n"
                    "zones=get_zonefile_instance().zones\n"
                    "sentencepiece.SetDataDir(str(Path(sentencepiece.__file__)."
                    "parent/'package_data'))\n"
                    "normalized=sentencepiece.SentencePieceNormalizer("
                    "rule_name='nmt_nfkc').normalize('ＡＢＣ')\n"
                    "RefactoringTool([])\n"
                    "assets_path=Path(importlib.import_module("
                    "'faster_whisper.utils').get_assets_path())\n"
                    "require_faster_whisper_vad_asset("
                    "enabled=False,assets_path=assets_path)\n"
                    "vad_blocked=False\n"
                    "try:\n"
                    " require_faster_whisper_vad_asset("
                    "enabled=True,assets_path=assets_path)\n"
                    "except BackendUnavailableError:\n"
                    " vad_blocked=True\n"
                    "result={'mel_shape':list(mel.shape),"
                    "'sobol_finite':bool(np.isfinite(sobol).all()),"
                    "'denver_zone':zones.get('America/Denver') is not None,"
                    "'sentencepiece_normalized':normalized,"
                    "'vad_false_supported':True,"
                    "'vad_true_blocked':vad_blocked}\n"
                    "print(json.dumps(result,sort_keys=True))"
                ),
            ),
            cwd=home,
            env=environment,
            capture=True,
        )
        retained_data_result = json.loads(retained_data.stdout)
        require(
            retained_data_result
            == {
                "denver_zone": True,
                "mel_shape": [80, 201],
                "sentencepiece_normalized": "ABC",
                "sobol_finite": True,
                "vad_false_supported": True,
                "vad_true_blocked": True,
            },
            f"{label}: retained runtime data smoke drift",
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
            "joblib_version": str(joblib_result.get("version", "")),
            "joblib_round_trip": str(joblib_result["round_trip"]),
            "joblib_test_reachable": str(joblib_result["test_reachable"]),
            "production_dependency_imports": json.dumps(
                production_dependency_versions,
                sort_keys=True,
            ),
            "retained_runtime_data": json.dumps(
                retained_data_result,
                sort_keys=True,
            ),
        }


def model_runtime_smoke(
    runtime: Path,
    model_bundle: Path,
    *,
    label: str,
) -> dict[str, Any]:
    """Verify a staged package component through the embedded release engine.

    This build-time probe calls the deterministic bundle verifier directly. It
    deliberately does not route a temporary extraction directory through the
    normal model-cache resolver, whose only marker-free authority is the exact
    installed system path.
    """

    bundle_record = verify_model_bundle(model_bundle)
    model_cache = model_bundle / "models"
    require(
        model_cache.is_dir() and not model_cache.is_symlink(),
        f"{label}: verified bundle has no model cache directory",
    )
    python = runtime / "bin/python3"
    require(python.is_file(), f"{label}: embedded Python is missing")
    with tempfile.TemporaryDirectory(prefix="auralign-model-runtime-smoke-") as raw:
        private = Path(raw)
        home = private / "home"
        cache = private / "cache"
        temporary = private / "tmp"
        for directory in (home, cache, temporary):
            directory.mkdir(mode=0o700)
        environment = {
            "HOME": str(home),
            "PATH": f"{runtime / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "LTM_OFFLINE": "1",
            "AURALIGN_BUILDER_VERIFIED_MODEL_BUNDLE": str(model_bundle),
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DO_NOT_TRACK": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "TMPDIR": str(temporary),
            "XDG_CACHE_HOME": str(cache),
            "HF_HOME": str(cache / "HuggingFace"),
        }
        completed = run(
            embedded_python_command(
                python,
                "-I",
                "-c",
                (
                    "import json;"
                    "import os;"
                    "from pathlib import Path;"
                    "from local_transcript_max.config import MODEL_MANIFEST;"
                    "from local_transcript_max.model_bundle import "
                    "STRICT_MODEL_KEYS,verify_model_bundle;"
                    "root=Path(os.environ['AURALIGN_BUILDER_VERIFIED_MODEL_BUNDLE']);"
                    "identity=verify_model_bundle(root);"
                    "print(json.dumps({"
                    "'count':len(STRICT_MODEL_KEYS),"
                    "'bundle_id':identity['bundle_id'],"
                    "'bundle_version':identity['bundle_version'],"
                    "'manifest_sha256':identity['manifest_sha256'],"
                    "'models':[{'role':MODEL_MANIFEST[key].role,'key':key,"
                    "'revision':MODEL_MANIFEST[key].revision,'present':True} "
                    "for key in STRICT_MODEL_KEYS]},sort_keys=True))"
                ),
            ),
            cwd=home,
            env=environment,
            capture=True,
        )
    runtime_record = json.loads(completed.stdout)
    require(
        runtime_record.get("count") == len(BOOTSTRAP_MODEL_KEYS),
        f"{label}: embedded engine did not verify every packaged bootstrap model",
    )
    require(
        all(
            isinstance(row, dict) and row.get("present") is True
            for row in runtime_record.get("models", [])
        ),
        f"{label}: embedded engine reported a non-ready model",
    )
    require(
        runtime_record.get("bundle_id") == MODEL_BUNDLE_IDENTIFIER
        and runtime_record.get("bundle_version") == MODEL_BUNDLE_VERSION
        and runtime_record.get("manifest_sha256") == bundle_record["manifest_sha256"],
        f"{label}: embedded engine model-bundle identity drift",
    )
    verify_model_bundle(model_bundle)
    verify_no_python_cache_artifacts(runtime)
    verify_no_python_cache_artifacts(model_bundle)
    return {
        "label": label,
        "bundle": bundle_record,
        "runtime": runtime_record,
    }


def model_inference_smoke(
    runtime: Path,
    model_bundle: Path,
    *,
    label: str,
) -> dict[str, Any]:
    """Run bounded real inference through every packaged bootstrap model.

    ASR choices are delivered as separately verified native-download packs and
    are exercised by the installed pack/readiness lane. This probe is scoped to
    the exact bytes actually present in the scriptless PKG.
    """

    bundle_record = verify_model_bundle(model_bundle)
    model_cache = model_bundle / "models"
    python = runtime / "bin/python3"
    ffmpeg = runtime / "bin/ffmpeg"
    for executable in (python, ffmpeg):
        require(executable.is_file(), f"{label}: embedded runtime executable is missing")
    say = Path("/usr/bin/say")
    require(say.is_file(), f"{label}: macOS synthetic-speech tool is unavailable")
    with tempfile.TemporaryDirectory(prefix="auralign-model-inference-smoke-") as raw:
        private = Path(raw)
        home = private / "home"
        cache = private / "cache"
        temporary = private / "tmp"
        for directory in (home, cache, temporary):
            directory.mkdir(mode=0o700)
        aiff = private / "synthetic-private-free.aiff"
        audio = private / "synthetic-private-free.wav"
        run(
            [
                str(say),
                "--voice",
                "Samantha",
                "--output-file",
                str(aiff),
                (
                    "Auralign verifies that every local model runs offline. "
                    "This synthetic voice contains no private media. "
                    "Auralign verification is complete."
                ),
            ],
            cwd=home,
            timeout=60,
        )
        run(
            [
                str(ffmpeg),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(aiff),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio),
            ],
            cwd=home,
            timeout=60,
        )
        environment = {
            "HOME": str(home),
            "PATH": f"{runtime / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "LTM_OFFLINE": "1",
            "LTM_MODEL_CACHE": str(model_cache),
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYANNOTE_METRICS_ENABLED": "false",
            "DO_NOT_TRACK": "1",
            "TOKENIZERS_PARALLELISM": "false",
            "TMPDIR": str(temporary),
            "XDG_CACHE_HOME": str(cache),
            "HF_HOME": str(cache / "HuggingFace"),
        }
        program = """
import json
import os
import resource
import wave
from pathlib import Path
from local_transcript_max import models as model_cache_authority
from local_transcript_max.model_bundle import (
    MODEL_BUNDLE_IDENTIFIER,
    STRICT_MODEL_KEYS,
    verify_model_bundle,
)

staged_model_bundle = Path(os.environ["AURALIGN_BUILDER_VERIFIED_MODEL_BUNDLE"])
staged_identity = verify_model_bundle(staged_model_bundle)
assert staged_identity["bundle_id"] == MODEL_BUNDLE_IDENTIFIER
assert staged_identity["model_keys"] == list(STRICT_MODEL_KEYS)
model_cache_authority.INSTALLED_MODEL_BUNDLE_ROOT = staged_model_bundle

from local_transcript_max.alignment.forced_alignment import WhisperXForcedAlignment
from local_transcript_max.config import MODEL_MANIFEST, preset_config
from local_transcript_max.diarization.pyannote_backend import PyannoteDiarizationBackend
from local_transcript_max.schemas import ASRResult, ASRSegment
from local_transcript_max.speaker_id.embeddings import extract_embedding

audio = Path(os.environ["AURALIGN_SMOKE_AUDIO"])
model_cache = Path(os.environ["LTM_MODEL_CACHE"])
with wave.open(str(audio), "rb") as handle:
    duration = handle.getnframes() / handle.getframerate()
config = preset_config("cpu", cwd=Path(os.environ["HOME"]))
config.offline = True
config.model_cache = model_cache
config.asr.language = "en"
config.asr.word_timestamps = True
config.asr.vad_filter = False
config.diarization.enabled = True
config.diarization.backend = "pyannote"
config.diarization.model = "community-1"
config.diarization.device = "cpu"
config.diarization.num_speakers = 1
config.alignment.enabled = True
config.alignment.backend = "forced"
config.alignment.language = "en"
config.alignment.model = "en"
config.alignment.device = "cpu"
config.speaker_id.mode = "auto"
config.speaker_id.model = "ecapa"
config.speaker_id.device = "cpu"

manual = ASRResult(
    segments=[
        ASRSegment(
            id=0,
            start=0.0,
            end=duration,
            text="Auralign verifies that every local model runs offline",
        )
    ],
    language="en",
)
aligner = WhisperXForcedAlignment()
words = aligner.align(audio, manual, config)
timestamped_words = [
    word for word in words if word.start is not None and word.end is not None
]
diarization = PyannoteDiarizationBackend().diarize(audio, config)
assert diarization.model is not None
assert diarization.model.revision == MODEL_MANIFEST["community-1"].revision
embedding_backend, embedding = extract_embedding(audio, cache_dir=model_cache)
payload = {
    "schema_version": "auralign-packaged-bootstrap-inference-smoke.v1",
    "offline": config.offline,
    "alignment": {
        "key": "en",
        "revision": MODEL_MANIFEST["en"].revision,
        "words": len(words),
        "timestamped_words": len(timestamped_words),
        "inference_seconds": aligner.last_inference_seconds,
    },
    "diarization": {
        "key": "community-1",
        "revision": diarization.model.revision,
        "turns": len(diarization.turns),
        "inference_seconds": diarization.metadata["timing"]["inference_seconds"],
    },
    "speaker_embedding": {
        "key": "ecapa",
        "revision": MODEL_MANIFEST["ecapa"].revision,
        "backend": embedding_backend,
        "dimensions": len(embedding),
    },
    "process_max_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
}
print("AURALIGN_SMOKE_JSON=" + json.dumps(payload, sort_keys=True))
""".strip()
        inference_environment = dict(environment)
        inference_environment["AURALIGN_SMOKE_AUDIO"] = str(audio)
        inference_environment["AURALIGN_BUILDER_VERIFIED_MODEL_BUNDLE"] = str(model_bundle)
        completed = run(
            embedded_python_command(python, "-I", "-c", program),
            cwd=home,
            env=inference_environment,
            capture=True,
            timeout=15 * 60,
        )
        rows = [
            line.removeprefix("AURALIGN_SMOKE_JSON=")
            for line in completed.stdout.splitlines()
            if line.startswith("AURALIGN_SMOKE_JSON=")
        ]
        require(len(rows) == 1, f"{label}: packaged inference evidence was not singular")
        inference = json.loads(rows[0])
        require(
            inference.get("schema_version") == "auralign-packaged-bootstrap-inference-smoke.v1",
            f"{label}: packaged inference evidence schema drift",
        )
        require(inference.get("offline") is True, f"{label}: inference was not offline")
        require(
            inference.get("alignment", {}).get("timestamped_words", 0) > 0,
            f"{label}: forced alignment produced no timestamped word",
        )
        require(
            inference.get("diarization", {}).get("turns", 0) > 0,
            f"{label}: diarization produced no synthetic-speech turn",
        )
        require(
            inference.get("speaker_embedding", {}).get("backend") == "speechbrain-ecapa"
            and inference.get("speaker_embedding", {}).get("dimensions") == 192,
            f"{label}: ECAPA inference contract drift",
        )
        verify_model_bundle(model_bundle)
        verify_no_python_cache_artifacts(runtime)
        verify_no_python_cache_artifacts(model_bundle)
        return {
            "label": label,
            "synthetic_input": {
                "kind": "macos-system-voice-private-free-v1",
                "sha256": sha256(audio),
            },
            "bundle": bundle_record,
            "inference": inference,
        }


def normalize_cyclonedx_dependency_graph(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Make CycloneDX leaf dependency rows explicit and deterministic."""

    raw_dependencies = payload.setdefault("dependencies", [])
    require(
        isinstance(raw_dependencies, list),
        "runtime SBOM dependency graph is invalid",
    )
    for row in raw_dependencies:
        require(
            isinstance(row, dict),
            "runtime SBOM dependency row is invalid",
        )
        reference = row.get("ref")
        require(
            isinstance(reference, str) and reference,
            "runtime SBOM dependency reference is invalid",
        )
        if "dependsOn" not in row:
            row["dependsOn"] = []
        targets = row.get("dependsOn")
        require(
            isinstance(targets, list)
            and all(isinstance(target, str) and target for target in targets),
            f"runtime SBOM dependency targets are invalid: {reference}",
        )
    return cast(list[dict[str, Any]], raw_dependencies)


def write_runtime_sbom(runtime: Path, evidence: Path, sources: dict[str, Any]) -> Path:
    destination = evidence / (
        f"Auralign-{sources['product_version']}-{sources['build_number']}-macos-arm64.cdx.json"
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
        str(component.get("name")) for component in components if not component.get("licenses")
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
                "licenses": [{"license": {"id": sources["python"]["project_license_expression"]}}],
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
                "licenses": [{"license": {"id": sources["ffmpeg"]["license_expression"]}}],
                "purl": f"pkg:generic/ffmpeg@{sources['ffmpeg']['version']}",
            },
        ]
    )
    metadata = payload.setdefault("metadata", {})
    require(isinstance(metadata, dict), "runtime SBOM metadata is invalid")
    properties = metadata.setdefault("properties", [])
    require(isinstance(properties, list), "runtime SBOM properties are invalid")
    build_info = json.loads((runtime / "release-build.json").read_text(encoding="utf-8"))
    properties.extend(
        {"name": name, "value": value}
        for name, value in (
            ("auralign:commit", str(build_info["source_revision"])),
            (
                "auralign:native-model-pack-catalog-schema",
                MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
            ),
            (
                "auralign:native-model-pack-catalog-version",
                MODEL_DELIVERY_CATALOG_VERSION,
            ),
            (
                "auralign:native-model-pack-catalog-sha256",
                model_delivery_catalog_sha256(),
            ),
            (
                "auralign:native-model-pack-count",
                str(sum(entry.delivery == "native-download" for entry in MODEL_MANIFEST.values())),
            ),
            (
                "auralign:native-model-pack-normal-presets",
                json.dumps(
                    NORMAL_PRESET_MODELS,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
            (
                "auralign:native-model-pack-sbom-contract",
                "CycloneDX-1.6-per-pack",
            ),
            ("auralign:platform", "darwin-arm64-cpu-app"),
            ("auralign:profile", "cpu"),
            ("auralign:python-version", str(sources["python"]["version"])),
            ("auralign:sbom-kind", "standalone-app-runtime"),
        )
    )
    properties.sort(key=lambda item: (str(item.get("name", "")), str(item.get("value", ""))))
    dependencies = normalize_cyclonedx_dependency_graph(payload)
    graph_refs = {
        str(item.get("ref")) for item in dependencies if isinstance(item, dict) and item.get("ref")
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


def augment_product_sbom_with_models(
    runtime_sbom: Path,
    model_bundle: Path,
) -> None:
    """Extend the reproducible runtime SBOM with exact packaged model bytes.

    Native-download packs are governed by the app's sealed delivery catalog and
    receive their own deterministic pack records when acquired. They are not
    represented as installed PKG bytes before acquisition.
    """

    verify_model_bundle(model_bundle)
    payload = json.loads(runtime_sbom.read_text(encoding="utf-8"))
    components = payload.get("components")
    dependencies = normalize_cyclonedx_dependency_graph(payload)
    metadata = payload.get("metadata")
    require(isinstance(components, list), "product SBOM component list is invalid")
    require(isinstance(metadata, dict), "product SBOM metadata is invalid")
    existing_refs = {
        str(component.get("bom-ref")) for component in components if isinstance(component, dict)
    }
    runtime_components = [
        component
        for component in components
        if isinstance(component, dict)
        and re.sub(r"[-_.]+", "-", str(component.get("name", "")).casefold())
        == "local-transcript-max"
    ]
    require(
        len(runtime_components) == 1,
        "product SBOM must contain exactly one local-transcript-max runtime",
    )
    runtime_reference = runtime_components[0].get("bom-ref")
    require(
        isinstance(runtime_reference, str) and runtime_reference,
        "product SBOM runtime reference is invalid",
    )
    runtime_dependency_rows = [
        row for row in dependencies if isinstance(row, dict) and row.get("ref") == runtime_reference
    ]
    require(
        len(runtime_dependency_rows) == 1,
        "product SBOM runtime dependency row is missing or duplicated",
    )
    raw_runtime_depends_on = runtime_dependency_rows[0].get("dependsOn")
    require(
        isinstance(raw_runtime_depends_on, list)
        and all(isinstance(reference, str) for reference in raw_runtime_depends_on),
        "product SBOM runtime dependency targets are invalid",
    )
    runtime_depends_on = cast(list[str], raw_runtime_depends_on)
    model_references: list[str] = []
    for key in BOOTSTRAP_MODEL_KEYS:
        entry = MODEL_MANIFEST[key]
        require(
            entry.revision is not None,
            f"product SBOM model revision is missing: {key}",
        )
        model_directory = model_bundle / "models" / entry.directory_name
        require(
            model_directory.is_dir() and not model_directory.is_symlink(),
            f"product SBOM model directory is missing: {key}",
        )
        actual_files = {
            relative: sha256(model_directory / relative) for relative in entry.runtime_files
        }
        require(
            actual_files == entry.expected_digest_map,
            f"product SBOM model digest contract drift: {key}",
        )
        aggregate = canonical_json_sha256(
            {
                "model_id": entry.model_id,
                "revision": entry.revision,
                "files": actual_files,
            }
        )
        reference = f"model:{entry.model_id}@{entry.revision}"
        require(reference not in existing_refs, f"duplicate model SBOM reference: {key}")
        components.append(
            {
                "bom-ref": reference,
                "type": "machine-learning-model",
                "group": entry.model_id.split("/", 1)[0],
                "name": entry.model_id.split("/", 1)[-1],
                "version": entry.revision,
                "description": entry.attribution,
                "hashes": [{"alg": "SHA-256", "content": aggregate}],
                "licenses": [
                    {
                        "license": {
                            "acknowledgement": "declared",
                            "id": entry.license_expression,
                        }
                    }
                ],
                "externalReferences": [
                    {"type": "website", "url": entry.source_url},
                    {"type": "license", "url": entry.terms_url},
                ],
                "properties": [
                    {"name": "auralign:model-key", "value": entry.key},
                    {"name": "auralign:model-role", "value": entry.role},
                    {
                        "name": "auralign:model-gated-acquisition",
                        "value": str(entry.gated).lower(),
                    },
                    {
                        "name": "auralign:model-bundle-id",
                        "value": MODEL_BUNDLE_IDENTIFIER,
                    },
                    {
                        "name": "auralign:model-bundle-version",
                        "value": MODEL_BUNDLE_VERSION,
                    },
                    {
                        "name": "auralign:model-file-manifest-sha256",
                        "value": aggregate,
                    },
                ],
            }
        )
        dependencies.append({"ref": reference, "dependsOn": []})
        existing_refs.add(reference)
        model_references.append(reference)
    require(
        not set(model_references) & set(runtime_depends_on),
        "product SBOM runtime already contains model dependency references",
    )
    runtime_depends_on.extend(model_references)
    runtime_depends_on.sort()
    components.sort(key=lambda row: str(row.get("bom-ref", "")))
    dependencies.sort(key=lambda row: str(row.get("ref", "")))
    properties = metadata.get("properties")
    require(isinstance(properties, list), "product SBOM metadata properties are invalid")
    kind_properties = [
        property_row
        for property_row in properties
        if isinstance(property_row, dict) and property_row.get("name") == "auralign:sbom-kind"
    ]
    require(
        len(kind_properties) == 1,
        "product SBOM kind property is missing or duplicated",
    )
    kind_properties[0]["value"] = "complete-product"
    require(
        not any(
            isinstance(property_row, dict)
            and property_row.get("name")
            in {"auralign:model-bundle-id", "auralign:model-bundle-version"}
            for property_row in properties
        ),
        "product SBOM already contains model-bundle properties",
    )
    properties.extend(
        [
            {
                "name": "auralign:model-bundle-id",
                "value": MODEL_BUNDLE_IDENTIFIER,
            },
            {
                "name": "auralign:model-bundle-version",
                "value": MODEL_BUNDLE_VERSION,
            },
        ]
    )
    properties.sort(key=lambda item: (str(item.get("name", "")), str(item.get("value", ""))))
    require(
        all(
            isinstance(component, dict) and component.get("bom-ref") and component.get("licenses")
            for component in components
        ),
        "product SBOM contains an unlicensed or unbound component",
    )
    runtime_sbom.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    verify_payload_safety(runtime_sbom, (ROOT, model_bundle))
    run(
        [
            sys.executable,
            "-B",
            str(ROOT / "scripts/validate_product_sbom.py"),
            str(runtime_sbom),
        ],
        env=no_bytecode_environment(),
        capture=True,
    )


def install_hash_locked_pure_python_sdists(
    work: Path,
    runtime: Path,
    downloads: Path,
    wheelhouse: Path,
    evidence: Path,
    sources: dict[str, Any],
) -> list[dict[str, Any]]:
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
    records: list[dict[str, Any]] = []
    for contract in contracts:
        require(isinstance(contract, dict), "pure-Python sdist entry is invalid")
        name = str(contract.get("name", ""))
        version = str(contract.get("version", ""))
        asset = str(contract.get("asset", ""))
        expected_sha256 = str(contract.get("sha256", ""))
        deterministic_patches_raw = contract.get("deterministic_patches", [])
        require(
            isinstance(deterministic_patches_raw, list)
            and all(isinstance(item, str) and item for item in deterministic_patches_raw),
            f"pure-Python deterministic patch list is invalid: {name}",
        )
        deterministic_patches = cast(list[str], deterministic_patches_raw)
        require(
            len(deterministic_patches) == len(set(deterministic_patches)),
            f"pure-Python deterministic patch list contains duplicates: {name}",
        )
        require(bool(name and version and asset), "pure-Python sdist identity is incomplete")
        source_archive = wheelhouse / asset
        require(source_archive.is_file(), f"pure-Python sdist missing: {asset}")
        require(
            sha256(source_archive) == expected_sha256,
            f"pure-Python sdist hash drift: {asset}",
        )

        build_sources: list[Path] = []
        if deterministic_patches:
            for label in ("first", "second"):
                source_root = fresh_directory(work / f"{name}-source-{label}")
                run(
                    [
                        "tar",
                        "-xzf",
                        str(source_archive),
                        "--strip-components=1",
                        "-C",
                        str(source_root),
                    ]
                )
                for patch_id in deterministic_patches:
                    patch_contract_or_none = PURE_PYTHON_SDIST_DETERMINISTIC_PATCHES.get(patch_id)
                    require(
                        patch_contract_or_none is not None,
                        f"unknown pure-Python deterministic patch: {patch_id}",
                    )
                    patch_contract = cast(dict[str, str], patch_contract_or_none)
                    patch_target = PurePosixPath(patch_contract["target"])
                    require(
                        bool(
                            not patch_target.is_absolute()
                            and ".." not in patch_target.parts
                            and patch_target.parts
                        ),
                        f"unsafe pure-Python deterministic patch target: {patch_target}",
                    )
                    target = source_root.joinpath(*patch_target.parts)
                    require(
                        target.is_file() and not target.is_symlink(),
                        f"pure-Python deterministic patch target is invalid: {target}",
                    )
                    require(
                        sha256(target) == patch_contract["original_sha256"],
                        f"pure-Python deterministic patch input drift: {name}/{patch_id}",
                    )
                    original = target.read_text(encoding="utf-8")
                    require(
                        original.count(patch_contract["old"]) == 1,
                        f"pure-Python deterministic patch match is not unique: {name}/{patch_id}",
                    )
                    target.write_text(
                        original.replace(patch_contract["old"], patch_contract["new"]),
                        encoding="utf-8",
                    )
                    require(
                        sha256(target) == patch_contract["patched_sha256"],
                        f"pure-Python deterministic patch output drift: {name}/{patch_id}",
                    )
                build_sources.append(source_root)
        else:
            build_sources = [source_archive, source_archive]

        first = fresh_directory(work / f"{name}-wheel-first")
        second = fresh_directory(work / f"{name}-wheel-second")
        for source, destination in zip(build_sources, (first, second), strict=True):
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
                    str(source),
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
            built_wheel.name == second_wheel.name and sha256(built_wheel) == sha256(second_wheel),
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
        record = {
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
        if deterministic_patches:
            record["deterministic_patches"] = [
                {
                    "id": patch_id,
                    "target": PURE_PYTHON_SDIST_DETERMINISTIC_PATCHES[patch_id]["target"],
                    "original_sha256": PURE_PYTHON_SDIST_DETERMINISTIC_PATCHES[patch_id][
                        "original_sha256"
                    ],
                    "patched_sha256": PURE_PYTHON_SDIST_DETERMINISTIC_PATCHES[patch_id][
                        "patched_sha256"
                    ],
                }
                for patch_id in deterministic_patches
            ]
        records.append(record)
    (evidence / "pure-python-sdist-builds.json").write_text(
        json.dumps(records, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return records


def project_runtime_requirements_without_source_builds(
    source: Path,
    destination: Path,
    contracts: list[object],
) -> dict[str, Any]:
    """Remove already-built source distributions from the final offline install."""
    require(source.is_file(), "runtime requirements source is missing")
    require(not destination.exists(), "runtime requirements projection already exists")
    excluded_names = {
        re.sub(r"[-_.]+", "-", str(contract.get("name", "")).lower())
        for contract in contracts
        if isinstance(contract, dict)
    }
    require(
        len(excluded_names) == len(contracts) and "" not in excluded_names,
        "runtime requirements source-build exclusions are invalid",
    )
    observed: Counter[str] = Counter()
    projected: list[str] = []
    skip_continuation = False
    for line in source.read_text(encoding="utf-8").splitlines(keepends=True):
        if line and not line[0].isspace():
            skip_continuation = False
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "--")):
                match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)", stripped)
                require(match is not None, "runtime requirement name is not parseable")
                requirement_name = re.sub(r"[-_.]+", "-", match.group(1).lower())
                if requirement_name in excluded_names:
                    observed[requirement_name] += 1
                    skip_continuation = True
        if not skip_continuation:
            projected.append(line)
    require(
        observed == Counter({name: 1 for name in excluded_names}),
        "runtime requirements source-build projection is incomplete or ambiguous",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(projected), encoding="utf-8")
    return {
        "schema_version": "auralign-runtime-requirements-projection.v1",
        "source_sha256": sha256(source),
        "projected_sha256": sha256(destination),
        "excluded_source_builds": sorted(excluded_names),
    }


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
            require(
                verified_cache_payload(
                    cache_root,
                    "runtime",
                    runtime_cache_inputs,
                )
                == cached_runtime,
                "diagnostic runtime cache changed during restore",
            )
            build_info = json.loads((runtime / "release-build.json").read_text(encoding="utf-8"))
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
    python_metadata = json.loads((metadata_root / "python/PYTHON.json").read_text(encoding="utf-8"))
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
        python_metadata.get("apple_sdk_deployment_target") == python["apple_sdk_deployment_target"],
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
        "--platform",
        "macosx_14_0_arm64",
        "--python-version",
        "3.11",
        "--implementation",
        "cp",
        "--abi",
        "cp311",
        "--no-deps",
        "--only-binary=:all:",
    )
    for contract in sources.get("pure_python_sdist_builds", []):
        download_requirements.extend(["--no-binary", str(contract["name"])])
    download_requirements.extend(["--dest", str(wheelhouse), "-r", str(REQUIREMENTS_PATH)])
    run(download_requirements, env=python_environment)
    pure_python_sdist_builds = install_hash_locked_pure_python_sdists(
        work,
        runtime,
        downloads,
        wheelhouse,
        evidence,
        sources,
    )
    effective_requirements = work / "effective-runtime-requirements.txt"
    requirements_projection = project_runtime_requirements_without_source_builds(
        REQUIREMENTS_PATH,
        effective_requirements,
        sources.get("pure_python_sdist_builds", []),
    )
    (evidence / "runtime-requirements-projection.json").write_text(
        json.dumps(requirements_projection, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    run(
        embedded_python_command(
            runtime / "bin/python3",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--require-hashes",
            "--find-links",
            str(wheelhouse),
            "-r",
            str(effective_requirements),
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
        ("--extra-ldflags=-mmacosx-version-min=14.0 -Wl,-headerpad_max_install_names"),
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
        require(
            verified_cache_payload(
                cache_root,
                "native",
                native_cache_inputs,
            )
            == cached_native,
            "diagnostic native cache changed during restore",
        )
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
    unbundled_model_assets = prune_unbundled_model_assets(runtime, evidence)
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
        "unbundled_model_assets": unbundled_model_assets,
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
        "models": {
            "app_bundle_included": False,
            "separate_component_required": True,
            "bundle_identifier": MODEL_BUNDLE_IDENTIFIER,
            "bundle_version": MODEL_BUNDLE_VERSION,
            "component_receipt_identifier": MODEL_RECEIPT_IDENTIFIER,
            "bootstrap_model_keys": list(BOOTSTRAP_MODEL_KEYS),
            "native_pack_catalog": {
                "schema_version": MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
                "catalog_version": MODEL_DELIVERY_CATALOG_VERSION,
                "sha256": model_delivery_catalog_sha256(),
                "normal_presets": dict(NORMAL_PRESET_MODELS),
                "model_keys": [
                    key
                    for key, entry in MODEL_MANIFEST.items()
                    if entry.delivery == "native-download"
                ],
            },
        },
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


def codesign_entitlements(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["codesign", "-d", "--entitlements", "-", "--xml", str(path)],
        cwd=ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    require(
        result.returncode == 0,
        f"codesign entitlements unavailable: {path}\n{result.stderr}",
    )
    if not result.stdout:
        return {}
    try:
        payload = plistlib.loads(result.stdout.encode("utf-8"))
    except plistlib.InvalidFileException as error:
        raise RuntimeError(f"codesign emitted invalid entitlements: {path}") from error
    require(isinstance(payload, dict), f"codesign entitlements are invalid: {path}")
    return cast(dict[str, Any], payload)


def verify_app_signatures(
    app: Path,
    *,
    expected_team: str | None,
    structure_only: bool,
) -> dict[str, str]:
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])
    runtime_bundle = app_runtime_bundle(app)
    runtime_python = (app_runtime_root(app) / "bin/python3").resolve(strict=True)
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
            expected_entitlements = (
                RUNTIME_PYTHON_ENTITLEMENTS if path.resolve(strict=True) == runtime_python else {}
            )
            require(
                codesign_entitlements(path) == expected_entitlements,
                f"signed entitlement contract drift: {path}",
            )
    outer = codesign_metadata(app)
    require(outer.get("Identifier") == APP_IDENTIFIER, "signed app identifier drift")
    require(
        codesign_metadata(runtime_bundle).get("Identifier") == RUNTIME_BUNDLE_IDENTIFIER,
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
    runtime_python = (app_runtime_root(app) / "bin/python3").resolve(strict=True)
    for path in sorted(
        macho_files(app),
        key=lambda candidate: len(candidate.relative_to(app).parts),
        reverse=True,
    ):
        if path == main_executable:
            continue
        entitlements = (
            [
                "--entitlements",
                str(RUNTIME_PYTHON_ENTITLEMENTS_PATH),
            ]
            if not structure_only and path.resolve(strict=True) == runtime_python
            else []
        )
        command = [
            "codesign",
            "--force",
            *hardened_runtime,
            *timestamp,
            *entitlements,
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
    model_bundle: Path,
    sources: dict[str, Any],
) -> tuple[Path, Path]:
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
    component_names = {candidate.name for candidate in sorted(expanded.glob("*.pkg"))}
    require(
        component_names == {APP_COMPONENT_FILENAME, MODEL_COMPONENT_FILENAME},
        f"expanded package component set drift: {sorted(component_names)}",
    )
    app_component = expanded / APP_COMPONENT_FILENAME
    model_component = expanded / MODEL_COMPONENT_FILENAME
    for component, label in (
        (app_component, "application component"),
        (model_component, "offline-model component"),
    ):
        require(
            {child.name for child in component.iterdir()} == {"Bom", "PackageInfo", "Payload"},
            f"{label} expanded archive contains unexpected top-level entries",
        )
    verify_component_bom(
        app_component,
        label="application component",
        required_prefix="Applications/Auralign.app",
        require_read_only=False,
    )
    verify_component_bom(
        model_component,
        label="offline-model component",
        required_prefix=(MODEL_INSTALL_PARENT / MODEL_BUNDLE_IDENTIFIER).as_posix(),
        require_read_only=True,
    )
    payload_app = app_component / "Payload/Applications/Auralign.app"
    payload_model_bundle = (
        model_component / "Payload" / MODEL_INSTALL_PARENT / MODEL_BUNDLE_IDENTIFIER
    )
    require(payload_app.is_dir(), "expanded package payload is missing Auralign.app")
    require(
        payload_model_bundle.is_dir(),
        "expanded package payload is missing the exact offline model bundle",
    )
    verify_component_payload_layout(
        app_component / "Payload",
        label="application component",
        required_prefix="Applications/Auralign.app",
        allowed_symlinks={
            ("Applications/Auralign.app/" + path.relative_to(signed_app).as_posix()): os.readlink(
                path
            )
            for path in sorted(signed_app.rglob("*"))
            if path.is_symlink()
        },
    )
    verify_component_payload_layout(
        model_component / "Payload",
        label="offline-model component",
        required_prefix=(MODEL_INSTALL_PARENT / MODEL_BUNDLE_IDENTIFIER).as_posix(),
    )
    app_package_info = app_component / "PackageInfo"
    model_package_info = model_component / "PackageInfo"
    require(app_package_info.is_file(), "expanded app component PackageInfo is missing")
    require(
        model_package_info.is_file(),
        "expanded model component PackageInfo is missing",
    )
    app_package_root = ET.parse(app_package_info).getroot()
    model_package_root = ET.parse(model_package_info).getroot()
    expected_version = f"{sources['product_version']}.{sources['build_number']}"
    require(
        app_package_root.attrib.get("identifier") == APP_RECEIPT_IDENTIFIER,
        "application component receipt identifier drift",
    )
    require(
        app_package_root.attrib.get("version") == expected_version,
        "application component receipt version drift",
    )
    require(
        model_package_root.attrib.get("identifier") == MODEL_RECEIPT_IDENTIFIER,
        "model component receipt identifier drift",
    )
    require(
        model_package_root.attrib.get("version") == MODEL_BUNDLE_VERSION,
        "model component receipt version drift",
    )
    require(
        inventory_rows(payload_app) == inventory_rows(signed_app),
        "expanded package application bytes or modes differ from signed app",
    )
    require(
        inventory_rows(payload_model_bundle) == inventory_rows(model_bundle),
        "expanded package model bytes or modes differ from verified model bundle",
    )
    verify_model_bundle(payload_model_bundle)
    distribution = expanded / "Distribution"
    require(distribution.is_file(), "expanded package Distribution is missing")
    verify_distribution_contract(
        distribution,
        sources=sources,
        productbuild_normalized=True,
    )
    expanded_resources = expanded / "Resources"
    verify_installer_resources(expanded_resources)
    for relative in INSTALLER_RESOURCE_RELATIVE_PATHS:
        expected = INSTALLER_RESOURCES_PATH / relative
        observed = expanded_resources / relative
        require(
            sha256(observed) == sha256(expected),
            f"installer presentation resource drift: {relative}",
        )
    verify_app_topology(payload_app)
    verify_native_closure(payload_app)
    verify_payload_safety(payload_app, (ROOT, package.parent))
    verify_payload_safety(payload_model_bundle, (ROOT, package.parent))
    return payload_app, payload_model_bundle


def verify_component_bom(
    component: Path,
    *,
    label: str,
    required_prefix: str,
    require_read_only: bool,
) -> list[dict[str, str]]:
    """Verify component ownership and immutable model modes from its BOM."""

    bom = component / "Bom"
    require(bom.is_file(), f"{label} BOM is missing")
    listing = run(["lsbom", "-p", "fmug", str(bom)], capture=True)
    rows: list[dict[str, str]] = []
    normalized_prefix = required_prefix.removeprefix("./").rstrip("/")
    matched_prefix = False
    for raw_line in listing.stdout.splitlines():
        if not raw_line:
            continue
        fields = raw_line.split("\t")
        require(len(fields) == 4, f"{label} BOM row is malformed")
        path, mode, uid, gid = fields
        normalized_path = path.removeprefix("./").rstrip("/")
        inside_required_prefix = normalized_path == normalized_prefix or normalized_path.startswith(
            normalized_prefix + "/"
        )
        is_required_ancestor = normalized_path == "." or normalized_prefix.startswith(
            normalized_path + "/"
        )
        require(
            inside_required_prefix or is_required_ancestor,
            f"{label} BOM contains a path outside its exact payload: {path}",
        )
        if inside_required_prefix:
            matched_prefix = True
        require(uid == "0" and gid == "0", f"{label} BOM is not root:wheel owned")
        if require_read_only and inside_required_prefix:
            require(
                mode in {"40555", "100444"},
                f"{label} BOM contains a mutable or irregular model entry: {path}",
            )
        rows.append(
            {
                "path": normalized_path,
                "mode": mode,
                "uid": uid,
                "gid": gid,
            }
        )
    require(rows, f"{label} BOM is empty")
    require(matched_prefix, f"{label} BOM omits its required payload prefix")
    payload_root = component / "Payload"
    require(payload_root.is_dir(), f"{label} expanded Payload is missing")
    bom_paths = {row["path"] for row in rows}
    payload_paths = {
        ".",
        *(path.relative_to(payload_root).as_posix() for path in sorted(payload_root.rglob("*"))),
    }
    require(
        bom_paths == payload_paths,
        f"{label} BOM and expanded Payload path set differ",
    )
    return rows


def verify_component_payload_layout(
    payload_root: Path,
    *,
    label: str,
    required_prefix: str,
    allowed_symlinks: dict[str, str] | None = None,
) -> None:
    """Reject entries outside one tree and links outside an exact source contract."""

    require(
        payload_root.is_dir() and not payload_root.is_symlink(),
        f"{label} expanded Payload is missing or unsafe",
    )
    normalized_prefix = required_prefix.removeprefix("./").rstrip("/")
    require(
        normalized_prefix
        and PurePosixPath(normalized_prefix).is_absolute() is False
        and ".." not in PurePosixPath(normalized_prefix).parts,
        f"{label} required payload prefix is unsafe",
    )
    symlink_contract = dict(allowed_symlinks or {})
    require(
        all(
            path.startswith(normalized_prefix + "/")
            and not PurePosixPath(path).is_absolute()
            and ".." not in PurePosixPath(path).parts
            and not os.path.isabs(target)
            for path, target in symlink_contract.items()
        ),
        f"{label} allowed-symlink contract is unsafe",
    )
    required_root = (payload_root / normalized_prefix).resolve(strict=True)
    matched_prefix = False
    observed_symlinks: set[str] = set()
    for path in [payload_root, *sorted(payload_root.rglob("*"))]:
        metadata = path.lstat()
        relative = "." if path == payload_root else path.relative_to(payload_root).as_posix()
        inside_required_prefix = relative == normalized_prefix or relative.startswith(
            normalized_prefix + "/"
        )
        is_required_ancestor = relative == "." or normalized_prefix.startswith(relative + "/")
        require(
            inside_required_prefix or is_required_ancestor,
            f"{label} expanded Payload contains an unexpected path: {relative}",
        )
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            require(
                symlink_contract.get(relative) == target,
                f"{label} expanded Payload contains an unapproved symlink: {relative}",
            )
            resolved = path.resolve(strict=True)
            require(
                resolved.is_relative_to(required_root) and resolved.is_file(),
                f"{label} expanded Payload symlink escapes or is irregular: {relative}",
            )
            observed_symlinks.add(relative)
            continue
        require(
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode),
            f"{label} expanded Payload contains an irregular entry: {relative}",
        )
        if relative == normalized_prefix:
            require(
                stat.S_ISDIR(metadata.st_mode),
                f"{label} required payload root is not a directory",
            )
            matched_prefix = True
    require(matched_prefix, f"{label} expanded Payload omits its required root")
    require(
        observed_symlinks == set(symlink_contract),
        f"{label} expanded Payload omits an approved symlink",
    )


def build_package(
    work: Path,
    app: Path,
    model_bundle: Path,
    sources: dict[str, Any],
    installer_identity: str | None,
    *,
    structure_only: bool,
) -> Path:
    app_root = fresh_directory(work / "app-pkgroot")
    applications = app_root / "Applications"
    applications.mkdir()
    shutil.copytree(app, applications / "Auralign.app", symlinks=True)
    model_root = fresh_directory(work / "model-pkgroot")
    installed_models = model_root / MODEL_INSTALL_PARENT
    installed_models.mkdir(parents=True)
    packaged_model_bundle = installed_models / MODEL_BUNDLE_IDENTIFIER
    shutil.copytree(model_bundle, packaged_model_bundle, symlinks=False)
    require(
        inventory_rows(packaged_model_bundle) == inventory_rows(model_bundle),
        "model component staging changed verified model bytes or modes",
    )
    verify_model_bundle(packaged_model_bundle)
    app_component = work / APP_COMPONENT_FILENAME
    model_component = work / MODEL_COMPONENT_FILENAME
    version = f"{sources['product_version']}.{sources['build_number']}"
    run(
        [
            "pkgbuild",
            "--root",
            str(app_root),
            "--component-plist",
            str(COMPONENT_PLIST_PATH),
            "--identifier",
            APP_RECEIPT_IDENTIFIER,
            "--version",
            version,
            "--install-location",
            "/",
            "--ownership",
            "recommended",
            str(app_component),
        ]
    )
    run(
        [
            "pkgbuild",
            "--root",
            str(model_root),
            "--identifier",
            MODEL_RECEIPT_IDENTIFIER,
            "--version",
            MODEL_BUNDLE_VERSION,
            "--install-location",
            "/",
            "--ownership",
            "recommended",
            str(model_component),
        ]
    )
    name = f"Auralign-{sources['product_version']}-{sources['build_number']}-macos-arm64" + (
        ".UNSIGNED-TEST.pkg" if structure_only else ".pkg"
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
    require(
        package.stat().st_size < GITHUB_RELEASE_ASSET_MAX_BYTES,
        "final product PKG exceeds GitHub's per-release-asset size limit",
    )
    expanded_app, expanded_model_bundle = verify_expanded_package(
        package,
        work / "expanded-pkg",
        signed_app=app,
        model_bundle=model_bundle,
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
    model_smoke = model_runtime_smoke(
        app_runtime_root(expanded_app),
        expanded_model_bundle,
        label="expanded-package-models",
    )
    (work / "evidence/package-model-smoke.json").write_text(
        json.dumps(model_smoke, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    if structure_only:
        inference_smoke = model_inference_smoke(
            app_runtime_root(expanded_app),
            expanded_model_bundle,
            label="expanded-structure-only-package-model-inference",
        )
        (work / "evidence/diagnostic-package-model-inference-smoke.json").write_text(
            json.dumps(inference_smoke, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    return package


def validate_notarization_log(payload: Any) -> None:
    require(isinstance(payload, dict), "Apple notarization log is not an object")
    require(
        payload.get("status") == "Accepted",
        "Apple notarization log does not report Accepted",
    )
    require("issues" in payload, "Apple notarization issue list is missing")
    issues = payload["issues"]
    require(
        issues is None or isinstance(issues, list),
        "Apple notarization issue list is malformed",
    )
    if issues is None:
        return
    require(
        not issues,
        f"Apple notarization reported {len(issues)} warning or error issue(s)",
    )


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
    validate_notarization_log(json.loads(log.stdout))
    run(["xcrun", "stapler", "staple", "-v", str(package)])
    run(["xcrun", "stapler", "validate", "-v", str(package)])


def verify_final_package(
    package: Path,
    *,
    app: Path,
    model_bundle: Path,
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
    payload_app, payload_model_bundle = verify_expanded_package(
        package,
        package.parent / "expanded-final-pkg",
        signed_app=app,
        model_bundle=model_bundle,
        sources=sources,
    )
    run(
        [
            "spctl",
            "--assess",
            "--type",
            "execute",
            "--verbose=4",
            str(payload_app),
        ]
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
    model_smoke = model_runtime_smoke(
        app_runtime_root(payload_app),
        payload_model_bundle,
        label="final-stapled-package-models",
    )
    (package.parent / "evidence/final-package-model-smoke.json").write_text(
        json.dumps(model_smoke, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    inference_smoke = model_inference_smoke(
        app_runtime_root(payload_app),
        payload_model_bundle,
        label="final-stapled-package-model-inference",
    )
    (package.parent / "evidence/final-package-model-inference-smoke.json").write_text(
        json.dumps(inference_smoke, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def write_release_manifest(
    work: Path,
    runtime: Path,
    app: Path,
    model_bundle: Path,
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
        f"Auralign-{sources['product_version']}-{sources['build_number']}-macos-arm64.cdx.json"
    )
    shutil.copy2(
        work / "evidence" / runtime_sbom.name,
        runtime_sbom,
    )
    ffmpeg_source = work / sources["ffmpeg"]["asset"]
    shutil.copy2(work / "downloads" / sources["ffmpeg"]["asset"], ffmpeg_source)
    verified_model_bundle = verify_model_bundle(model_bundle)
    model_artifacts: list[Path] = []
    for name in (
        MODEL_BUNDLE_MANIFEST_FILENAME,
        MODEL_BUNDLE_VERIFICATION_FILENAME,
        MODEL_BUNDLE_PROVENANCE_FILENAME,
        MODEL_BUNDLE_COMPLETION_FILENAME,
    ):
        source = model_bundle / name
        destination = work / name
        shutil.copy2(source, destination)
        model_artifacts.append(destination)
    model_inventory = work / "model-bundle-inventory.json"
    write_inventory(
        model_bundle,
        model_inventory,
        schema_version="auralign-macos-model-bundle-inventory.v1",
    )
    model_artifacts.append(model_inventory)
    build_info = json.loads((runtime / "release-build.json").read_text(encoding="utf-8"))
    payload = {
        "schema_version": "auralign-macos-pkg-release.v2",
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
            "component_receipt_identifier": APP_RECEIPT_IDENTIFIER,
            "component_version": (f"{sources['product_version']}.{sources['build_number']}"),
            "runtime_bundle": RUNTIME_BUNDLE_NAME,
            "runtime_bundle_identifier": RUNTIME_BUNDLE_IDENTIFIER,
            "runtime_relative_path": (
                f"Contents/Frameworks/{RUNTIME_BUNDLE_NAME}/Contents/Resources/Runtime"
            ),
            "runtime_python_entitlements": dict(RUNTIME_PYTHON_ENTITLEMENTS),
            "main_executable_sha256": sha256(app / "Contents/MacOS/Auralign"),
            "code_directory_hash": app_signature.get("CDHash"),
            "team_identifier": app_signature.get("TeamIdentifier"),
        },
        "models": {
            "included": True,
            "inclusion_scope": "bootstrap-component-only",
            "component_receipt_identifier": MODEL_RECEIPT_IDENTIFIER,
            "component_version": MODEL_BUNDLE_VERSION,
            "bundle_identifier": MODEL_BUNDLE_IDENTIFIER,
            "install_path": ("/" + (MODEL_INSTALL_PARENT / MODEL_BUNDLE_IDENTIFIER).as_posix()),
            "inventory_sha256": canonical_json_sha256(inventory_rows(model_bundle)),
            "verification": verified_model_bundle,
            "artifacts": [
                {
                    "name": artifact.name,
                    "bytes": artifact.stat().st_size,
                    "sha256": sha256(artifact),
                }
                for artifact in model_artifacts
            ],
            "native_packs": {
                "included_in_pkg": False,
                "delivery": "signed-native-exact-verified-per-user-packs",
                "catalog_schema_version": MODEL_DELIVERY_CATALOG_SCHEMA_VERSION,
                "catalog_version": MODEL_DELIVERY_CATALOG_VERSION,
                "catalog_sha256": model_delivery_catalog_sha256(),
                "normal_presets": dict(NORMAL_PRESET_MODELS),
                "model_keys": [
                    key
                    for key, entry in MODEL_MANIFEST.items()
                    if entry.delivery == "native-download"
                ],
            },
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
        "models_included": True,
        "models_included_scope": "bootstrap-component-only",
        "expected_team_identifier": expected_team,
        "installer_design": {
            "source_commit": "bd94520ecf106b7502ad04e19e4997dbb74d05dd",
            "background_sha256": sha256(INSTALLER_RESOURCES_PATH / "background.png"),
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
        *model_artifacts,
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
        default=Path.home() / "Library/Caches/com.auralign.studio/release-builder-diagnostics",
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
    parser.add_argument(
        "--model-cache",
        type=Path,
        help=(
            "Explicit ignored acquisition cache containing the three exact "
            "bootstrap models plus deterministic Punkt inputs. Private markers "
            "and cache metadata are verified but never copied into the package."
        ),
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
    if not args.structure_only:
        require(args.no_cache, "Developer ID release builds require --no-cache")
        # Fail before creating or populating the authoritative output tree.
        # build_runtime() deliberately rechecks the clean source and captures
        # its exact tree digest immediately before runtime assembly.
        require_clean_main()
    require(
        args.model_cache is not None,
        "Every standalone build requires an explicit --model-cache.",
    )
    require_host()
    sources = load_sources()
    verify_packaging_contract(sources)
    work = fresh_directory(args.work_dir.expanduser().resolve())
    cache_root = None if args.no_cache else args.cache_dir.expanduser().resolve()
    source_model_cache = args.model_cache.expanduser().resolve()
    model_license_inputs = fresh_directory(work / "model-license-inputs")
    for license_name in MODEL_BUNDLE_LICENSE_FILENAMES:
        shutil.copy2(
            ROOT / "packaging/macos/model-licenses" / license_name,
            model_license_inputs / license_name,
        )
    model_notices_path = work / "model-bundle-notices.txt"
    model_notices_path.write_bytes(model_bundle_notices_bytes())
    model_bundle_parent = fresh_directory(work / "model-bundle")
    model_bundle_path = model_bundle_parent / MODEL_BUNDLE_IDENTIFIER
    model_bundle = assemble_model_bundle(
        source_model_cache,
        model_bundle_path,
        license_directory=model_license_inputs,
        notices_path=model_notices_path,
    )
    require(
        model_bundle == verify_model_bundle(model_bundle_path),
        "model bundle assembly and independent verification records differ",
    )
    verify_payload_safety(
        model_bundle_path,
        (ROOT, work, source_model_cache),
    )
    runtime = build_runtime(
        work,
        sources,
        allow_dirty=args.structure_only,
        cache_root=cache_root,
    )
    augment_product_sbom_with_models(
        work
        / "evidence"
        / (f"Auralign-{sources['product_version']}-{sources['build_number']}-macos-arm64.cdx.json"),
        model_bundle_path,
    )
    (work / "evidence/model-bundle-assembly.json").write_text(
        json.dumps(model_bundle, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
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
        model_bundle_path,
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
        model_bundle=model_bundle_path,
        sources=sources,
        expected_team=expected_team,
        structure_only=args.structure_only,
    )
    write_release_manifest(
        work,
        runtime,
        app,
        model_bundle_path,
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
