# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

from local_transcript_max.config import load_config
from local_transcript_max.diarization.pyannote_backend import (
    PYANNOTE_MPS_EXPERIMENTAL_WARNING,
    PyannoteDiarizationBackend,
    PyannoteProgressTelemetryHook,
    ensure_pyannote_privacy_defaults,
    resolve_diarization_device,
)
from local_transcript_max.errors import BackendUnavailableError
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

    assert actual_device is None
    assert pipeline.requested_devices == ["mps"]
    assert PYANNOTE_MPS_EXPERIMENTAL_WARNING in warnings
    assert all("actual=mps" not in warning for warning in warnings)


def test_pyannote_mps_move_failure_rebuilds_fresh_cpu_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        fake_torch_module(cuda_available=False, mps_available=True),
    )

    class FailingMpsPipeline(FakePyannotePipeline):
        def to(self, device: str) -> None:
            super().to(device)
            raise RuntimeError("operator is not implemented for the MPS device")

    partial = FailingMpsPipeline()
    clean_cpu = FakePyannotePipeline()
    pipelines = iter([partial, clean_cpu])

    class FakePipelineFactory:
        @staticmethod
        def from_pretrained(_model_path: str) -> FakePyannotePipeline:
            return next(pipelines)

    pipeline, actual_device, warnings = PyannoteDiarizationBackend()._pipeline(
        model_path=tmp_path,
        requested_device="mps",
        pipeline_factory=FakePipelineFactory,
    )

    assert pipeline is clean_cpu
    assert pipeline is not partial
    assert actual_device == "cpu"
    assert partial.requested_devices == ["mps"]
    assert clean_cpu.requested_devices == ["cpu"]
    assert any(
        warning.startswith("pyannote_device_fallback:")
        and "actual=cpu" in warning
        and "stage=move" in warning
        and "reason=RuntimeError" in warning
        for warning in warnings
    )
    assert all("actual=mps" not in warning for warning in warnings)


def test_pyannote_generic_mps_move_error_propagates_without_cpu_rebuild(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        fake_torch_module(cuda_available=False, mps_available=True),
    )

    class FailingMpsPipeline(FakePyannotePipeline):
        def to(self, device: str) -> None:
            super().to(device)
            raise RuntimeError("generic pipeline transfer failure")

    pipeline = FailingMpsPipeline()

    class FakePipelineFactory:
        calls = 0

        @classmethod
        def from_pretrained(cls, _model_path: str) -> FailingMpsPipeline:
            cls.calls += 1
            return pipeline

    with pytest.raises(RuntimeError, match="generic pipeline transfer failure"):
        PyannoteDiarizationBackend()._pipeline(
            model_path=tmp_path,
            requested_device="mps",
            pipeline_factory=FakePipelineFactory,
        )

    assert FakePipelineFactory.calls == 1
    assert pipeline.requested_devices == ["mps"]


def test_pyannote_explicit_cuda_device_failure_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        fake_torch_module(cuda_available=True, mps_available=False),
    )

    class FailingPipeline(FakePyannotePipeline):
        def to(self, device: str) -> None:
            super().to(device)
            raise RuntimeError("native CUDA boundary failed")

    pipeline = FailingPipeline()

    class FakePipelineFactory:
        @staticmethod
        def from_pretrained(_model_path: str) -> FailingPipeline:
            return pipeline

    with pytest.raises(BackendUnavailableError, match="required cuda device"):
        PyannoteDiarizationBackend()._pipeline(
            model_path=tmp_path,
            requested_device="cuda",
            pipeline_factory=FakePipelineFactory,
        )

    assert pipeline.requested_devices == ["cuda"]


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
    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        "importlib.util.find_spec",
        lambda name: object()
        if name == "pyannote.audio"
        else original_find_spec(name),
    )
    pyannote_audio = types.ModuleType("pyannote.audio")
    pyannote_audio.Pipeline = object
    pyannote_package = types.ModuleType("pyannote")
    pyannote_package.__path__ = []  # type: ignore[attr-defined]
    pyannote_package.audio = pyannote_audio  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyannote", pyannote_package)
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


def test_pyannote_recognized_mps_inference_failure_retries_once_on_clean_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mps_pipeline = CallableFakePipeline(
        error=RuntimeError(
            "The operator 'aten::example' is not currently implemented for the MPS device"
        )
    )
    cpu_pipeline = CallableFakePipeline(output=FakeDiarizationOutput())
    pipelines = iter([mps_pipeline, cpu_pipeline])

    class FakePipelineFactory:
        @staticmethod
        def from_pretrained(_model_path: str) -> CallableFakePipeline:
            return next(pipelines)

    backend, config = configured_mps_backend(
        monkeypatch,
        tmp_path,
        pipeline_factory=FakePipelineFactory,
    )

    result = backend.diarize(tmp_path / "audio.wav", config)

    assert mps_pipeline.requested_devices == ["mps"]
    assert cpu_pipeline.requested_devices == ["cpu"]
    assert mps_pipeline.call_count == 1
    assert cpu_pipeline.call_count == 1
    assert result.metadata["resolved_device"] == "mps"
    assert result.metadata["device"] == "cpu"
    assert result.metadata["actual_device"] == "cpu"
    assert result.metadata["mps_inference_attempted"] is True
    assert result.metadata["device_fallback"] is True
    assert result.metadata["fallback_stage"] == "inference"
    assert result.metadata["fallback_reason"] == "RuntimeError"
    assert any(
        warning.startswith("pyannote_device_fallback:")
        and "stage=inference" in warning
        for warning in result.warnings
    )
    assert all("actual=mps" not in warning for warning in result.warnings)
    result.model_dump_json()


