# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from typer.testing import CliRunner

import local_transcript_max.cli as cli_module
from local_transcript_max.benchmark.corpus import MediaProbe
from local_transcript_max.cli import app
from local_transcript_max.schemas import Transcript
from local_transcript_max.utils import write_json


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_models_dry_run() -> None:
    result = CliRunner().invoke(
        app, ["models", "pull", "--asr", "large-v3", "--cache-dir", "models", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "Systran/faster-whisper-large-v3" in result.stdout


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


def test_transcribe_asr_backend_env_survives_cli_defaults(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_transcribe_file(input_file, out_dir, config, debug_report_path=None):
        captured["config"] = config
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe_file)
    monkeypatch.setenv("LTM_ASR_BACKEND", "mlx-whisper")
    monkeypatch.setenv("LTM_ASR_MLX_FP16", "0")
    monkeypatch.setenv("LTM_ASR_MLX_VAD_WINDOWED", "1")
    audio_path = tmp_path / "input.wav"
    audio_path.write_bytes(b"placeholder")

    result = CliRunner().invoke(
        app,
        ["transcribe", str(audio_path), "--out", str(tmp_path / "out")],
    )

    assert result.exit_code == 0
    config = captured["config"]
    assert config.asr.backend == "mlx-whisper"
    assert config.asr.mlx_fp16 is False
    assert config.asr.mlx_vad_windowed is True


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


def test_inspect_reports_artifact_presence(tmp_path) -> None:
    write_json(
        tmp_path / "qa_report.json",
        {"summary": {"flag_count": 0, "overall_quality_summary": "ok"}},
    )
    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])
    assert result.exit_code == 0
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


def test_inspect_rejects_symlink_and_non_regular_artifacts_without_following_them(
    tmp_path,
) -> None:
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
    private_marker = "PRIVATE_TRANSCRIPT_MARKER"
    qa_path = tmp_path / "qa_report.json"
    qa_path.write_text(private_marker, encoding="utf-8")
    qa_path.chmod(0o600)

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
    tmp_path.chmod(0o755)
    (tmp_path / "qa_report.json").chmod(0o644)

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "job_directory=unsafe_permissions" in result.stdout
    assert "qa_report.json" not in result.stdout


def test_inspect_rejects_unsafe_candidate_directory_children(tmp_path) -> None:
    candidates = tmp_path / "diarization_candidates"
    candidates.mkdir(mode=0o700)
    target = tmp_path / "target.json"
    write_json(target, {"private": True})
    (candidates / "candidate_001.json").symlink_to(target)

    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])

    assert result.exit_code == 1
    assert "diarization_candidates=unsafe_candidate_contents" in result.stdout


def test_inspect_stops_candidate_iteration_at_entry_limit(tmp_path, monkeypatch) -> None:
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
