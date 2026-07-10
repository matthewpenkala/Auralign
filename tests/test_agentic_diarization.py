# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import stat
from pathlib import Path

from local_transcript_max.config import load_config
from local_transcript_max.fusion.diarization_refinement import (
    parse_candidate_range,
    run_agentic_diarization,
    score_candidate,
    select_candidate,
)
from local_transcript_max.schemas import (
    AlignedWord,
    DiarizationResult,
    DiarizationTurn,
    VADRegion,
)
from local_transcript_max.utils import read_json


class CandidateBackend:
    def __init__(self) -> None:
        self.calls: list[int | None] = []

    def diarize(self, audio_path: Path, config):
        self.calls.append(config.diarization.num_speakers)
        count = config.diarization.num_speakers or 2
        turns = [
            DiarizationTurn(
                start=float(index),
                end=float(index) + 1.0,
                speaker=f"SPEAKER_{index:02d}",
            )
            for index in range(count)
        ]
        return DiarizationResult(turns=turns, exclusive_turns=turns, metadata={"count": count})


def words() -> list[AlignedWord]:
    return [
        AlignedWord(text="a", start=0.0, end=0.4, alignment_source="whisper"),
        AlignedWord(text="b", start=1.0, end=1.4, alignment_source="whisper"),
        AlignedWord(text="c", start=2.0, end=2.4, alignment_source="whisper"),
        AlignedWord(text="d", start=3.0, end=3.4, alignment_source="whisper"),
    ]


def vad_regions() -> list[VADRegion]:
    return [VADRegion(start=0.0, end=4.0, source="test")]


def test_parse_candidate_range() -> None:
    assert parse_candidate_range("2:4") == [2, 3, 4]


def test_known_four_speaker_path_runs_single_candidate(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.agentic_diarization.enabled = True
    config.agentic_diarization.known_speakers = 4
    backend = CandidateBackend()
    result, report = run_agentic_diarization(
        tmp_path / "audio.wav",
        config,
        backend,
        words=words(),
        vad_regions=vad_regions(),
        out_dir=tmp_path,
    )
    assert backend.calls == [4]
    assert report["selected_candidate"] == "candidate_001"
    assert len(result.turns) == 4
    candidate_dir = tmp_path / "diarization_candidates"
    candidate_json = candidate_dir / "candidate_001.json"
    candidate_rttm = candidate_dir / "candidate_001.rttm"
    assert candidate_json.exists()
    assert candidate_rttm.exists()
    assert stat.S_IMODE(candidate_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(candidate_json.stat().st_mode) == 0o600
    assert stat.S_IMODE(candidate_rttm.stat().st_mode) == 0o600
    assert not list(candidate_dir.glob(".candidate_001.rttm.*"))


def test_unknown_speaker_count_scoring_and_report_schema(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.agentic_diarization.enabled = True
    config.agentic_diarization.candidate_range = "2:3"
    backend = CandidateBackend()
    _result, report = run_agentic_diarization(
        tmp_path / "audio.wav",
        config,
        backend,
        words=words(),
        vad_regions=vad_regions(),
        out_dir=tmp_path,
    )
    assert backend.calls == [2, 3]
    assert report["candidate_count"] == 2
    assert "deterministic_tie_break" in report
    saved = read_json(tmp_path / "diarization_candidates" / "candidate_001.json")
    assert saved["candidate_id"] == "candidate_001"


def test_candidate_directory_symlink_is_rejected_without_touching_target(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.agentic_diarization.enabled = True
    config.agentic_diarization.known_speakers = 2
    external = tmp_path / "external"
    external.mkdir()
    original_mode = stat.S_IMODE(external.stat().st_mode)
    (tmp_path / "diarization_candidates").symlink_to(external, target_is_directory=True)

    try:
        run_agentic_diarization(
            tmp_path / "audio.wav",
            config,
            CandidateBackend(),
            words=words(),
            vad_regions=vad_regions(),
            out_dir=tmp_path,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("pre-existing candidate directory must be rejected")

    assert stat.S_IMODE(external.stat().st_mode) == original_mode
    assert not list(external.iterdir())


def test_flapping_and_unlabeled_penalties() -> None:
    flappy = DiarizationResult(
        turns=[
            DiarizationTurn(start=0.0, end=0.5, speaker="A"),
            DiarizationTurn(start=0.5, end=0.9, speaker="B"),
        ]
    )
    score = score_candidate(flappy, words=words(), vad_regions=vad_regions(), expected_speakers=2)
    assert score["speaker_flapping_penalty"] > 0
    assert score["long_unlabeled_region_seconds"] > 0
    assert "long_unlabeled_regions" in score["warnings"]


def test_overlap_preservation_counts_overlap_ratio() -> None:
    result = DiarizationResult(
        turns=[
            DiarizationTurn(start=0, end=1, speaker="A", overlap=True),
            DiarizationTurn(start=0.5, end=1.5, speaker="B", overlap=True),
        ]
    )
    score = score_candidate(
        result,
        words=words(),
        vad_regions=vad_regions(),
        expected_speakers=None,
    )
    assert score["overlap_turn_ratio"] == 1.0


def test_overlapping_turns_do_not_double_count_vad_coverage_or_hide_tail() -> None:
    result = DiarizationResult(
        turns=[
            DiarizationTurn(start=0.0, end=5.0, speaker="A"),
            DiarizationTurn(start=0.0, end=5.0, speaker="B"),
        ]
    )
    score = score_candidate(
        result,
        words=[],
        vad_regions=[VADRegion(start=0.0, end=10.0, source="test")],
        expected_speakers=2,
    )

    assert score["vad_coverage"] == 0.5
    assert score["long_unlabeled_region_seconds"] == 5.0


def test_disjoint_turns_use_union_coverage() -> None:
    result = DiarizationResult(
        turns=[
            DiarizationTurn(start=0.0, end=2.0, speaker="A"),
            DiarizationTurn(start=4.0, end=6.0, speaker="B"),
        ]
    )
    score = score_candidate(
        result,
        words=[],
        vad_regions=[VADRegion(start=0.0, end=8.0, source="test")],
        expected_speakers=2,
    )

    assert score["vad_coverage"] == 0.5
    assert score["long_unlabeled_region_seconds"] == 4.0


def test_deterministic_tie_break_prefers_lower_speaker_count() -> None:
    selected = select_candidate(
        [
            {"candidate_id": "candidate_002", "score": {"total": 1.0, "speaker_count": 3}},
            {"candidate_id": "candidate_001", "score": {"total": 1.0, "speaker_count": 2}},
        ]
    )
    assert selected["candidate_id"] == "candidate_001"
