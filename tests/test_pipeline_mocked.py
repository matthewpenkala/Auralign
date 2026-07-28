# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import math
import os
import stat
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_transcript_max import pipeline as pipeline_module
from local_transcript_max.config import load_config
from local_transcript_max.errors import BackendUnavailableError, LTMError
from local_transcript_max.pipeline import preflight_required_models, transcribe_file
from local_transcript_max.platform_security import private_path_is_secure
from local_transcript_max.runtime_policy import HostCapabilities, resolve_runtime_policy
from local_transcript_max.schemas import (
    AlignedWord,
    ASRResult,
    ASRSegment,
    DiarizationResult,
    DiarizationTurn,
    StageTiming,
)
from local_transcript_max.utils import read_json, sha256_file


def mocked_config(tmp_path: Path):
    config = load_config(cwd=tmp_path)
    config.speaker_id.mode = "off"
    config.alignment.backend = "fallback"
    return config


class MockASR:
    name = "mock-asr"

    def transcribe(self, audio_path: Path, config):
        return ASRResult(
            segments=[
                ASRSegment(
                    id=0,
                    start=0,
                    end=1,
                    text="hello world",
                    words=[
                        AlignedWord(text="hello", start=0.0, end=0.4, alignment_source="whisper"),
                        AlignedWord(text="world", start=0.5, end=1.0, alignment_source="whisper"),
                    ],
                )
            ],
            language="en",
            raw={"segments": [{"text": "hello world"}]},
        )


def test_detailed_backend_timings_preserve_asr_backend_scope() -> None:
    rows = pipeline_module.detailed_backend_timings(
        asr_result=ASRResult(
            segments=[],
            config={
                "timing": {
                    "model_load_seconds": 1.25,
                    "inference_seconds": 2.5,
                }
            },
            timing=StageTiming(name="asr", duration_seconds=4.0),
        ),
        alignment=pipeline_module.AlignmentOutcome(
            words=[],
            backend="test",
            warnings=[],
        ),
        diarization_result=DiarizationResult(turns=[]),
    )

    assert {row.name: row.duration_seconds for row in rows} == {
        "asr_backend_total": 4.0,
        "asr_model_load": 1.25,
        "asr_inference": 2.5,
    }


class FragmentASR(MockASR):
    def transcribe(self, audio_path: Path, config):
        return ASRResult(
            segments=[
                ASRSegment(
                    id=0,
                    start=0,
                    end=1.2,
                    text="hello small world",
                    words=[
                        AlignedWord(text="hello", start=0.0, end=0.4),
                        AlignedWord(text="small", start=0.5, end=0.7),
                        AlignedWord(text="world", start=0.8, end=1.2),
                    ],
                )
            ],
            language="en",
            raw={"segments": [{"text": "hello small world"}]},
        )


class RecordingASR(MockASR):
    def __init__(self) -> None:
        self.audio_paths: list[Path] = []

    def transcribe(self, audio_path: Path, config):
        self.audio_paths.append(audio_path)
        return super().transcribe(audio_path, config)


class SourceMutatingASR(MockASR):
    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path

    def transcribe(self, audio_path: Path, config):
        with self.source_path.open("ab") as handle:
            handle.write(b"changed-during-run")
        return super().transcribe(audio_path, config)


def test_offline_transcribe_configures_fail_closed_environment_before_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"placeholder")
    config = mocked_config(tmp_path)
    config.offline = True
    observed: list[Path] = []

    def configure(*, nltk_data: Path | None = None) -> None:
        assert nltk_data is not None
        observed.append(nltk_data)

    def stop_after_environment(_config) -> None:
        raise LTMError("stop after environment")

    monkeypatch.setattr(pipeline_module, "configure_offline_environment", configure)
    monkeypatch.setattr(pipeline_module, "preflight_required_models", stop_after_environment)

    with pytest.raises(LTMError, match="stop after environment"):
        transcribe_file(input_path, tmp_path / "out", config)

    assert observed == [config.model_cache / "runtime-assets" / "nltk_data"]


