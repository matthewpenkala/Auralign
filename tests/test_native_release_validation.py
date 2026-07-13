# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pytest import MonkeyPatch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


validation_capsule = _load_script("validation_capsule")
trusted_validation = _load_script("run_trusted_platform_validation")


def _fixed_manifest() -> dict[str, object]:
    return {
        "schema_version": "auralign-validation-capsule.v1",
        "source": {
            "size_bytes": validation_capsule.SOURCE_SIZE,
            "sha256": validation_capsule.SOURCE_SHA256,
        },
        "canary": {
            "file": "canary-10m.mp4",
            "start_seconds": validation_capsule.CANARY_START,
            "end_seconds": validation_capsule.CANARY_START
            + validation_capsule.CANARY_DURATION,
            "expected_speaker_count": validation_capsule.EXPECTED_SPEAKERS,
            "sha256": validation_capsule.EXPECTED_FILE_SHA256["canary-10m.mp4"],
            "media": validation_capsule.EXPECTED_MEDIA["canary-10m.mp4"],
        },
        "enrollments": [
            {
                "file": filename,
                "intervals_seconds": [[start, end] for start, end in intervals],
                "sha256": validation_capsule.EXPECTED_FILE_SHA256[filename],
                "media": validation_capsule.EXPECTED_MEDIA[filename],
            }
            for filename, intervals in validation_capsule.ENROLLMENT_INTERVALS.items()
        ],
        "tooling": {
            "ffmpeg_version": "ffmpeg version reviewed",
            "ffmpeg_build": "built with reviewed compiler",
            "canary_command": validation_capsule.EXPECTED_CANARY_COMMAND,
        },
    }


def _write_capsule_fixture(path: Path, manifest: dict[str, object]) -> None:
    path.mkdir()
    for filename in validation_capsule.CAPSULE_FILES:
        (path / filename).write_bytes(b"fixture")
    (path / "validation-capsule.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "SHA256SUMS").write_text(
        "".join(
            f"{digest}  {filename}\n"
            for filename, digest in validation_capsule.EXPECTED_FILE_SHA256.items()
        ),
        encoding="utf-8",
    )


def test_capsule_verifier_requires_fixed_hashes_manifest_and_ffprobe(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    capsule = tmp_path / "capsule"
    manifest = _fixed_manifest()
    _write_capsule_fixture(capsule, manifest)
    monkeypatch.setattr(
        validation_capsule,
        "sha256_file",
        lambda path: validation_capsule.EXPECTED_FILE_SHA256[path.name],
    )
    monkeypatch.setattr(
        validation_capsule,
        "normalized_media_metadata",
        lambda path: validation_capsule.EXPECTED_MEDIA[path.name],
    )

    validation_capsule.verify(capsule)

    manifest["canary"] = {**manifest["canary"], "file": "substitute.mp4"}  # type: ignore[dict-item]
    (capsule / "validation-capsule.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="canary contract"):
        validation_capsule.verify(capsule)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _valid_real_job(job: Path, *, device: str, compute_type: str) -> None:
    job.mkdir()
    speakers = ["SPEAKER_00", "SPEAKER_01"]
    _write_json(
        job / "run_metadata.json",
        {
            "alignment_backend": "whisperx-forced",
            "alignment_device": device,
            "compute_type": compute_type,
            "device": device,
            "diarization_resolved_device": device,
            "diarization_device_fallback": False,
        },
    )
    turns = [
        {"speaker": speaker, "start": index, "end": index + 1}
        for index, speaker in enumerate(speakers)
    ]
    _write_json(
        job / "transcript.json",
        {
            "words": [{"start": 0.0, "end": 0.5, "alignment_source": "forced"}],
            "exclusive_diarization_turns": turns,
        },
    )
    _write_json(job / "diarization.json", {"turns": turns})
    _write_json(job / "exclusive_diarization.json", {"turns": turns})
    _write_json(
        job / "speaker_embedding_report.json",
        {
            "device": device,
            "speakers": {speaker: {"embedding_dimensions": 2} for speaker in speakers},
        },
    )
    _write_json(
        job / "speaker_cluster_embeddings.json",
        {"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.0, 1.0]},
    )
    _write_json(
        job / "speaker_map.json",
        {
            "mappings": [
                {"label": "canary-speaker-00"},
                {"label": "canary-speaker-01"},
            ]
        },
    )


@pytest.mark.parametrize(
    ("profile", "device", "compute_type"),
    [("cpu", "cpu", "int8"), ("cu128", "cuda", "float16")],
)
def test_real_acceptance_requires_exact_profile_metadata_and_normalized_vectors(
    tmp_path: Path, profile: str, device: str, compute_type: str
) -> None:
    job = tmp_path / "job"
    _valid_real_job(job, device=device, compute_type=compute_type)
    trusted_validation.assert_real_outputs(job, profile=profile)

    _write_json(job / "speaker_cluster_embeddings.json", {"SPEAKER_00": [2.0, 0.0]})
    with pytest.raises(RuntimeError, match="cover|unit-normalized"):
        trusted_validation.assert_real_outputs(job, profile=profile)


def test_native_install_and_workflow_contracts_are_location_stable_and_checked() -> None:
    windows = (REPO_ROOT / "install-windows.ps1").read_text(encoding="utf-8")
    macos = (REPO_ROOT / "install-macos.sh").read_text(encoding="utf-8")
    bootstrap_windows = (REPO_ROOT / "bootstrap.ps1").read_text(encoding="utf-8")
    bootstrap_macos = (REPO_ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    workflow = (
        REPO_ROOT / ".github/workflows/trusted-windows-validation.yml"
    ).read_text(encoding="utf-8")

    assert "function Invoke-Native" in windows
    assert "Test-Cuda128Prerequisites" in windows
    assert "-SourceCheckout" in windows
    assert 'Join-Path $PSScriptRoot ".auralign-runtime"' in windows
    assert "--source-checkout" in macos
    assert '$script_dir/.auralign-runtime' in macos
    assert "/Users/" not in macos and "/home/" not in macos
    assert "[string]$RuntimeDir" in bootstrap_windows
    assert "--runtime-dir" in bootstrap_macos
    assert "LTM_MODEL_CACHE" in workflow
    assert "Provision exact locked profile outside checkout" in workflow
    assert "--no-sync" not in workflow


@pytest.mark.parametrize(
    "arguments",
    [
        ["--config"],
        ["--cache-dir", "--repair"],
        ["--runtime-dir", "--source-checkout"],
    ],
)
def test_macos_bootstrap_rejects_missing_option_values(arguments: list[str]) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable on this portable test host")

    completed = subprocess.run(
        [bash, str(REPO_ROOT / "bootstrap.sh"), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "requires a value" in completed.stderr


def test_capsule_schema_pins_all_three_media_files() -> None:
    schema = json.loads(
        (REPO_ROOT / "docs/schemas/validation-capsule-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(schema, sort_keys=True)
    for filename, digest in validation_capsule.EXPECTED_FILE_SHA256.items():
        if filename != "validation-capsule.json":
            assert filename in serialized
            assert digest in serialized
