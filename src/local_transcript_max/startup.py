# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Heavyweight startup probes for the selected local inference backends."""

from __future__ import annotations

import gc
import platform
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .alignment.forced_alignment import preflight_forced_alignment
from .asr.faster_whisper_backend import (
    resolve_compute_type,
    resolve_cpu_threads,
    resolve_device,
    resolve_num_workers,
)
from .config import PipelineConfig
from .diarization.pyannote_backend import (
    ensure_pyannote_privacy_defaults,
    resolve_diarization_device,
)
from .models import require_model
from .redaction import privacy_safe_text
from .runtime_assets import configure_offline_environment, verify_generated_punkt


@dataclass(frozen=True)
class StartupProbe:
    name: str
    ok: bool
    message: str


def run_startup_probes(config: PipelineConfig) -> list[StartupProbe]:
    """Load every backend selected by ``config`` without running private media."""

    if config.offline:
        configure_offline_environment()
    probes: list[tuple[str, Callable[[], str]]] = []
    if config.diarization.enabled or (
        config.alignment.enabled and config.alignment.backend == "forced"
    ):
        probes.append(("startup:native:torchcodec", _probe_torchcodec))
    if config.asr.backend == "faster-whisper":
        probes.append(("startup:asr:faster-whisper", lambda: _probe_faster_whisper(config)))
    if config.diarization.enabled and config.diarization.backend in {"auto", "pyannote"}:
        probes.append(("startup:diarization:pyannote", lambda: _probe_pyannote(config)))
    if config.alignment.enabled and config.alignment.backend == "forced":
        probes.append(("startup:alignment:whisperx", lambda: _probe_whisperx(config)))
    if config.speaker_id.mode == "auto":
        probes.append(("startup:speaker-id:ecapa", lambda: _probe_ecapa(config)))

    results: list[StartupProbe] = []
    for name, probe in probes:
        try:
            message = probe()
        except Exception as exc:  # dependency/model/native boundary
            results.append(StartupProbe(name=name, ok=False, message=privacy_safe_text(str(exc))))
        else:
            results.append(StartupProbe(name=name, ok=True, message=message))
        finally:
            _release_accelerator_memory()
    return results


def _probe_faster_whisper(config: PipelineConfig) -> str:
    from faster_whisper import WhisperModel

    path = require_model("asr", config.asr.model, config.model_cache, offline=config.offline)
    device = resolve_device(config.asr.device)
    compute_type = resolve_compute_type(config.asr.compute_type, device)
    kwargs: dict[str, Any] = {"device": device, "compute_type": compute_type}
    cpu_threads = resolve_cpu_threads(config.asr.cpu_threads, device)
    num_workers = resolve_num_workers(config.asr.num_workers)
    if cpu_threads is not None:
        kwargs["cpu_threads"] = cpu_threads
    if num_workers is not None:
        kwargs["num_workers"] = num_workers
    model = WhisperModel(str(path), **kwargs)
    del model
    return f"loaded pinned local model device={device} compute_type={compute_type}"


def _probe_torchcodec() -> str:
    import torchcodec

    version = getattr(torchcodec, "__version__", "installed")
    return f"loaded native TorchCodec/FFmpeg boundary version={version}"


def _probe_pyannote(config: PipelineConfig) -> str:
    ensure_pyannote_privacy_defaults()
    from pyannote.audio import Pipeline

    path = require_model(
        "diarization", config.diarization.model, config.model_cache, offline=config.offline
    )
    pipeline = Pipeline.from_pretrained(str(path))
    if pipeline is None:
        raise RuntimeError("pyannote returned no pipeline for the selected local model")
    device = resolve_diarization_device(config.diarization.device)
    if device:
        import torch

        pipeline.to(torch.device(device))
    del pipeline
    return f"loaded pinned local pipeline device={device or 'cpu'} metrics=false"


def _probe_whisperx(config: PipelineConfig) -> str:
    verify_generated_punkt(config.model_cache)
    import whisperx

    path = preflight_forced_alignment(config)
    language = config.alignment.language or "en"
    requested = config.alignment.device or config.asr.device
    device = "cuda" if requested == "auto" and _cuda_available() else requested
    if device == "auto":
        device = "cpu"
    model, metadata = whisperx.load_align_model(
        language_code=language,
        device=device,
        model_name=path,
        model_dir=str(config.model_cache.expanduser()),
        model_cache_only=True,
    )
    if not isinstance(metadata, dict) or not metadata.get("dictionary"):
        raise RuntimeError("WhisperX alignment metadata is incomplete")
    del model
    return f"loaded pinned local CTC model language={language} device={device}"


def _probe_ecapa(config: PipelineConfig) -> str:
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import FetchConfig

    model_name = getattr(config.speaker_id, "model", "ecapa")
    path = require_model(
        "speaker-embedding", model_name, config.model_cache, offline=config.offline
    )
    device = _speaker_device(getattr(config.speaker_id, "device", "auto"))
    classifier = EncoderClassifier.from_hparams(
        source=str(path),
        savedir=str(path),
        overrides={"pretrained_path": str(path)},
        run_opts={"device": device},
        fetch_config=FetchConfig(allow_network=False),
    )
    del classifier
    return f"loaded pinned local ECAPA classifier device={device} network=false"


def _speaker_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if platform.system() == "Windows" and _cuda_available():
        return "cuda"
    return "cpu"


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _release_accelerator_memory() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