def test_ecapa_classifier_is_loaded_during_model_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_transcript_max.speaker_id import cluster_embeddings

    config = mocked_config(tmp_path)
    config.diarization.enabled = False
    config.alignment.enabled = False
    config.speaker_id.mode = "auto"
    config.speaker_id.device = "cpu"
    model_path = tmp_path / "models" / "ecapa"
    loaded: list[tuple[str, str]] = []
    monkeypatch.setattr(pipeline_module, "validate_compute_type", lambda *_args: None)
    monkeypatch.setattr(pipeline_module, "require_model", lambda *_args, **_kwargs: model_path)
    monkeypatch.setattr(cluster_embeddings, "_resolve_device", lambda requested: "cpu")
    monkeypatch.setattr(
        cluster_embeddings,
        "_load_classifier",
        lambda path, device: loaded.append((path, device)),
    )

    preflight_required_models(config)

    assert loaded == [(str(model_path), "cpu")]


def test_ecapa_classifier_startup_failure_is_a_preflight_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_transcript_max.speaker_id import cluster_embeddings

    config = mocked_config(tmp_path)
    config.diarization.enabled = False
    config.alignment.enabled = False
    config.speaker_id.mode = "auto"
    model_path = tmp_path / "models" / "ecapa"
    monkeypatch.setattr(pipeline_module, "validate_compute_type", lambda *_args: None)
    monkeypatch.setattr(pipeline_module, "require_model", lambda *_args, **_kwargs: model_path)
    monkeypatch.setattr(cluster_embeddings, "_resolve_device", lambda requested: "cpu")
    monkeypatch.setattr(
        cluster_embeddings,
        "_load_classifier",
        lambda _path, _device: (_ for _ in ()).throw(RuntimeError("HyperPyYAML failed")),
    )

    with pytest.raises(BackendUnavailableError, match="ECAPA speaker-ID preflight failed"):
        preflight_required_models(config)


class MockMlxASR(MockASR):
    name = "mlx-whisper"

    def transcribe(self, audio_path: Path, config):
        result = super().transcribe(audio_path, config)
        result.config = {
            "backend": "mlx-whisper",
            "compute_type": "float16",
            "model_path": str(config.model_cache / "mlx-community--whisper-large-v3-turbo"),
        }
        return result


class MockDiarization:
    name = "mock-diarization"

    def diarize(self, audio_path: Path, config):
        return DiarizationResult(
            turns=[DiarizationTurn(start=0, end=1.2, speaker="SPEAKER_00", source="mock")],
            exclusive_turns=[
                DiarizationTurn(start=0, end=1.2, speaker="SPEAKER_00", source="mock-exclusive")
            ],
            metadata={
                "backend": "mock",
                "device": config.diarization.device,
                "requested_device": config.diarization.device,
                "exclusive_diarization_support": True,
            },
        )


class ProgressDiarization(MockDiarization):
    def diarize_with_progress(self, audio_path: Path, config, progress):
        progress({"step": "segmentation", "completed": 1, "total": 2, "fraction": 0.5})
        return self.diarize(audio_path, config)


class FragmentDiarization:
    name = "mock-diarization"

    def diarize(self, audio_path: Path, config):
        turns = [
            DiarizationTurn(start=0.0, end=0.4, speaker="SPEAKER_00", source="mock"),
            DiarizationTurn(start=0.8, end=1.2, speaker="SPEAKER_00", source="mock"),
        ]
        return DiarizationResult(
            turns=turns,
            exclusive_turns=turns,
            metadata={
                "backend": "mock",
                "device": config.diarization.device,
                "requested_device": config.diarization.device,
                "exclusive_diarization_support": True,
            },
        )


