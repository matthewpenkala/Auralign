# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max.config import load_config, model_local_path, resolve_model_entry
from local_transcript_max.errors import LTMError


def test_asr_threading_defaults_are_unset(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    assert config.asr.cpu_threads is None
    assert config.asr.num_workers is None


def test_preset_config_uses_confirmed_model_ids(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    entry = resolve_model_entry("asr", config.asr.model)
    assert entry is not None
    assert entry.model_id == "Systran/faster-whisper-large-v3"
    assert "large-v3-turbo" not in entry.model_id
    assert model_local_path(entry, tmp_path / "models").name == "Systran--faster-whisper-large-v3"


def test_env_overrides_model_cache_and_offline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_MODEL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("LTM_OFFLINE", "1")
    monkeypatch.setenv("LTM_DEVICE", "cpu")
    monkeypatch.setenv("LTM_ASR_CPU_THREADS", "12")
    monkeypatch.setenv("LTM_ASR_NUM_WORKERS", "2")
    monkeypatch.setenv("LTM_ASR_BATCHED", "1")
    monkeypatch.setenv("LTM_ASR_BATCH_SIZE", "6")
    monkeypatch.setenv("LTM_ASR_BACKEND", "mlx-whisper")
    monkeypatch.setenv("LTM_ASR_MLX_MODEL", "mlx-large-v3-turbo")
    monkeypatch.setenv("LTM_ASR_MLX_MODEL_PATH", str(tmp_path / "mlx-model"))
    monkeypatch.setenv("LTM_ASR_MLX_FP16", "0")
    monkeypatch.setenv("LTM_ASR_MLX_VAD_WINDOWED", "1")
    monkeypatch.setenv("LTM_ASR_MLX_VAD_WINDOW_SECONDS", "20.5")
    monkeypatch.setenv("LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS", "0.25")
    monkeypatch.setenv("LTM_ALIGNMENT_DEVICE", "mps")
    config = load_config(cwd=tmp_path)
    assert config.model_cache == tmp_path / "cache"
    assert config.offline is True
    assert config.asr.device == "cpu"
    assert config.asr.cpu_threads == 12
    assert config.asr.num_workers == 2
    assert config.asr.batched is True
    assert config.asr.batch_size == 6
    assert config.asr.backend == "mlx-whisper"
    assert config.asr.mlx_model == "mlx-large-v3-turbo"
    assert config.asr.mlx_model_path == tmp_path / "mlx-model"
    assert config.asr.mlx_fp16 is False
    assert config.asr.mlx_vad_windowed is True
    assert config.asr.mlx_vad_window_seconds == 20.5
    assert config.asr.mlx_vad_window_padding_seconds == 0.25
    assert config.alignment.device == "mps"


def test_empty_asr_thread_env_values_behave_like_unset(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_ASR_CPU_THREADS", "")
    monkeypatch.setenv("LTM_ASR_NUM_WORKERS", " ")
    config = load_config(cwd=tmp_path)
    assert config.asr.cpu_threads is None
    assert config.asr.num_workers is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LTM_ASR_CPU_THREADS", "0"),
        ("LTM_ASR_CPU_THREADS", "-1"),
        ("LTM_ASR_CPU_THREADS", "many"),
        ("LTM_ASR_NUM_WORKERS", "0"),
        ("LTM_ASR_NUM_WORKERS", "-1"),
        ("LTM_ASR_NUM_WORKERS", "many"),
        ("LTM_ASR_BATCH_SIZE", "0"),
        ("LTM_ASR_BATCH_SIZE", "-1"),
        ("LTM_ASR_BATCH_SIZE", "many"),
    ],
)
def test_invalid_asr_thread_env_values_are_rejected(
    tmp_path: Path,
    monkeypatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(LTMError, match="positive integer"):
        load_config(cwd=tmp_path)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("LTM_ASR_MLX_VAD_WINDOW_SECONDS", "0", "greater than 0"),
        ("LTM_ASR_MLX_VAD_WINDOW_SECONDS", "-1", "non-negative number"),
        ("LTM_ASR_MLX_VAD_WINDOW_SECONDS", "many", "non-negative number"),
        ("LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS", "-1", "non-negative number"),
        ("LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS", "many", "non-negative number"),
    ],
)
def test_invalid_mlx_vad_window_env_values_are_rejected(
    tmp_path: Path,
    monkeypatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(LTMError, match=message):
        load_config(cwd=tmp_path)


def test_invalid_asr_thread_config_file_values_are_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "asr:",
                "  cpu_threads: 0",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(LTMError, match="Invalid config file") as exc_info:
        load_config(config_file=config_path, cwd=tmp_path)
    assert "cpu_threads" in str(exc_info.value)


def test_asr_batched_config_file_values_are_honored(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "asr:",
                "  batched: true",
                "  batch_size: 4",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_file=config_path, cwd=tmp_path)
    assert config.asr.batched is True
    assert config.asr.batch_size == 4


def test_asr_mlx_config_file_values_are_honored(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    model_path = tmp_path / "mlx-model"
    config_path.write_text(
        "\n".join(
            [
                "asr:",
                "  backend: mlx-whisper",
                "  mlx_model: mlx-large-v3-turbo",
                f"  mlx_model_path: {model_path}",
                "  mlx_fp16: false",
                "  mlx_vad_windowed: true",
                "  mlx_vad_window_seconds: 15",
                "  mlx_vad_window_padding_seconds: 0.5",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_file=config_path, cwd=tmp_path)
    assert config.asr.backend == "mlx-whisper"
    assert config.asr.mlx_model == "mlx-large-v3-turbo"
    assert config.asr.mlx_model_path == model_path
    assert config.asr.mlx_fp16 is False
    assert config.asr.mlx_vad_windowed is True
    assert config.asr.mlx_vad_window_seconds == 15
    assert config.asr.mlx_vad_window_padding_seconds == 0.5


def test_subtitle_export_config_file_values_are_honored(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "subtitle_export:",
                "  split_long_cues: false",
                "  max_line_width: 32",
                "  max_line_count: 1",
                "  speaker_labels: false",
                "  use_mapped_names: false",
                "  highlight_words: true",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_file=config_path, cwd=tmp_path)

    assert config.subtitle_export.split_long_cues is False
    assert config.subtitle_export.max_line_width == 32
    assert config.subtitle_export.max_line_count == 1
    assert config.subtitle_export.speaker_labels is False
    assert config.subtitle_export.use_mapped_names is False
    assert config.subtitle_export.highlight_words is True


def test_invalid_asr_backend_env_value_is_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LTM_ASR_BACKEND", "metal-magic")
    with pytest.raises(LTMError, match="LTM_ASR_BACKEND"):
        load_config(cwd=tmp_path)


def test_config_file_model_cache_is_honored_without_env_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("LTM_MODEL_CACHE", raising=False)
    config_path = tmp_path / "config.yaml"
    configured_cache = tmp_path / "configured-models"
    config_path.write_text(f"model_cache: {configured_cache}\n", encoding="utf-8")
    config = load_config(config_file=config_path, cwd=tmp_path)
    assert config.model_cache == configured_cache


def test_balanced_preset_uses_cpu_supported_compute_type(tmp_path: Path) -> None:
    config = load_config(preset="balanced", cwd=tmp_path)
    assert config.asr.compute_type == "int8_float32"


def test_split_device_env_overrides_keep_asr_and_diarization_separate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("LTM_ASR_DEVICE", "cpu")
    monkeypatch.setenv("LTM_DIARIZATION_DEVICE", "mps")
    monkeypatch.setenv("LTM_ALIGNMENT_DEVICE", "cpu")
    config = load_config(cwd=tmp_path)
    assert config.asr.device == "cpu"
    assert config.diarization.device == "mps"
    assert config.alignment.device == "cpu"


def test_alignment_auto_alias_resolves_manifest_entry() -> None:
    entry = resolve_model_entry("alignment", "auto")
    assert entry is not None
    assert entry.key == "en"


def test_legacy_qa_targeted_redecode_enables_low_confidence_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "qa:",
                "  targeted_redecode: true",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(config_file=config_path, cwd=tmp_path)
    assert config.redecode.mode == "low-confidence"


def test_legacy_redecode_repaired_transcript_true_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "redecode:\n  write_repaired_transcript: true\n",
        encoding="utf-8",
    )

    with pytest.raises(LTMError, match="candidate-only"):
        load_config(config_file=config_path, cwd=tmp_path)


def test_legacy_redecode_repaired_transcript_false_remains_accepted(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "redecode:\n  write_repaired_transcript: false\n",
        encoding="utf-8",
    )

    config = load_config(config_file=config_path, cwd=tmp_path)
    assert config.redecode.write_repaired_transcript is False


def test_speaker_identity_config_defaults_to_off(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)

    assert config.speaker_identity.mode == "off"
    assert config.speaker_identity.known_speakers is None
    assert config.speaker_identity.roster_path is None
    assert config.speaker_identity.write_identity_mapped_exports is False
    assert config.speaker_identity.deliverable_format == "srt"


def test_speaker_identity_config_file_values_are_honored(tmp_path: Path) -> None:
    roster = tmp_path / "roster.json"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "speaker_identity:",
                "  mode: apply-safe",
                "  known_speakers: 4",
                f"  roster_path: {roster}",
                "  write_identity_mapped_exports: true",
                "  audit_csv: false",
                "  deliverable_format: all",
                "  source_media_review: none",
                "  turn_boundary_repair: label-only",
                "  min_apply_confidence: 0.95",
                "  max_fragment_words: 8",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_file=config_path, cwd=tmp_path)

    assert config.speaker_identity.mode == "apply-safe"
    assert config.speaker_identity.known_speakers == 4
    assert config.speaker_identity.roster_path == roster
    assert config.speaker_identity.write_identity_mapped_exports is True
    assert config.speaker_identity.audit_csv is False
    assert config.speaker_identity.deliverable_format == "all"
    assert config.speaker_identity.source_media_review == "none"
    assert config.speaker_identity.turn_boundary_repair == "label-only"
    assert config.speaker_identity.min_apply_confidence == 0.95
    assert config.speaker_identity.max_fragment_words == 8


@pytest.mark.parametrize(
    "speaker_identity_yaml",
    [
        "mode: apply-approved",
        "mode: apply-safe\nknown_speakers: 2",
        "mode: review\napproved_map_path: approval.json",
        "mode: review\nsource_media_review: reviewed-report",
        "mode: review\nmedia_review_report_path: report.json",
        "mode: review\nmin_apply_confidence: .nan",
        "mode: review\nmax_sibling_jobs: 101",
        "mode: review\nturn_boundary_repair: structural",
    ],
)
def test_invalid_speaker_identity_config_is_rejected(
    tmp_path: Path,
    speaker_identity_yaml: str,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "speaker_identity:\n  " + speaker_identity_yaml.replace("\n", "\n  ") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(LTMError, match="Invalid config file"):
        load_config(config_file=config_path, cwd=tmp_path)


def test_unsupported_nemo_diarization_config_is_rejected(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("diarization:\n  backend: nemo-sortformer\n", encoding="utf-8")

    with pytest.raises(LTMError, match="Invalid config file"):
        load_config(config_file=config_path, cwd=tmp_path)


@pytest.mark.parametrize(
    "payload",
    [
        "diarization:\n  num_speakers: 0\n",
        "diarization:\n  min_speakers: 9\n  max_speakers: 2\n",
        "agentic_diarization:\n  candidates: 0\n",
        "agentic_diarization:\n  known_speakers: -4\n",
        "agentic_diarization:\n  candidate_range: 0:3\n",
        "agentic_diarization:\n  candidate_range: 4:2\n",
        "agentic_diarization:\n  candidate_range: invalid\n",
    ],
)
def test_invalid_diarization_count_config_is_rejected(tmp_path: Path, payload: str) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(payload, encoding="utf-8")

    with pytest.raises(LTMError, match="Invalid config file"):
        load_config(config_file=config_path, cwd=tmp_path)
