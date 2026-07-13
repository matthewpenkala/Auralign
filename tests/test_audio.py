# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import math
import wave
from pathlib import Path

import pytest

from local_transcript_max.audio.inspect import inspect_media
from local_transcript_max.audio.normalize import build_ffmpeg_command
from local_transcript_max.audio.preprocess import (
    can_reuse_as_canonical,
    canonical_audio_decision,
    prepare_canonical_audio,
)
from local_transcript_max.config import AudioConfig
from local_transcript_max.platform_security import private_path_is_secure
from local_transcript_max.schemas import AudioMetadata


def write_tiny_wav(path: Path) -> None:
    rate = 16000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        samples = []
        for index in range(rate // 10):
            value = int(0.1 * 32767 * math.sin(2 * math.pi * 440 * index / rate))
            samples.append(value.to_bytes(2, "little", signed=True))
        wav.writeframes(b"".join(samples))


def test_ffprobe_inspection(tmp_path: Path) -> None:
    path = tmp_path / "tiny.wav"
    write_tiny_wav(path)
    metadata = inspect_media(path)
    assert metadata.sample_rate == 16000
    assert metadata.channels == 1


def test_ffmpeg_command_includes_requested_audio_filters(tmp_path: Path) -> None:
    command = build_ffmpeg_command(
        tmp_path / "input.mp4",
        tmp_path / "canonical.wav",
        AudioConfig(normalize_loudness=True, denoise="mild"),
    )
    assert command[:4] == ["ffmpeg", "-y", "-i", str(tmp_path / "input.mp4")]
    assert "loudnorm=I=-16:TP=-1.5:LRA=11,afftdn=nr=0.08" in command
    assert command[-8:] == [
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(tmp_path / "canonical.wav"),
    ]
    assert command[-1] == str(tmp_path / "canonical.wav")


def test_prepare_canonical_audio_uses_expected_output_path(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    def fake_normalize_audio(input_path: Path, output_path: Path, config: AudioConfig) -> list[str]:
        captured["input_path"] = input_path
        captured["output_path"] = output_path
        captured["config"] = config
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"synthetic-canonical")
        return ["ffmpeg", "..."]

    monkeypatch.setattr(
        "local_transcript_max.audio.preprocess.normalize_audio",
        fake_normalize_audio,
    )
    config = AudioConfig(sample_rate=8000)
    canonical, command = prepare_canonical_audio(
        tmp_path / "input.wav",
        tmp_path / "out",
        config,
        input_metadata=AudioMetadata(path=str(tmp_path / "input.wav")),
    )
    assert canonical == tmp_path / "out" / "canonical.wav"
    assert command == ["ffmpeg", "..."]
    assert captured == {
        "input_path": tmp_path / "input.wav",
        "output_path": canonical,
        "config": config,
    }


def test_prepare_canonical_audio_reuses_already_canonical_wav(tmp_path: Path, monkeypatch) -> None:
    input_path = tmp_path / "input.wav"
    write_tiny_wav(input_path)
    metadata = inspect_media(input_path)

    def fail_normalize(*_args, **_kwargs):
        raise AssertionError("ffmpeg normalization should be skipped")

    monkeypatch.setattr("local_transcript_max.audio.preprocess.normalize_audio", fail_normalize)
    canonical, command = prepare_canonical_audio(
        input_path,
        tmp_path / "out",
        AudioConfig(),
        input_metadata=metadata,
    )

    assert canonical.exists()
    assert private_path_is_secure(canonical)
    original_bytes = input_path.read_bytes()
    assert not canonical.samefile(input_path)
    input_path.write_bytes(b"source changed after canonical publication")
    assert canonical.read_bytes() == original_bytes
    assert command[0] == "reuse-canonical-wav"
    assert can_reuse_as_canonical(metadata, AudioConfig()) is True
    decision = canonical_audio_decision(input_path, canonical, metadata, AudioConfig(), command)
    assert decision["action"] == "reused"
    assert decision["ffmpeg_invoked"] is False
    assert decision["input_matched_canonical_requirements"] is True


def test_prepare_canonical_audio_makes_normalized_output_private(
    tmp_path: Path, monkeypatch
) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"synthetic-media")

    def fake_normalize_audio(
        _input_path: Path, output_path: Path, _config: AudioConfig
    ) -> list[str]:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"synthetic-canonical")
        output_path.chmod(0o644)
        return ["ffmpeg", "..."]

    monkeypatch.setattr(
        "local_transcript_max.audio.preprocess.normalize_audio",
        fake_normalize_audio,
    )
    canonical, command = prepare_canonical_audio(
        input_path,
        tmp_path / "out",
        AudioConfig(),
        input_metadata=AudioMetadata(path=str(input_path)),
    )

    assert command == ["ffmpeg", "..."]
    assert canonical.read_bytes() == b"synthetic-canonical"
    assert private_path_is_secure(canonical)


def test_prepare_canonical_audio_replaces_stale_reused_wav(tmp_path: Path, monkeypatch) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(first)
    write_tiny_wav(second)
    with second.open("ab") as handle:
        handle.write(b"distinct-tail")
    first_metadata = inspect_media(first)
    second_metadata = inspect_media(second)

    def fail_normalize(*_args, **_kwargs):
        raise AssertionError("ffmpeg normalization should be skipped")

    monkeypatch.setattr("local_transcript_max.audio.preprocess.normalize_audio", fail_normalize)
    canonical, _command = prepare_canonical_audio(
        first,
        out_dir,
        AudioConfig(),
        input_metadata=first_metadata,
    )
    first_bytes = canonical.read_bytes()
    canonical, command = prepare_canonical_audio(
        second,
        out_dir,
        AudioConfig(),
        input_metadata=second_metadata,
    )

    assert command[0] == "reuse-canonical-wav"
    assert canonical.read_bytes() != first_bytes
    assert canonical.read_bytes() == second.read_bytes()


def test_prepare_canonical_audio_preserves_existing_copy_when_copy_fails(
    tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    out_dir = tmp_path / "out"
    write_tiny_wav(first)
    write_tiny_wav(second)
    first_metadata = inspect_media(first)
    second_metadata = inspect_media(second)
    canonical, _command = prepare_canonical_audio(
        first,
        out_dir,
        AudioConfig(),
        input_metadata=first_metadata,
    )
    published_bytes = canonical.read_bytes()

    def fail_copy(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"partial copy")
        raise OSError("synthetic copy failure")

    monkeypatch.setattr("local_transcript_max.audio.preprocess.shutil.copy2", fail_copy)

    with pytest.raises(OSError, match="synthetic copy failure"):
        prepare_canonical_audio(
            second,
            out_dir,
            AudioConfig(),
            input_metadata=second_metadata,
        )

    assert canonical.read_bytes() == published_bytes
    assert list(out_dir.glob(".canonical.wav.*.tmp")) == []
