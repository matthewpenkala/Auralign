# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import os
import sys
import types
from pathlib import Path

from local_transcript_max.config import load_config
from local_transcript_max.diarization.pyannote_backend import (
    PYANNOTE_MPS_EXPERIMENTAL_WARNING,
    PyannoteDiarizationBackend,
    PyannoteProgressTelemetryHook,
    ensure_pyannote_privacy_defaults,
    resolve_diarization_device,
)
from local_transcript_max.pipeline import DisabledDiarizationBackend, select_diarization_backend


def test_diarization_backend_off_returns_empty_result(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.diarization.backend = "off"
    config.diarization.enabled = False
    backend = select_diarization_backend(config)
    assert isinstance(backend, DisabledDiarizationBackend)
    result = backend.diarize(tmp_path / "audio.wav", config)
    assert result.turns == []
    assert result.metadata["backend"] == "off"


def test_pyannote_is_selected_for_auto_backend(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.diarization.backend = "auto"
    backend = select_diarization_backend(config)
    assert isinstance(backend, PyannoteDiarizationBackend)


def test_pyannote_metrics_are_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("PYANNOTE_METRICS_ENABLED", raising=False)
    ensure_pyannote_privacy_defaults()
    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "false"


def test_pyannote_explicit_mps_device_is_preserved() -> None:
    assert resolve_diarization_device("mps") == "mps"


def test_pyannote_auto_prefers_cpu_when_only_mps_is_available(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        fake_torch_module(cuda_available=False, mps_available=True),
    )

    assert resolve_diarization_device("auto") == "cpu"


def test_pyannote_auto_uses_cuda_when_available(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        fake_torch_module(cuda_available=True, mps_available=True),
    )

    assert resolve_diarization_device("auto") == "cuda"


def test_pyannote_explicit_mps_attempts_device_and_warns(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        fake_torch_module(cuda_available=False, mps_available=True),
    )
    pipeline = FakePyannotePipeline()

    class FakePipelineFactory:
        @staticmethod
        def from_pretrained(_model_path: str) -> FakePyannotePipeline:
            return pipeline

    _pipeline, actual_device, warnings = PyannoteDiarizationBackend()._pipeline(
        model_path=tmp_path,
        requested_device="mps",
        pipeline_factory=FakePipelineFactory,
    )

    assert actual_device == "mps"
    assert pipeline.requested_devices == ["mps"]
    assert PYANNOTE_MPS_EXPERIMENTAL_WARNING in warnings


def test_pyannote_progress_hook_emits_monotonic_local_payloads() -> None:
    events: list[dict[str, object]] = []
    hook = PyannoteProgressTelemetryHook(events.append)

    hook("segmentation", total=10, completed=5)
    hook("embeddings", total=4, completed=2)
    hook("segmentation", total=10, completed=1)

    fractions = [float(event["fraction"]) for event in events]
    assert fractions == sorted(fractions)
    assert events[0]["step"] == "segmentation"
    assert hook.summary()["event_count"] == 3
    assert hook.summary()["steps"] == ["embeddings", "segmentation"]


def test_pyannote_progress_hook_is_not_retained_in_serialized_result(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeAnnotation:
        def itertracks(self, *, yield_label: bool):
            assert yield_label is True
            segment = types.SimpleNamespace(start=0.0, end=1.0)
            return iter([(segment, None, "SPEAKER_00")])

    class FakeOutput:
        speaker_diarization = FakeAnnotation()
        exclusive_speaker_diarization = FakeAnnotation()

    class CallablePipeline:
        def __call__(self, _audio, **kwargs):
            kwargs["hook"]("embeddings", total=1, completed=1)
            return FakeOutput()

    config = load_config(cwd=tmp_path)
    config.diarization.num_speakers = 1
    backend = PyannoteDiarizationBackend()
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())
    pyannote_audio = types.ModuleType("pyannote.audio")
    pyannote_audio.Pipeline = object
    monkeypatch.setitem(sys.modules, "pyannote.audio", pyannote_audio)
    monkeypatch.setattr(
        "local_transcript_max.diarization.pyannote_backend.require_model",
        lambda *_args, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(
        backend,
        "_pipeline",
        lambda **_kwargs: (CallablePipeline(), "cpu", ()),
    )
    monkeypatch.setattr(backend, "_audio", lambda _path: {"waveform": object()})

    result = backend.diarize_with_progress(tmp_path / "audio.wav", config, lambda _event: None)

    assert result.metadata["speaker_constraints"] == {"num_speakers": 1}
    assert "hook" not in result.raw["kwargs"]
    result.model_dump_json()


class FakePyannotePipeline:
    def __init__(self) -> None:
        self.requested_devices: list[str] = []

    def to(self, device: str) -> None:
        self.requested_devices.append(device)


def fake_torch_module(*, cuda_available: bool, mps_available: bool) -> types.ModuleType:
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)
    torch.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps_available)
    )
    torch.device = lambda value: value
    return torch
