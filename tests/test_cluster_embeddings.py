# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from local_transcript_max.schemas import DiarizationTurn
from local_transcript_max.speaker_id import cluster_embeddings as module
from local_transcript_max.speaker_id.cluster_embeddings import (
    AudioBuffer,
    build_cluster_embeddings,
    duration_weighted_embedding,
    select_exclusive_segments,
)


def test_segment_selection_excludes_overlap_and_short_turns_and_caps_duration() -> None:
    turns = [
        DiarizationTurn(start=20.0, end=24.0, speaker="SPEAKER_00"),
        DiarizationTurn(start=0.0, end=8.0, speaker="SPEAKER_00"),
        DiarizationTurn(start=10.0, end=19.0, speaker="SPEAKER_00"),
        DiarizationTurn(start=8.0, end=9.5, speaker="SPEAKER_00", overlap=True),
        DiarizationTurn(start=0.0, end=0.5, speaker="SPEAKER_01"),
    ]

    selected, speakers = select_exclusive_segments(
        turns,
        audio_duration_seconds=30.0,
        minimum_segment_seconds=1.0,
        maximum_speech_seconds=10.0,
    )

    assert speakers == ("SPEAKER_00", "SPEAKER_01")
    assert [(segment.start, segment.end) for segment in selected["SPEAKER_00"]] == [
        (0.0, 8.0),
        (10.0, 12.0),
    ]
    assert selected["SPEAKER_01"] == []


def test_duration_weighted_embedding_normalizes_segments_and_final_vector() -> None:
    vector = duration_weighted_embedding([[3.0, 0.0], [0.0, 4.0]], [1.0, 3.0])

    assert vector == pytest.approx([1.0 / math.sqrt(10.0), 3.0 / math.sqrt(10.0)])
    assert math.hypot(*vector) == pytest.approx(1.0)


def test_build_cluster_embeddings_loads_backend_once_and_reports_no_eligible_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    classifier = object()
    loader_calls: list[tuple[str, str]] = []
    encoded: list[tuple[str, float, float]] = []
    fake_entry = SimpleNamespace(
        model_id="speechbrain/spkrec-ecapa-voxceleb",
        revision="immutable-revision",
    )
    fake_status = SimpleNamespace(present=True, path=tmp_path / "models" / "ecapa")
    monkeypatch.setattr(
        module,
        "_load_audio",
        lambda path: AudioBuffer(waveform=object(), sample_rate=16_000, duration_seconds=20.0),
    )
    monkeypatch.setattr(module, "_resolve_device", lambda requested: "cpu")
    monkeypatch.setattr(module, "resolve_model_entry", lambda role, key: fake_entry)
    monkeypatch.setattr(module, "status_for", lambda entry, cache: fake_status)

    def fake_loader(path: str, device: str) -> object:
        loader_calls.append((path, device))
        return classifier

    def fake_encode(backend, audio, segment, *, device):
        assert backend is classifier
        encoded.append((segment.speaker, segment.start, segment.end))
        return [1.0, 0.0] if segment.start < 3.0 else [0.0, 1.0]

    monkeypatch.setattr(module, "_load_classifier", fake_loader)
    monkeypatch.setattr(module, "_encode_segment", fake_encode)
    result = build_cluster_embeddings(
        tmp_path / "canonical.wav",
        [
            DiarizationTurn(start=0.0, end=2.0, speaker="SPEAKER_00"),
            DiarizationTurn(start=4.0, end=8.0, speaker="SPEAKER_00"),
            DiarizationTurn(start=8.0, end=8.5, speaker="SPEAKER_01"),
        ],
        cache_dir=tmp_path / "models",
    )

    assert loader_calls == [(str(fake_status.path), "cpu")]
    assert encoded == [("SPEAKER_00", 0.0, 2.0), ("SPEAKER_00", 4.0, 8.0)]
    assert math.hypot(*result.embeddings["SPEAKER_00"]) == pytest.approx(1.0)
    assert "SPEAKER_01" not in result.embeddings
    speakers = result.report["speakers"]
    assert isinstance(speakers, dict)
    assert speakers["SPEAKER_01"]["skip_reason"] == "no_eligible_speech"
    assert "canonical.wav" not in str(result.report)
    assert "transcript" not in str(result.report).lower()
    assert result.embeddings["SPEAKER_00"] not in result.report["speakers"].values()
