# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max.config import MODEL_MANIFEST, ModelEntry, resolve_model_entry
from local_transcript_max.errors import ModelCacheError, OfflineModeError
from local_transcript_max.models import (
    MODEL_PROVENANCE_FILENAME,
    audit_payload,
    ensure_pull_preconditions,
    import_mlx_model,
    model_provenance_payload,
    prune_plan,
    pull_model,
    require_model,
    selected_entries,
    status_for,
    verify_models,
    write_model_audit,
    write_model_provenance,
)
from local_transcript_max.utils import write_json


def write_complete_model(model_dir: Path, entry: ModelEntry) -> None:
    for required in entry.required_files:
        (model_dir / required).parent.mkdir(parents=True, exist_ok=True)
        (model_dir / required).write_text("ok", encoding="utf-8")
    write_model_provenance(entry, model_dir)


def test_complete_model_cache_is_present(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    write_complete_model(model_dir, entry)
    status = status_for(entry, tmp_path)
    assert status.present is True
    assert status.offline_usable is True
    assert status.revision_verified is True


def test_missing_partial_and_zero_byte_cache_detection(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None
    missing = status_for(entry, tmp_path)
    assert missing.exists is False
    assert missing.present is False
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.bin").write_bytes(b"")
    partial = status_for(entry, tmp_path)
    assert partial.partial is True
    assert partial.zero_size_files == ("model.bin",)


def test_complete_files_without_revision_linkage_are_not_offline_usable(
    tmp_path: Path,
) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    for required in entry.required_files:
        (model_dir / required).parent.mkdir(parents=True, exist_ok=True)
        (model_dir / required).write_text("ok", encoding="utf-8")

    status = status_for(entry, tmp_path)

    assert status.files_complete is True
    assert status.revision_verified is False
    assert status.present is False
    assert MODEL_PROVENANCE_FILENAME in status.provenance_problem


def test_wrong_revision_linkage_fails_closed(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    write_complete_model(model_dir, entry)
    marker = model_provenance_payload(entry)
    marker["revision"] = "0" * 40
    write_json(model_dir / MODEL_PROVENANCE_FILENAME, marker)

    status = status_for(entry, tmp_path)

    assert status.files_complete is True
    assert status.revision_verified is False
    assert status.present is False
    assert "does not match" in status.provenance_problem


def test_explicit_pinned_pull_writes_revision_linkage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None

    def fake_snapshot_download(**kwargs: object) -> str:
        assert kwargs["repo_id"] == entry.model_id
        assert kwargs["revision"] == entry.revision
        local_dir = Path(str(kwargs["local_dir"]))
        for required in entry.required_files:
            (local_dir / required).parent.mkdir(parents=True, exist_ok=True)
            (local_dir / required).write_text("ok", encoding="utf-8")
        return str(local_dir)

    monkeypatch.setattr("local_transcript_max.models.snapshot_download", fake_snapshot_download)

    path = pull_model(entry, tmp_path)

    assert (path / MODEL_PROVENANCE_FILENAME).exists()
    assert status_for(entry, tmp_path).revision_verified is True


def test_offline_missing_model_never_attempts_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_download(**_kwargs: object) -> str:
        raise AssertionError("offline resolution must not download")

    monkeypatch.setattr("local_transcript_max.models.snapshot_download", forbidden_download)

    with pytest.raises(OfflineModeError, match="Offline mode is enabled"):
        require_model("asr", "large-v3", tmp_path, offline=True)


def test_gated_precondition_blocks_without_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("LTM_HF_TOKEN", raising=False)
    entry = resolve_model_entry("diarization", "community-1")
    assert entry is not None
    with pytest.raises(ModelCacheError, match="requires HF_TOKEN or LTM_HF_TOKEN"):
        ensure_pull_preconditions([entry], tmp_path)


def test_gated_precondition_allows_present_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_HF_TOKEN", "hf_" + "fake_for_unit_test_only")
    entry = resolve_model_entry("diarization", "community-1")
    assert entry is not None
    ensure_pull_preconditions([entry], tmp_path)


def test_combined_pull_selection_excludes_optional_by_default() -> None:
    entries = selected_entries(all_models=True, include_optional=False)
    assert all(not entry.optional for entry in entries)
    assert {entry.key for entry in entries} >= {
        "large-v3",
        "turbo",
        "distil-large-v3",
        "distil-large-v3.5",
        "community-1",
    }


def test_combined_pull_selection_includes_optional_when_requested() -> None:
    entries = selected_entries(all_models=True, include_optional=True)
    roles = {(entry.role, entry.key) for entry in entries}
    assert ("alignment", "en") in roles
    assert ("speaker-embedding", "ecapa") in roles
    assert ("asr-mlx", "mlx-large-v3-turbo") not in roles


def test_import_mlx_model_copies_valid_local_directory(tmp_path: Path) -> None:
    source = tmp_path / "downloaded-mlx"
    source.mkdir()
    (source / "config.json").write_text("{}", encoding="utf-8")
    (source / "weights.npz").write_bytes(b"ok")
    cache_dir = tmp_path / "models"

    status = import_mlx_model(source, cache_dir)

    assert status.present is True
    assert status.path == cache_dir / "mlx-community--whisper-large-v3-turbo"
    assert (status.path / "config.json").exists()
    assert (status.path / "weights.npz").exists()
    with pytest.raises(ModelCacheError, match="Destination already exists"):
        import_mlx_model(source, cache_dir)


def test_import_mlx_model_rejects_partial_source(tmp_path: Path) -> None:
    source = tmp_path / "partial-mlx"
    source.mkdir()
    (source / "config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ModelCacheError, match="MLX model source is incomplete"):
        import_mlx_model(source, tmp_path / "models")


def test_model_audit_json_is_redacted(tmp_path: Path, monkeypatch) -> None:
    secret = "hf_" + "fake_secret_value_for_unit_test_only"
    monkeypatch.setenv("HF_TOKEN", secret)
    output = write_model_audit(tmp_path)
    text = output.read_text(encoding="utf-8")
    assert secret not in text
    payload = audit_payload(tmp_path)
    assert payload["environment"]["hf_token_present"] is True
    assert "models" in payload


def test_verify_models_reports_missing_required_entries(tmp_path: Path) -> None:
    with pytest.raises(ModelCacheError, match="Model verification failed"):
        verify_models(tmp_path, include_optional=False)


def test_alignment_manifest_uses_whisperx_compatible_ctc_model() -> None:
    entry = resolve_model_entry("alignment", "auto")
    assert entry is not None
    assert entry.model_id == "facebook/wav2vec2-base-960h"
    assert "vocab.json" in entry.required_files
    assert "tokenizer_config.json" in entry.required_files
    assert entry.any_required_files == ("pytorch_model.bin", "model.safetensors")
    assert entry.optional is True


@pytest.mark.parametrize("weight_name", ["pytorch_model.bin", "model.safetensors"])
def test_alignment_cache_accepts_either_reviewed_weight_filename(
    tmp_path: Path, weight_name: str
) -> None:
    entry = resolve_model_entry("alignment", "auto")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    for required in entry.required_files:
        (model_dir / required).write_text("ok", encoding="utf-8")
    (model_dir / weight_name).write_bytes(b"ok")
    write_model_provenance(entry, model_dir)

    status = status_for(entry, tmp_path)

    assert status.present is True
    assert status.revision_verified is True


def test_alignment_cache_requires_ctc_tokenizer_and_weights(tmp_path: Path) -> None:
    entry = resolve_model_entry("alignment", "auto")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "preprocessor_config.json").write_text("{}", encoding="utf-8")

    status = status_for(entry, tmp_path)

    assert status.present is False
    assert "tokenizer_config.json" in status.missing_files
    assert "vocab.json" in status.missing_files
    assert "pytorch_model.bin or model.safetensors" in status.missing_files


def test_manifest_has_no_unclassified_entries() -> None:
    assert MODEL_MANIFEST
    assert all(entry.role for entry in MODEL_MANIFEST.values())


def test_strict_model_pointer_matrix_is_exact_and_immutable() -> None:
    expected = {
        ("asr", "large-v3"): (
            "Systran/faster-whisper-large-v3",
            "edaa852ec7e145841d8ffdb056a99866b5f0a478",
            "Systran--faster-whisper-large-v3",
        ),
        ("diarization", "community-1"): (
            "pyannote/speaker-diarization-community-1",
            "3533c8cf8e369892e6b79ff1bf80f7b0286a54ee",
            "pyannote--speaker-diarization-community-1",
        ),
        ("alignment", "en"): (
            "facebook/wav2vec2-base-960h",
            "22aad52d435eb6dbaf354bdad9b0da84ce7d6156",
            "facebook--wav2vec2-base-960h",
        ),
    }

    observed = {
        (entry.role, entry.key): (entry.model_id, entry.revision, entry.directory_name)
        for entry in MODEL_MANIFEST.values()
        if (entry.role, entry.key) in expected
    }

    assert observed == expected


def test_turbo_redirect_alias_cache_is_accepted(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3-turbo")
    assert entry is not None
    assert entry.model_id == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    alias_dir = tmp_path / "dropbox-dash--faster-whisper-large-v3-turbo"
    alias_dir.mkdir()
    write_complete_model(alias_dir, entry)
    status = status_for(entry, tmp_path)
    assert status.present is True
    assert status.path == alias_dir
    assert prune_plan(tmp_path)["candidates"] == []


def test_mlx_manifest_alias_directory_cache_is_accepted(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr-mlx", "turbo")
    assert entry is not None
    assert entry.key == "mlx-large-v3-turbo"
    alias_dir = tmp_path / "mlx-turbo"
    alias_dir.mkdir()
    (alias_dir / "config.json").write_text("{}", encoding="utf-8")
    (alias_dir / "weights.safetensors").write_bytes(b"ok")

    status = status_for(entry, tmp_path)

    assert status.present is True
    assert status.path == alias_dir
    assert prune_plan(tmp_path)["candidates"] == []


def test_complete_alias_cache_wins_over_partial_canonical_cache(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3-turbo")
    assert entry is not None
    canonical_dir = tmp_path / entry.directory_name
    canonical_dir.mkdir()
    (canonical_dir / "config.json").write_text("{}", encoding="utf-8")
    alias_dir = tmp_path / "dropbox-dash--faster-whisper-large-v3-turbo"
    alias_dir.mkdir()
    write_complete_model(alias_dir, entry)

    status = status_for(entry, tmp_path)
    assert status.present is True
    assert status.path == alias_dir
