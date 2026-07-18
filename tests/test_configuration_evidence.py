# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import json
from pathlib import Path

from local_transcript_max.config import PipelineConfig
from local_transcript_max.configuration_evidence import configuration_evidence


def test_configuration_evidence_persists_no_private_text_paths_or_credentials(
    tmp_path: Path,
) -> None:
    config = PipelineConfig(model_cache=tmp_path / "private-models")
    config.asr.initial_prompt = "PRIVATE CUSTOMER NAME"
    config.asr.prefix = "PRIVATE MEETING PREFIX"
    config.asr.glossary_file = tmp_path / "private-glossary.txt"
    config.asr.mlx_model_path = tmp_path / "private-mlx-model"
    config.speaker_id.profile_dir = tmp_path / "private-speaker-profiles"
    config.speaker_identity.sibling_jobs = [
        tmp_path / "private-job-a",
        tmp_path / "private-job-b",
    ]
    config.cleanup.endpoint = (
        "https://user:pass@example.invalid/v1?token=private-token"
    )

    projection, _digest = configuration_evidence(config)
    serialized = json.dumps(projection, sort_keys=True)

    assert "PRIVATE CUSTOMER NAME" not in serialized
    assert "PRIVATE MEETING PREFIX" not in serialized
    assert "user:pass" not in serialized
    assert "private-token" not in serialized
    assert str(tmp_path) not in serialized
    assert projection["asr"]["initial_prompt"] == {
        "configured": True,
        "kind": "private_text",
    }
    assert projection["asr"]["glossary_file"] == {
        "configured": True,
        "kind": "local_path",
    }
    assert projection["speaker_identity"]["sibling_jobs"] == [
        {"configured": True, "kind": "local_path"},
        {"configured": True, "kind": "local_path"},
    ]


def test_configuration_evidence_hash_binds_values_hidden_from_projection(
    tmp_path: Path,
) -> None:
    first = PipelineConfig(model_cache=tmp_path / "models-a")
    first.asr.initial_prompt = "first private prompt"
    second = PipelineConfig(model_cache=tmp_path / "models-b")
    second.asr.initial_prompt = "second private prompt"

    first_projection, first_digest = configuration_evidence(first)
    second_projection, second_digest = configuration_evidence(second)

    assert first_projection == second_projection
    assert first_digest != second_digest
    assert configuration_evidence(first) == (first_projection, first_digest)


def test_configuration_evidence_redacts_string_alignment_model_path(
    tmp_path: Path,
) -> None:
    config = PipelineConfig()
    private_model = tmp_path / "private-client" / "wav2vec2"
    config.alignment.model = str(private_model)

    projection, digest = configuration_evidence(config)

    assert str(private_model) not in json.dumps(projection)
    assert projection["alignment"]["model"] == {
        "configured": True,
        "kind": "local_path",
    }
    assert len(digest) == 64
