# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import stat
import sys
import wave
from pathlib import Path
from types import ModuleType

import pytest

from local_transcript_max.config import MODEL_MANIFEST, ModelEntry
from local_transcript_max.model_delivery import NORMAL_PRESET_MODELS

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_native_model_acceptance.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_native_model_acceptance", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


runner = load_script()


def native_entries() -> tuple[ModelEntry, ...]:
    return tuple(
        entry for entry in MODEL_MANIFEST.values() if entry.delivery == "native-download"
    )


def fake_entry(content: bytes = b"exact model bytes\n") -> ModelEntry:
    digest = hashlib.sha256(content).hexdigest()
    return ModelEntry(
        key="fixture",
        role="asr",
        model_id="example/fixture",
        required_files=("nested/model.bin",),
        expected_sha256=(("nested/model.bin", digest),),
        expected_size=(("nested/model.bin", len(content)),),
        revision="a" * 40,
        license_expression="MIT",
        source_url="https://example.invalid/fixture",
        terms_url="https://example.invalid/fixture",
        attribution="Fixture model.",
        acquisition_status="approved_exact_pinned_files",
        runtime_status="approved_local_real_inference",
        redistribution_status="permitted_with_license_terms",
        review_status="verified_exact_files_and_inference",
        reviewed_at="2026-07-25",
        product_classification="optional",
        blocker=None,
        backend_compatibility="fixture backend",
        modification_status="unmodified exact pinned runtime files",
        package_bundle_id="com.auralign.studio.modelpack.fixture.v1",
        package_bundle_version="1.0.0",
        package_relative_path="models/example--fixture",
        delivery="native-download",
    )


def test_acceptance_plan_closes_all_sixteen_packs_and_four_presets() -> None:
    entries = runner.native_download_entries()
    cases = runner.acceptance_cases()

    assert len(entries) == 16
    assert {entry.key for entry in entries} == {
        entry.key for entry in native_entries()
    }
    assert len(cases) == 20
    assert [case.model_key for case in cases[:16]] == [entry.key for entry in entries]
    assert {
        case.preset: case.model_key for case in cases[16:]
    } == NORMAL_PRESET_MODELS


