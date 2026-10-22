# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import json
import math
import struct
import subprocess
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
from local_transcript_max.errors import LTMError
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

    def fake_normalize_audio(
        input_path: Path, output_path: Path, config: AudioConfig, *, input_metadata=None
    ) -> list[str]:
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


def test_prepare_canonical_audio_reuses_read_only_source(tmp_path: Path, monkeypatch) -> None:
    input_path = tmp_path / "input.wav"
    write_tiny_wav(input_path)
    metadata = inspect_media(input_path)

    def fail_normalize(*_args, **_kwargs):
        raise AssertionError("ffmpeg normalization should be skipped")

    monkeypatch.setattr("local_transcript_max.audio.preprocess.normalize_audio", fail_normalize)
    input_path.chmod(0o400)
    try:
        canonical, command = prepare_canonical_audio(
            input_path,
            tmp_path / "out",
            AudioConfig(),
            input_metadata=metadata,
        )
    finally:
        input_path.chmod(0o600)

    assert command[0] == "reuse-canonical-wav"
    assert canonical.read_bytes() == input_path.read_bytes()
    assert private_path_is_secure(canonical)


def test_prepare_canonical_audio_makes_normalized_output_private(
    tmp_path: Path, monkeypatch
) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"synthetic-media")

    def fake_normalize_audio(
        _input_path: Path, output_path: Path, _config: AudioConfig, *, input_metadata=None
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

    monkeypatch.setattr("local_transcript_max.audio.preprocess.shutil.copyfile", fail_copy)

    with pytest.raises(OSError, match="synthetic copy failure"):
        prepare_canonical_audio(
            second,
            out_dir,
            AudioConfig(),
            input_metadata=second_metadata,
        )

    assert canonical.read_bytes() == published_bytes
    assert list(out_dir.glob(".canonical.wav.*.tmp")) == []


def test_selected_audio_metadata_uses_absolute_index(tmp_path: Path, monkeypatch) -> None:
    payload = {
        "streams": [
            {"index": 0, "codec_type": "video"},
            {"index": 2, "codec_type": "audio", "channels": 1, "sample_rate": "16000"},
            {
                "index": 4,
                "codec_type": "audio",
                "channels": 2,
                "sample_rate": "48000",
                "duration": "N/A",
                "start_time": "1.5",
                "time_base": "1/48000",
            },
        ],
        "format": {"duration": "3.0"},
    }
    monkeypatch.setattr(
        "local_transcript_max.audio.inspect.run_command",
        lambda args: subprocess.CompletedProcess(args, 0, json.dumps(payload)),
    )
    default = inspect_media(tmp_path / "input.mkv")
    selected = inspect_media(tmp_path / "input.mkv", stream_index=4)
    assert default.selected_audio_stream_index == 2
    assert default.audio_stream_count == 2
    assert selected.selected_audio_stream_index == 4
    assert selected.channels == 2
    assert selected.sample_rate == 48000
    assert selected.time_base == "1/48000"
    assert selected.start_time_seconds == 1.5
    assert selected.duration_seconds == 3.0
    for invalid_index in [0, 1, 3, 5]:
        with pytest.raises(LTMError, match="absent or is not an audio stream"):
            inspect_media(tmp_path / "input.mkv", stream_index=invalid_index)


def test_no_audio_fails_before_canonical_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "local_transcript_max.audio.inspect.run_command",
        lambda args: subprocess.CompletedProcess(
            args, 0, json.dumps({"streams": [{"index": 0, "codec_type": "video"}]})
        ),
    )
    out_dir = tmp_path / "out"
    with pytest.raises(LTMError, match="no audio streams"):
        prepare_canonical_audio(tmp_path / "silent-video.mkv", out_dir, AudioConfig())
    assert not out_dir.exists()


@pytest.mark.parametrize("invalid_index", [-1, 1.5, True, "1"])
def test_audio_stream_config_requires_non_negative_integer(invalid_index) -> None:
    with pytest.raises(ValueError):
        AudioConfig(stream_index=invalid_index)


def test_rejects_mismatched_probe_before_reusing_canonical(tmp_path: Path) -> None:
    with pytest.raises(LTMError, match="does not match"):
        prepare_canonical_audio(
            tmp_path / "input.wav",
            tmp_path / "out",
            AudioConfig(stream_index=2),
            input_metadata=AudioMetadata(
                path="input.wav",
                selected_audio_stream_index=0,
                audio_stream_count=1,
                channels=1,
                sample_rate=16000,
                codec="pcm_s16le",
                format_name="wav",
            ),
        )
    assert not (tmp_path / "out").exists()


def _write_tone(path: Path, *, frequency: int, channels: int) -> bytes:
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * frequency * index / 16000)))
        * channels
        for index in range(4800)
    )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(frames)
    return frames


