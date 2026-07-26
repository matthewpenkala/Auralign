# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import local_transcript_max.cli as cli_module
from local_transcript_max.benchmark.corpus import MediaProbe
from local_transcript_max.cli import app
from local_transcript_max.config import preset_config
from local_transcript_max.doctor import DoctorReport
from local_transcript_max.platform_security import ensure_private_directory, ensure_private_file
from local_transcript_max.schemas import Transcript
from local_transcript_max.utils import write_json


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_doctor_suppresses_unredacted_dependency_stderr(monkeypatch) -> None:
    private_path = Path.home() / "private" / "models"

    def fake_doctor(*_args, **_kwargs):
        print(f"native loader failed at {private_path}", file=__import__("sys").stderr)
        return DoctorReport(ok=True, offline=True)

    monkeypatch.setattr("local_transcript_max.cli.run_doctor", fake_doctor)
    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert str(private_path) not in result.output


def test_doctor_json_emits_machine_readable_environment(monkeypatch) -> None:
    private_path = Path.home() / "private" / "models"
    secret = "hf_" + "abcdefghijklmnopqrstuvwxyz"
    report = DoctorReport(
        ok=True,
        offline=True,
        environment={
            "host_capabilities": {"is_native_apple_silicon": True},
            "model_cache": str(private_path),
            "credentials": {"token": secret},
        },
    )
    report.add(
        "model_startup",
        "error",
        f"failed at {private_path}/model.bin; HF_TOKEN={secret}",
    )
    monkeypatch.setattr("local_transcript_max.cli.run_doctor", lambda *_args, **_kwargs: report)

    result = CliRunner().invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = __import__("json").loads(result.stdout)
    assert payload["environment"]["host_capabilities"]["is_native_apple_silicon"] is True
    assert payload["environment"]["model_cache"] == "<local-path>"
    assert payload["environment"]["credentials"] == "<redacted>"
    assert str(private_path) not in result.stdout
    assert secret not in result.stdout
    assert "<local-path>" in payload["checks"][0]["message"]
    assert "<redacted>" in payload["checks"][0]["message"]


