# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pytest import MonkeyPatch

from local_transcript_max.config import resolve_model_entry

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
environment_sbom = _load_script("generate_environment_sbom")
dependency_sbom = _load_script("validate_dependency_sbom")


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
            "end_seconds": validation_capsule.CANARY_START + validation_capsule.CANARY_DURATION,
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
            "model_revision": resolve_model_entry("speaker-embedding", "ecapa").revision,
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
            "schema_version": "speaker_map.v2",
            "threshold": 0.75,
            "ambiguity_margin": 0.05,
            "mappings": [
                {
                    "speaker": f"SPEAKER_{index:02d}",
                    "label": f"canary-speaker-{index:02d}",
                    "confidence": 1.0,
                    "ambiguous": False,
                    "source_profile_id": f"canary-speaker-{index:02d}",
                    "profile_backend": "speechbrain-ecapa",
                    "reason": "conservative_embedding_match",
                }
                for index in range(2)
            ],
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
    profiles = {
        "canary-speaker-00": ("speechbrain-ecapa", [1.0, 0.0]),
        "canary-speaker-01": ("speechbrain-ecapa", [0.0, 1.0]),
    }
    trusted_validation.assert_real_outputs(
        job,
        profile=profile,
        enrolled_profiles=profiles,
    )

    _write_json(job / "speaker_cluster_embeddings.json", {"SPEAKER_00": [2.0, 0.0]})
    with pytest.raises(RuntimeError, match="cover|unit-normalized"):
        trusted_validation.assert_real_outputs(
            job,
            profile=profile,
            enrolled_profiles=profiles,
        )


def test_real_acceptance_rejects_swapped_identity_labels(tmp_path: Path) -> None:
    job = tmp_path / "job"
    _valid_real_job(job, device="cpu", compute_type="int8")
    speaker_map = json.loads((job / "speaker_map.json").read_text(encoding="utf-8"))
    for row in speaker_map["mappings"]:
        index = 1 if row["speaker"] == "SPEAKER_00" else 0
        row["label"] = f"canary-speaker-{index:02d}"
        row["source_profile_id"] = f"canary-speaker-{index:02d}"
    _write_json(job / "speaker_map.json", speaker_map)

    with pytest.raises(RuntimeError, match="disagrees with independent"):
        trusted_validation.assert_real_outputs(
            job,
            profile="cpu",
            enrolled_profiles={
                "canary-speaker-00": ("speechbrain-ecapa", [1.0, 0.0]),
                "canary-speaker-01": ("speechbrain-ecapa", [0.0, 1.0]),
            },
        )