@pytest.mark.parametrize("selected_index,expected_channels", [(None, 1), (0, 1), (1, 2)])
def test_real_multistream_decode_matches_selected_microphone(
    tmp_path: Path, selected_index: int | None, expected_channels: int
) -> None:
    """Prove decoded PCM identity, rather than only inspecting command arguments."""
    mono = tmp_path / "mono.wav"
    stereo = tmp_path / "stereo.wav"
    mono_frames = _write_tone(mono, frequency=440, channels=1)
    stereo_frames = _write_tone(stereo, frequency=880, channels=2)
    source = tmp_path / "two-streams.mkv"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y", "-v", "error",
            "-i", str(mono), "-i", str(stereo),
            "-map", "0:a", "-map", "1:a", "-c:a", "copy",
            "-disposition:a:0", "0", "-disposition:a:1", "default", str(source),
        ],
        capture_output=True,
        check=True,
    )
    config = AudioConfig(stream_index=selected_index, preserve_channels=True)
    metadata = inspect_media(source, stream_index=selected_index)
    canonical, command = prepare_canonical_audio(
        source, tmp_path / "out", config, input_metadata=metadata
    )
    canonical_metadata = inspect_media(canonical)
    assert metadata.channels == canonical_metadata.channels == expected_channels
    assert metadata.audio_stream_count == 2
    assert command[command.index("-map") + 1] == f"0:{selected_index or 0}"
    with wave.open(str(canonical), "rb") as wav:
        actual_frames = wav.readframes(wav.getnframes())
    assert actual_frames == (stereo_frames if selected_index == 1 else mono_frames)
    decision = canonical_audio_decision(source, canonical, metadata, config, command)
    assert decision["selected_audio_stream_index"] == (selected_index or 0)
    assert decision["audio_stream_count"] == 2


def _write_tiny_raw_avi(path: Path) -> None:
    """Write one 2x2 RGB frame without requiring a video encoder or lavfi."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return kind + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) % 2)

    avi_header = struct.pack("<14I", 1000000, 16, 0, 16, 1, 0, 1, 16, 2, 2, 0, 0, 0, 0)
    stream_header = struct.pack(
        "<4s4sIHH8I4h", b"vids", b"DIB ", 0, 0, 0, 0, 1, 1, 0, 1, 16, 0xFFFFFFFF, 0, 0, 0, 2, 2
    )
    bitmap_header = struct.pack("<IiiHH6I", 40, 2, 2, 1, 24, 0, 16, 0, 0, 0, 0)
    streams = chunk(b"strh", stream_header) + chunk(b"strf", bitmap_header)
    headers = chunk(b"avih", avi_header) + chunk(b"LIST", b"strl" + streams)
    body = chunk(b"LIST", b"hdrl" + headers) + chunk(b"LIST", b"movi" + chunk(b"00db", b"\0" * 16))
    path.write_bytes(chunk(b"RIFF", b"AVI " + body))


def test_real_selection_uses_absolute_index_after_video_stream(tmp_path: Path) -> None:
    mono = tmp_path / "mono.wav"
    stereo = tmp_path / "stereo.wav"
    expected = _write_tone(mono, frequency=440, channels=1)
    _write_tone(stereo, frequency=880, channels=2)
    video = tmp_path / "frame.avi"
    _write_tiny_raw_avi(video)
    source = tmp_path / "video-first.avi"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y", "-v", "error",
            "-i", str(video), "-i", str(mono), "-i", str(stereo),
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c", "copy", str(source),
        ],
        capture_output=True,
        check=True,
    )
    metadata = inspect_media(source, stream_index=1)
    assert metadata.selected_audio_stream_index == 1
    assert metadata.audio_stream_count == 2
    assert inspect_media(source).selected_audio_stream_index == 1
    canonical, command = prepare_canonical_audio(
        source,
        tmp_path / "out",
        AudioConfig(stream_index=1, preserve_channels=True),
        input_metadata=metadata,
    )
    assert command[command.index("-map") + 1] == "0:1"
    with wave.open(str(canonical), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.readframes(wav.getnframes()) == expected
    with pytest.raises(LTMError, match="not an audio stream"):
        inspect_media(source, stream_index=0)


def test_pipeline_binds_selected_pcm_and_completion_metadata(tmp_path: Path) -> None:
    """Real ingestion/receipt integration; synthetic ASR proves no model quality."""
    from local_transcript_max.config import PipelineConfig
    from local_transcript_max.pipeline import transcribe_file
    from local_transcript_max.schemas import AlignedWord, ASRResult, ASRSegment
    from local_transcript_max.utils import sha256_file

    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _write_tone(first, frequency=440, channels=1)
    expected = _write_tone(second, frequency=880, channels=1)
    source = tmp_path / "two-microphones.mkv"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(first), "-i", str(second),
         "-map", "0:a", "-map", "1:a", "-c:a", "copy", str(source)],
        capture_output=True, check=True, timeout=30,
    )
    config = PipelineConfig()
    config.audio.stream_index = 1
    config.diarization.enabled = False
    config.alignment.enabled = False
    config.speaker_id.mode = "off"

    class SyntheticASR:
        name = "synthetic-asr"

        def transcribe(self, audio_path: Path, config: PipelineConfig) -> ASRResult:
            with wave.open(str(audio_path), "rb") as wav:
                assert wav.readframes(wav.getnframes()) == expected
            return ASRResult(segments=[ASRSegment(
                id=0, start=0, end=0.2, text="synthetic",
                words=[AlignedWord(text="synthetic", start=0, end=0.2)],
            )])

    output = tmp_path / "completed"
    transcribe_file(source, output, config, asr_backend=SyntheticASR(), precheck_models=False)
    metadata = json.loads((output / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["input_metadata"]["selected_audio_stream_index"] == 1
    assert metadata["input_metadata"]["audio_stream_count"] == 2
    assert metadata["source_configuration"]["audio"]["stream_index"] == 1
    command = metadata["ffmpeg_command"]
    assert command[command.index("-map") + 1] == "0:1"
    completion = json.loads((output / "completion_timing.json").read_text(encoding="utf-8"))
    assert completion["run_metadata_sha256"] == sha256_file(output / "run_metadata.json")
