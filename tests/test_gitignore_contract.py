# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from local_transcript_max.cli import _CANONICAL_JOB_ARTIFACTS, _STRICT_VALIDATION_ARTIFACTS

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _load_distribution_verifier() -> ModuleType:
    script_path = SCRIPTS / "verify_distribution.py"
    scripts_path = str(SCRIPTS)
    inserted = scripts_path not in sys.path
    if inserted:
        sys.path.insert(0, scripts_path)
    try:
        spec = importlib.util.spec_from_file_location("verify_distribution", script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load distribution verifier")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if inserted:
            sys.path.remove(scripts_path)


verify_distribution = _load_distribution_verifier()


def test_private_job_artifact_contract_is_git_ignored() -> None:
    artifacts = [Path("arbitrary-job") / name for name in _CANONICAL_JOB_ARTIFACTS]
    for name, kind in _STRICT_VALIDATION_ARTIFACTS:
        path = Path("arbitrary-job") / name
        artifacts.append(path / "candidate_001.json" if kind == "directory" else path)

    for artifact in artifacts:
        completed = subprocess.run(
            ["git", "check-ignore", "--no-index", "-q", "--", str(artifact)],
            cwd=REPO_ROOT,
            check=False,
        )
        assert completed.returncode == 0, f"private generated artifact is not ignored: {artifact}"


@pytest.mark.parametrize(
    "path",
    [
        "models/Systran--faster-whisper-large-v3/model.bin",
        ".hf-cache/hub/models--Systran--faster-whisper-large-v3/snapshots/rev/model.bin",
        ".huggingface/hub/models--example/snapshots/rev/model.safetensors",
        "huggingface/hub/models--example/blobs/object",
        "hf-cache/hub/models--example/refs/main",
        "stray/model.bin",
        "stray/pytorch_model.bin",
        "stray/model.safetensors",
        "stray/weights.safetensors",
        "stray/weights.npz",
        "stray/plda.npz",
        "stray/xvec_transform.npz",
        "stray/model_audit.json",
        "stray/.ltm-model.json",
        "private-capsule/canary-10m.mp4",
        "private-capsule/enrollment-speaker-00.wav",
        "private-capsule/private-source.aiff",
        "private-capsule/private-source.avi",
        "private-capsule/private-source.m4v",
        "private-capsule/private-source.mxf",
        "private-capsule/private-source.wma",
        "private-capsule/validation-capsule.json",
        "private-capsule/SHA256SUMS",
    ],
)
def test_model_caches_weights_and_audits_are_git_ignored(path: str) -> None:
    completed = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", "--", path],
        cwd=REPO_ROOT,
        check=False,
    )
    assert completed.returncode == 0, f"model artifact is not ignored: {path}"


def test_git_index_has_no_symlinks_or_model_artifacts() -> None:
    completed = subprocess.run(
        ["git", "ls-files", "-s"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    prohibited_names = {
        ".ltm-model.json",
        "model.bin",
        "model.safetensors",
        "model_audit.json",
        "plda.npz",
        "pytorch_model.bin",
        "weights.npz",
        "weights.safetensors",
        "xvec_transform.npz",
    }
    for line in completed.stdout.splitlines():
        metadata, path = line.split("\t", maxsplit=1)
        mode = metadata.split(maxsplit=1)[0]
        assert mode != "120000", f"tracked symlink is prohibited: {path}"
        assert Path(path).name not in prohibited_names, f"tracked model artifact: {path}"


@pytest.mark.parametrize(
    "archive_path",
    [
        "models/example/model.bin",
        ".hf-cache/hub/snapshots/rev/model.bin",
        "private/model.safetensors",
        "private/weights.npz",
        "private/model_audit.json",
        "private/.ltm-model.json",
        "profiles/speaker.npz",
        "outputs/job/transcript.json",
        "private/validation-capsule.json",
        "private/SHA256SUMS",
        "private/source.aiff",
        "private/source.avi",
        "private/source.m4v",
        "private/source.mxf",
        "private/source.wma",
    ],
)
def test_distribution_verifier_rejects_model_and_private_artifacts(
    archive_path: str,
) -> None:
    with pytest.raises(RuntimeError):
        verify_distribution.verify_no_prohibited_content(
            {archive_path: b"synthetic-placeholder"},
            "synthetic",
        )


def test_distribution_verifier_accepts_only_reviewed_swift_models_namespace() -> None:
    reviewed_source = "macos/Auralign/Models/ReadinessProjection.swift"
    verify_distribution.verify_no_prohibited_content(
        {reviewed_source: b"reviewed Swift source"},
        "synthetic",
    )

    for unreviewed_path in (
        "macos/Auralign/Models/Unreviewed.swift",
        "macos/Auralign/Models/weights.safetensors",
        "macos/OtherApp/Models/ReadinessProjection.swift",
        "models/ReadinessProjection.swift",
        "src/models/ReadinessProjection.swift",
    ):
        with pytest.raises(RuntimeError, match="private path"):
            verify_distribution.verify_no_prohibited_content(
                {unreviewed_path: b"synthetic-placeholder"},
                "synthetic",
            )


def test_distribution_token_scan_requires_a_real_credential_boundary() -> None:
    verify_distribution.verify_no_prohibited_content(
        {
            "license.txt": b"Asterisk-linking-protocols-exception",
            "encoded.txt": b"base64prefixaAKIAAQAAAAAABAAHAM0A",
        },
        "synthetic",
    )

    with pytest.raises(RuntimeError, match="token"):
        verify_distribution.verify_no_prohibited_content(
            {"payload.txt": b"token=hf_" + b"a" * 26},
            "synthetic",
        )