def test_pyannote_generic_mps_inference_error_propagates_without_cpu_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mps_pipeline = CallableFakePipeline(error=RuntimeError("generic MPS execution failure"))

    class FakePipelineFactory:
        calls = 0

        @classmethod
        def from_pretrained(cls, _model_path: str) -> CallableFakePipeline:
            cls.calls += 1
            return mps_pipeline

    backend, config = configured_mps_backend(
        monkeypatch,
        tmp_path,
        pipeline_factory=FakePipelineFactory,
    )

    with pytest.raises(RuntimeError, match="generic MPS execution failure"):
        backend.diarize(tmp_path / "audio.wav", config)

    assert FakePipelineFactory.calls == 1
    assert mps_pipeline.call_count == 1


def test_pyannote_successful_mps_inference_records_actual_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mps_pipeline = CallableFakePipeline(output=FakeDiarizationOutput())

    class FakePipelineFactory:
        @staticmethod
        def from_pretrained(_model_path: str) -> CallableFakePipeline:
            return mps_pipeline

    backend, config = configured_mps_backend(
        monkeypatch,
        tmp_path,
        pipeline_factory=FakePipelineFactory,
    )

    result = backend.diarize(tmp_path / "audio.wav", config)

    assert result.metadata["resolved_device"] == "mps"
    assert result.metadata["device"] == "mps"
    assert result.metadata["actual_device"] == "mps"
    assert result.metadata["mps_inference_attempted"] is True
    assert result.metadata["device_fallback"] is False
    assert result.metadata["fallback_stage"] is None
    assert result.metadata["fallback_reason"] is None
    assert result.metadata["mps_synchronized_before"] is True
    assert result.metadata["mps_synchronized_after"] is True
    components = result.metadata["component_devices"]
    assert components["segmentation"]["actual_device"] == "mps"
    assert components["embedding"]["actual_device"] == "mps"
    assert components["clustering_and_postprocessing"]["actual_device"] == "cpu"
    assert components["overall"] == "mixed"
    assert all("actual=mps" not in warning for warning in result.warnings)


class FakePyannotePipeline:
    def __init__(self) -> None:
        self.requested_devices: list[str] = []
        self._segmentation = types.SimpleNamespace(device="cpu")
        self._embedding = types.SimpleNamespace(device="cpu")

    def to(self, device: str) -> None:
        self.requested_devices.append(device)
        self._segmentation.device = device
        self._embedding.device = device


class FakeDiarizationAnnotation:
    def itertracks(self, *, yield_label: bool):
        assert yield_label is True
        segment = types.SimpleNamespace(start=0.0, end=1.0)
        return iter([(segment, None, "SPEAKER_00")])


class FakeDiarizationOutput:
    speaker_diarization = FakeDiarizationAnnotation()
    exclusive_speaker_diarization = FakeDiarizationAnnotation()


class CallableFakePipeline(FakePyannotePipeline):
    def __init__(
        self,
        *,
        output: FakeDiarizationOutput | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.output = output
        self.error = error
        self.call_count = 0

    def __call__(self, _audio, **_kwargs):
        self.call_count += 1
        if self.error is not None:
            raise self.error
        assert self.output is not None
        return self.output


def configured_mps_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    pipeline_factory,
) -> tuple[PyannoteDiarizationBackend, object]:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        fake_torch_module(cuda_available=False, mps_available=True),
    )
    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        "importlib.util.find_spec",
        lambda name: object()
        if name == "pyannote.audio"
        else original_find_spec(name),
    )
    pyannote_audio = types.ModuleType("pyannote.audio")
    pyannote_audio.Pipeline = pipeline_factory
    pyannote_package = types.ModuleType("pyannote")
    pyannote_package.__path__ = []  # type: ignore[attr-defined]
    pyannote_package.audio = pyannote_audio  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyannote", pyannote_package)
    monkeypatch.setitem(sys.modules, "pyannote.audio", pyannote_audio)
    monkeypatch.setattr(
        "local_transcript_max.diarization.pyannote_backend.require_model",
        lambda *_args, **_kwargs: tmp_path,
    )
    backend = PyannoteDiarizationBackend()
    monkeypatch.setattr(backend, "_audio", lambda _path: {"waveform": object()})
    config = load_config(cwd=tmp_path)
    config.diarization.device = "mps"
    return backend, config


def fake_torch_module(*, cuda_available: bool, mps_available: bool) -> types.ModuleType:
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)
    torch.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps_available)
    )
    torch.mps = types.SimpleNamespace(synchronize=lambda: None)
    torch.device = lambda value: value
    return torch
