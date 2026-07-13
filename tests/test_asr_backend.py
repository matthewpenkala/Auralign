# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import sys
import wave
from pathlib import Path
from types import ModuleType

import pytest

from local_transcript_max.asr import faster_whisper_backend
from local_transcript_max.asr.faster_whisper_backend import (
    FasterWhisperBackend,
    resolve_cpu_threads,
    resolve_num_workers,
    validate_compute_type,
)
from local_transcript_max.asr.mlx_whisper_backend import (
    MlxWhisperBackend,
    mlx_model_status,
    resolve_mlx_model_path,
    segments_from_mlx_payload,
)
from local_transcript_max.config import load_config, resolve_model_entry
from local_transcript_max.errors import BackendUnavailableError, ModelCacheError
from local_transcript_max.schemas import VADRegion


class FakeWhisperModel:
    load_count = 0
    instances = []

    def __init__(
        self,
        model_path: str,
        *,
        device: str,
        compute_type: str,
        cpu_threads: int | None = None,
        num_workers: int | None = None,
    ) -> None:
        FakeWhisperModel.load_count += 1
        self.model_path = model_path
        self.device = device
        self.compute_type = compute_type
        self.cpu_threads = cpu_threads
        self.num_workers = num_workers
        FakeWhisperModel.instances.append(self)


def test_faster_whisper_backend_reuses_loaded_model_for_same_runtime_key(
    tmp_path: Path,
) -> None:
    FakeWhisperModel.load_count = 0
    FakeWhisperModel.instances = []
    backend = FasterWhisperBackend()
    first = backend._model(
        model_path=tmp_path / "model",
        device="cpu",
        compute_type="int8",
        cpu_threads=8,
        num_workers=1,
        model_factory=FakeWhisperModel,
    )
    second = backend._model(
        model_path=tmp_path / "model",
        device="cpu",
        compute_type="int8",
        cpu_threads=8,
        num_workers=1,
        model_factory=FakeWhisperModel,
    )
    third = backend._model(
        model_path=tmp_path / "model",
        device="cpu",
        compute_type="float32",
        cpu_threads=8,
        num_workers=1,
        model_factory=FakeWhisperModel,
    )
    fourth = backend._model(
        model_path=tmp_path / "model",
        device="cpu",
        compute_type="int8",
        cpu_threads=16,
        num_workers=1,
        model_factory=FakeWhisperModel,
    )
    fifth = backend._model(
        model_path=tmp_path / "model",
        device="cpu",
        compute_type="int8",
        cpu_threads=8,
        num_workers=2,
        model_factory=FakeWhisperModel,
    )

    assert first is second
    assert third is not first
    assert fourth is not first
    assert fifth is not first
    assert first.cpu_threads == 8
    assert fifth.num_workers == 2
    assert FakeWhisperModel.load_count == 4


def test_faster_whisper_backend_preserves_constructor_shape_when_unset(
    tmp_path: Path,
) -> None:
    captured = {}

    class CapturingWhisperModel:
        def __init__(self, model_path: str, **kwargs) -> None:
            captured["model_path"] = model_path
            captured["kwargs"] = kwargs

    backend = FasterWhisperBackend()
    backend._model(
        model_path=tmp_path / "model",
        device="cpu",
        compute_type="int8",
        cpu_threads=None,
        num_workers=None,
        model_factory=CapturingWhisperModel,
    )

    assert captured["kwargs"] == {"device": "cpu", "compute_type": "int8"}


def test_faster_whisper_backend_reuses_batched_transcriber_for_same_model() -> None:
    class FakeBatchedPipeline:
        load_count = 0

        def __init__(self, model) -> None:
            FakeBatchedPipeline.load_count += 1
            self.model = model

    backend = FasterWhisperBackend()
    key = ("model", "cpu", "int8", 8, 2)
    model = object()

    first = backend._batched_transcriber(
        key=key,
        model=model,
        pipeline_factory=FakeBatchedPipeline,
    )
    second = backend._batched_transcriber(
        key=key,
        model=model,
        pipeline_factory=FakeBatchedPipeline,
    )

    assert first is second
    assert first.model is model
    assert FakeBatchedPipeline.load_count == 1


