# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""GUI bridge protocol, schema-manifest sync, and catalog coverage tests."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from local_transcript_max.config import MODEL_MANIFEST, PipelineConfig
from local_transcript_max.gui_bridge import (
    BRIDGE_COMMANDS,
    JOB_ACCEPTANCE_SCHEMA_VERSION,
    JOB_RESULT_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    MAX_RESULT_ARTIFACTS,
    MAX_RESULT_DIRECTORY_ENTRIES,
    MAX_RESULT_QA_BYTES,
    MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES,
    MAX_STDIN_PAYLOAD_BYTES,
    BridgeUsageError,
    JobResultEnvelope,
    ResultContractError,
    _decode_stdin_payload,
    _decode_transcript_prefix,
    _public_pipeline_failure,
    _require_request_shape,
    _validate_arguments,
    config_manifest,
    doctor_payload,
    dump_manifest_json,
    handshake_payload,
    models_payload,
    preset_payload,
    probe_payload,
    run_transcribe,
    validate_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_MANIFEST = REPO_ROOT / "macos" / "Auralign" / "Resources" / "config_manifest.json"
FIELD_CATALOG = REPO_ROOT / "macos" / "Auralign" / "Resources" / "FieldCatalog.json"
SHARED_RESULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gui_job_result_v1.json"

REGENERATE_HINT = (
    "Regenerate with: uv run python -m local_transcript_max.gui_bridge schema "
    "--write macos/Auralign/Resources/config_manifest.json"
)

EXPECTED_FIELD_COUNT = 112
EXPECTED_KIND_COUNTS = {
    "bool": 30,
    "enum": 19,
    "float": 17,
    "float_or_float_list": 1,
    "int": 18,
    "path": 11,
    "path_list": 1,
    "string": 15,
}
EXPECTED_SECTION_IDS = [
    "general",
    "audio",
    "asr",
    "diarization",
    "alignment",
    "qa_redecode",
    "speakers",
    "identity",
    "cleanup",
    "subtitles",
]
EXPECTED_DISPOSITION_COUNTS = {
    "primary": 35,
    "advanced": 76,
    "readonly": 1,
}
EXPECTED_CONTROL_COUNTS = {
    "toggle": 30,
    "enum_picker": 19,
    "integer_editor": 18,
    "float_editor": 17,
    "text_editor": 10,
    "model_picker": 5,
    "path_picker": 11,
    "path_list_editor": 1,
    "numeric_sequence_editor": 1,
}


def _walk_model_keys(model: type[Any], prefix: tuple[str, ...] = ()) -> set[str]:
    from pydantic import BaseModel

    keys: set[str] = set()
    for name, info in model.model_fields.items():
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            keys.update(_walk_model_keys(annotation, (*prefix, name)))
        else:
            keys.add(".".join((*prefix, name)))
    return keys


def _control_projection(field: dict[str, Any]) -> str:
    """Mirror FieldRowView's exhaustive kind/model-role editor selection."""

    kind = field["kind"]
    if kind == "bool":
        return "toggle"
    if kind == "enum":
        return "enum_picker"
    if kind == "int":
        return "integer_editor"
    if kind == "float":
        return "float_editor"
    if kind == "string":
        return "model_picker" if field.get("model_role") is not None else "text_editor"
    if kind == "path":
        return "path_picker"
    if kind == "path_list":
        return "path_list_editor"
    if kind in {"float_list", "float_or_float_list"}:
        return "numeric_sequence_editor"
    raise AssertionError(f"unmapped GUI field kind: {kind!r}")


def test_manifest_covers_every_pipeline_config_field() -> None:
    manifest_keys = {field["key"] for field in config_manifest()["fields"]}
    assert manifest_keys == _walk_model_keys(PipelineConfig)
    assert len(manifest_keys) == EXPECTED_FIELD_COUNT


def test_manifest_defaults_match_engine_defaults() -> None:
    defaults = PipelineConfig().model_dump(mode="json")
    for field in config_manifest()["fields"]:
        node: Any = defaults
        for part in field["key"].split("."):
            node = node[part]
        assert field["default"] == node, field["key"]


def test_bundled_manifest_matches_generated_manifest() -> None:
    assert BUNDLED_MANIFEST.exists(), f"missing {BUNDLED_MANIFEST}; {REGENERATE_HINT}"
    bundled = BUNDLED_MANIFEST.read_text(encoding="utf-8")
    generated = dump_manifest_json(config_manifest())
    assert bundled == generated, (
        "The macOS app's bundled configuration manifest is stale. " + REGENERATE_HINT
    )


def test_manifest_has_exact_shipped_field_shape_and_control_projection() -> None:
    fields = config_manifest()["fields"]
    keys = [field["key"] for field in fields]

    assert len(fields) == EXPECTED_FIELD_COUNT
    assert len(set(keys)) == EXPECTED_FIELD_COUNT, "manifest field keys must be unique"
    assert dict(Counter(field["kind"] for field in fields)) == EXPECTED_KIND_COUNTS
    assert dict(Counter(_control_projection(field) for field in fields)) == (
        EXPECTED_CONTROL_COUNTS
    )
    assert {
        field["key"] for field in fields if field.get("model_role") is not None
    } == {
        "asr.model",
        "asr.mlx_model",
        "diarization.model",
        "alignment.model",
        "speaker_id.model",
    }


def test_field_catalog_exactly_covers_manifest() -> None:
    catalog = json.loads(FIELD_CATALOG.read_text(encoding="utf-8"))
    manifest_fields = config_manifest()["fields"]
    catalog_fields = catalog["fields"]
    manifest_keys = {field["key"] for field in manifest_fields}
    catalog_keys = {field["key"] for field in catalog_fields}
    assert len(manifest_fields) == EXPECTED_FIELD_COUNT
    assert len(catalog_fields) == EXPECTED_FIELD_COUNT
    assert len(manifest_keys) == len(manifest_fields), "duplicate manifest field key"
    assert len(catalog_keys) == len(catalog_fields), "duplicate catalog field key"
    missing = manifest_keys - catalog_keys
    unknown = catalog_keys - manifest_keys
    assert not missing, f"engine fields absent from the GUI catalog: {sorted(missing)}"
    assert not unknown, f"GUI catalog names unknown engine fields: {sorted(unknown)}"


def test_field_catalog_has_exact_sections_dispositions_and_readonly_field() -> None:
    catalog = json.loads(FIELD_CATALOG.read_text(encoding="utf-8"))
    fields = catalog["fields"]
    section_ids = [section["id"] for section in catalog["sections"]]

    assert catalog["catalog_version"] == "gui_field_catalog.v1"
    assert section_ids == EXPECTED_SECTION_IDS
    assert len(set(section_ids)) == 10
    assert dict(Counter(field["disposition"] for field in fields)) == (
        EXPECTED_DISPOSITION_COUNTS
    )
    assert [
        field["key"] for field in fields if field["disposition"] == "readonly"
    ] == ["redecode.write_repaired_transcript"]
    assert all(
        any(field["section"] == section_id for field in fields)
        for section_id in section_ids
    )


def test_field_catalog_entries_are_well_formed() -> None:
    catalog = json.loads(FIELD_CATALOG.read_text(encoding="utf-8"))
    section_ids = {section["id"] for section in catalog["sections"]}
    manifest_keys = {field["key"] for field in config_manifest()["fields"]}
    for entry in catalog["fields"]:
        assert entry["disposition"] in {"primary", "advanced", "readonly"}, entry["key"]
        assert entry["section"] in section_ids, entry["key"]
        assert entry["title"].strip(), entry["key"]
        assert entry["help"].strip(), entry["key"]
        for requirement in entry.get("requires", []):
            assert requirement["key"] in manifest_keys, (
                f"{entry['key']} depends on unknown field {requirement['key']}"
            )


def test_manifest_is_deterministic_and_versioned() -> None:
    first = dump_manifest_json(config_manifest())
    second = dump_manifest_json(config_manifest())
    assert first == second
    payload = json.loads(first)
    assert payload["schema_version"] == MANIFEST_SCHEMA_VERSION


def test_manifest_carries_authoritative_model_policy_metadata() -> None:
    models = {
        (entry["role"], entry["key"]): entry
        for entry in config_manifest()["model_manifest"]
    }
    assert set(models) == {(entry.role, entry.key) for entry in MODEL_MANIFEST.values()}
    for source in MODEL_MANIFEST.values():
        entry = models[(source.role, source.key)]
        assert entry["aliases"] == list(source.aliases)
        assert entry["directory_name"] == source.directory_name
        assert entry["required_files"] == list(source.required_files)
        assert entry["any_required_files"] == list(source.any_required_files)
        assert entry["runtime_files"] == list(source.runtime_files)
        assert entry["revision"] == source.revision
        assert entry["license_expression"] == source.license_expression
        assert entry["acquisition_status"] == source.acquisition_status
        assert entry["runtime_status"] == source.runtime_status
        assert entry["redistribution_status"] == source.redistribution_status
        assert entry["review_status"] == source.review_status
        assert entry["reviewed_at"] == source.reviewed_at
        assert entry["source_url"] == source.source_url
        assert entry["terms_url"] == source.terms_url
        assert entry["attribution"] == source.attribution


def test_handshake_lists_commands() -> None:
    payload = handshake_payload()
    assert payload["protocol"] == "auralign_gui_bridge.v1"
    assert tuple(payload["commands"]) == BRIDGE_COMMANDS
    assert payload["job_acceptance_schema_version"] == JOB_ACCEPTANCE_SCHEMA_VERSION
    assert payload["job_result_schema_version"] == JOB_RESULT_SCHEMA_VERSION


def test_shared_result_fixture_validates_against_python_contract() -> None:
    envelope = JobResultEnvelope.model_validate_json(
        SHARED_RESULT_FIXTURE.read_bytes(),
        strict=True,
    )
    assert envelope.schema_version == JOB_RESULT_SCHEMA_VERSION
    assert envelope.output_directory == "/private/tmp/Auralign Shared Fixture"
    assert [artifact.name for artifact in envelope.artifacts] == [
        "qa_report.json",
        "transcript.txt",
    ]


def test_presets_are_complete_configs() -> None:
    payload = preset_payload()
    assert set(payload["presets"]) == {"max", "balanced", "fast", "cpu"}
    for name, dumped in payload["presets"].items():
        config = PipelineConfig.model_validate(dumped)
        assert config.preset == name


def test_validate_reports_structured_errors() -> None:
    result = validate_payload(
        {"config": {"diarization": {"min_speakers": 9, "max_speakers": 2}}}
    )
    assert result["ok"] is False
    assert any(error["loc"] == ["diarization"] for error in result["errors"])

    ok = validate_payload({"config": PipelineConfig().model_dump(mode="json")})
    assert ok["ok"] is True
    assert PipelineConfig.model_validate(ok["config"]) == PipelineConfig()


def test_probe_rejects_missing_path(tmp_path: Path) -> None:
    from local_transcript_max.gui_bridge import BridgeUsageError

    with pytest.raises(BridgeUsageError):
        probe_payload({"path": str(tmp_path / "missing.wav")})
    with pytest.raises(BridgeUsageError):
        probe_payload({"path": 42})


def test_invalid_doctor_config_error_does_not_leak_values(tmp_path: Path) -> None:
    from local_transcript_max.gui_bridge import BridgeUsageError, _require_config

    private = str(tmp_path / "secret-location")
    with pytest.raises(BridgeUsageError) as excinfo:
        _require_config(
            {"config": {"model_cache": private, "asr": {"beam_size": "not-a-number"}}}
        )
    message = str(excinfo.value)
    assert private not in message
    assert "asr.beam_size" in message


def test_doctor_forwards_startup_and_redacts_shareable_payload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import local_transcript_max.doctor as doctor_module

    calls: list[bool] = []
    private_path = tmp_path / "private" / "model.bin"

    class FakeReport:
        def model_dump(self, *, mode: str) -> dict[str, Any]:
            assert mode == "json"
            return {
                "ok": True,
                "offline": True,
                "checks": [
                    {
                        "name": "model:asr",
                        "status": "ok",
                        "message": f"loaded {private_path}",
                    }
                ],
            }

    def fake_run_doctor(
        config: PipelineConfig,
        *,
        include_docker: bool,
        startup: bool,
    ) -> FakeReport:
        assert isinstance(config, PipelineConfig)
        assert include_docker is False
        calls.append(startup)
        return FakeReport()

    monkeypatch.setattr(doctor_module, "run_doctor", fake_run_doctor)
    config = PipelineConfig().model_dump(mode="json")
    default_report = doctor_payload({"config": config})
    startup_report = doctor_payload({"config": config, "startup": True})

    assert calls == [False, True]
    serialized = json.dumps([default_report, startup_report])
    assert str(private_path) not in serialized
    assert "<local-path>" in serialized


def test_doctor_rejects_non_boolean_startup_flag() -> None:
    from local_transcript_max.gui_bridge import BridgeUsageError

    with pytest.raises(BridgeUsageError, match="startup.*boolean"):
        doctor_payload(
            {
                "config": PipelineConfig().model_dump(mode="json"),
                "startup": "false",
            }
        )


def test_models_rejects_non_string_cache_path() -> None:
    from local_transcript_max.gui_bridge import BridgeUsageError

    with pytest.raises(BridgeUsageError, match="model_cache.*path string"):
        models_payload({"model_cache": 42})


def test_models_payload_is_private_policy_complete_and_configuration_aware(
    tmp_path: Path,
) -> None:
    config = PipelineConfig().model_dump(mode="json")
    private_cache = tmp_path / "private-model-cache"
    payload = models_payload(
        {
            "model_cache": str(private_cache),
            "config": config,
        }
    )

    assert str(private_cache) not in payload["model_cache"]
    statuses = {
        (status["role"], status["key"]): status for status in payload["models"]
    }
    for source in MODEL_MANIFEST.values():
        status = statuses[(source.role, source.key)]
        assert status["aliases"] == list(source.aliases)
        assert status["directory_name"] == source.directory_name
        assert status["required_files"] == list(source.required_files)
        assert status["any_required_files"] == list(source.any_required_files)
        assert status["runtime_files"] == list(source.runtime_files)
        assert status["revision"] == source.revision
        assert status["license_expression"] == source.license_expression
        assert status["runtime_status"] == source.runtime_status
        assert status["redistribution_status"] == source.redistribution_status
        assert status["review_status"] == source.review_status
        assert status["attribution"] == source.attribution
        assert isinstance(status["revision_verified"], bool)

    selected = {
        identity for identity, status in statuses.items() if status["selected_for_config"]
    }
    assert selected == {
        ("asr", "large-v3"),
        ("diarization", "community-1"),
        ("speaker-embedding", "ecapa"),
    }


class _FakeTranscript:
    language = "en"
    speaker_turns = [object()]
    words = [object(), object()]
    segments = [object()]


def test_run_transcribe_streams_events_and_result(tmp_path: Path) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    out_dir = tmp_path / "job"
    events: list[tuple[str, dict[str, Any]]] = []

    def fake_transcribe(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        assert source == input_file
        assert destination == out_dir
        assert config.asr.beam_size == 9
        assert debug_report_path == out_dir / "debug_report.json"
        assert destination.is_dir()
        assert not any(destination.iterdir())
        (destination / "transcript.txt").write_text("SPEAKER: hi\n", encoding="utf-8")
        progress("asr_done", {"segments": 1})
        return _FakeTranscript()

    config = PipelineConfig().model_dump(mode="json")
    config["asr"]["beam_size"] = 9
    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(out_dir),
            "config": config,
            "debug_report": True,
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=fake_transcribe,
    )
    assert code == 0
    names = [name for name, _ in events]
    assert names[0] == "job_accepted"
    assert "asr_done" in names
    assert names[-1] == "result"
    result = events[-1][1]
    acceptance = events[0][1]
    assert set(acceptance) == {
        "schema_version",
        "output_directory",
        "output_identity",
    }
    assert acceptance == {
        "schema_version": JOB_ACCEPTANCE_SCHEMA_VERSION,
        "output_directory": str(out_dir),
        "output_identity": {
            "device": out_dir.stat().st_dev,
            "inode": out_dir.stat().st_ino,
        },
    }
    assert result["schema_version"] == JOB_RESULT_SCHEMA_VERSION
    assert result["output_directory"] == str(out_dir)
    assert result["artifact_scope"] == "immediate_regular_files"
    assert result["artifact_count"] == 1
    assert result["speaker_turns"] == 1
    assert result["artifacts"] == [
        {
            "name": "transcript.txt",
            "size_bytes": len(b"SPEAKER: hi\n"),
            "identity": {
                "device": (out_dir / "transcript.txt").stat().st_dev,
                "inode": (out_dir / "transcript.txt").stat().st_ino,
            },
        }
    ]
    assert result["output_identity"] == {
        "device": out_dir.stat().st_dev,
        "inode": out_dir.stat().st_ino,
    }
    assert result["qa"]["status"] == "missing"
    assert result["transcript_preview"] == {
        "status": "available",
        "source_artifact": "transcript.txt",
        "byte_limit": 65_536,
        "text": "SPEAKER: hi\n",
        "displayed_bytes": len(b"SPEAKER: hi\n"),
        "source_bytes": len(b"SPEAKER: hi\n"),
        "truncated": False,
    }


def test_run_transcribe_result_rejects_visible_symlink(tmp_path: Path) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    out_dir = tmp_path / "job"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    def fake_transcribe(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        assert destination.is_dir()
        (destination / "unsafe.txt").symlink_to(outside)
        return _FakeTranscript()

    events: list[tuple[str, dict[str, Any]]] = []
    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(out_dir),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=fake_transcribe,
    )
    assert code == 1
    assert [name for name, _ in events] == ["job_accepted", "bridge_error"]
    assert events[-1][1]["type"] == "ResultContractError"
    assert events[-1][1]["code"] == "result_contract_invalid"


def test_run_transcribe_result_is_bounded_sorted_and_regular_file_only(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    out_dir = tmp_path / "job"
    events: list[tuple[str, dict[str, Any]]] = []

    def fake_transcribe(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        assert destination.is_dir()
        (destination / "z.txt").write_text("z", encoding="utf-8")
        (destination / "a.txt").write_text("a", encoding="utf-8")
        (destination / ".speaker_identity.lock").write_bytes(b"")
        (destination / "candidate-files").mkdir()
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(out_dir),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=fake_transcribe,
    )

    assert code == 0
    result = events[-1][1]
    assert result["artifact_count"] == 2
    assert [artifact["name"] for artifact in result["artifacts"]] == [
        "a.txt",
        "z.txt",
    ]
    assert result["artifact_count"] <= MAX_RESULT_ARTIFACTS


@pytest.mark.parametrize(
    ("artifact_count", "expected_code"),
    [
        (MAX_RESULT_ARTIFACTS, 0),
        (MAX_RESULT_ARTIFACTS + 1, 1),
    ],
)
def test_run_transcribe_enforces_exact_visible_artifact_boundary(
    tmp_path: Path,
    artifact_count: int,
    expected_code: int,
) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []

    def many_artifacts(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        for index in range(artifact_count):
            (destination / f"artifact-{index:03d}.txt").write_bytes(b"")
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=many_artifacts,
    )
    assert code == expected_code
    if expected_code == 0:
        assert events[-1][1]["artifact_count"] == MAX_RESULT_ARTIFACTS
    else:
        assert events[-1][0] == "bridge_error"
        assert not any(name == "result" for name, _ in events)


def test_run_transcribe_rejects_invalid_config(tmp_path: Path) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []
    config = PipelineConfig().model_dump(mode="json")
    config["diarization"]["min_speakers"] = 9
    config["diarization"]["max_speakers"] = 1
    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": config,
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=lambda *args, **kwargs: pytest.fail("must not run"),
    )
    assert code == 2
    assert events[0][0] == "config_invalid"
    assert not (tmp_path / "job").exists()


def test_run_transcribe_rejects_non_boolean_debug_report(tmp_path: Path) -> None:
    from local_transcript_max.gui_bridge import BridgeUsageError

    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    with pytest.raises(BridgeUsageError, match="debug_report.*boolean"):
        run_transcribe(
            {
                "input": str(input_file),
                "output_root": str(tmp_path),
                "out_dir": str(tmp_path / "job"),
                "config": PipelineConfig().model_dump(mode="json"),
                "debug_report": "false",
            },
            lambda name, payload: None,
            transcribe=lambda *args, **kwargs: pytest.fail("must not run"),
        )


def test_run_transcribe_reports_engine_failure(tmp_path: Path) -> None:
    from local_transcript_max.errors import LTMError

    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []

    def failing_transcribe(*args: Any, **kwargs: Any) -> None:
        raise LTMError(f"Output directory {tmp_path} must be empty")

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=failing_transcribe,
    )
    assert code == 1
    assert events[-1][0] == "bridge_error"
    message = events[-1][1]["message"]
    assert str(tmp_path) not in message, "error text must not leak local paths"


