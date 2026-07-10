# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Deterministic energy-based voice activity detection for canonical PCM WAV audio."""

from __future__ import annotations

import math
import struct
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from local_transcript_max.schemas import VADRegion


@dataclass(frozen=True)
class EnergyVADConfig:
    """Validated parameters for the intentionally simple energy gate."""

    frame_ms: int = 30
    threshold_ratio: float = 0.12
    min_speech_ms: int = 200
    speech_pad_start_ms: int = 150
    speech_pad_end_ms: int = 100
    merge_gap_ms: int = 350

    def __post_init__(self) -> None:
        _validate_integer("frame_ms", self.frame_ms, minimum=1, maximum=1_000)
        _validate_ratio("threshold_ratio", self.threshold_ratio)
        _validate_integer("min_speech_ms", self.min_speech_ms, minimum=0, maximum=60_000)
        _validate_integer(
            "speech_pad_start_ms",
            self.speech_pad_start_ms,
            minimum=0,
            maximum=60_000,
        )
        _validate_integer(
            "speech_pad_end_ms",
            self.speech_pad_end_ms,
            minimum=0,
            maximum=60_000,
        )
        _validate_integer("merge_gap_ms", self.merge_gap_ms, minimum=0, maximum=60_000)


@dataclass(frozen=True)
class EnergyVADAudioMetadata:
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_seconds: float


@dataclass(frozen=True)
class EnergyVADMetadata:
    """Typed metadata describing what the energy backend actually executed."""

    config: EnergyVADConfig
    audio: EnergyVADAudioMetadata
    schema_version: ClassVar[Literal["vad.v1"]] = "vad.v1"
    backend: ClassVar[Literal["energy"]] = "energy"
    algorithm: ClassVar[Literal["pcm_s16le_rms_peak_ratio"]] = "pcm_s16le_rms_peak_ratio"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "backend": self.backend,
            "algorithm": self.algorithm,
            "config": asdict(self.config),
            "audio": asdict(self.audio),
        }


@dataclass(frozen=True)
class EnergyVADResult:
    metadata: EnergyVADMetadata
    regions: tuple[VADRegion, ...]

    def to_artifact(self) -> dict[str, Any]:
        payload = self.metadata.to_dict()
        payload["regions"] = [region.model_dump(mode="json") for region in self.regions]
        return payload


@dataclass(frozen=True)
class _EnergyFrame:
    start: float
    end: float
    mean_square: float


class EnergyVAD:
    """A local energy gate; this backend does not load or emulate Silero."""

    def __init__(
        self,
        frame_ms: int = 30,
        threshold_ratio: float = 0.12,
        *,
        min_speech_ms: int = 200,
        speech_pad_start_ms: int = 150,
        speech_pad_end_ms: int = 100,
        merge_gap_ms: int = 350,
    ) -> None:
        self.config = EnergyVADConfig(
            frame_ms=frame_ms,
            threshold_ratio=threshold_ratio,
            min_speech_ms=min_speech_ms,
            speech_pad_start_ms=speech_pad_start_ms,
            speech_pad_end_ms=speech_pad_end_ms,
            merge_gap_ms=merge_gap_ms,
        )

    def detect(self, audio_path: Path) -> list[VADRegion]:
        """Return speech regions while preserving the historical backend protocol."""

        return list(self.detect_with_metadata(audio_path).regions)

    def detect_with_metadata(self, audio_path: Path) -> EnergyVADResult:
        frames, audio_metadata = _read_pcm16_energy_frames(audio_path, self.config.frame_ms)
        regions = self._regions_from_frames(frames, audio_metadata.duration_seconds)
        return EnergyVADResult(
            metadata=EnergyVADMetadata(config=self.config, audio=audio_metadata),
            regions=tuple(regions),
        )

    def _regions_from_frames(
        self,
        frames: list[_EnergyFrame],
        duration_seconds: float,
    ) -> list[VADRegion]:
        if not frames:
            return []

        peak_mean_square = max(frame.mean_square for frame in frames)
        if peak_mean_square <= 0.0:
            return []
        threshold = peak_mean_square * self.config.threshold_ratio**2

        regions: list[VADRegion] = []
        active_start: float | None = None
        active_end: float | None = None
        for frame in frames:
            active = frame.mean_square > 0.0 and frame.mean_square >= threshold
            if active:
                if active_start is None:
                    active_start = frame.start
                active_end = frame.end
            elif active_start is not None and active_end is not None:
                self._append_region(regions, active_start, active_end, duration_seconds)
                active_start = None
                active_end = None

        if active_start is not None and active_end is not None:
            self._append_region(regions, active_start, active_end, duration_seconds)

        return merge_short_gaps(
            regions,
            max_gap=self.config.merge_gap_ms / 1_000,
            duration_seconds=duration_seconds,
        )

    def _append_region(
        self,
        regions: list[VADRegion],
        speech_start: float,
        speech_end: float,
        duration_seconds: float,
    ) -> None:
        speech_ms = (speech_end - speech_start) * 1_000
        if speech_ms + 1e-9 < self.config.min_speech_ms:
            return
        start = max(0.0, speech_start - self.config.speech_pad_start_ms / 1_000)
        end = min(duration_seconds, speech_end + self.config.speech_pad_end_ms / 1_000)
        if end > start:
            regions.append(VADRegion(start=start, end=end, confidence=0.5, source="energy"))