def test_native_install_and_workflow_contracts_are_location_stable_and_checked() -> None:
    windows = (REPO_ROOT / "install-windows.ps1").read_text(encoding="utf-8")
    macos = (REPO_ROOT / "install-macos.sh").read_text(encoding="utf-8")
    bootstrap_windows = (REPO_ROOT / "bootstrap.ps1").read_text(encoding="utf-8")
    bootstrap_macos = (REPO_ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    workflow = (REPO_ROOT / ".github/workflows/trusted-windows-validation.yml").read_text(
        encoding="utf-8"
    )

    assert "function Invoke-Native" in windows
    assert "Test-Cuda128Prerequisites" in windows
    assert "-SourceCheckout" in windows
    assert 'Join-Path $PSScriptRoot ".auralign-runtime"' in windows
    assert '"--index-strategy", "unsafe-best-match"' in windows
    assert '"--require-hashes"' in windows
    assert "--source-checkout" in macos
    assert "$script_dir/.auralign-runtime" in macos
    assert "--index-strategy unsafe-best-match" in macos
    assert "--require-hashes" in macos
    assert 'if [ -n "${BASH_VERSION:-}" ]' in macos
    assert 'elif [ -n "${ZSH_VERSION:-}" ]' in macos
    assert "/" + "Users/" not in macos and "/" + "home/" not in macos
    assert "[string]$RuntimeDir" in bootstrap_windows
    assert "--runtime-dir" in bootstrap_macos
    assert "LTM_MODEL_CACHE" in workflow
    assert "LTM_ACCEPTANCE_EVIDENCE_DIR" in workflow
    assert "generate_environment_sbom.py" in workflow
    assert "Provision exact locked profile outside checkout" in workflow
    assert "--no-sync" not in workflow
    assert 'PYTHONDONTWRITEBYTECODE: "1"' in workflow
    assert "& $python -B scripts/run_trusted_platform_validation.py" in workflow
    assert "& $toolPython -B scripts/generate_environment_sbom.py" in workflow
    assert "& $toolPython -B scripts/validate_dependency_sbom.py" in workflow
    assert "& $python scripts/run_trusted_platform_validation.py" not in workflow
    assert "& $toolPython scripts/generate_environment_sbom.py" not in workflow
    assert "& $toolPython scripts/validate_dependency_sbom.py" not in workflow
    validator = (REPO_ROOT / "scripts/run_trusted_platform_validation.py").read_text(
        encoding="utf-8"
    )
    assert 'profile_dir=work / "profiles"' in validator
    assert 'os.environ.get("LTM_SPEAKER_PROFILE_DIR")' not in validator


def test_trusted_validation_python_probe_forces_no_bytecode(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(trusted_validation.subprocess, "run", fake_run)
    environment = {"EXISTING": "preserved"}

    trusted_validation.validate_cuda(environment)

    command = captured["command"]
    child_environment = captured["environment"]
    assert isinstance(command, list)
    assert command[:3] == [sys.executable, "-B", "-c"]
    assert isinstance(child_environment, dict)
    assert child_environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert child_environment["EXISTING"] == "preserved"
    assert "PYTHONDONTWRITEBYTECODE" not in environment


def test_environment_sbom_target_probe_forces_no_bytecode(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        stdout = json.dumps({"machine": "arm64", "python": "3.11.15", "sys_platform": "darwin"})
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(environment_sbom.subprocess, "run", fake_run)
    python = Path(sys.executable)

    runtime = environment_sbom.target_runtime(python)

    command = captured["command"]
    child_environment = captured["environment"]
    assert runtime["python"] == "3.11.15"
    assert isinstance(command, list)
    assert command[:3] == [str(python), "-B", "-c"]
    assert isinstance(child_environment, dict)
    assert child_environment["PYTHONDONTWRITEBYTECODE"] == "1"


def test_environment_sbom_generator_forces_no_bytecode(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        environment_sbom,
        "target_runtime",
        lambda _python: {
            "machine": "arm64",
            "python": "3.11.15",
            "sys_platform": "darwin",
        },
    )

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        raw = Path(command[command.index("--output-file") + 1])
        raw.write_text(
            json.dumps(
                {
                    "bomFormat": "CycloneDX",
                    "components": [],
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(environment_sbom.subprocess, "run", fake_run)
    output = tmp_path / "environment.cdx.json"

    count = environment_sbom.generate(
        python=Path("/controlled/runtime/bin/python3.11"),
        platform_name="darwin-arm64-cpu",
        profile="cpu",
        commit="a" * 40,
        output=output,
    )

    command = captured["command"]
    child_environment = captured["environment"]
    assert count == 0
    assert output.is_file()
    assert isinstance(command, list)
    assert command[:4] == [sys.executable, "-B", "-m", "cyclonedx_py"]
    assert isinstance(child_environment, dict)
    assert child_environment["PYTHONDONTWRITEBYTECODE"] == "1"


def test_platform_acceptance_schema_rejects_cross_platform_field_mix() -> None:
    schema = json.loads(
        (REPO_ROOT / "docs/schemas/platform-acceptance-v2.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    payload = {
        "schema_version": "auralign-platform-acceptance.v2",
        "platform": "windows-amd64-cu128",
        "profile": "cu128",
        "commit": "a" * 40,
        "passed": True,
        "owner_attested": True,
        "completed_at": "2026-07-13T00:00:00Z",
        "python_version": "3.11",
        "validator": "scripts/run_trusted_platform_validation.py",
        "validator_sha256": "b" * 64,
        "config": "examples/config.strict-windows-amd64-cu128.yaml",
        "config_sha256": "c" * 64,
        "model_contract_sha256": "d" * 64,
        "capsule_id": "auralign-validation-capsule.v1:5a652951669c",
        "dependency_sbom": "windows-amd64-cu128.cdx.json",
        "dependency_sbom_sha256": "e" * 64,
    }

    assert not list(validator.iter_errors(payload))
    payload["profile"] = "cpu"
    assert list(validator.iter_errors(payload))
    payload["profile"] = "cu128"
    payload["dependency_sbom"] = "darwin-arm64-cpu.cdx.json"
    assert list(validator.iter_errors(payload))


def test_installed_environment_sbom_is_path_redacted_and_platform_bound(
    tmp_path: Path,
) -> None:
    commit = "a" * 40
    versions = dependency_sbom.NATIVE_PACKAGE_VERSIONS["windows-amd64-cpu"]
    packages = sorted(versions)
    components = [
        {
            "bom-ref": f"{name}=={versions[name]}",
            "name": name,
            "version": versions[name],
        }
        for name in packages
    ]
    components[packages.index("local-transcript-max")]["externalReferences"] = [
        {"type": "distribution", "url": "file:///private/source/checkout"}
    ]
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"properties": [{"name": "cdx:reproducible", "value": "true"}]},
        "components": components,
        "dependencies": [{"ref": item["bom-ref"], "dependsOn": []} for item in components],
    }
    environment_sbom.bind_environment(
        payload,
        platform_name="windows-amd64-cpu",
        profile="cpu",
        commit=commit,
        runtime={"machine": "amd64", "python": "3.11.13", "sys_platform": "win32"},
    )
    path = tmp_path / "windows-amd64-cpu.cdx.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    dependency_sbom.validate(
        path,
        "cpu",
        platform_name="windows-amd64-cpu",
        commit=commit,
    )
    assert "file://" not in path.read_text(encoding="utf-8")

    payload["dependencies"].append(dict(payload["dependencies"][0]))  # type: ignore[index,union-attr]
    with pytest.raises(RuntimeError, match="duplicate dependency reference"):
        dependency_sbom.validate_payload(
            payload,
            "cpu",
            label="synthetic",
            platform_name="windows-amd64-cpu",
            commit=commit,
        )
    payload["dependencies"].pop()  # type: ignore[union-attr]

    payload["components"].append(  # type: ignore[union-attr]
        {"bom-ref": "torch-duplicate", "name": "Torch", "version": "2.8.0"}
    )
    payload["dependencies"].append({"ref": "torch-duplicate", "dependsOn": []})  # type: ignore[union-attr]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicate package"):
        dependency_sbom.validate(
            path,
            "cpu",
            platform_name="windows-amd64-cpu",
            commit=commit,
        )


def test_installed_environment_sbom_rejects_version_and_package_set_drift(
    tmp_path: Path,
) -> None:
    commit = "a" * 40
    platform_name = "windows-amd64-cpu"
    versions = dependency_sbom.NATIVE_PACKAGE_VERSIONS[platform_name]
    components = [
        {"bom-ref": f"{name}=={version}", "name": name, "version": version}
        for name, version in sorted(versions.items())
    ]
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "properties": [
                {"name": "auralign:commit", "value": commit},
                {"name": "auralign:platform", "value": platform_name},
                {"name": "auralign:profile", "value": "cpu"},
                {"name": "auralign:python-version", "value": "3.11.13"},
                {"name": "auralign:sbom-kind", "value": "installed-environment"},
            ]
        },
        "components": components,
        "dependencies": [{"ref": row["bom-ref"], "dependsOn": []} for row in components],
    }
    path = tmp_path / "native.cdx.json"

    wrong = next(row for row in components if row["name"] == "transformers")
    wrong["version"] = "999.0"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="version drift"):
        dependency_sbom.validate_payload(
            payload,
            "cpu",
            label=str(path),
            platform_name=platform_name,
            commit=commit,
        )

    wrong["version"] = versions["transformers"]
    payload["components"].append(
        {"bom-ref": "unexpected==1", "name": "unexpected-package", "version": "1"}
    )
    payload["dependencies"].append({"ref": "unexpected==1", "dependsOn": []})
    with pytest.raises(RuntimeError, match="unexpected native runtime packages"):
        dependency_sbom.validate_payload(
            payload,
            "cpu",
            label=str(path),
            platform_name=platform_name,
            commit=commit,
        )


@pytest.mark.parametrize(
    "private_path",
    [
        "/" + "Users/privateowner",
        "/" + "home/privateowner",
        "C:" + "\\Users\\privateowner",
    ],
)
def test_installed_environment_sbom_rejects_terminal_home_paths(
    private_path: str,
) -> None:
    commit = "a" * 40
    platform_name = "windows-amd64-cpu"
    versions = dependency_sbom.NATIVE_PACKAGE_VERSIONS[platform_name]
    components = [
        {"bom-ref": f"{name}=={version}", "name": name, "version": version}
        for name, version in sorted(versions.items())
    ]
    components[0]["description"] = private_path
    payload = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {
            "properties": [
                {"name": "auralign:commit", "value": commit},
                {"name": "auralign:platform", "value": platform_name},
                {"name": "auralign:profile", "value": "cpu"},
                {"name": "auralign:python-version", "value": "3.11.13"},
                {"name": "auralign:sbom-kind", "value": "installed-environment"},
            ]
        },
        "components": components,
        "dependencies": [{"ref": row["bom-ref"], "dependsOn": []} for row in components],
    }
    with pytest.raises(RuntimeError, match="private home path leak"):
        dependency_sbom.validate_payload(
            payload,
            "cpu",
            label="synthetic",
            platform_name=platform_name,
            commit=commit,
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtual-environment launcher contract")
def test_environment_sbom_cli_preserves_virtual_environment_launcher_symlink(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    launcher = tmp_path / "runtime" / "bin" / "python"
    launcher.parent.mkdir(parents=True)
    launcher.symlink_to(sys.executable)
    output = tmp_path / "evidence" / "darwin-arm64-cpu.cdx.json"
    captured: dict[str, Path] = {}

    def fake_generate(**kwargs):
        captured["python"] = kwargs["python"]
        return 1

    monkeypatch.setattr(environment_sbom, "generate", fake_generate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_environment_sbom.py",
            "--python",
            str(launcher),
            "--platform",
            "darwin-arm64-cpu",
            "--profile",
            "cpu",
            "--commit",
            "a" * 40,
            "--output",
            str(output),
        ],
    )

    assert environment_sbom.main() == 0
    assert captured["python"] == launcher.absolute()
    assert captured["python"] != launcher.resolve()


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
        (REPO_ROOT / "docs/schemas/validation-capsule-v1.schema.json").read_text(encoding="utf-8")
    )
    serialized = json.dumps(schema, sort_keys=True)
    for filename, digest in validation_capsule.EXPECTED_FILE_SHA256.items():
        if filename != "validation-capsule.json":
            assert filename in serialized
            assert digest in serialized