def test_apple_silicon_canary_cli_builds_dry_run_request(
    tmp_path,
    monkeypatch,
) -> None:
    from local_transcript_max.benchmark import apple_silicon_runner

    captured = {}

    class FakeResult:
        def model_dump_json(self, *, indent):
            assert indent == 2
            return '{"status":"planned"}'

    def fake_run(request):
        captured["request"] = request
        return FakeResult()

    monkeypatch.setattr(apple_silicon_runner, "run_apple_silicon_canary", fake_run)
    result = CliRunner().invoke(
        app,
        [
            "apple-silicon-canary",
            str(tmp_path / "not-required-for-dry-run.wav"),
            "--num-speakers",
            "4",
            "--out",
            str(tmp_path / "not-created"),
            "--warmup-runs",
            "2",
            "--measured-runs",
            "4",
            "--minimum-diarization-speedup-ratio",
            "0.12",
            "--maximum-total-time-regression-ratio",
            "0.03",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    request = captured["request"]
    assert request.speaker_count == 4
    assert request.options.dry_run is True
    assert request.options.warmup_runs == 2
    assert request.options.measured_runs == 4
    assert request.options.policy.minimum_diarization_speedup_ratio == 0.12
    assert request.options.policy.maximum_total_time_regression_ratio == 0.03
    assert not (tmp_path / "not-created").exists()


def test_apple_silicon_canary_cli_redacts_unexpected_errors(
    tmp_path,
    monkeypatch,
) -> None:
    from local_transcript_max.benchmark import apple_silicon_runner

    private_path = tmp_path / "private-person" / "input.wav"
    secret = "hf_" + "abcdefghijklmnopqrstuvwxyz"

    def fail(_request):
        raise PermissionError(f"failed at {private_path}; HF_TOKEN={secret}")

    monkeypatch.setattr(apple_silicon_runner, "run_apple_silicon_canary", fail)
    result = CliRunner().invoke(
        app,
        [
            "apple-silicon-canary",
            str(private_path),
            "--num-speakers",
            "4",
            "--out",
            str(tmp_path / "not-created"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 1
    assert "PermissionError" in result.stderr
    assert str(private_path) not in result.stderr
    assert secret not in result.stderr
    assert "<local-path>" in result.stderr
    assert "<redacted>" in result.stderr


def test_models_dry_run() -> None:
    result = CliRunner().invoke(
        app, ["models", "pull", "--asr", "large-v3", "--cache-dir", "models", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "Systran/faster-whisper-large-v3" in result.stdout


def test_models_mlx_dry_run_uses_exact_role_selector() -> None:
    result = CliRunner().invoke(
        app,
        [
            "models",
            "pull",
            "--asr-mlx",
            "mlx-tiny",
            "--cache-dir",
            "models",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "asr-mlx:mlx-tiny" in result.stdout
    assert "mlx-community/whisper-tiny-mlx" in result.stdout


@pytest.mark.parametrize(
    ("backend", "selector", "expected_asr"),
    [
        ("faster-whisper", "turbo", "turbo"),
        ("mlx-whisper", "mlx-tiny", "mlx-tiny"),
    ],
)
def test_prepare_runtime_uses_exact_effective_model_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    selector: str,
    expected_asr: str,
) -> None:
    config = preset_config("balanced")
    config.offline = True
    config.model_cache = tmp_path / "models"
    config.asr.backend = backend
    if backend == "mlx-whisper":
        config.asr.mlx_model = selector
    else:
        config.asr.model = selector
    captured: list[str] = []

    monkeypatch.setattr(cli_module, "_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        cli_module,
        "preflight_runtime_preparation",
        lambda entries, *_args, **_kwargs: captured.extend(entry.key for entry in entries),
    )
    monkeypatch.setattr(cli_module, "ensure_pull_preconditions", lambda *_args: None)
    monkeypatch.setattr(
        cli_module,
        "pull_model",
        lambda entry, *_args, **_kwargs: config.model_cache / entry.directory_name,
    )
    monkeypatch.setattr(cli_module, "prepare_generated_punkt", lambda *_args: None)
    monkeypatch.setattr(cli_module, "verify_entries", lambda *_args: [])
    monkeypatch.setattr(cli_module, "write_model_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "_run_doctor_without_upstream_stderr",
        lambda *_args, **_kwargs: DoctorReport(ok=True, offline=True),
    )

    result = CliRunner().invoke(app, ["prepare-runtime"])

    assert result.exit_code == 0, result.output
    assert captured == [expected_asr, "community-1", "en", "ecapa"]


def test_serve_rejects_network_bind_without_explicit_flag() -> None:
    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code == 1
    assert "Refusing to bind" in result.stderr


def test_serve_loopback_runs_without_network_bind_flag(monkeypatch) -> None:
    captured = {}

    def fake_run(app_path, *, host, port):
        captured["app_path"] = app_path
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(app, ["serve", "--host", "127.0.0.1", "--port", "8766"])
    assert result.exit_code == 0
    assert captured == {
        "app_path": "local_transcript_max.server.app:app",
        "host": "127.0.0.1",
        "port": 8766,
    }


def test_serve_network_bind_requires_explicit_flag(monkeypatch) -> None:
    captured = {}

    def fake_run(app_path, *, host, port):
        captured["app_path"] = app_path
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(
        app,
        ["serve", "--host", "0.0.0.0", "--allow-network-bind"],
    )
    assert result.exit_code == 0
    assert captured["host"] == "0.0.0.0"
    assert "unauthenticated local API" in result.stderr


def test_transcribe_debug_report_on_model_failure(tmp_path, monkeypatch) -> None:
    import math
    import wave

    secret = "hf_" + "fake_secret_value_for_cli_smoke"
    monkeypatch.setenv("HF_TOKEN", secret)
    monkeypatch.setenv("LTM_MODEL_CACHE", str(tmp_path / "missing-models"))
    monkeypatch.delenv("LTM_OFFLINE", raising=False)
    monkeypatch.setattr(
        "local_transcript_max.pipeline.validate_compute_type",
        lambda _compute_type, _device: None,
    )
    wav_path = tmp_path / "sample.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        frames = []
        for index in range(800):
            value = int(0.1 * 32767 * math.sin(2 * math.pi * 440 * index / 16000))
            frames.append(value.to_bytes(2, "little", signed=True))
        wav.writeframes(b"".join(frames))
    out_dir = tmp_path / "out"
    result = CliRunner().invoke(
        app,
        ["transcribe", str(wav_path), "--out", str(out_dir), "--debug-report"],
    )
    assert result.exit_code == 1
    report = out_dir / "debug_report.json"
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "ASR model large-v3 is not ready" in text
    assert secret not in text


def test_transcribe_unexpected_error_hides_private_path_and_secret(tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "private-person" / "input.wav"
    audio_path.parent.mkdir()
    audio_path.write_bytes(b"placeholder")
    secret = "hf_" + "private_token_value_for_error"

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"failed reading {audio_path} with {secret}")

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fail)
    result = CliRunner().invoke(app, ["transcribe", str(audio_path)])

    assert result.exit_code == 1
    assert str(audio_path) not in result.stderr
    assert secret not in result.stderr
    assert "<local-path>" in result.stderr
    assert "<redacted>" in result.stderr


def test_transcribe_config_file_values_survive_cli_defaults(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "asr:",
                "  word_timestamps: false",
                "  mlx_vad_windowed: true",
                "  mlx_vad_window_seconds: 12",
                "  mlx_vad_window_padding_seconds: 0.25",
                "diarization:",
                "  backend: off",
                "  enabled: false",
                "alignment:",
                "  backend: fallback",
                "  enabled: true",
                "audio:",
                "  denoise: mild",
                "agentic_diarization:",
                "  enabled: true",
                "redecode:",
                "  mode: hallucination",
                "speaker_id:",
                "  mode: auto",
                "cleanup:",
                "  endpoint: http://127.0.0.1:9999/v1",
            ]
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio_path),
            "--config",
            str(config_path),
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert config.asr.word_timestamps is False
    assert config.asr.mlx_vad_windowed is True
    assert config.asr.mlx_vad_window_seconds == 12
    assert config.asr.mlx_vad_window_padding_seconds == 0.25
    assert config.diarization.backend == "off"
    assert config.diarization.enabled is False
    assert config.alignment.backend == "fallback"
    assert config.audio.denoise == "mild"
    assert config.agentic_diarization.enabled is True
    assert config.redecode.mode == "hallucination"
    assert config.speaker_id.mode == "auto"
    assert config.cleanup.endpoint == "http://127.0.0.1:9999/v1"


def test_transcribe_fast_preset_keeps_fallback_alignment_default(
    tmp_path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")

    result = CliRunner().invoke(
        app,
        ["transcribe", str(audio_path), "--preset", "fast"],
    )

    assert result.exit_code == 0
    assert captured["config"].alignment.backend == "fallback"


def test_transcribe_explicit_offline_cache_and_asr_device_flags(
    tmp_path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")
    cache = tmp_path / "local-models"

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio_path),
            "--offline",
            "--model-cache",
            str(cache),
            "--asr-device",
            "cpu",
            "--model-lifecycle",
            "per-job",
        ],
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert config.offline is True
    assert config.model_cache == cache
    assert config.asr.device == "cpu"
    assert config.runtime.model_lifecycle == "per-job"


def test_speaker_enrollment_does_not_expose_test_fallback_option() -> None:
    result = CliRunner().invoke(app, ["speakers", "enroll", "--help"])

    assert result.exit_code == 0
    assert "allow-fallback-embedding" not in result.stdout


def test_transcribe_cleanup_endpoint_env_survives_cli_default_and_explicit_cli_wins(
    tmp_path, monkeypatch
) -> None:
    captured = []

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured.append(config.cleanup.endpoint)
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    monkeypatch.setenv("LTM_CLEANUP_ENDPOINT", "http://127.0.0.1:9999/v1")
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")

    env_result = CliRunner().invoke(app, ["transcribe", str(audio_path)])
    cli_result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio_path),
            "--cleanup-endpoint",
            "http://127.0.0.1:7777/v1",
        ],
    )

    assert env_result.exit_code == 0
    assert cli_result.exit_code == 0
    assert captured == ["http://127.0.0.1:9999/v1", "http://127.0.0.1:7777/v1"]


@pytest.mark.parametrize(
    "options",
    [
        ["--num-speakers", "0"],
        ["--known-speakers", "-1"],
        ["--min-speakers", "9", "--max-speakers", "2"],
        ["--diarization-candidates", "0"],
        ["--diarization-candidate-range", "0:3"],
        ["--diarization-candidate-range", "4:2"],
    ],
)
def test_transcribe_rejects_invalid_speaker_constraints(tmp_path, monkeypatch, options) -> None:
    monkeypatch.setattr(
        "local_transcript_max.cli.transcribe_file",
        lambda *_args, **_kwargs: Transcript(input_path="unused"),
    )
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")

    result = CliRunner().invoke(app, ["transcribe", str(audio_path), *options])

    assert result.exit_code != 0


def test_transcribe_explicit_cli_values_override_config_file(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("alignment:\n  backend: fallback\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio_path),
            "--config",
            str(config_path),
            "--forced-alignment",
            "off",
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert config.alignment.backend == "off"
    assert config.alignment.enabled is False


def test_transcribe_explicit_mlx_vad_window_flags_override_config_file(
    tmp_path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("asr:\n  mlx_vad_windowed: false\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio_path),
            "--config",
            str(config_path),
            "--asr-mlx-vad-windowed",
            "--asr-mlx-vad-window-seconds",
            "9",
            "--asr-mlx-vad-window-padding-seconds",
            "0.1",
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert config.asr.mlx_vad_windowed is True
    assert config.asr.mlx_vad_window_seconds == 9
    assert config.asr.mlx_vad_window_padding_seconds == 0.1


def test_transcribe_accepts_verified_mlx_backend_from_environment(
    tmp_path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    monkeypatch.setenv("LTM_ASR_BACKEND", "mlx-whisper")
    monkeypatch.setenv("LTM_ASR_MLX_MODEL", "mlx-tiny")
    monkeypatch.setenv("LTM_ASR_MLX_FP16", "0")
    monkeypatch.setenv("LTM_ASR_MLX_VAD_WINDOWED", "1")
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")

    result = CliRunner().invoke(
        app,
        ["transcribe", str(audio_path), "--out", str(tmp_path / "out")],
    )

    assert result.exit_code == 0
    assert captured["config"].asr.backend == "mlx-whisper"
    assert captured["config"].asr.mlx_model == "mlx-tiny"
    assert captured["config"].asr.mlx_fp16 is False
    assert captured["config"].asr.mlx_vad_windowed is True


def test_transcribe_help_exposes_verified_mlx_selector_but_not_arbitrary_path() -> None:
    result = CliRunner().invoke(app, ["transcribe", "--help"])

    assert result.exit_code == 0
    assert "--asr-backend" in result.stdout
    assert "faster-whisper" in result.stdout
    assert "mlx-whisper" in result.stdout
    assert "--asr-mlx-model" in result.stdout
    assert "--asr-mlx-model-path" not in result.stdout
    assert "--asr-mlx-fp16" not in result.stdout
    assert "--asr-mlx-vad-windowed" not in result.stdout


def test_model_and_benchmark_help_expose_only_exact_selection_contracts() -> None:
    pull_help = CliRunner().invoke(app, ["models", "pull", "--help"])
    verify_help = CliRunner().invoke(app, ["models", "verify", "--help"])
    benchmark_help = CliRunner().invoke(app, ["benchmark-corpus", "--help"])

    assert pull_help.exit_code == 0
    assert verify_help.exit_code == 0
    assert benchmark_help.exit_code == 0
    assert "--all" in pull_help.stdout
    assert "--all" in verify_help.stdout
    assert "--include-optional" not in pull_help.stdout
    assert "--include-optional" not in verify_help.stdout
    assert "--asr-mlx-model" in benchmark_help.stdout
    assert "--asr-mlx-model-path" not in benchmark_help.stdout


def test_transcribe_rejects_unknown_asr_model_from_config_before_pipeline(
    tmp_path,
    monkeypatch,
) -> None:
    called = False

    def fake_transcribe_file(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unknown product selection must not reach the pipeline")

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")
    config_path = tmp_path / "unknown.yaml"
    config_path.write_text("asr:\n  model: not-a-reviewed-model\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio_path),
            "--config",
            str(config_path),
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 1
    assert "Unknown asr model" in result.stderr
    assert called is False


def test_transcribe_rejects_unbundled_silero_vad_from_config(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "local_transcript_max.cli.transcribe_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unbundled Silero selector must not reach the pipeline")
        ),
    )
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")
    config_path = tmp_path / "silero.yaml"
    config_path.write_text("asr:\n  vad_filter: true\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio_path),
            "--config",
            str(config_path),
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 1
    assert "Silero model is not part of the verified model component" in result.stderr


def test_transcribe_device_flags_override_config(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "diarization:\n  device: cpu\nalignment:\n  device: cpu\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(audio_path),
            "--config",
            str(config_path),
            "--diarization-device",
            "mps",
            "--alignment-device",
            "mps",
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert config.diarization.device == "mps"
    assert config.alignment.device == "mps"


def test_transcribe_verbose_infer_speakers_and_asr_threads(
    tmp_path,
    monkeypatch,
) -> None:
    captured = {}

    def fake_transcribe_file(
        input_file,
        out_dir,
        config,
        debug_report_path=None,
        progress=None,
    ):
        captured["config"] = config
        assert progress is not None
        progress("asr_start", {"backend": "fake"})
        return Transcript(input_path=str(input_file), speaker_turns=[])

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    media = tmp_path / "2026-06-16 10-55-49_most-recent_5-way_call.mp4"
    media.write_bytes(b"placeholder")

    result = CliRunner().invoke(
        app,
        [
            "transcribe",
            str(media),
            "--infer-speakers",
            "--verbose",
            "--asr-cpu-threads",
            "12",
            "--asr-num-workers",
            "2",
            "--asr-batched",
            "--asr-batch-size",
            "6",
            "--out",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert config.diarization.num_speakers == 5
    assert config.agentic_diarization.known_speakers == 5
    assert config.asr.cpu_threads == 12
    assert config.asr.num_workers == 2
    assert config.asr.batched is True
    assert config.asr.batch_size == 6
    assert "asr_start backend=fake" in result.stderr


def test_inspect_reports_missing_canonical_artifacts_and_fails(tmp_path) -> None:
    write_json(
        tmp_path / "qa_report.json",
        {"summary": {"flag_count": 0, "overall_quality_summary": "ok"}},
    )
    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])
    assert result.exit_code == 1
    assert str(tmp_path) not in result.stdout
    assert "qa_report.json=present" in result.stdout
    assert "canonical.wav=missing" in result.stdout
    assert "transcript.aligned.json=missing" in result.stdout
    assert "alignment_report.json=missing" in result.stdout
    assert "exclusive_diarization.json=missing" in result.stdout
    assert "diarization_decision_report.json=missing" in result.stdout
    assert "diarization_candidates=missing" in result.stdout
    assert "redecode_report.json=missing" in result.stdout
    assert "canonical_artifact_present_count=1" in result.stdout
    assert "artifact_contract_unsafe_count=0" in result.stdout
    assert "qa_report_parse=valid" in result.stdout
    assert "qa_flag_count=0" in result.stdout


def test_inspect_accepts_a_complete_canonical_contract(tmp_path) -> None:
    ensure_private_directory(tmp_path)
    for artifact in cli_module._CANONICAL_JOB_ARTIFACTS:
        artifact_path = tmp_path / artifact
        if artifact == "qa_report.json":
            write_json(
                artifact_path,
                {"summary": {"flag_count": 0, "overall_quality_summary": "ok"}},
            )
        else:
            artifact_path.write_bytes(b"artifact")
            ensure_private_file(artifact_path)

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 0
    assert "canonical_artifact_missing_count=0" in result.stdout
    assert "canonical_artifact_unsafe_count=0" in result.stdout
    assert "qa_report_parse=valid" in result.stdout


def test_inspect_rejects_symlink_and_non_regular_artifacts_without_following_them(
    tmp_path,
) -> None:
    ensure_private_directory(tmp_path)
    private_target = tmp_path / "private-target.json"
    private_target.write_text('{"summary":{"flag_count":99}}', encoding="utf-8")
    (tmp_path / "qa_report.json").symlink_to(private_target)
    (tmp_path / "canonical.wav").mkdir()

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert str(tmp_path) not in result.stdout
    assert "qa_report.json=unsafe_symlink" in result.stdout
    assert "canonical.wav=unsafe_non_regular" in result.stdout
    assert "qa_flag_count=99" not in result.stdout
    assert "qa_report_parse=" not in result.stdout
    assert "artifact_contract_unsafe_count=2" in result.stdout


def test_inspect_rejects_invalid_qa_report_without_exposing_contents(tmp_path) -> None:
    ensure_private_directory(tmp_path)
    private_marker = "PRIVATE_TRANSCRIPT_MARKER"
    qa_path = tmp_path / "qa_report.json"
    qa_path.write_text(private_marker, encoding="utf-8")
    ensure_private_file(qa_path)

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "qa_report.json=present" in result.stdout
    assert "qa_report_parse=invalid" in result.stdout
    assert private_marker not in result.stdout


def test_inspect_rejects_valid_json_with_untrusted_summary_text(tmp_path) -> None:
    private_marker = "PRIVATE_TRANSCRIPT_MARKER"
    write_json(
        tmp_path / "qa_report.json",
        {
            "summary": {
                "flag_count": private_marker,
                "overall_quality_summary": private_marker,
            }
        },
    )

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "qa_report_parse=invalid" in result.stdout
    assert private_marker not in result.stdout


def test_inspect_rejects_qa_symlink_swap_at_open(tmp_path, monkeypatch) -> None:
    target = tmp_path / "private.json"
    write_json(
        target,
        {"summary": {"flag_count": 99, "overall_quality_summary": "ok"}},
    )
    qa_path = tmp_path / "qa_report.json"
    write_json(
        qa_path,
        {"summary": {"flag_count": 0, "overall_quality_summary": "ok"}},
    )
    original_status = cli_module._artifact_status

    def swapping_status(path, *, expected_kind="file"):
        status = original_status(path, expected_kind=expected_kind)
        if path == qa_path and status == "present":
            path.unlink()
            path.symlink_to(target)
        return status

    monkeypatch.setattr(cli_module, "_artifact_status", swapping_status)
    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "qa_report_parse=invalid" in result.stdout
    assert "qa_flag_count=99" not in result.stdout


def test_inspect_rejects_oversize_qa_report(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cli_module, "_MAX_INSPECT_QA_BYTES", 4)
    write_json(
        tmp_path / "qa_report.json",
        {"summary": {"flag_count": 0, "overall_quality_summary": "ok"}},
    )

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "qa_report_parse=invalid" in result.stdout


def test_inspect_rejects_overpermissive_job_and_artifact_modes(tmp_path) -> None:
    write_json(
        tmp_path / "qa_report.json",
        {"summary": {"flag_count": 0, "overall_quality_summary": "ok"}},
    )
    if os.name == "nt":
        for candidate in (tmp_path, tmp_path / "qa_report.json"):
            completed = subprocess.run(
                ["icacls", str(candidate), "/grant", "*S-1-1-0:(F)"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert completed.returncode == 0
    else:
        tmp_path.chmod(0o755)
        (tmp_path / "qa_report.json").chmod(0o644)

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "job_directory=unsafe_permissions" in result.stdout
    assert "qa_report.json" not in result.stdout


def test_inspect_rejects_unsafe_candidate_directory_children(tmp_path) -> None:
    candidates = tmp_path / "diarization_candidates"
    ensure_private_directory(candidates)
    target = tmp_path / "target.json"
    write_json(target, {"private": True})
    (candidates / "candidate_001.json").symlink_to(target)

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "diarization_candidates=unsafe_candidate_contents" in result.stdout


def test_inspect_stops_candidate_iteration_at_entry_limit(tmp_path, monkeypatch) -> None:
    ensure_private_directory(tmp_path)
    candidates = tmp_path / "diarization_candidates"
    candidates.mkdir(mode=0o700)
    monkeypatch.setattr(cli_module, "_MAX_INSPECT_CANDIDATE_ENTRIES", 2)
    for index in range(3):
        write_json(candidates / f"candidate_{index:03d}.json", {})

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "diarization_candidates=unsafe_entry_limit" in result.stdout


def test_inspect_rejects_job_root_symlink_before_reading_children(tmp_path) -> None:
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    write_json(
        external / "qa_report.json",
        {"summary": {"flag_count": 99, "overall_quality_summary": "ok"}},
    )
    alias = tmp_path / "job-alias"
    alias.symlink_to(external, target_is_directory=True)

    result = CliRunner().invoke(app, ["inspect", str(alias)])

    assert result.exit_code == 1
    assert "job_directory=unsafe_symlink" in result.stdout
    assert "qa_report" not in result.stdout
    assert "qa_flag_count=99" not in result.stdout


def test_benchmark_corpus_dry_run_writes_inventory(tmp_path, monkeypatch) -> None:
    corpus = tmp_path / "RECORDED MEETINGS" / "PROJECT"
    corpus.mkdir(parents=True)
    media = corpus / "2026-06-15 12-09-59_meeting.mp4"
    media.write_bytes(b"fake")
    (corpus / "2026-06-15 12-09-59__youtube-transcript-rip.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "local_transcript_max.benchmark.corpus.probe_media",
        lambda path: MediaProbe(
            duration_seconds=123.0,
            container="mov,mp4",
            audio_codec="aac",
            video_codec="h264",
            size_bytes=4,
        ),
    )

    out = tmp_path / "bench"
    result = CliRunner().invoke(
        app,
        [
            "benchmark-corpus",
            str(tmp_path / "RECORDED MEETINGS"),
            "--dry-run",
            "--out",
            str(out),
            "--presets",
            "max,fast",
            "--asr-batched",
            "--asr-batch-size",
            "6",
        ],
    )

    assert result.exit_code == 0
    assert "wrote corpus benchmark report media=1 jobs=2" in result.stdout
    inventory = (out / "corpus_inventory.json").read_text(encoding="utf-8")
    assert '"inferred_speakers": 2' in inventory
    assert "youtube-transcript-rip.srt" in inventory
    planned = (out / "planned_jobs.json").read_text(encoding="utf-8")
    assert "transcribe" in planned
    assert "--asr-batched" in planned
    assert '"asr_batch_size": 6' in planned