def write_tiny_wav(path: Path) -> None:
    rate = 16000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = []
        for index in range(rate // 5):
            value = int(0.1 * 32767 * math.sin(2 * math.pi * 220 * index / rate))
            frames.append(value.to_bytes(2, "little", signed=True))
        wav.writeframes(b"".join(frames))


def test_mocked_pipeline_writes_phase1_artifacts(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    config.asr.cpu_threads = 8
    config.asr.num_workers = 2
    transcript = transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=MockASR(),
        diarization_backend=MockDiarization(),
        precheck_models=False,
    )
    assert transcript.speaker_turns[0].speaker == "SPEAKER_00"
    expected_artifacts = {
        "audio_metadata.json",
        "transcript.raw.json",
        "transcript.aligned.json",
        "alignment_report.json",
        "transcript.diarized.json",
        "transcript.json",
        "transcript.jsonl",
        "transcript.txt",
        "transcript.md",
        "transcript.srt",
        "transcript.vtt",
        "transcript.rttm",
        "diarization.rttm",
        "diarization.json",
        "exclusive_diarization.json",
        "words.csv",
        "qa_report.json",
        "run_metadata.json",
        "completion_timing.json",
    }
    assert expected_artifacts.issubset({path.name for path in out_dir.iterdir()})
    assert read_json(out_dir / "transcript.raw.json")["raw"]["segments"][0]["text"] == "hello world"
    fused = read_json(out_dir / "transcript.json")
    assert fused["exclusive_diarization_turns"][0]["source"] == "mock-exclusive"
    assert read_json(out_dir / "run_metadata.json")["asr_backend"] == "mock-asr"
    run_metadata = read_json(out_dir / "run_metadata.json")
    assert run_metadata["input_file_hash"] == sha256_file(input_path)
    assert len(run_metadata["source_config_sha256"]) == 64
    assert (
        run_metadata["source_config_sha256_scope"]
        == "complete_runtime_configuration.v1"
    )
    assert (
        run_metadata["source_configuration_schema"]
        == "privacy_safe_configuration.v1"
    )
    assert run_metadata["source_configuration"]["model_cache"] == {
        "configured": True,
        "kind": "local_path",
    }
    assert run_metadata["source_configuration"]["asr"]["device"] == "auto"
    assert run_metadata["code_revision"]
    assert run_metadata["alignment_backend"] == "whisper-word-timestamps"
    alignment_report = read_json(out_dir / "alignment_report.json")
    assert alignment_report["backend"] == "whisper-word-timestamps"
    assert alignment_report["word_count"] == 2
    assert alignment_report["source_counts"] == {"whisper": 2}
    assert run_metadata["alignment_device"] is None
    assert run_metadata["diarization_requested_device"] == "auto"
    assert run_metadata["diarization_resolved_device"] == "auto"
    assert run_metadata["diarization_actual_device"] == "auto"
    assert run_metadata["diarization_device_fallback"] is False
    assert run_metadata["diarization_warnings"] == []
    assert run_metadata["asr_cpu_threads"] == 8
    assert run_metadata["asr_num_workers"] == 2
    assert run_metadata["audio_preprocessing"]["action"] == "reused"
    assert run_metadata["audio_preprocessing"]["ffmpeg_invoked"] is False
    asr_device_compute = run_metadata["runtime_environment"]["asr_device_compute"]
    assert asr_device_compute["requested_cpu_threads"] == 8
    assert asr_device_compute["effective_cpu_threads"] == 8
    assert asr_device_compute["requested_num_workers"] == 2
    assert asr_device_compute["effective_num_workers"] == 2
    assert run_metadata["resource_usage"]["sample_count"] >= 1
    assert "rss_peak_sampled_bytes" in run_metadata["resource_usage"]
    assert run_metadata["resource_usage"]["stage_samples"]
    assert run_metadata["device_policy"]["asr"]["requested_device"] == "auto"
    timing_names = {timing["name"] for timing in run_metadata["timings"]}
    assert {
        "preflight",
        "media_probe",
        "audio_preprocessing",
        "vad",
        "asr",
        "alignment",
        "speaker_assignment",
        "qa",
        "serialization",
        "pipeline_core",
    }.issubset(timing_names)
    completion_timing = read_json(out_dir / "completion_timing.json")
    assert completion_timing["schema_version"] == "completion_timing.v1"
    assert completion_timing["run_metadata_sha256"] == sha256_file(
        out_dir / "run_metadata.json"
    )
    assert (
        completion_timing["observed_end_to_end_seconds"]
        >= completion_timing["pipeline_core_seconds"]
    )
    assert completion_timing["stage_durations"]["invocation_setup"] >= 0
    assert completion_timing["resource_usage"]["stage_samples"][-1]["stage"] == (
        "pipeline_end_to_end_complete"
    )


def test_completion_timing_includes_model_audit_preflight(
    tmp_path: Path,
    monkeypatch,
) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    original = pipeline_module.base_debug_report

    def delayed_debug_report(*args, **kwargs):
        time.sleep(0.03)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        pipeline_module,
        "base_debug_report",
        delayed_debug_report,
    )

    transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=MockASR(),
        diarization_backend=MockDiarization(),
        precheck_models=False,
    )

    completion = read_json(out_dir / "completion_timing.json")
    assert completion["pipeline_core_seconds"] >= 0.025
    assert completion["observed_end_to_end_seconds"] >= 0.025


