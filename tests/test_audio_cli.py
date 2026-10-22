# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcript_max.cli import app
from local_transcript_max.schemas import Transcript


@pytest.mark.parametrize("cli_index,expected", [(None, 4), (0, 0), (2, 2)])
def test_audio_stream_cli_preserves_yaml_unless_explicit(
    tmp_path: Path, monkeypatch, cli_index: int | None, expected: int
) -> None:
    source = tmp_path / "input.mkv"
    source.write_bytes(b"placeholder")
    config = tmp_path / "config.yaml"
    config.write_text("audio:\n  stream_index: 4\n", encoding="utf-8")
    captured = {}

    def fake_transcribe(input_file, out_dir, config, **_kwargs):
        captured["index"] = config.audio.stream_index
        return Transcript(input_path=str(input_file))

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fake_transcribe)
    arguments = ["transcribe", str(source), "--config", str(config), "--out", str(tmp_path / "out")]
    if cli_index is not None:
        arguments.extend(["--audio-stream-index", str(cli_index)])
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert captured["index"] == expected


def test_negative_audio_stream_cli_rejected_before_processing(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "input.mkv"
    source.write_bytes(b"placeholder")

    def fail(*_args, **_kwargs):
        raise AssertionError("Invalid selection must not reach transcription")

    monkeypatch.setattr("local_transcript_max.cli.transcribe_file", fail)
    result = CliRunner().invoke(app, ["transcribe", str(source), "--audio-stream-index", "-1"])
    assert result.exit_code == 2
