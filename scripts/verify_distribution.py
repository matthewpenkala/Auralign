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
import tarfile
import zipfile
from collections.abc import Mapping
from email.parser import BytesParser
from pathlib import Path, PurePosixPath

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
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".p12",
    ".pem",
    ".pfx",
    ".rttm",
    ".srt",
    ".vtt",
    ".wav",
    ".webm",
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
            files[info.filename] = archive.read(info)
    return files


def read_sdist(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, mode="r:gz") as archive:
        files: dict[str, bytes] = {}
        for member in archive.getmembers():
            safe_path(member.name)
            require(not member.issym() and not member.islnk(), f"archive link: {member.name}")
            if not member.isfile():
                continue
            handle = archive.extractfile(member)
            require(handle is not None, f"unreadable sdist member: {member.name}")
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


def spdx_id(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"SPDXRef-File-{digest}"


def write_spdx(scope: str, archive: Path, files: Mapping[str, bytes], output: Path) -> None:
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
                "versionInfo": "0.1.0",
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
    verify_wheel(wheel_files)
    verify_sdist(sdist_files)
    write_spdx("wheel", wheels[0], wheel_files, args.sbom_dir / "wheel.spdx.json")
    write_spdx("sdist", sdists[0], sdist_files, args.sbom_dir / "sdist.spdx.json")
    print(
        "distribution_compliance_ok=true "
        f"wheel_files={len(wheel_files)} sdist_files={len(sdist_files)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