def test_run_transcribe_requires_fresh_direct_child_of_declared_root(
    tmp_path: Path,
) -> None:
    from local_transcript_max.gui_bridge import BridgeUsageError

    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    nested = tmp_path / "nested"
    nested.mkdir()
    config = PipelineConfig().model_dump(mode="json")
    for payload, message in (
        (
            {
                "input": str(input_file),
                "out_dir": str(tmp_path / "job"),
                "config": config,
            },
            "output_root",
        ),
        (
            {
                "input": str(input_file),
                "output_root": str(tmp_path),
                "out_dir": str(nested / "job"),
                "config": config,
            },
            "fresh child",
        ),
    ):
        with pytest.raises(BridgeUsageError, match=message):
            run_transcribe(
                payload,
                lambda name, event: None,
                transcribe=lambda *args, **kwargs: pytest.fail("must not run"),
            )

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(BridgeUsageError, match="fresh and private"):
        run_transcribe(
            {
                "input": str(input_file),
                "output_root": str(tmp_path),
                "out_dir": str(existing),
                "config": config,
            },
            lambda name, event: None,
            transcribe=lambda *args, **kwargs: pytest.fail("must not run"),
        )


def test_run_transcribe_rejects_replaced_output_directory(tmp_path: Path) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    out_dir = tmp_path / "job"
    displaced = tmp_path / "displaced"
    events: list[tuple[str, dict[str, Any]]] = []

    def replace_output(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        destination.rename(displaced)
        destination.mkdir(mode=0o700)
        (destination / "transcript.txt").write_text("replacement", encoding="utf-8")
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(out_dir),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=replace_output,
    )
    assert code == 1
    assert events[-1][0] == "bridge_error"
    assert events[-1][1]["code"] == "result_contract_invalid"
    assert not any(name == "result" for name, _ in events)


