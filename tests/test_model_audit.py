# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from dataclasses import replace
from pathlib import Path

from typer.testing import CliRunner

from local_transcript_max.cli import app
from local_transcript_max.config import MODEL_MANIFEST, resolve_model_entry
from local_transcript_max.models import audit_payload, status_for, write_model_provenance
from local_transcript_max.utils import read_json


def test_model_status_detects_partial_and_zero_size_cache(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.bin").write_bytes(b"")
    status = status_for(entry, tmp_path)
    assert status.exists is True
    assert status.present is False
    assert status.partial is True
    assert status.zero_size_files == ("model.bin",)
    assert status.offline_usable is False


def test_asr_cache_without_tokenizer_is_partial(tmp_path: Path) -> None:
    entry = resolve_model_entry("asr", "large-v3")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "model.bin").write_text("ok", encoding="utf-8")

    status = status_for(entry, tmp_path)

    assert status.present is False
    assert "tokenizer.json" in status.missing_files
    assert "preprocessor_config.json" in status.missing_files


def test_pyannote_config_only_cache_is_partial(tmp_path: Path) -> None:
    entry = resolve_model_entry("diarization", "community-1")
    assert entry is not None
    model_dir = tmp_path / entry.directory_name
    model_dir.mkdir()
    (model_dir / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")

    status = status_for(entry, tmp_path)

    assert status.present is False
    assert "segmentation/pytorch_model.bin" in status.missing_files
    assert "embedding/pytorch_model.bin" in status.missing_files


def test_models_audit_reports_configured_ids_and_token_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_MODEL_CACHE", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("LTM_HF_TOKEN", raising=False)
    result = CliRunner().invoke(app, ["models", "audit"])
    assert result.exit_code == 0
    assert "Systran/faster-whisper-large-v3" in result.stdout
    assert "pyannote/speaker-diarization-community-1" in result.stdout
    assert "HF_TOKEN or LTM_HF_TOKEN is required" in result.stdout
    community_row = next(
        row
        for row in result.stdout.splitlines()
        if "pyannote/speaker-diarization-community-1" in row
    )
    community_columns = community_row.split("\t")
    assert community_columns[3].startswith("<model-cache>:")
    assert community_columns[7] == "yes"
    assert community_columns[8] == "yes"
    assert community_columns[9] == "no"
    assert (tmp_path / "model_audit.json").exists()
    audit = read_json(tmp_path / "model_audit.json")
    assert "models" in audit
    audit_text = (tmp_path / "model_audit.json").read_text(encoding="utf-8")
    assert "hf_secret" not in audit_text


def test_models_audit_reports_configured_mlx_model_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_MODEL_CACHE", str(tmp_path / "models"))
    model_dir = tmp_path / "mlx-model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "weights.safetensors").write_bytes(b"ok")
    config_path = tmp_path / "mlx.yaml"
    config_path.write_text(
        "\n".join(
            [
                "asr:",
                "  backend: mlx-whisper",
                f"  mlx_model_path: {model_dir}",
                "diarization:",
                "  backend: off",
                "  enabled: false",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["models", "audit", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "asr-mlx" in result.stdout
    assert "mlx-community/whisper-large-v3-turbo" in result.stdout
    assert str(model_dir) not in result.stdout
    assert "<local-model-path>" in result.stdout
    assert "alignment\ten\tfacebook/wav2vec2-base-960h" in result.stdout
    assert "model.safetensors" in result.stdout
    audit = audit_payload(tmp_path / "models")
    mlx_row = next(row for row in audit["models"] if row["key"] == "mlx-large-v3-turbo")
    assert "mlx-turbo" in mlx_row["aliases"]
    assert "mlx-turbo" in mlx_row["directory_aliases"]


def test_models_import_mlx_cli_copies_valid_local_directory(tmp_path: Path) -> None:
    source = tmp_path / "mlx-source"
    source.mkdir()
    (source / "config.json").write_text("{}", encoding="utf-8")
    (source / "weights.safetensors").write_bytes(b"ok")
    cache_dir = tmp_path / "models"

    result = CliRunner().invoke(
        app,
        ["models", "import-mlx", str(source), "--cache-dir", str(cache_dir)],
    )

    assert result.exit_code == 0
    assert "asr-mlx:mlx-large-v3-turbo" in result.stdout
    assert (cache_dir / "mlx-community--whisper-large-v3-turbo" / "config.json").exists()
    assert (cache_dir / "mlx-community--whisper-large-v3-turbo" / "weights.safetensors").exists()


def test_pull_preconditions_block_gated_model_before_asr_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("LTM_HF_TOKEN", raising=False)
    result = CliRunner().invoke(
        app,
        [
            "models",
            "pull",
            "--asr",
            "large-v3",
            "--diarization",
            "community-1",
            "--cache-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "requires HF_TOKEN or LTM_HF_TOKEN" in result.stderr
    assert not any(tmp_path.iterdir())


def test_model_audit_payload_includes_optional_classification(tmp_path: Path) -> None:
    payload = audit_payload(tmp_path, include_optional=True)
    records = {(row["role"], row["key"]): row for row in payload["models"]}
    assert records[("alignment", "en")]["classification"] == "optional"
    assert records[("alignment", "en")]["model_id"] == "facebook/wav2vec2-base-960h"
    assert records[("alignment", "en")]["any_required_files"] == []
    assert records[("alignment", "en")]["runtime_file_allowlist"][-1] == "model.safetensors"
    assert records[("speaker-embedding", "ecapa")]["classification"] == "optional"
    assert records[("speaker-embedding", "ecapa")]["runtime_file_allowlist"] == [
        "hyperparams.yaml",
        "embedding_model.ckpt",
        "classifier.ckpt",
        "mean_var_norm_emb.ckpt",
        "label_encoder.txt",
        "config.json",
    ]
    assert set(records[("speaker-embedding", "ecapa")]["expected_sha256"]) == set(
        records[("speaker-embedding", "ecapa")]["runtime_file_allowlist"]
    )
    assert records[("asr-mlx", "mlx-large-v3-turbo")]["classification"] == "optional"
    assert records[("asr-mlx", "mlx-large-v3-turbo")]["pullable"] is False
    assert records[("asr-mlx", "mlx-large-v3-turbo")]["any_required_files"] == [
        "weights.safetensors",
        "weights.npz",
    ]


def test_models_verify_fails_and_writes_audit_for_missing_cache(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["models", "verify", "--cache-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "Model verification failed" in result.stderr
    assert (tmp_path / "model_audit.json").exists()


def test_models_verify_can_target_the_exact_strict_lane(
    tmp_path: Path, monkeypatch
) -> None:
    cache_dir = tmp_path / "models"
    for role, key in (
        ("asr", "large-v3"),
        ("diarization", "community-1"),
        ("alignment", "en"),
    ):
        entry = resolve_model_entry(role, key)
        assert entry is not None
        entry = replace(entry, expected_sha256=())
        monkeypatch.setitem(MODEL_MANIFEST, key, entry)
        model_dir = cache_dir / entry.directory_name
        model_dir.mkdir(parents=True)
        for required in entry.required_files:
            (model_dir / required).parent.mkdir(parents=True, exist_ok=True)
            (model_dir / required).write_text("ok", encoding="utf-8")
        if entry.any_required_files:
            (model_dir / entry.any_required_files[0]).write_bytes(b"ok")
        write_model_provenance(entry, model_dir)

    result = CliRunner().invoke(
        app,
        [
            "models",
            "verify",
            "--asr",
            "large-v3",
            "--diarization",
            "community-1",
            "--alignment",
            "en",
            "--cache-dir",
            str(cache_dir),
        ],
    )

    assert result.exit_code == 0
    assert "verified 3 model cache entries with immutable revision linkage" in result.stdout
    assert str(tmp_path) not in result.stdout


def test_models_prune_dry_run_reports_unknown_directory(tmp_path: Path) -> None:
    (tmp_path / "unknown-model").mkdir()
    result = CliRunner().invoke(
        app,
        ["models", "prune", "--cache-dir", str(tmp_path), "--dry-run"],
    )
    assert result.exit_code == 0
    assert "unknown-model" in result.stdout


def test_speaker_id_pull_alias_is_supported() -> None:
    result = CliRunner().invoke(
        app,
        ["models", "pull", "--speaker-id", "ecapa", "--cache-dir", "models", "--dry-run"],
    )
    assert result.exit_code == 0
    assert "speechbrain/spkrec-ecapa-voxceleb" in result.stdout


def test_gated_model_dry_run_does_not_require_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("LTM_HF_TOKEN", raising=False)
    result = CliRunner().invoke(
        app,
        [
            "models",
            "pull",
            "--diarization",
            "community-1",
            "--cache-dir",
            str(tmp_path),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "pyannote/speaker-diarization-community-1" in result.stdout
    assert not any(tmp_path.iterdir())
