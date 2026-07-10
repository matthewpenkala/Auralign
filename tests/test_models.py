# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max.config import MODEL_MANIFEST, resolve_model_entry
from local_transcript_max.errors import ModelCacheError
from local_transcript_max.models import (
    audit_payload,
    ensure_pull_preconditions,
    import_mlx_model,
    prune_plan,
    selected_entries,
    status_for,
    verify_models,
    write_model_audit,
)


def write_required_files(model_dir: Path, required_files: tuple[str, ...]) -> None:
    for required in required_files:
        (model_dir / required).write_text("ok", encoding="utf-8")


def test_complete_model_cache_is_present(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    write_required_files(model_dir, entry.required_files)
    status = status_for(entry, tmp_path)
    assert status.present is True
    assert status.offline_usable is True


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


def test_turbo_redirect_alias_cache_is_accepted(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3-turbo")
    assert entry is not None
    assert entry.model_id == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    alias_dir = tmp_path / "dropbox-dash--faster-whisper-large-v3-turbo"
    alias_dir.mkdir()
    write_required_files(alias_dir, entry.required_files)
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
    write_required_files(alias_dir, entry.required_files)

    status = status_for(entry, tmp_path)
    assert status.present is True
    assert status.path == alias_dir