def merge_short_gaps(
    regions: list[VADRegion],
    max_gap: float = 0.35,
    *,
    duration_seconds: float | None = None,
) -> list[VADRegion]:
    if not math.isfinite(max_gap) or max_gap < 0.0:
        raise ValueError("max_gap must be a finite non-negative number")
    if duration_seconds is not None and (
        not math.isfinite(duration_seconds) or duration_seconds < 0.0
    ):
        raise ValueError("duration_seconds must be a finite non-negative number")
    if not regions:
        return []

    merged: list[VADRegion] = []
    for region in regions:
        upper_bound = duration_seconds if duration_seconds is not None else region.end
        start = max(0.0, min(region.start, upper_bound))
        end = max(start, min(region.end, upper_bound))
        if end <= start:
            continue
        bounded = region.model_copy(update={"start": start, "end": end})
        if merged and bounded.start - merged[-1].end <= max_gap:
            previous = merged[-1]
            merged[-1] = previous.model_copy(update={"end": max(previous.end, bounded.end)})
        else:
            merged.append(bounded)
    return merged


def _read_pcm16_energy_frames(
    audio_path: Path,
    frame_ms: int,
) -> tuple[list[_EnergyFrame], EnergyVADAudioMetadata]:
    with wave.open(str(audio_path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        expected_frame_count = wav.getnframes()
        if wav.getcomptype() != "NONE":
            raise ValueError("EnergyVAD requires an uncompressed PCM WAV file")
        if sample_rate <= 0:
            raise ValueError("EnergyVAD requires a positive WAV sample rate")
        if channels <= 0:
            raise ValueError("EnergyVAD requires at least one WAV channel")
        if sample_width != 2:
            raise ValueError("EnergyVAD requires 16-bit little-endian PCM WAV samples")

        samples_per_frame = max(1, sample_rate * frame_ms // 1_000)
        bytes_per_audio_frame = channels * sample_width
        frames: list[_EnergyFrame] = []
        frames_read = 0
        while True:
            data = wav.readframes(samples_per_frame)
            if not data:
                break
            if len(data) % bytes_per_audio_frame != 0:
                raise ValueError("EnergyVAD received a truncated PCM WAV frame")
            audio_frames_read = len(data) // bytes_per_audio_frame
            if audio_frames_read == 0:
                break
            start = frames_read / sample_rate
            frames_read += audio_frames_read
            frames.append(
                _EnergyFrame(
                    start=start,
                    end=frames_read / sample_rate,
                    mean_square=_pcm16_mean_square(data),
                )
            )

    if frames_read != expected_frame_count:
        raise ValueError(
            "EnergyVAD received a truncated PCM WAV file "
            f"(expected {expected_frame_count} frames, read {frames_read})"
        )
    audio_metadata = EnergyVADAudioMetadata(
        sample_rate_hz=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width,
        frame_count=frames_read,
        duration_seconds=frames_read / sample_rate,
    )
    return frames, audio_metadata


def _pcm16_mean_square(data: bytes) -> float:
    if not data or len(data) % 2 != 0:
        raise ValueError("EnergyVAD requires complete 16-bit PCM samples")
    sample_count = len(data) // 2
    square_sum = 0
    for unpacked in struct.iter_unpack("<h", data):
        sample = int(unpacked[0])
        square_sum += sample * sample
    return square_sum / sample_count


def _validate_integer(name: str, value: int, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")


def _validate_ratio(name: str, value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 < value <= 1.0
    ):
        raise ValueError(f"{name} must be a finite number greater than 0 and at most 1")