def test_faster_whisper_backend_fails_clearly_if_explicit_threading_rejected(
    tmp_path: Path,
) -> None:
    class OldWhisperModel:
        def __init__(self, model_path: str, *, device: str, compute_type: str) -> None:
            pass

    backend = FasterWhisperBackend()

    with pytest.raises(BackendUnavailableError, match="explicit ASR threading options"):
        backend._model(
            model_path=tmp_path / "model",
            device="cpu",
            compute_type="int8",
            cpu_threads=8,
            num_workers=None,
            model_factory=OldWhisperModel,
        )


def test_validate_compute_type_rejects_unsupported_apple_cpu_combo(monkeypatch) -> None:
    monkeypatch.setattr(
        faster_whisper_backend,
        "supported_compute_types",
        lambda device: {"int8", "int8_float32", "float32"},
    )

    with pytest.raises(BackendUnavailableError, match="int8_float16"):
        validate_compute_type("int8_float16", "cpu")


def test_validate_compute_type_requires_usable_ctranslate2(monkeypatch) -> None:
    monkeypatch.setattr(
        faster_whisper_backend,
        "supported_compute_types",
        lambda device: None,
    )

    with pytest.raises(BackendUnavailableError, match="compatible ctranslate2 wheel"):
        validate_compute_type("int8", "cpu")


def test_resolve_asr_threading_preserves_unset_and_positive_values() -> None:
    assert resolve_cpu_threads(None, "cpu") is None
    assert resolve_cpu_threads(8, "cpu") == 8
    assert resolve_cpu_threads(8, "cuda") is None
    assert resolve_num_workers(None) is None
    assert resolve_num_workers(2) == 2
    with pytest.raises(BackendUnavailableError, match="cpu_threads"):
        resolve_cpu_threads(0, "cpu")
    with pytest.raises(BackendUnavailableError, match="num_workers"):
        resolve_num_workers(0)


def test_mlx_model_path_validation_accepts_required_weight_alternatives(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    entry = resolve_model_entry("asr-mlx", "mlx-large-v3-turbo")
    assert entry is not None
    model_dir = tmp_path / "models" / entry.directory_name

    status = mlx_model_status(config)
    assert status.present is False
    assert "config.json" in status.missing_files

    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    status = mlx_model_status(config)
    assert status.present is False
    assert "weights.safetensors or weights.npz" in status.missing_files

    (model_dir / "weights.safetensors").write_bytes(b"")
    status = mlx_model_status(config)
    assert status.present is False
    assert status.zero_size_files == ("weights.safetensors",)

    (model_dir / "weights.safetensors").write_bytes(b"ok")
    assert resolve_mlx_model_path(config) == model_dir

    npz_dir = tmp_path / "npz-model"
    npz_dir.mkdir()
    (npz_dir / "config.json").write_text("{}", encoding="utf-8")
    (npz_dir / "weights.npz").write_bytes(b"ok")
    config.asr.mlx_model_path = npz_dir
    assert resolve_mlx_model_path(config) == npz_dir


def test_mlx_model_path_validation_rejects_unknown_model(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    config.asr.mlx_model = "unknown"
    with pytest.raises(ModelCacheError, match="Unknown MLX ASR model"):
        mlx_model_status(config)


def test_mlx_payload_conversion_preserves_segments_and_words() -> None:
    segments, raw_segments, warnings = segments_from_mlx_payload(
        {
            "segments": [
                {
                    "id": 7,
                    "start": 1.0,
                    "end": 2.5,
                    "text": " hello",
                    "avg_logprob": -0.2,
                    "no_speech_prob": 0.1,
                    "compression_ratio": 1.2,
                    "words": [
                        {
                            "word": " hello",
                            "start": 1.0,
                            "end": 1.4,
                            "probability": 0.9,
                        }
                    ],
                }
            ],
            "language": "en",
        },
        require_word_timestamps=True,
    )

    assert warnings == []
    assert raw_segments[0]["text"] == " hello"
    assert segments[0].id == 7
    assert segments[0].text == "hello"
    assert segments[0].words[0].text == "hello"
    assert segments[0].words[0].alignment_source == "whisper"


def test_mlx_backend_uses_local_path_and_never_repo_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    config.asr.language = "en"
    config.asr.mlx_fp16 = False
    entry = resolve_model_entry("asr-mlx", "mlx-large-v3-turbo")
    assert entry is not None
    model_dir = tmp_path / "models" / entry.directory_name
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "weights.safetensors").write_bytes(b"ok")
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"not-real-audio")

    captured: dict[str, object] = {}
    fake_module = ModuleType("mlx_whisper")

    def fake_transcribe(audio: str, **kwargs):
        captured["audio"] = audio
        captured["kwargs"] = kwargs
        return {
            "language": "en",
            "segments": [
                {
                    "id": 0,
                    "start": 0.0,
                    "end": 1.0,
                    "text": "hello",
                    "words": [{"word": "hello", "start": 0.0, "end": 0.5, "probability": 0.8}],
                }
            ],
        }

    fake_module.transcribe = fake_transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_module)
    monkeypatch.setattr(
        "local_transcript_max.asr.mlx_whisper_backend.importlib.util.find_spec",
        lambda name: object() if name == "mlx_whisper" else None,
    )

    result = MlxWhisperBackend().transcribe(audio_path, config)

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["path_or_hf_repo"] == str(model_dir)
    assert kwargs["fp16"] is False
    assert kwargs["language"] == "en"
    assert "beam_size" not in kwargs
    assert result.model is not None
    assert result.model.role == "asr-mlx"
    assert result.config["backend"] == "mlx-whisper"
    assert result.config["runtime_downloads_disabled"] is True
    assert "mlx_beam_search_unsupported" in result.warnings
    assert result.segments[0].text == "hello"


