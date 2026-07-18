# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import sys
import types
from pathlib import Path

import local_transcript_max.diagnostics as diagnostics_module
from local_transcript_max.config import load_config
from local_transcript_max.diagnostics import (
    _thermal_state,
    alignment_diagnostic_payload,
    base_debug_report,
    module_status,
    torch_mps_environment,
)
from local_transcript_max.schemas import AlignedWord


def test_physical_memory_handles_platforms_without_sysconf(monkeypatch) -> None:
    monkeypatch.setattr(diagnostics_module.platform, "system", lambda: "Windows")
    monkeypatch.delattr(diagnostics_module.os, "sysconf", raising=False)

    assert diagnostics_module.physical_memory_bytes() is None


def test_runtime_environment_records_torch_and_cpu_thread_controls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    torch = types.ModuleType("torch")
    torch.__version__ = "2.test"  # type: ignore[attr-defined]
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)  # type: ignore[attr-defined]
    torch.backends = types.SimpleNamespace(  # type: ignore[attr-defined]
        mps=types.SimpleNamespace(
            is_built=lambda: True,
            is_available=lambda: True,
        )
    )
    torch.mps = types.SimpleNamespace()  # type: ignore[attr-defined]
    torch.get_num_threads = lambda: 10  # type: ignore[attr-defined]
    torch.get_num_interop_threads = lambda: 4  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setenv("OMP_NUM_THREADS", "10")
    monkeypatch.setenv("MKL_NUM_THREADS", "8")
    monkeypatch.setenv("VECLIB_MAXIMUM_THREADS", "6")
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)

    environment = diagnostics_module.runtime_environment(load_config(cwd=tmp_path))

    assert environment["torch"]["intra_op_threads"] == 10
    assert environment["torch"]["inter_op_threads"] == 4
    assert environment["thread_environment"] == {
        "OMP_NUM_THREADS": "10",
        "MKL_NUM_THREADS": "8",
        "VECLIB_MAXIMUM_THREADS": "6",
        "OPENBLAS_NUM_THREADS": None,
    }

    delattr(torch, "get_num_threads")

    def unavailable_interop_threads() -> int:
        raise RuntimeError("thread pool unavailable")

    torch.get_num_interop_threads = unavailable_interop_threads  # type: ignore[attr-defined]
    optional_info = diagnostics_module.torch_runtime_info()
    assert optional_info["intra_op_threads"] is None
    assert optional_info["inter_op_threads"] is None


def test_power_state_uses_only_the_active_pmset_profile(monkeypatch) -> None:
    monkeypatch.setattr(diagnostics_module.platform, "system", lambda: "Darwin")

    def fake_run_command(command, *, check=False):
        del check
        if command[-1] == "batt":
            return types.SimpleNamespace(
                stdout=(
                    "Now drawing from 'AC Power'\n"
                    " -InternalBattery-0 (id=1)\t80%; charging; 0:00 remaining"
                ),
                stderr="",
                returncode=0,
            )
        if command[-1] == "custom":
            return types.SimpleNamespace(
                stdout=(
                    "Battery Power:\n"
                    " lowpowermode 1\n"
                    " powermode 0\n"
                    "AC Power:\n"
                    " lowpowermode 0\n"
                    " powermode 2\n"
                ),
                stderr="",
                returncode=0,
            )
        return types.SimpleNamespace(
            stdout="CPU_Speed_Limit = 100\n",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(diagnostics_module, "run_command", fake_run_command)

    state = diagnostics_module.power_state()

    assert state["source"] == "AC Power"
    assert state["low_power_mode"] is False
    assert state["power_mode"] == 2


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


def test_mps_environment_and_nominal_thermal_state_are_machine_readable(
    monkeypatch,
) -> None:
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "0")

    environment = torch_mps_environment()
    thermal = _thermal_state(
        "\n".join(
            [
                "Note: No thermal warning level has been recorded",
                "Note: No performance warning level has been recorded",
            ]
        ),
        returncode=0,
    )

    assert environment["PYTORCH_ENABLE_MPS_FALLBACK"] == "0"
    assert thermal == {
        "thermal_state": "nominal",
        "thermal_pressure": "nominal",
        "serious_thermal_pressure": False,
    }


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
    assert payload["alignment_backend"] == "whisper-word-timestamps"
    assert payload["source_counts"] == {"forced": 1, "missing": 1}
    assert payload["forced_aligned_word_count"] == 1
    assert payload["forced_aligned_word_percentage"] == 50.0
    assert payload["whisper_timestamp_word_count"] == 0
    assert payload["estimated_interpolated_word_count"] == 0
    assert payload["missing_timestamp_count"] == 1
    assert payload["timestamp_coverage_percentage"] == 50.0
    assert payload["invalid_or_non_monotonic_timestamp_count"] == 0
    assert payload["missing_timestamp_spans"] == [
        {"start_word_index": 1, "end_word_index": 1, "word_count": 1}
    ]
    assert "private" not in str(payload)
    assert payload["word_warning_counts"] == {"missing_word_timestamp": 1}