def test_run_transcribe_counts_hidden_entries_before_ignoring_them(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []

    def many_hidden_entries(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        for index in range(MAX_RESULT_DIRECTORY_ENTRIES + 1):
            (destination / f".entry-{index:04d}").write_bytes(b"")
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=many_hidden_entries,
    )
    assert code == 1
    assert events[-1][1]["type"] == "ResultContractError"
    assert not any(name == "result" for name, _ in events)


def test_run_transcribe_accepts_exact_hidden_entry_boundary(tmp_path: Path) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []

    def exact_hidden_entries(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        for index in range(MAX_RESULT_DIRECTORY_ENTRIES):
            (destination / f".entry-{index:04d}").write_bytes(b"")
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=exact_hidden_entries,
    )
    assert code == 0
    assert events[-1][1]["artifact_count"] == 0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX FIFO contract")
def test_run_transcribe_rejects_special_artifact_without_blocking(tmp_path: Path) -> None:
    import os

    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []

    def fifo_artifact(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        os.mkfifo(destination / "blocked.pipe")
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=fifo_artifact,
    )
    assert code == 1
    assert events[-1][1]["code"] == "result_contract_invalid"


def test_run_transcribe_projects_real_qa_and_bounded_utf8_preview(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []
    preview_source = ("a" * (MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES - 1)) + "€tail"

    def write_result_evidence(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        (destination / "transcript.txt").write_text(preview_source, encoding="utf-8")
        (destination / "qa_report.json").write_text(
            json.dumps(
                {
                    "flags": [
                        {
                            "code": "alignment_gap",
                            "severity": "warning",
                            "message": "Review one deterministic alignment warning.",
                            "start": 1.0,
                            "end": 2.0,
                            "evidence": {"private_detail": "not projected"},
                        }
                    ],
                    "changed_spans": [{"private_transcript": "not projected"}],
                    "summary": {
                        "flag_count": 1,
                        "error_flag_count": 0,
                        "warning_flag_count": 1,
                        "overall_quality_summary": "review_warnings",
                    },
                }
            ),
            encoding="utf-8",
        )
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=write_result_evidence,
    )
    assert code == 0
    result = events[-1][1]
    assert result["qa"] == {
        "status": "available",
        "source_artifact": "qa_report.json",
        "byte_limit": MAX_RESULT_QA_BYTES,
        "overall_quality_summary": "review_warnings",
        "flag_count": 1,
        "error_flag_count": 0,
        "warning_flag_count": 1,
        "info_flag_count": 0,
        "changed_span_count": 1,
        "flags": [
            {
                "code": "alignment_gap",
                "severity": "warning",
                "message": "Review one deterministic alignment warning.",
            }
        ],
        "omitted_flag_count": 0,
    }
    serialized = json.dumps(result["qa"])
    assert "private_detail" not in serialized
    assert "private_transcript" not in serialized
    preview = result["transcript_preview"]
    assert preview["status"] == "available"
    assert preview["displayed_bytes"] == MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES - 1
    assert preview["source_bytes"] == len(preview_source.encode("utf-8"))
    assert preview["truncated"] is True
    assert preview["text"] == "a" * (MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES - 1)


@pytest.mark.parametrize(
    ("qa_bytes", "expected_status"),
    [
        (b"not-json", "invalid"),
        (b"x" * (MAX_RESULT_QA_BYTES + 1), "oversized"),
    ],
)
def test_run_transcribe_distinguishes_invalid_and_oversized_qa(
    tmp_path: Path,
    qa_bytes: bytes,
    expected_status: str,
) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []

    def write_qa(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        (destination / "qa_report.json").write_bytes(qa_bytes)
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=write_qa,
    )
    assert code == 0
    assert events[-1][1]["qa"]["status"] == expected_status
    assert events[-1][1]["qa"]["flag_count"] is None


def test_run_transcribe_qa_disabled_does_not_claim_a_pass(tmp_path: Path) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []
    config = PipelineConfig().model_dump(mode="json")
    config["qa"]["enabled"] = False

    def write_disabled_qa(
        source: Path,
        destination: Path,
        config: PipelineConfig,
        *,
        debug_report_path: Path | None,
        progress: Any,
    ) -> _FakeTranscript:
        del source, config, debug_report_path, progress
        (destination / "qa_report.json").write_text(
            '{"flags":[],"changed_spans":[],"summary":{}}',
            encoding="utf-8",
        )
        return _FakeTranscript()

    code = run_transcribe(
        {
            "input": str(input_file),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / "job"),
            "config": config,
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=write_disabled_qa,
    )
    assert code == 0
    qa = events[-1][1]["qa"]
    assert qa["status"] == "disabled"
    assert qa["overall_quality_summary"] is None
    assert qa["flag_count"] == 0


def test_run_transcribe_rejects_invalid_transcript_utf8_and_language(
    tmp_path: Path,
) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")

    class InvalidLanguageTranscript(_FakeTranscript):
        language = True

    for index, (content, transcript) in enumerate(
        (
            (b"\xff", _FakeTranscript()),
            (b"valid", InvalidLanguageTranscript()),
        )
    ):
        events: list[tuple[str, dict[str, Any]]] = []

        def write_invalid(
            source: Path,
            destination: Path,
            config: PipelineConfig,
            *,
            debug_report_path: Path | None,
            progress: Any,
            content: bytes = content,
            transcript: _FakeTranscript = transcript,
        ) -> _FakeTranscript:
            del source, config, debug_report_path, progress
            (destination / "transcript.txt").write_bytes(content)
            return transcript

        code = run_transcribe(
            {
                "input": str(input_file),
                "output_root": str(tmp_path),
                "out_dir": str(tmp_path / f"job-{index}"),
                "config": PipelineConfig().model_dump(mode="json"),
            },
            lambda name, payload, event_log=events: event_log.append((name, payload)),
            transcribe=write_invalid,
        )
        assert code == 1
        assert events[-1][1]["code"] == "result_contract_invalid"
        assert not any(name == "result" for name, _ in events)


@pytest.mark.parametrize("scalar", ["¢", "€", "💡"])
def test_transcript_preview_trims_only_a_split_final_utf8_scalar(scalar: str) -> None:
    encoded = scalar.encode("utf-8")
    for split_count in range(1, len(encoded)):
        prefix = b"a" * (MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES - split_count)
        source = prefix + encoded + b"tail"
        bounded = source[:MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES]
        assert _decode_transcript_prefix(
            bounded,
            source_bytes=len(source),
        ) == prefix.decode("utf-8")


def test_transcript_preview_rejects_invalid_utf8_at_the_cap_boundary() -> None:
    bounded = b"a" * (MAX_RESULT_TRANSCRIPT_PREVIEW_BYTES - 1) + b"\xff"
    with pytest.raises(ResultContractError, match="not valid UTF-8"):
        _decode_transcript_prefix(
            bounded,
            source_bytes=len(bounded) + 4,
        )


def test_bridge_subprocess_handshake_and_schema_are_clean_jsonl() -> None:
    for command in (["handshake"], ["schema"]):
        completed = subprocess.run(
            [sys.executable, "-m", "local_transcript_max.gui_bridge", *command],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
            cwd=REPO_ROOT,
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == command[0]


def _run_bridge(
    arguments: list[str],
    payload: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "local_transcript_max.gui_bridge", *arguments],
        input=payload,
        capture_output=True,
        timeout=120,
        check=False,
        cwd=REPO_ROOT,
    )


def _protocol_rows(completed: subprocess.CompletedProcess[bytes]) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in completed.stdout.decode("utf-8").splitlines()
        if line.strip()
    ]


def test_strict_stdin_accepts_exact_boundary_and_rejects_oversize() -> None:
    exact = _run_bridge(["handshake"], b" " * MAX_STDIN_PAYLOAD_BYTES)
    assert exact.returncode == 0
    assert [row["event"] for row in _protocol_rows(exact)] == ["handshake"]

    oversized = _run_bridge(
        ["handshake"],
        b" " * (MAX_STDIN_PAYLOAD_BYTES + 1),
    )
    assert oversized.returncode == 64
    rows = _protocol_rows(oversized)
    assert [row["event"] for row in rows] == ["bridge_error"]
    assert rows[0]["payload"]["type"] == "BridgeUsageError"


@pytest.mark.parametrize(
    "payload",
    [
        b"\xff",
        b'{"config":{},"config":{}}',
        b'{"outer":{"key":1,"key":2}}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e9999}',
        b'{"value":-1e9999}',
        b"[]",
        b"true",
    ],
)
def test_strict_stdin_rejects_ambiguous_or_non_object_json(payload: bytes) -> None:
    with pytest.raises(BridgeUsageError) as excinfo:
        _decode_stdin_payload(payload)
    assert "strict JSON" in str(excinfo.value) or "JSON object" in str(excinfo.value)
    decoded = payload.decode("utf-8", errors="ignore")
    if decoded:
        assert decoded not in str(excinfo.value)


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["unknown"],
        ["handshake", "extra"],
        ["presets", "extra"],
        ["validate", "extra"],
        ["doctor", "extra"],
        ["models", "extra"],
        ["probe", "extra"],
        ["transcribe", "extra"],
        ["schema", "junk"],
        ["schema", "--write"],
        ["schema", "--write", ""],
        ["schema", "--write", "target", "extra"],
    ],
)
def test_malformed_bridge_argv_fails_validation(
    arguments: list[str],
) -> None:
    with pytest.raises(BridgeUsageError):
        _validate_arguments(arguments)


