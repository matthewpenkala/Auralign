# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcript_max.cli import app
from local_transcript_max.config import load_config
from local_transcript_max.errors import LTMError


@pytest.mark.parametrize(
    "contents,expected",
    [
        (
            "cleanup:\n  proper_nouns: [PrivateNameCanary, privatenamEcanary]\n",
            "cleanup.proper_nouns (value_error)",
        ),
        ("asr:\n  initial_prompt: [PrivateNameCanary]\n", "asr.initial_prompt (string_type)"),
        ("cleanup:\n  proper_nouns: [PrivateNameCanary\n", "yaml_syntax at line"),
        ("[PrivateNameCanary]\n", "configuration (mapping_type)"),
        ("PrivateNameCanary\n", "configuration (mapping_type)"),
        ("asr:\n  model: PrivateNameCanary\n", "Unknown asr model selector"),
    ],
)
def test_preview_configuration_errors_omit_private_inputs(
    tmp_path: Path, contents: str, expected: str,
) -> None:
    media = tmp_path / "input.wav"
    media.write_bytes(b"not-probed")
    config = tmp_path / "config.yaml"
    config.write_text(contents, encoding="utf-8")
    output = tmp_path / "absent"
    result = CliRunner().invoke(app, [
        "transcribe", str(media), "--config", str(config), "--out", str(output),
        "--print-effective-config",
    ])
    assert result.exit_code == 1
    assert expected in result.output
    assert "PrivateNameCanary" not in result.output
    assert "privatenamEcanary" not in result.output
    assert "input_value" not in result.output
    assert not output.exists()


def test_preview_cli_validation_omits_private_inputs(tmp_path: Path) -> None:
    media = tmp_path / "input.wav"
    media.write_bytes(b"not-probed")
    output = tmp_path / "absent"
    result = CliRunner().invoke(app, [
        "transcribe", str(media), "--out", str(output), "--print-effective-config",
        "--cleanup-proper-noun", "PrivateNameCanary",
        "--cleanup-proper-noun", "privatenamEcanary",
    ])
    assert result.exit_code == 1
    assert "cleanup.proper_nouns (value_error)" in result.output
    assert "PrivateNameCanary" not in result.output
    assert "privatenamEcanary" not in result.output
    assert not output.exists()


def test_invalid_yaml_encoding_is_safe_and_preserves_cause(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_bytes(b"PrivateNameCanary: \xff\n")
    with pytest.raises(LTMError, match="invalid_utf8_encoding") as caught:
        load_config(config_file=config, cwd=tmp_path)
    assert "PrivateNameCanary" not in str(caught.value)
    assert isinstance(caught.value.__cause__, UnicodeError)
