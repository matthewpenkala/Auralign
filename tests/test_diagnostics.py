# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

from local_transcript_max.config import load_config
from local_transcript_max.diagnostics import (
    alignment_diagnostic_payload,
    base_debug_report,
    module_status,
)
from local_transcript_max.schemas import AlignedWord


def test_debug_report_reflects_active_runtime_features(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.redecode.mode = "low-confidence"
    config.redecode.write_report = True
    config.speaker_id.mode = "auto"
    config.diarization.device = "mps"
    config.alignment.device = "mps"
    config.cleanup.mode = "local-llm"
    config.cleanup.endpoint = "http://user:pass@127.0.0.1:11434/v1?token=secret"
    config.cleanup.write_diff = True

    report = base_debug_report(tmp_path / "input.wav", tmp_path / "out", config)

    assert report["targeted_redecode_config"]["enabled"] is True
    assert report["targeted_redecode_config"]["mode"] == "low-confidence"
    assert report["targeted_redecode_config"]["replacement_policy"] == (
        "preserve_raw_record_candidates_only"
    )
    assert report["targeted_redecode_config"]["temporary_audio_policy"] == (
        "private_ephemeral_cleanup"
    )
    assert report["targeted_redecode_config"]["candidate_timestamp_coordinates"] == [
        "window_relative",
        "canonical",
    ]
    assert report["speaker_id_config"]["enabled"] is True
    assert report["speaker_id_config"]["mode"] == "auto"
    assert report["cleanup_config"]["enabled"] is True
    assert report["cleanup_config"]["mode"] == "local-llm"
    assert "user:pass" not in report["cleanup_config"]["endpoint"]
    assert "token=secret" not in report["cleanup_config"]["endpoint"]
    assert "asr_device_compute" in report["environment"]
    assert report["environment"]["diarization_device"]["requested_device"] == "mps"
    assert report["environment"]["alignment_device"]["requested_device"] == "mps"
    asr_device_compute = report["environment"]["asr_device_compute"]
    assert asr_device_compute["requested_cpu_threads"] is None
    assert asr_device_compute["effective_cpu_threads"] is None
    assert asr_device_compute["cpu_threads_state"] == "unset"
    assert asr_device_compute["requested_num_workers"] is None
    assert asr_device_compute["effective_num_workers"] is None
    assert asr_device_compute["num_workers_state"] == "unset"
    assert "deferred_to" not in report["targeted_redecode_config"]
    assert "deferred_to" not in report["cleanup_config"]


def test_debug_report_records_explicit_asr_threading(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.device = "cpu"
    config.asr.cpu_threads = 8
    config.asr.num_workers = 2

    report = base_debug_report(tmp_path / "input.wav", tmp_path / "out", config)
    asr_device_compute = report["environment"]["asr_device_compute"]

    assert asr_device_compute["requested_cpu_threads"] == 8
    assert asr_device_compute["effective_cpu_threads"] == 8
    assert asr_device_compute["cpu_threads_state"] == "explicit"
    assert asr_device_compute["requested_num_workers"] == 2
    assert asr_device_compute["effective_num_workers"] == 2
    assert asr_device_compute["num_workers_state"] == "explicit"


def test_debug_report_records_mlx_backend_without_ct2_resolution(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    config.asr.cpu_threads = 8
    config.asr.num_workers = 2

    report = base_debug_report(tmp_path / "input.wav", tmp_path / "out", config)
    asr_device_compute = report["environment"]["asr_device_compute"]

    assert report["device"] == "mlx"
    assert report["compute_type"] == "float16"
    assert asr_device_compute["backend"] == "mlx-whisper"
    assert asr_device_compute["runtime_downloads_disabled"] is True
    assert asr_device_compute["effective_cpu_threads"] is None
    assert asr_device_compute["cpu_threads_state"] == "ignored"
    assert asr_device_compute["effective_num_workers"] is None
    assert asr_device_compute["num_workers_state"] == "ignored"
    assert asr_device_compute["model_complete"] is False


def test_module_status_handles_missing_parent_package() -> None:
    status = module_status(
        "local_transcript_max_missing_optional_parent.child",
        "local-transcript-max-missing-optional-parent",
    )

    assert status == {"available": False, "version": None}


def test_alignment_diagnostic_payload_counts_sources_without_text() -> None:
    payload = alignment_diagnostic_payload(
        requested_backend="auto",
        backend="whisper-word-timestamps",
        device=None,
        words=[
            AlignedWord(text="private", start=0.0, end=0.1, alignment_source="forced"),
            AlignedWord(
                text="content",
                alignment_source="missing",
                warnings=["missing_word_timestamp"],
            ),
        ],
        warnings=["forced alignment unavailable"],
        duration_seconds=1.25,
    )

    assert payload["fallback_used"] is True
    assert payload["source_counts"] == {"forced": 1, "missing": 1}
    assert payload["missing_timestamp_count"] == 1
    assert payload["missing_timestamp_spans"] == [
        {"start_word_index": 1, "end_word_index": 1, "word_count": 1}
    ]
    assert "private" not in str(payload)
    assert payload["word_warning_counts"] == {"missing_word_timestamp": 1}
