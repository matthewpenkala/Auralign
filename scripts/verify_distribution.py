#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Verify release archives and emit deterministic, scope-specific SPDX manifests."""

from __future__ import annotations

import argparse
import email.policy
import hashlib
import json
import re
import stat
import tarfile
import tomllib
import zipfile
from collections.abc import Mapping
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
PROJECT_LICENSE = "LicenseRef-PolyForm-Internal-Use-1.0.0"
EXPECTED_LICENSE_FILES = {
    "compliance/THIRD_PARTY_COMPONENTS.generated.md",
    "LICENSE",
    "LICENSES/LicenseRef-PolyForm-Internal-Use-1.0.0.txt",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
}
NATIVE_SUFFIXES = {".a", ".dll", ".dylib", ".o", ".pyd", ".so", ".wasm"}
PROHIBITED_PARTS = {
    ".cache",
    ".git",
    ".hf-cache",
    ".huggingface",
    ".job-store",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "benchmark_runs",
    "data",
    "datasets",
    "embeddings",
    "hf-cache",
    "huggingface",
    "models",
    "outputs",
    "profiles",
    "transcripts",
}
PROHIBITED_SUFFIXES = {
    ".aac",
    ".aif",
    ".aiff",
    ".avi",
    ".flac",
    ".m4a",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".mxf",
    ".ogg",
    ".p12",
    ".pem",
    ".pfx",
    ".rttm",
    ".srt",
    ".vtt",
    ".wav",
    ".webm",
    ".wma",
    ".key",
    ".bin",
    ".ckpt",
    ".npz",
    ".onnx",
    ".pt",
    ".pth",
    ".safetensors",
}
PROHIBITED_BASENAMES = {
    ".ltm-model.json",
    "access_token",
    "access_token.txt",
    "hf_token",
    "hf_token.txt",
    "model_audit.json",
    "validation-capsule.json",
    "sha256sums",
    "token",
    "token.txt",
}
SENSITIVE_CONTENT = (
    b"BEGIN OPENSSH" + b" PRIVATE KEY",
    b"BEGIN RSA" + b" PRIVATE KEY",
    b"BEGIN EC" + b" PRIVATE KEY",
)
PRIVATE_HOME_PREFIXES = (b"/" + b"Users/", b"/" + b"home/")
PRIVATE_HOME_PATTERNS = tuple(
    re.compile(re.escape(prefix) + rb"[^/\s]+/") for prefix in PRIVATE_HOME_PREFIXES
) + (
    re.compile(rb"[A-Za-z]:[\\/]" + re.escape(b"Users") + rb"[\\/][^\\/\s]+[\\/]"),
)
TOKEN_PATTERNS = (
    re.compile(b"gh" + rb"[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(b"hf" + rb"_[A-Za-z0-9]{24,}"),
    re.compile(b"sk" + rb"-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(b"AK" + rb"IA[0-9A-Z]{16}"),
    re.compile(b"Bearer" + rb"\s+[A-Za-z0-9._~+/-]{32,}={0,2}"),
)
SYNTHETIC_PATH_ALLOWLIST = {
    ("tests/test_redaction.py", b"/" + b"Users/" + b"example/")
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def sha(data: bytes, algorithm: str) -> str:
    return hashlib.new(algorithm, data).hexdigest()


def safe_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    require(not path.is_absolute(), f"absolute archive member: {name}")
    require(".." not in path.parts, f"parent traversal archive member: {name}")
    return path


def read_wheel(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        files: dict[str, bytes] = {}
        for info in archive.infolist():
            safe_path(info.filename)
            require(not info.is_dir(), f"unexpected wheel directory entry: {info.filename}")
            unix_mode = info.external_attr >> 16
            require(
                stat.S_IFMT(unix_mode) in {0, stat.S_IFREG},
                f"non-regular wheel member: {info.filename}",
            )
            require(info.filename not in files, f"duplicate wheel member: {info.filename}")
            files[info.filename] = archive.read(info)
    return files


def read_sdist(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, mode="r:gz") as archive:
        files: dict[str, bytes] = {}
        for member in archive.getmembers():
            safe_path(member.name)
            require(
                member.isfile() or member.isdir(),
                f"non-file sdist member: {member.name}",
            )
            if member.isdir():
                continue
            require(member.name not in files, f"duplicate sdist member: {member.name}")
            handle = archive.extractfile(member)
            if handle is None:
                raise RuntimeError(f"unreadable sdist member: {member.name}")
            files[member.name] = handle.read()
    return files


def read_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        files: dict[str, bytes] = {}
        for info in archive.infolist():
            safe_path(info.filename)
            if info.is_dir():
                continue
            unix_mode = info.external_attr >> 16
            require(
                stat.S_IFMT(unix_mode) in {0, stat.S_IFREG},
                f"non-regular ZIP member: {info.filename}",
            )
            require(info.filename not in files, f"duplicate ZIP member: {info.filename}")
            files[info.filename] = archive.read(info)
    return files


def read_tar(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, mode="r:gz") as archive:
        files: dict[str, bytes] = {}
        for member in archive.getmembers():
            safe_path(member.name)
            require(
                member.isfile() or member.isdir(),
                f"non-file tar member: {member.name}",
            )
            if member.isdir():
                continue
            require(member.name not in files, f"duplicate tar member: {member.name}")
            handle = archive.extractfile(member)
            if handle is None:
                raise RuntimeError(f"unreadable tar member: {member.name}")
            files[member.name] = handle.read()
    return files


def strip_sdist_root(files: Mapping[str, bytes]) -> dict[str, bytes]:
    roots = {PurePosixPath(name).parts[0] for name in files}
    require(len(roots) == 1, f"sdist must have one root directory, found: {sorted(roots)}")
    root = next(iter(roots))
    return {name.removeprefix(f"{root}/"): data for name, data in files.items()}


def verify_no_prohibited_content(files: Mapping[str, bytes], scope: str) -> None:
    for name, data in files.items():
        path = safe_path(name)
        basename = path.name.lower()
        lowered = {part.lower() for part in path.parts}
        require(not lowered.intersection(PROHIBITED_PARTS), f"{scope} private path: {name}")
        require(
            basename != ".env" and not basename.startswith(".env."),
            f"{scope} environment file: {name}",
        )
        require(basename not in PROHIBITED_BASENAMES, f"{scope} private file: {name}")
        require(path.suffix.lower() not in PROHIBITED_SUFFIXES, f"{scope} private file: {name}")
        require(not any(marker in data for marker in SENSITIVE_CONTENT), f"{scope} secret: {name}")
        require(
            not any(pattern.search(data) for pattern in TOKEN_PATTERNS),
            f"{scope} token: {name}",
        )
        for pattern in PRIVATE_HOME_PATTERNS:
            for match in pattern.finditer(data):
                require(
                    (name, match.group()) in SYNTHETIC_PATH_ALLOWLIST,
                    f"{scope} private home path: {name}",
                )


def verify_legal_payload(files: Mapping[str, bytes], prefix: str = "") -> None:
    for relative in EXPECTED_LICENSE_FILES:
        member = f"{prefix}{relative}"
        require(member in files, f"missing legal payload: {member}")
        require(files[member] == (ROOT / relative).read_bytes(), f"changed legal payload: {member}")


def verify_wheel(files: Mapping[str, bytes]) -> None:
    metadata_names = [name for name in files if name.endswith(".dist-info/METADATA")]
    require(len(metadata_names) == 1, "wheel must contain exactly one METADATA file")
    metadata = BytesParser(policy=email.policy.default).parsebytes(files[metadata_names[0]])
    require(metadata["Author"] == "Matthew Penkala", "wheel author drift")
    require(metadata["License-Expression"] == PROJECT_LICENSE, "wheel license drift")
    require("Private :: Do Not Upload" in metadata.get_all("Classifier", []), "PyPI guard missing")
    require(
        set(metadata.get_all("License-File", [])) == EXPECTED_LICENSE_FILES,
        "wheel License-File metadata drift",
    )
    dist_info = metadata_names[0].removesuffix("METADATA")
    verify_legal_payload(files, f"{dist_info}licenses/")
    for name in files:
        require(
            name.startswith("local_transcript_max/") or ".dist-info/" in name,
            f"unexpected wheel top-level content: {name}",
        )
        require(
            PurePosixPath(name).suffix.lower() not in NATIVE_SUFFIXES,
            f"native wheel file: {name}",
        )
    verify_no_prohibited_content(files, "wheel")


def verify_sdist(files: Mapping[str, bytes]) -> None:
    verify_legal_payload(files)
    required = {
        "pyproject.toml",
        "compliance/external_assets.toml",
        "docs/legal/README.md",
        "scripts/check_compliance.py",
        "src/local_transcript_max/__init__.py",
    }
    missing = required.difference(files)
    require(not missing, f"sdist required content missing: {sorted(missing)}")
    verify_no_prohibited_content(files, "sdist")


def verify_native_bundle(
    archive: Path,
    *,
    root_name: str,
    expected_relative_content: Mapping[str, bytes],
) -> None:
    files = read_zip(archive) if archive.suffix == ".zip" else read_tar(archive)
    expected = {f"{root_name}/{name}": data for name, data in expected_relative_content.items()}
    require(set(files) == set(expected), f"{archive.name} member allowlist drift")
    for name, data in expected.items():
        require(files[name] == data, f"{archive.name} changed bundled file: {name}")
    verify_no_prohibited_content(files, archive.name)
    verify_legal_payload(files, f"{root_name}/")


def verify_sbom_bundle(archive: Path, expected_names: set[str]) -> None:
    files = read_zip(archive)
    require(set(files) == expected_names, "release SBOM member allowlist drift")
    verify_no_prohibited_content(files, "release SBOM")
    source = files["source.spdx"]
    require(b"SPDXVersion: SPDX-2." in source, "source SPDX is missing or malformed")
    require(b"DocumentName: Auralign" in source, "source SPDX document is missing")
    require(b"Creator: Tool: reuse-" in source, "source SPDX creator is missing")
    for name, data in files.items():
        if not name.endswith(".json"):
            continue
        try:
            payload = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid JSON SBOM: {name}") from exc
        require(isinstance(payload, dict), f"invalid JSON SBOM shape: {name}")
        if name.endswith(".spdx.json"):
            require(payload.get("spdxVersion") == "SPDX-2.3", f"invalid SPDX version: {name}")
            require(isinstance(payload.get("packages"), list), f"missing SPDX packages: {name}")
            require(isinstance(payload.get("files"), list), f"missing SPDX files: {name}")
        else:
            require(payload.get("bomFormat") == "CycloneDX", f"invalid CycloneDX BOM: {name}")
            require(isinstance(payload.get("specVersion"), str), f"missing BOM version: {name}")
            require(isinstance(payload.get("components"), list), f"missing BOM components: {name}")


def verify_sha256sums(path: Path, expected_names: set[str]) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if match is None:
            raise RuntimeError("malformed SHA256SUMS entry")
        digest, name = match.groups()
        require(name not in sums, f"duplicate SHA256SUMS entry: {name}")
        sums[name] = digest
    require(set(sums) == expected_names, "SHA256SUMS artifact allowlist drift")
    for name, digest in sums.items():
        require(
            sha((path.parent / name).read_bytes(), "sha256") == digest,
            f"hash mismatch: {name}",
        )
    return sums


def verify_private_release(
    release_dir: Path,
    *,
    version: str,
    commit: str,
    native_common_files: set[str],
) -> None:
    wheel_name = f"local_transcript_max-{version}-py3-none-any.whl"
    sdist_name = f"local_transcript_max-{version}.tar.gz"
    windows_name = f"Auralign-{version}-windows-amd64.zip"
    macos_name = f"Auralign-{version}-darwin-arm64.tar.gz"
    sbom_name = f"Auralign-{version}-sboms.zip"
    artifact_names = {
        wheel_name,
        sdist_name,
        windows_name,
        macos_name,
        "model-manifest.json",
        sbom_name,
        "release-manifest.json",
        "SHA256SUMS",
    }
    actual = {path.name for path in release_dir.iterdir() if path.is_file()}
    require(actual == artifact_names, "private release top-level artifact allowlist drift")
    require(
        not any(path.is_symlink() for path in release_dir.iterdir()),
        "private release must not contain links",
    )
    checksum_names = artifact_names - {"SHA256SUMS"}
    sums = verify_sha256sums(release_dir / "SHA256SUMS", checksum_names)

    manifest = json.loads((release_dir / "release-manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == "auralign-private-release.v1", "release schema")
    require(manifest.get("version") == version, "release version drift")
    require(manifest.get("commit") == commit, "release commit drift")
    require(manifest.get("repository_visibility") == "private", "release visibility drift")
    rows = manifest.get("artifacts")
    require(isinstance(rows, list), "release artifact manifest is malformed")
    payload_names = artifact_names - {"release-manifest.json", "SHA256SUMS"}
    require(
        {row.get("name") for row in rows if isinstance(row, dict)} == payload_names,
        "release payload allowlist drift",
    )
    for row in rows:
        require(isinstance(row, dict), "release artifact row is malformed")
        name = row.get("name")
        require(isinstance(name, str) and name in payload_names, "release artifact name invalid")
        target = release_dir / name
        require(row.get("size") == target.stat().st_size, f"release size mismatch: {name}")
        require(row.get("sha256") == sums[name], f"release digest mismatch: {name}")

    model_manifest = json.loads((release_dir / "model-manifest.json").read_text(encoding="utf-8"))
    require(
        model_manifest.get("schema_version") == "auralign-release-model-manifest.v1",
        "release model manifest schema drift",
    )
    models = model_manifest.get("models")
    require(isinstance(models, list) and len(models) == 4, "release model manifest is incomplete")
    expected_roles = {"asr", "diarization", "alignment", "speaker-embedding"}
    require(
        {row.get("role") for row in models if isinstance(row, dict)} == expected_roles,
        "release model roles drifted",
    )
    for row in models:
        require(isinstance(row, dict), "release model row is malformed")
        require(isinstance(row.get("key"), str), "release model key is missing")
        require(isinstance(row.get("model_id"), str), "release model ID is missing")
        require(re.fullmatch(r"[0-9a-f]{40}", str(row.get("revision"))) is not None, "revision")
        require(
            re.fullmatch(r"[0-9a-f]{64}", str(row.get("repository_manifest_digest")))
            is not None,
            "repository model digest is invalid",
        )
        runtime_files = row.get("runtime_files")
        require(isinstance(runtime_files, dict) and bool(runtime_files), "runtime files missing")
        require(
            all(
                isinstance(name, str)
                and re.fullmatch(r"[0-9a-f]{64}", str(digest)) is not None
                for name, digest in runtime_files.items()
            ),
            "runtime file digest is invalid",
        )

    verify_wheel(read_wheel(release_dir / wheel_name))
    verify_sdist(strip_sdist_root(read_sdist(release_dir / sdist_name)))

    common_content = {
        name: (release_dir / name).read_bytes()
        if name in {wheel_name, "model-manifest.json"}
        else (ROOT / name).read_bytes()
        for name in native_common_files
    }

    windows_files = {
        "install-windows.ps1",
        "bootstrap.ps1",
        "examples/config.strict-windows-amd64-cpu.yaml",
        "examples/config.strict-windows-amd64-cu128.yaml",
        "requirements/windows-cpu-py311.txt",
        "requirements/windows-cu128-py311.txt",
    }
    macos_files = {
        "install-macos.sh",
        "bootstrap.sh",
        "examples/config.strict-macos-arm64-cpu.yaml",
        "requirements/macos-arm64-cpu-py311.txt",
    }
    windows_content = common_content | {
        name: (ROOT / name).read_bytes() for name in windows_files
    }
    macos_content = common_content | {name: (ROOT / name).read_bytes() for name in macos_files}
    verify_native_bundle(
        release_dir / windows_name,
        root_name=f"Auralign-{version}-windows-amd64",
        expected_relative_content=windows_content,
    )
    verify_native_bundle(
        release_dir / macos_name,
        root_name=f"Auralign-{version}-darwin-arm64",
        expected_relative_content=macos_content,
    )
    verify_sbom_bundle(
        release_dir / sbom_name,
        {
            "source.spdx",
            "wheel.spdx.json",
            "sdist.spdx.json",
            "macos-arm64-cpu.cdx.json",
            "windows-amd64-cpu.cdx.json",
            "windows-amd64-cu128.cdx.json",
            "linux-amd64-cu126.cdx.json",
        },
    )


def spdx_id(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"SPDXRef-File-{digest}"


def project_version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = payload.get("project", {}).get("version")
    require(isinstance(version, str) and bool(version), "project version is missing")
    return cast(str, version)


def write_spdx(
    scope: str,
    archive: Path,
    files: Mapping[str, bytes],
    output: Path,
    *,
    version: str,
) -> None:
    archive_digest = sha(archive.read_bytes(), "sha256")
    spdx_files = []
    relationships = []
    sha1_values = []
    for name in sorted(files):
        data = files[name]
        identifier = spdx_id(name)
        sha1_value = sha(data, "sha1")
        sha1_values.append(sha1_value)
        spdx_files.append(
            {
                "SPDXID": identifier,
                "checksums": [
                    {"algorithm": "SHA1", "checksumValue": sha1_value},
                    {"algorithm": "SHA256", "checksumValue": sha(data, "sha256")},
                ],
                "copyrightText": "NOASSERTION",
                "fileName": f"./{name}",
                "licenseConcluded": "NOASSERTION",
                "licenseInfoInFiles": ["NOASSERTION"],
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package",
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": identifier,
            }
        )
    payload = {
        "SPDXID": "SPDXRef-DOCUMENT",
        "creationInfo": {
            "created": "1970-01-01T00:00:00Z",
            "creators": ["Tool: Auralign scripts/verify_distribution.py"],
        },
        "dataLicense": "CC0-1.0",
        "documentDescribes": ["SPDXRef-Package"],
        "documentNamespace": f"https://auralign.invalid/spdx/{scope}/{archive_digest}",
        "files": spdx_files,
        "hasExtractedLicensingInfos": [
            {
                "extractedText": (ROOT / "LICENSE").read_text(encoding="utf-8"),
                "licenseId": PROJECT_LICENSE,
                "name": "PolyForm Internal Use License 1.0.0 with Auralign required notice",
                "seeAlsos": ["https://polyformproject.org/licenses/internal-use/1.0.0"],
            }
        ],
        "name": f"Auralign-{scope}-{archive.name}",
        "packages": [
            {
                "SPDXID": "SPDXRef-Package",
                "copyrightText": "Copyright 2026 Matthew Penkala",
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": True,
                "licenseConcluded": PROJECT_LICENSE,
                "licenseDeclared": PROJECT_LICENSE,
                "name": archive.name,
                "packageVerificationCode": {
                    "packageVerificationCodeValue": sha(
                        "".join(sorted(sha1_values)).encode("ascii"), "sha1"
                    )
                },
                "versionInfo": version,
            }
        ],
        "relationships": relationships,
        "spdxVersion": "SPDX-2.3",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--sbom-dir", type=Path, default=ROOT / "build/sbom")
    args = parser.parse_args()
    wheels = sorted(args.dist_dir.glob("*.whl"))
    sdists = sorted(args.dist_dir.glob("*.tar.gz"))
    require(len(wheels) == 1, f"expected one wheel, found {len(wheels)}")
    require(len(sdists) == 1, f"expected one sdist, found {len(sdists)}")

    wheel_files = read_wheel(wheels[0])
    sdist_files = strip_sdist_root(read_sdist(sdists[0]))
    version = project_version()
    require(version in wheels[0].name, "wheel filename does not match the project version")
    require(version in sdists[0].name, "sdist filename does not match the project version")
    verify_wheel(wheel_files)
    verify_sdist(sdist_files)
    write_spdx(
        "wheel", wheels[0], wheel_files, args.sbom_dir / "wheel.spdx.json", version=version
    )
    write_spdx(
        "sdist", sdists[0], sdist_files, args.sbom_dir / "sdist.spdx.json", version=version
    )
    print(
        "distribution_compliance_ok=true "
        f"wheel_files={len(wheel_files)} sdist_files={len(sdist_files)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