def test_work_directory_normalizes_inherited_group_before_children(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    alternate_groups = tuple(
        group for group in os.getgroups() if group != os.getegid()
    )
    if not alternate_groups:
        pytest.skip("current process has no safe alternate group")
    work_dir = tmp_path / "acceptance"
    real_ensure = runner.ensure_private_directory

    def ensure_with_inherited_group(path: Path) -> Path:
        directory = real_ensure(path)
        try:
            os.chown(
                directory,
                os.geteuid(),
                alternate_groups[0],
                follow_symlinks=False,
            )
        except (NotImplementedError, PermissionError):
            pytest.skip("current process cannot safely simulate an inherited group")
        return directory

    monkeypatch.setattr(
        runner,
        "ensure_private_directory",
        ensure_with_inherited_group,
    )

    created = runner.create_acceptance_work_directory(work_dir)
    metadata = created.lstat()

    assert created == work_dir
    assert stat.S_ISDIR(metadata.st_mode)
    assert metadata.st_uid == os.geteuid()
    assert metadata.st_gid == os.getegid()
    assert stat.S_IMODE(metadata.st_mode) == 0o700


def test_source_roots_resolve_exactly_once_without_aggregate_cache(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)
    entries = native_entries()
    for index, entry in enumerate(entries):
        root = first if index % 2 == 0 else second
        (root / entry.directory_name).mkdir(mode=0o700)

    resolved = runner.resolve_source_models([first, second])

    assert set(resolved) == {entry.key for entry in entries}
    assert all(path.parent in {first, second} for path in resolved.values())


def test_source_roots_fail_closed_on_missing_or_duplicate_key(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)
    entries = native_entries()
    for entry in entries:
        (first / entry.directory_name).mkdir(mode=0o700)

    duplicate = entries[0]
    (second / duplicate.key).mkdir(mode=0o700)
    with pytest.raises(runner.NativeModelAcceptanceError, match="ambiguous"):
        runner.resolve_source_models([first, second])

    (second / duplicate.key).rmdir()
    (first / entries[-1].directory_name).rmdir()
    with pytest.raises(runner.NativeModelAcceptanceError, match="missing"):
        runner.resolve_source_models([first, second])


def test_acquisition_marker_is_filtered_from_exact_transient_tree(tmp_path: Path) -> None:
    content = b"exact model bytes\n"
    entry = fake_entry(content)
    source = tmp_path / "source"
    (source / "nested").mkdir(mode=0o700, parents=True)
    (source / "nested" / "model.bin").write_bytes(content)
    (source / "nested" / "model.bin").chmod(0o600)
    (source / ".ltm-model.json").write_text(
        json.dumps({"private": "acquisition metadata"}),
        encoding="utf-8",
    )
    (source / ".ltm-model.json").chmod(0o600)
    destination = tmp_path / "exact"

    assert runner._classify_source_tree(source, entry) == "acquisition-cache"
    runner._copy_exact_model_tree(source, destination, entry)

    assert runner._classify_source_tree(destination, entry) == "exact"
    assert (destination / "nested" / "model.bin").read_bytes() == content
    assert not (destination / ".ltm-model.json").exists()


def test_exact_source_with_alternate_group_uses_unchanged_transient_view(
    tmp_path: Path,
) -> None:
    alternate_groups = tuple(
        group for group in os.getgroups() if group != os.getegid()
    )
    if not alternate_groups:
        pytest.skip("current process has no safe alternate group")
    content = b"exact model bytes\n"
    entry = fake_entry(content)
    source = tmp_path / "source"
    nested = source / "nested"
    model = nested / "model.bin"
    nested.mkdir(mode=0o755, parents=True)
    model.write_bytes(content)
    model.chmod(0o644)
    try:
        os.chown(source, os.geteuid(), alternate_groups[0], follow_symlinks=False)
        os.chown(nested, os.geteuid(), alternate_groups[0], follow_symlinks=False)
        os.chown(model, os.geteuid(), alternate_groups[0], follow_symlinks=False)
    except (NotImplementedError, PermissionError):
        pytest.skip("current process cannot safely simulate an inherited group")
    before = {
        path: (
            path.lstat().st_uid,
            path.lstat().st_gid,
            stat.S_IMODE(path.lstat().st_mode),
            path.lstat().st_size,
        )
        for path in (source, nested, model)
    }
    work_root = runner.create_acceptance_work_directory(tmp_path / "work")
    destination = work_root / "exact"

    assert runner._classify_source_tree(source, entry) == "exact"
    assert runner._source_requires_transient_view(source, entry) is True
    runner._copy_exact_model_tree(source, destination, entry)

    assert runner._classify_source_tree(destination, entry) == "exact"
    assert runner._source_requires_transient_view(destination, entry) is False
    assert (destination / "nested" / "model.bin").read_bytes() == content
    assert {
        path: (
            path.lstat().st_uid,
            path.lstat().st_gid,
            stat.S_IMODE(path.lstat().st_mode),
            path.lstat().st_size,
        )
        for path in (source, nested, model)
    } == before


def test_model_source_rejects_links_and_unexpected_content(tmp_path: Path) -> None:
    content = b"exact model bytes\n"
    entry = fake_entry(content)
    source = tmp_path / "source"
    (source / "nested").mkdir(mode=0o700, parents=True)
    model = source / "nested" / "model.bin"
    model.write_bytes(content)
    model.chmod(0o600)
    unexpected = source / "debug.txt"
    unexpected.write_text("debug", encoding="utf-8")
    unexpected.chmod(0o600)

    with pytest.raises(runner.NativeModelAcceptanceError, match="file contract"):
        runner._classify_source_tree(source, entry)

    unexpected.unlink()
    model.unlink()
    model.symlink_to(source / "missing")
    with pytest.raises(runner.NativeModelAcceptanceError, match="linked"):
        runner._classify_source_tree(source, entry)


def test_worker_environment_removes_credentials_proxies_and_overrides(tmp_path: Path) -> None:
    case = runner.CaseSpec(case_kind="model", model_key="large-v3")
    environment = runner.worker_environment(
        case,
        packs_root=tmp_path / "packs",
        audio_path=tmp_path / "audio.wav",
        result_path=tmp_path / "result.json",
        base={
            "HF_TOKEN": "secret",
            "HTTPS_PROXY": "https://proxy.invalid",
            "LTM_ASR_MODEL": "unreviewed",
            "LTM_ASR_BACKEND": "mlx-whisper",
            "KEEP": "value",
        },
    )

    assert "HF_TOKEN" not in environment
    assert "HTTPS_PROXY" not in environment
    assert "LTM_ASR_BACKEND" not in environment
    assert "LTM_ASR_MODEL" not in environment
    assert environment["LTM_OFFLINE"] == "1"
    assert environment["HF_HUB_OFFLINE"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["KEEP"] == "value"
    assert environment["AURALIGN_ACCEPTANCE_MODEL_KEY"] == "large-v3"


def test_network_guard_denies_ip_connect_resolution_and_datagrams() -> None:
    with runner.deny_ip_network() as guard:
        stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        datagram = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(runner.NetworkDeniedError):
                stream.connect(("127.0.0.1", 9))
            with pytest.raises(runner.NetworkDeniedError):
                socket.getaddrinfo("example.invalid", 443)
            with pytest.raises(runner.NetworkDeniedError):
                datagram.sendto(b"x", ("127.0.0.1", 9))
        finally:
            stream.close()
            datagram.close()
        assert guard.denied_attempts == 3


def test_model_and_preset_configs_bind_authoritative_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in tuple(runner._REMOVED_WORKER_ENVIRONMENT):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LTM_OFFLINE", "1")

    for entry in native_entries():
        case = runner.CaseSpec(case_kind="model", model_key=entry.key)
        config = runner.config_for_case(case, entry)
        assert config.offline is True
        assert config.asr.mlx_model_path is None
        if entry.role == "asr":
            assert config.asr.backend == "faster-whisper"
            assert config.asr.model == entry.key
            assert config.asr.compute_type == "int8_float32"
        else:
            assert config.asr.backend == "mlx-whisper"
            assert config.asr.mlx_model == entry.key

    for preset, model_key in NORMAL_PRESET_MODELS.items():
        entry = MODEL_MANIFEST[model_key]
        case = runner.CaseSpec(
            case_kind="preset",
            model_key=model_key,
            preset=preset,
        )
        config = runner.config_for_case(case, entry)
        assert config.preset == preset
        assert config.asr.backend == "faster-whisper"
        assert config.asr.model == model_key


def test_fixture_contract_is_bounded_and_publishes_no_hash_or_path(tmp_path: Path) -> None:
    source = tmp_path / "fixture.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(b"\x00\x00" * 16_000)
    source.chmod(0o600)

    record = runner.copy_and_describe_fixture(
        source,
        tmp_path / "private.wav",
        maximum_seconds=2.0,
    )

    assert record == {
        "kind": "operator-controlled-local-pcm-wav",
        "channels": 1,
        "sample_rate_hz": 16_000,
        "sample_width_bytes": 2,
        "duration_milliseconds": 1_000,
        "content_and_hash_withheld": True,
    }
    assert "path" not in json.dumps(record)
    assert "sha256" not in json.dumps(record)


def test_acceptance_json_is_complete_and_rejects_private_text() -> None:
    model_results = [
        {
            "case_id": f"model:{entry.key}",
            "status": "pass",
            "model_key": entry.key,
            "transcript_withheld": True,
        }
        for entry in native_entries()
    ]
    preset_results = [
        {
            "case_id": f"preset:{preset}",
            "status": "pass",
            "model_key": model_key,
            "transcript_withheld": True,
        }
        for preset, model_key in NORMAL_PRESET_MODELS.items()
    ]
    identities = {entry.key: {"state": "verified"} for entry in native_entries()}

    payload = runner.build_acceptance_payload(
        fixture={"kind": "controlled", "content_and_hash_withheld": True},
        pack_identities=identities,
        model_results=model_results,
        preset_results=preset_results,
    )

    encoded = json.dumps(payload)
    assert payload["status"] == "pass"
    assert payload["scope"]["all_required_cases_closed"] is True
    assert len(payload["models"]) == 16
    assert len(payload["presets"]) == 4
    assert "/Users/" not in encoded
    assert "transcript.json" not in encoded

    with pytest.raises(runner.NativeModelAcceptanceError, match="private"):
        runner._assert_privacy_safe_payload({"diagnostic": "/" "Users/private/model"})
    with pytest.raises(runner.NativeModelAcceptanceError, match="private"):
        runner._assert_privacy_safe_payload({"diagnostic": "token=secret"})


def test_worker_failure_record_withholds_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.json"
    values = {
        "AURALIGN_ACCEPTANCE_AUDIO": str(tmp_path / "audio.wav"),
        "AURALIGN_ACCEPTANCE_CASE_KIND": "model",
        "AURALIGN_ACCEPTANCE_MODEL_KEY": "large-v3",
        "AURALIGN_ACCEPTANCE_PACKS_ROOT": str(tmp_path / "packs"),
        "AURALIGN_ACCEPTANCE_PRESET": "",
        "AURALIGN_ACCEPTANCE_RESULT": str(result),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("/" "Users/private token=secret transcript text")

    monkeypatch.setattr(runner, "run_worker_case", fail)

    assert runner.worker_main() == 1
    payload = json.loads(result.read_text(encoding="utf-8"))
    encoded = json.dumps(payload)
    assert payload["error_type"] == "RuntimeError"
    assert payload["diagnostic_text_withheld"] is True
    assert "/Users/" not in encoded
    assert "secret" not in encoded
    assert "transcript text" not in encoded
