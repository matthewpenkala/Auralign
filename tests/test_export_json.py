# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

from local_transcript_max.export.json_export import write_json_artifact
from local_transcript_max.schemas import Transcript
from local_transcript_max.utils import read_json


def test_json_export_writes_pydantic_model(tmp_path: Path) -> None:
    path = tmp_path / "transcript.json"
    write_json_artifact(path, Transcript(input_path="sample.wav"))
    payload = read_json(path)
    assert payload["input_path"] == "sample.wav"
