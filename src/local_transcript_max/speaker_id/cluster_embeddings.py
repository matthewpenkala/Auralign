# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Offline ECAPA embeddings for anonymous diarization clusters."""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from local_transcript_max.config import default_model_cache, resolve_model_entry
from local_transcript_max.models import status_for
from local_transcript_max.schemas import (
    DiarizationTurn,
    SpeakerEmbeddingClusterReport,
    SpeakerEmbeddingReport,
)

from .matching import validate_embedding

ECAPA_SAMPLE_RATE = 16_000


@dataclass(frozen=True)
class SelectedSegment:
    """An eligible, duration-capped exclusive-diarization segment."""

    speaker: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class AudioBuffer:
    """A canonical mono waveform loaded once for all cluster slices."""

    waveform: Any
    sample_rate: int
    duration_seconds: float


@dataclass(frozen=True)
class ClusterEmbeddingResult:
    """Compatible embedding mapping and privacy-safe operational metadata."""

    embeddings: dict[str, list[float]]
    report: dict[str, object]


def select_exclusive_segments(
    turns: Sequence[DiarizationTurn],
    *,
    audio_duration_seconds: float,
    minimum_segment_seconds: float = 1.0,
    maximum_speech_seconds: float = 60.0,
) -> tuple[dict[str, list[SelectedSegment]], tuple[str, ...]]:
    """Select non-overlap speech deterministically under a per-cluster budget."""

    if not math.isfinite(audio_duration_seconds) or audio_duration_seconds < 0:
        raise ValueError("Audio duration must be finite and non-negative.")
    if not math.isfinite(minimum_segment_seconds) or minimum_segment_seconds <= 0:
        raise ValueError("Minimum segment duration must be finite and positive.")
    if not math.isfinite(maximum_speech_seconds) or maximum_speech_seconds <= 0:
        raise ValueError("Maximum speech duration must be finite and positive.")

    speakers = tuple(sorted({turn.speaker.strip() for turn in turns if turn.speaker.strip()}))
    selected: dict[str, list[SelectedSegment]] = {speaker: [] for speaker in speakers}
    used = {speaker: 0.0 for speaker in speakers}

    for turn in sorted(turns, key=lambda item: (item.speaker, item.start, item.end)):
        speaker = turn.speaker.strip()
        if not speaker or turn.overlap:
            continue
        if not math.isfinite(turn.start) or not math.isfinite(turn.end):
            continue
        start = max(0.0, min(float(turn.start), audio_duration_seconds))
        end = max(start, min(float(turn.end), audio_duration_seconds))
        if end - start < minimum_segment_seconds:
            continue
        remaining = maximum_speech_seconds - used[speaker]
        if remaining < minimum_segment_seconds:
            continue
        end = min(end, start + remaining)
        if end - start < minimum_segment_seconds:
            continue
        segment = SelectedSegment(speaker=speaker, start=start, end=end)
        selected[speaker].append(segment)
        used[speaker] += segment.duration

    return selected, speakers


def duration_weighted_embedding(
    vectors: Sequence[Sequence[object]],
    durations: Sequence[float],
) -> list[float]:
    """Normalize each vector, duration-weight their mean, and normalize the result."""

    if len(vectors) != len(durations) or not vectors:
        raise ValueError("Embedding vectors and durations must have the same non-zero length.")
    normalized: list[list[float]] = []
    dimensions: int | None = None
    for index, raw_vector in enumerate(vectors):
        vector = validate_embedding(raw_vector, label=f"segment embedding {index}")
        norm = math.hypot(*vector)
        if norm == 0:
            raise ValueError(f"Segment embedding {index} is a zero vector.")
        if dimensions is None:
            dimensions = len(vector)
        elif len(vector) != dimensions:
            raise ValueError("Segment embedding dimensions must match.")
        normalized.append([value / norm for value in vector])

    weights: list[float] = []
    for duration in durations:
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Segment durations must be finite and positive.")
        weights.append(float(duration))
    total_weight = math.fsum(weights)
    assert dimensions is not None
    averaged = [
        math.fsum(
            vector[column] * weight
            for vector, weight in zip(normalized, weights, strict=True)
        )
        / total_weight
        for column in range(dimensions)
    ]
    final_norm = math.hypot(*averaged)
    if final_norm == 0:
        raise ValueError("Duration-weighted embedding is a zero vector.")
    return validate_embedding(
        [value / final_norm for value in averaged],
        label="cluster embedding",
    )