def test_pipeline_runtime_policy_cannot_be_bypassed_with_model_precheck_off(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    config.alignment.backend = "forced"
    config.alignment.device = "mps"
    config.alignment.allow_experimental_mps = False

    with pytest.raises(LTMError, match="alignment_mps_approval_required"):
        transcribe_file(
            input_path,
            out_dir,
            config,
            asr_backend=MockASR(),
            diarization_backend=MockDiarization(),
            precheck_models=False,
        )


def test_pipeline_fails_closed_when_input_changes_during_processing(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    initial_hash = sha256_file(input_path)
    config = mocked_config(tmp_path)

    with pytest.raises(LTMError, match="Input media changed while processing"):
        transcribe_file(
            input_path,
            out_dir,
            config,
            asr_backend=SourceMutatingASR(input_path),
            diarization_backend=MockDiarization(),
            precheck_models=False,
        )

    assert sha256_file(input_path) != initial_hash
    assert (out_dir / "canonical.wav").exists()
    assert not (out_dir / "run_metadata.json").exists()


def test_pipeline_honors_disabled_diarization_decision_report(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    config.agentic_diarization.enabled = True
    config.agentic_diarization.known_speakers = 1
    config.agentic_diarization.write_decision_report = False

    transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=MockASR(),
        diarization_backend=MockDiarization(),
        precheck_models=False,
    )

    assert (out_dir / "diarization_candidates" / "candidate_001.json").exists()
    assert not (out_dir / "diarization_decision_report.json").exists()


def test_pipeline_accepts_an_empty_precreated_job_directory(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    out_dir.mkdir()
    out_dir.chmod(0o755)

    transcript = transcribe_file(
        input_path,
        out_dir,
        mocked_config(tmp_path),
        asr_backend=MockASR(),
        diarization_backend=MockDiarization(),
        precheck_models=False,
    )

    assert transcript.speaker_turns[0].speaker == "SPEAKER_00"
    assert (out_dir / "run_metadata.json").is_file()
    assert private_path_is_secure(out_dir, directory=True)
    assert private_path_is_secure(out_dir / "canonical.wav")


def test_pipeline_rejects_existing_job_artifacts_without_overwriting(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    out_dir.mkdir()
    existing = out_dir / "transcript.raw.json"
    existing.write_bytes(b"preserve-existing-job")

    with pytest.raises(LTMError, match="already contains Auralign pipeline artifacts"):
        transcribe_file(
            input_path,
            out_dir,
            mocked_config(tmp_path),
            asr_backend=MockASR(),
            diarization_backend=MockDiarization(),
            precheck_models=False,
        )

    assert existing.read_bytes() == b"preserve-existing-job"
    assert {path.name for path in out_dir.iterdir()} == {"transcript.raw.json"}


def test_pipeline_rejects_nonempty_unrelated_output_directory(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    out_dir.mkdir(mode=0o755)
    unrelated = out_dir / "keep.txt"
    unrelated.write_bytes(b"preserve")

    with pytest.raises(LTMError, match="must be empty"):
        transcribe_file(
            input_path,
            out_dir,
            mocked_config(tmp_path),
            asr_backend=MockASR(),
            diarization_backend=MockDiarization(),
            precheck_models=False,
        )

    assert unrelated.read_bytes() == b"preserve"
    if os.name != "nt":
        assert stat.S_IMODE(out_dir.stat().st_mode) == 0o755


def test_pipeline_rejects_existing_candidate_directory_symlink(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    external = tmp_path / "external"
    write_tiny_wav(input_path)
    out_dir.mkdir()
    external.mkdir()
    original_mode = stat.S_IMODE(external.stat().st_mode)
    (out_dir / "diarization_candidates").symlink_to(external, target_is_directory=True)

    with pytest.raises(LTMError, match="already contains Auralign pipeline artifacts"):
        transcribe_file(
            input_path,
            out_dir,
            mocked_config(tmp_path),
            asr_backend=MockASR(),
            diarization_backend=MockDiarization(),
            precheck_models=False,
        )

    if os.name != "nt":
        assert stat.S_IMODE(external.stat().st_mode) == original_mode
    assert not list(external.iterdir())


def test_pipeline_rejects_resolved_input_alias_of_canonical_destination(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    canonical = out_dir / "canonical.wav"
    write_tiny_wav(canonical)
    alias = tmp_path / "source-alias.wav"
    alias.symlink_to(canonical)
    original = canonical.read_bytes()
    before = canonical.stat()

    with pytest.raises(LTMError, match="resolves to the job's canonical.wav destination"):
        transcribe_file(
            alias,
            out_dir,
            mocked_config(tmp_path),
            asr_backend=MockASR(),
            diarization_backend=MockDiarization(),
            precheck_models=False,
        )

    after = canonical.stat()
    assert canonical.read_bytes() == original
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    assert alias.samefile(canonical)
    assert {path.name for path in out_dir.iterdir()} == {"canonical.wav"}


def test_vad_is_metadata_and_asr_uses_canonical_audio(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    asr = RecordingASR()

    transcript = transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=asr,
        diarization_backend=MockDiarization(),
        precheck_models=False,
    )

    assert (out_dir / "vad.json").exists()
    vad_artifact = read_json(out_dir / "vad.json")
    assert vad_artifact["schema_version"] == "vad.v1"
    assert vad_artifact["backend"] == "energy"
    assert vad_artifact["algorithm"] == "pcm_s16le_rms_peak_ratio"
    assert vad_artifact["config"] == {
        "frame_ms": 30,
        "merge_gap_ms": 350,
        "min_speech_ms": 200,
        "speech_pad_end_ms": 100,
        "speech_pad_start_ms": 150,
        "threshold_ratio": 0.12,
    }
    assert vad_artifact["audio"]["sample_width_bytes"] == 2
    assert "regions" in vad_artifact
    assert [path.resolve() for path in asr.audio_paths] == [(out_dir / "canonical.wav").resolve()]
    assert read_json(out_dir / "transcript.raw.json")["raw"]["segments"][0]["text"] == "hello world"
    assert transcript.speaker_turns[0].text == "hello world"
    assert "SPEAKER_00: hello world" in (out_dir / "transcript.srt").read_text(encoding="utf-8")


def test_pipeline_repairs_sandwiched_unknown_fragment_labels(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)

    transcript = transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=FragmentASR(),
        diarization_backend=FragmentDiarization(),
        precheck_models=False,
    )

    assert [turn.speaker for turn in transcript.speaker_turns] == [
        "SPEAKER_00",
        "SPEAKER_00",
        "SPEAKER_00",
    ]
    assert transcript.speaker_turns[1].text == "small"
    assert transcript.speaker_turns[1].words[0].speaker is None
    exported = read_json(out_dir / "transcript.json")
    assert [turn["speaker"] for turn in exported["speaker_turns"]] == [
        "SPEAKER_00",
        "SPEAKER_00",
        "SPEAKER_00",
    ]
    assert exported["words"][1]["speaker"] is None


def test_mocked_pipeline_records_mlx_runtime_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    config.asr.backend = "mlx-whisper"
    native_apple_silicon = HostCapabilities(
        system="Darwin",
        process_architecture="arm64",
        hardware_architecture="arm64",
        is_apple_silicon=True,
        is_native_arm64=True,
        is_native_apple_silicon=True,
        mlx_available=True,
        mlx_whisper_available=True,
    )

    def resolve_test_policy(config):
        return resolve_runtime_policy(config, native_apple_silicon)

    monkeypatch.setattr(pipeline_module, "resolve_runtime_policy", resolve_test_policy)

    transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=MockMlxASR(),
        diarization_backend=MockDiarization(),
        precheck_models=False,
    )

    run_metadata = read_json(out_dir / "run_metadata.json")
    assert run_metadata["asr_backend"] == "mlx-whisper"
    assert run_metadata["device"] == "mlx"
    assert run_metadata["compute_type"] == "float16"
    assert run_metadata["asr_cpu_threads"] is None
    assert run_metadata["asr_batched"] is False
    asr_device_compute = run_metadata["runtime_environment"]["asr_device_compute"]
    assert asr_device_compute["backend"] == "mlx-whisper"
    assert asr_device_compute["runtime_downloads_disabled"] is True


def test_pipeline_per_job_lifecycle_records_explicit_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    config.runtime.model_lifecycle = "per-job"
    released: list[tuple[object | None, ...]] = []
    events: list[tuple[str, dict[str, object]]] = []

    def fake_release(backends):
        released.append(tuple(backends))
        return {
            "policy": "per-job",
            "released_backends": ["MockASR", "MockDiarization"],
            "cleanup_performed": True,
        }

    monkeypatch.setattr(pipeline_module, "release_runtime_models", fake_release)
    asr = MockASR()
    diarization = MockDiarization()

    transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=asr,
        diarization_backend=diarization,
        precheck_models=False,
        progress=lambda event, payload: events.append((event, payload)),
    )

    metadata = read_json(out_dir / "run_metadata.json")
    assert len(released) == 1
    assert released[0][0] is asr
    assert released[0][2] is diarization
    assert metadata["resource_usage"]["model_lifecycle"]["policy"] == "per-job"
    assert any(row["name"] == "model_release" for row in metadata["timings"])
    event_names = [name for name, _payload in events]
    assert event_names.index("model_release_start") < event_names.index("model_release_done")
    release_done = next(payload for name, payload in events if name == "model_release_done")
    assert float(release_done["duration_seconds"]) >= 0.0


def test_pipeline_emits_real_post_export_optional_stage_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from local_transcript_max.speaker_id import cluster_embeddings, enrollment

    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    config.speaker_id.mode = "auto"
    config.cleanup.mode = "local-llm"
    events: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        cluster_embeddings,
        "build_cluster_embeddings",
        lambda *_args, **_kwargs: SimpleNamespace(
            embeddings={"SPEAKER_00": [0.0, 1.0]},
            report={"status": "mocked"},
        ),
    )
    monkeypatch.setattr(enrollment, "match_job_speakers", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        pipeline_module,
        "run_cleanup",
        lambda *_args, **_kwargs: {"enabled": True, "accepted": True},
    )

    transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=MockASR(),
        diarization_backend=MockDiarization(),
        precheck_models=False,
        progress=lambda event, payload: events.append((event, payload)),
    )

    event_names = [name for name, _payload in events]
    ordered = [
        "export_done",
        "speaker_id_start",
        "speaker_id_done",
        "cleanup_start",
        "cleanup_done",
        "complete",
    ]
    assert [event_names.index(name) for name in ordered] == sorted(
        event_names.index(name) for name in ordered
    )
    for done_event in ("speaker_id_done", "cleanup_done"):
        payload = next(payload for name, payload in events if name == done_event)
        assert float(payload["duration_seconds"]) >= 0.0


def test_pipeline_emits_diarization_subprogress_when_backend_supports_it(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    events: list[tuple[str, dict[str, object]]] = []

    transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=MockASR(),
        diarization_backend=ProgressDiarization(),
        precheck_models=False,
        progress=lambda event, payload: events.append((event, payload)),
    )

    pyannote_events = [payload for event, payload in events if event == "pyannote_progress"]
    assert pyannote_events == [
        {"step": "segmentation", "completed": 1, "total": 2, "fraction": 0.5}
    ]


def test_pipeline_identity_resolver_is_opt_in_and_runs_after_metadata(tmp_path: Path) -> None:
    input_path = tmp_path / "sample.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(input_path)
    config = mocked_config(tmp_path)
    config.speaker_identity.mode = "review"
    events: list[str] = []

    transcribe_file(
        input_path,
        out_dir,
        config,
        asr_backend=MockASR(),
        diarization_backend=MockDiarization(),
        precheck_models=False,
        progress=lambda event, payload: events.append(event),
    )

    report = read_json(out_dir / "speaker_identity_report.json")
    assert report["mode"] == "review"
    assert "run_metadata.json" in report["resolver_context"]["artifacts_read"]
    assert "speaker_identity_start" in events
    assert "speaker_identity_done" in events
    assert not (out_dir / "transcript.identity_mapped.srt").exists()
    assert (out_dir / ".speaker_identity.lock").exists()


def test_strict_alignment_preflight_fails_before_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = mocked_config(tmp_path)
    config.alignment.backend = "forced"
    monkeypatch.setattr(pipeline_module, "require_model", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(pipeline_module, "validate_requested_device", lambda _config: None)
    monkeypatch.setattr(pipeline_module, "validate_compute_type", lambda *_args: None)

    def unavailable(_config):
        raise BackendUnavailableError("forced alignment unavailable")

    monkeypatch.setattr(pipeline_module, "preflight_forced_alignment", unavailable)

    with pytest.raises(BackendUnavailableError, match="alignment unavailable"):
        preflight_required_models(config)
