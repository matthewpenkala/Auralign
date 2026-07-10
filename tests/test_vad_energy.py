# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import math
import wave
from pathlib import Path

import pytest

from local_transcript_max.vad.energy import EnergyVAD


def write_pcm_wav(
    path: Path,
    samples: list[int],
    *,
    sample_rate: int = 1_000,
    sample_width: int = 2,
) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(
            b"".join(sample.to_bytes(sample_width, "little", signed=True) for sample in samples)
        )


def test_energy_vad_is_deterministic_and_bounds_regions_to_audio(tmp_path: Path) -> None:
    audio_path = tmp_path / "bounded.wav"
    write_pcm_wav(audio_path, [0] * 120 + [1_000] * 380)
    backend = EnergyVAD()

    first = backend.detect_with_metadata(audio_path)
    second = backend.detect_with_metadata(audio_path)

    assert first == second
    assert len(first.regions) == 1
    assert first.regions[0].start == 0.0
    assert first.regions[0].end == 0.5
    assert first.regions[0].source == "energy"
    assert first.metadata.backend == "energy"
    assert first.metadata.algorithm == "pcm_s16le_rms_peak_ratio"
    assert first.metadata.audio.duration_seconds == 0.5
    assert first.metadata.audio.frame_count == 500


def test_energy_vad_reports_typed_metadata_when_no_regions_exist(tmp_path: Path) -> None:
    audio_path = tmp_path / "silence.wav"
    write_pcm_wav(audio_path, [0] * 100)

    result = EnergyVAD().detect_with_metadata(audio_path)
    artifact = result.to_artifact()

    assert result.regions == ()
    assert artifact == {
        "schema_version": "vad.v1",
        "backend": "energy",
        "algorithm": "pcm_s16le_rms_peak_ratio",
        "config": {
            "frame_ms": 30,
            "threshold_ratio": 0.12,
            "min_speech_ms": 200,
            "speech_pad_start_ms": 150,
            "speech_pad_end_ms": 100,
            "merge_gap_ms": 350,
        },
        "audio": {
            "sample_rate_hz": 1_000,
            "channels": 1,
            "sample_width_bytes": 2,
            "frame_count": 100,
            "duration_seconds": 0.1,
        },
        "regions": [],
    }


def test_energy_vad_rejects_unsupported_pcm_width(tmp_path: Path) -> None:
    audio_path = tmp_path / "pcm8.wav"
    write_pcm_wav(audio_path, [0] * 100, sample_width=1)

    with pytest.raises(ValueError, match="16-bit little-endian PCM"):
        EnergyVAD().detect(audio_path)


def test_energy_vad_validates_configuration() -> None:
    with pytest.raises(ValueError, match="frame_ms"):
        EnergyVAD(frame_ms=0)
    with pytest.raises(ValueError, match="frame_ms"):
        EnergyVAD(frame_ms=True)
    with pytest.raises(ValueError, match="threshold_ratio"):
        EnergyVAD(threshold_ratio=0.0)
    with pytest.raises(ValueError, match="threshold_ratio"):
        EnergyVAD(threshold_ratio=math.nan)
    with pytest.raises(ValueError, match="min_speech_ms"):
        EnergyVAD(min_speech_ms=-1)
    with pytest.raises(ValueError, match="speech_pad_start_ms"):
        EnergyVAD(speech_pad_start_ms=-1)
    with pytest.raises(ValueError, match="speech_pad_end_ms"):
        EnergyVAD(speech_pad_end_ms=-1)
    with pytest.raises(ValueError, match="merge_gap_ms"):
        EnergyVAD(merge_gap_ms=-1)