def test_mlx_backend_vad_windowed_offsets_chunk_timestamps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(cwd=tmp_path)
    config.asr.backend = "mlx-whisper"
    config.asr.language = "en"
    config.asr.mlx_vad_windowed = True
    config.asr.mlx_vad_window_seconds = 0.6
    entry = resolve_model_entry("asr-mlx", "mlx-large-v3-turbo")
    assert entry is not None
    model_dir = tmp_path / "models" / entry.directory_name
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "weights.safetensors").write_bytes(b"ok")
    audio_path = tmp_path / "audio.wav"
    write_silent_wav(audio_path, duration_seconds=2.0)

    calls: list[dict[str, object]] = []
    fake_module = ModuleType("mlx_whisper")

    def fake_transcribe(audio: str, **kwargs):
        calls.append({"audio": audio, "kwargs": kwargs})
        index = len(calls) - 1
        return {
            "language": "en",
            "segments": [
                {
                    "id": 90 + index,
                    "start": 0.0,
                    "end": 0.25,
                    "text": f"chunk{index}",
                    "words": [
                        {
                            "word": f"chunk{index}",
                            "start": 0.0,
                            "end": 0.25,
                            "probability": 0.8,
                        }
                    ],
                }
            ],
        }

    fake_module.transcribe = fake_transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_module)
    monkeypatch.setattr(
        "local_transcript_max.asr.mlx_whisper_backend.importlib.util.find_spec",
        lambda name: object() if name == "mlx_whisper" else None,
    )

    result = MlxWhisperBackend().transcribe(
        audio_path,
        config,
        vad_regions=[
            VADRegion(start=0.0, end=0.4, source="test"),
            VADRegion(start=1.0, end=1.4, source="test"),
        ],
    )

    assert len(calls) == 2
    for call in calls:
        kwargs = call["kwargs"]
        assert isinstance(kwargs, dict)
        assert kwargs["path_or_hf_repo"] == str(model_dir)
    assert result.config["inference_mode"] == "mlx-vad-windowed"
    assert result.config["vad_window_count"] == 2
    assert [segment.start for segment in result.segments] == [0.0, 1.0]
    assert [segment.words[0].start for segment in result.segments] == [0.0, 1.0]

    fallback = MlxWhisperBackend().transcribe(audio_path, config, vad_regions=[])

    assert calls[-1]["audio"] == str(audio_path)
    assert fallback.config["inference_mode"] == "mlx"
    assert fallback.config["vad_window_count"] == 0
    assert "mlx_vad_windowed_no_regions_fell_back_full" in fallback.warnings


def write_silent_wav(path: Path, *, duration_seconds: float, sample_rate: int = 16000) -> None:
    frames = int(duration_seconds * sample_rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * frames)