def test_malformed_bridge_subprocess_emits_one_usage_error() -> None:
    completed = _run_bridge(["unknown"])
    assert completed.returncode == 64
    rows = _protocol_rows(completed)
    assert [row["event"] for row in rows] == ["bridge_error"]
    assert rows[0]["payload"]["type"] == "BridgeUsageError"


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        ("handshake", {"extra": True}),
        ("schema", {"extra": True}),
        ("presets", {"extra": True}),
        ("validate", {"config": {}, "extra": True}),
        ("doctor", {"config": {}, "extra": True}),
        ("models", {"extra": True}),
        ("probe", {"path": "unused", "extra": True}),
        (
            "transcribe",
            {
                "input": "unused",
                "output_root": "unused",
                "out_dir": "unused",
                "config": {},
                "extra": True,
            },
        ),
    ],
)
def test_every_command_rejects_unknown_top_level_fields(
    command: str,
    payload: dict[str, Any],
) -> None:
    with pytest.raises(BridgeUsageError, match="unsupported fields"):
        _require_request_shape(command, payload)


@pytest.mark.parametrize(
    ("command", "payload", "missing_name"),
    [
        ("validate", {}, "config"),
        ("doctor", {}, "config"),
        ("probe", {}, "path"),
        ("transcribe", {}, "config"),
    ],
)
def test_required_request_fields_fail_closed(
    command: str,
    payload: dict[str, Any],
    missing_name: str,
) -> None:
    with pytest.raises(BridgeUsageError, match=missing_name):
        _require_request_shape(command, payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"model_cache": "models"},
        {"config": PipelineConfig().model_dump(mode="json")},
        {
            "model_cache": "models",
            "config": PipelineConfig().model_dump(mode="json"),
        },
    ],
)
def test_models_request_shapes_remain_supported(payload: dict[str, Any]) -> None:
    _require_request_shape("models", payload)


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"preset": "max"},
    ],
)
def test_transcribe_rejects_implicit_or_partial_configuration_before_output(
    tmp_path: Path,
    config: object,
) -> None:
    input_file = tmp_path / "input.wav"
    input_file.write_bytes(b"RIFF")
    output = tmp_path / "job"
    payload: dict[str, Any] = {
        "input": str(input_file),
        "output_root": str(tmp_path),
        "out_dir": str(output),
        "config": config,
    }
    with pytest.raises(BridgeUsageError, match="config"):
        run_transcribe(
            payload,
            lambda name, event: pytest.fail("must not emit"),
            transcribe=lambda *args, **kwargs: pytest.fail("must not run"),
        )
    assert not output.exists()


