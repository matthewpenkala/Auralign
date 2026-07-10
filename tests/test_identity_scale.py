# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from __future__ import annotations

import json
import tracemalloc
from pathlib import Path

from local_transcript_max.schemas import AlignedWord, SpeakerTurn, Transcript
from local_transcript_max.speaker_id.identity_io import deterministic_json_bytes
from local_transcript_max.speaker_id.identity_models import (
    AttributionAudit,
    ResolverMode,
    ResolverPolicy,
    SourceMediaReviewMode,
)
from local_transcript_max.speaker_id.identity_resolver import (
    ResolveIdentityOptions,
    resolve_identities,
)


def _write_large_job(job_dir: Path, *, turn_count: int) -> None:
    job_dir.mkdir()
    words: list[AlignedWord] = []
    turns: list[SpeakerTurn] = []
    for index in range(turn_count):
        speaker = f"SPEAKER_{index % 4:02d}"
        start = index * 0.25
        word = AlignedWord(
            text=f"synthetic-{index}",
            start=start,
            end=start + 0.20,
            speaker=speaker,
            assignment_source="exclusive_diarization",
            alignment_source="forced",
        )
        words.append(word)
        turns.append(
            SpeakerTurn(
                speaker=speaker,
                start=word.start,
                end=word.end,
                text=word.text,
                words=[word],
            )
        )
    transcript = Transcript(input_path="synthetic.wav", words=words, speaker_turns=turns)
    (job_dir / "transcript.json").write_bytes(deterministic_json_bytes(transcript))
    (job_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "input_file_hash": "a" * 64,
                "processing_timestamp": "2026-07-09T00:00:00Z",
                "canonical_audio_metadata": {"duration_seconds": turn_count * 0.25},
            }
        ),
        encoding="utf-8",
    )


def test_large_synthetic_job_has_linear_exact_audit_and_bounded_peak_memory(
    tmp_path: Path,
) -> None:
    turn_count = 2_000
    job_dir = tmp_path / "large-job"
    _write_large_job(job_dir, turn_count=turn_count)

    tracemalloc.start()
    try:
        result = resolve_identities(
            job_dir,
            ResolveIdentityOptions(
                policy=ResolverPolicy(
                    mode=ResolverMode.REVIEW,
                    source_media_review=SourceMediaReviewMode.NONE,
                )
            ),
        )
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(result.report.cluster_decisions) == 4
    assert len(result.report.turn_decisions) == turn_count
    audit = AttributionAudit.model_validate_json(
        (job_dir / "speaker_identity_attribution_audit.json").read_bytes()
    )
    assert len(audit.rows) == turn_count + 4
    assert result.report.validation_summary.audit_complete is True
    assert peak_bytes < 256 * 1024 * 1024