def build_cluster_embeddings(
    audio_path: Path,
    exclusive_turns: Sequence[DiarizationTurn],
    *,
    cache_dir: Path | None = None,
    device: Literal["auto", "cpu", "cuda"] = "auto",
    minimum_segment_seconds: float = 1.0,
    maximum_speech_seconds: float = 60.0,
) -> ClusterEmbeddingResult:
    """Compute real local ECAPA embeddings from exclusive diarization turns."""

    audio = _load_audio(audio_path)
    selected, speakers = select_exclusive_segments(
        exclusive_turns,
        audio_duration_seconds=audio.duration_seconds,
        minimum_segment_seconds=minimum_segment_seconds,
        maximum_speech_seconds=maximum_speech_seconds,
    )
    usable_speakers = [speaker for speaker in speakers if selected[speaker]]
    resolved_device = _resolve_device(device) if usable_speakers else "cpu"
    entry = resolve_model_entry("speaker-embedding", "ecapa")
    if entry is None:  # pragma: no cover - manifest invariant
        raise RuntimeError("Speaker embedding manifest entry 'ecapa' is missing.")

    classifier: Any | None = None
    model_path: Path | None = None
    if usable_speakers:
        status = status_for(entry, cache_dir or default_model_cache())
        if not status.present:
            raise RuntimeError(status.problem_summary(cache_dir or default_model_cache()))
        model_path = status.path
        classifier = _load_classifier(str(model_path), resolved_device)

    embeddings: dict[str, list[float]] = {}
    speaker_report: dict[str, SpeakerEmbeddingClusterReport] = {}
    for speaker in speakers:
        segments = selected[speaker]
        if not segments:
            speaker_report[speaker] = SpeakerEmbeddingClusterReport(
                selected_duration_seconds=0.0,
                embedding_dimensions=None,
                skip_reason="no_eligible_speech",
            )
            continue
        assert classifier is not None and model_path is not None
        vectors = [
            _encode_segment(classifier, audio, segment, device=resolved_device)
            for segment in segments
        ]
        vector = duration_weighted_embedding(vectors, [segment.duration for segment in segments])
        embeddings[speaker] = vector
        speaker_report[speaker] = SpeakerEmbeddingClusterReport(
            selected_duration_seconds=round(
                math.fsum(segment.duration for segment in segments), 6
            ),
            embedding_dimensions=len(vector),
            skip_reason=None,
        )

    report = SpeakerEmbeddingReport(
        model_revision=entry.revision or "",
        device=resolved_device,
        speakers=speaker_report,
    )
    return ClusterEmbeddingResult(
        embeddings=embeddings,
        report=report.model_dump(mode="json"),
    )


def _load_audio(audio_path: Path) -> AudioBuffer:
    import torchaudio

    waveform, sample_rate = torchaudio.load(str(audio_path))
    if waveform.ndim != 2 or waveform.shape[1] == 0:
        raise ValueError("Canonical audio must contain a non-empty channel-by-sample waveform.")
    waveform = waveform.mean(dim=0, keepdim=True)
    if sample_rate != ECAPA_SAMPLE_RATE:
        waveform = torchaudio.functional.resample(waveform, sample_rate, ECAPA_SAMPLE_RATE)
        sample_rate = ECAPA_SAMPLE_RATE
    return AudioBuffer(
        waveform=waveform,
        sample_rate=sample_rate,
        duration_seconds=float(waveform.shape[1]) / sample_rate,
    )


def _resolve_device(requested: Literal["auto", "cpu", "cuda"]) -> Literal["cpu", "cuda"]:
    if requested == "cpu":
        return "cpu"
    try:
        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available to Torch.")
        probe = torch.ones(1, device="cuda")
        if float(probe.sum().cpu().item()) != 1.0:
            raise RuntimeError("CUDA execution probe returned an invalid result.")
        torch.cuda.synchronize()
        return "cuda"
    except Exception as exc:
        if requested == "cuda":
            raise RuntimeError(
                f"CUDA speaker embedding was requested but validation failed: {exc}"
            ) from exc
        return "cpu"


@lru_cache(maxsize=4)
def _load_classifier(model_path: str, device: Literal["cpu", "cuda"]) -> Any:
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import FetchConfig, LocalStrategy

    return EncoderClassifier.from_hparams(
        source=model_path,
        savedir=None,
        overrides={"pretrained_path": model_path},
        run_opts={"device": device},
        local_strategy=LocalStrategy.NO_LINK,
        fetch_config=FetchConfig(allow_network=False),
    )


def _encode_segment(
    classifier: Any,
    audio: AudioBuffer,
    segment: SelectedSegment,
    *,
    device: Literal["cpu", "cuda"],
) -> list[float]:
    start_frame = max(0, round(segment.start * audio.sample_rate))
    end_frame = min(audio.waveform.shape[1], round(segment.end * audio.sample_rate))
    if end_frame <= start_frame:
        raise ValueError("Selected speaker segment contains no audio samples.")
    signal = audio.waveform[:, start_frame:end_frame].to(device)
    embedding = classifier.encode_batch(signal)
    raw_vector = embedding.squeeze().detach().cpu().tolist()
    if isinstance(raw_vector, float):
        raw_vector = [raw_vector]
    if not isinstance(raw_vector, list):
        raise ValueError("SpeechBrain ECAPA returned an invalid embedding shape.")
    return validate_embedding(raw_vector, label="SpeechBrain segment embedding")