def test_probe_projects_only_bounded_core_facts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import local_transcript_max.audio.inspect as inspect_module
    from local_transcript_max.schemas import AudioMetadata

    source = tmp_path / "source.wav"
    source.write_bytes(b"RIFF")
    canary = "PRIVATE_MEDIA_TAG_CANARY"
    monkeypatch.setattr(
        inspect_module,
        "inspect_media",
        lambda path: AudioMetadata(
            path=str(path),
            duration_seconds=1.25,
            sample_rate=16_000,
            channels=1,
            codec="pcm_s16le",
            bit_rate=256_000,
            format_name="wav",
            metadata={"comment": canary},
        ),
    )

    projected = probe_payload({"path": str(source)})

    assert set(projected) == {
        "duration_seconds",
        "sample_rate",
        "channels",
        "codec",
        "format_name",
    }
    assert canary not in json.dumps(projected)
    assert str(source) not in json.dumps(projected)


@pytest.mark.parametrize("failure_kind", ["ltm", "unexpected"])
def test_pipeline_failure_events_never_expose_content_canary(
    tmp_path: Path,
    failure_kind: str,
) -> None:
    from local_transcript_max.errors import LTMError

    source = tmp_path / "input.wav"
    source.write_bytes(b"RIFF")
    events: list[tuple[str, dict[str, Any]]] = []
    canary = "TRANSCRIPT_SENTENCE_CANARY_DO_NOT_EXPOSE"

    def fail_with_dynamic_progress(
        *args: Any,
        progress: Any,
        **kwargs: Any,
    ) -> None:
        del args, kwargs
        progress("failed", {"type": "DynamicFailure", "message": canary})
        if failure_kind == "ltm":
            raise LTMError(canary)
        raise RuntimeError(canary)

    code = run_transcribe(
        {
            "input": str(source),
            "output_root": str(tmp_path),
            "out_dir": str(tmp_path / f"job-{failure_kind}"),
            "config": PipelineConfig().model_dump(mode="json"),
        },
        lambda name, payload: events.append((name, payload)),
        transcribe=fail_with_dynamic_progress,
    )

    assert code == 1
    assert [name for name, _ in events] == ["job_accepted", "bridge_error"]
    assert canary not in json.dumps(events)


def test_public_failure_projection_is_fixed_and_allowlisted() -> None:
    from local_transcript_max.errors import (
        BackendUnavailableError,
        CommandExecutionError,
        LTMError,
        ModelCacheError,
        OfflineModeError,
    )

    canary = "TRANSCRIPT_CANARY"
    projections = [
        _public_pipeline_failure(OfflineModeError(canary)),
        _public_pipeline_failure(ModelCacheError(canary)),
        _public_pipeline_failure(BackendUnavailableError(canary)),
        _public_pipeline_failure(CommandExecutionError(canary)),
        _public_pipeline_failure(LTMError(canary)),
        _public_pipeline_failure(RuntimeError(canary)),
    ]
    serialized = json.dumps(projections)
    assert canary not in serialized
    assert {projection["type"] for projection in projections} == {
        "ModelUnavailableError",
        "BackendUnavailableError",
        "CommandExecutionError",
        "EngineError",
        "InternalEngineError",
    }
