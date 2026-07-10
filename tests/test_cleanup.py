# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max.cleanup import (
    CleanupRejectedError,
    cleanup_is_safe,
    run_cleanup,
    validate_cleanup_endpoint,
)
from local_transcript_max.config import load_config
from local_transcript_max.schemas import SpeakerTurn, Transcript


def test_cleanup_disabled_default(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    report = run_cleanup(Transcript(input_path="x"), tmp_path, config)
    assert report["enabled"] is False


def test_localhost_accepted_remote_rejected() -> None:
    validate_cleanup_endpoint("http://127.0.0.1:11434/v1", allow_remote_cleanup=False)
    with pytest.raises(CleanupRejectedError):
        validate_cleanup_endpoint("https://example.com/v1", allow_remote_cleanup=False)
    validate_cleanup_endpoint("https://example.com/v1", allow_remote_cleanup=True)
    with pytest.raises(CleanupRejectedError, match="inline credentials"):
        validate_cleanup_endpoint(
            "http://user:pass@127.0.0.1:11434/v1",
            allow_remote_cleanup=False,
        )
    with pytest.raises(CleanupRejectedError, match="inline credentials"):
        validate_cleanup_endpoint(
            "http://127.0.0.1:11434/v1?token=secret",
            allow_remote_cleanup=False,
        )
    with pytest.raises(CleanupRejectedError, match="Offline mode"):
        validate_cleanup_endpoint(
            "https://example.com/v1",
            allow_remote_cleanup=True,
            offline=True,
        )


def test_divergence_rejection() -> None:
    accepted, reason = cleanup_is_safe(
        "one two three four",
        "one two three four five six seven eight",
        max_word_delta_ratio=0.15,
    )
    assert accepted is False
    assert reason == "word_count_diverged_too_far"


def test_cleanup_writes_diff_and_preserves_raw(tmp_path: Path, monkeypatch) -> None:
    config = load_config(cwd=tmp_path)
    config.cleanup.mode = "local-llm"
    config.cleanup.write_diff = True
    transcript = Transcript(
        input_path="x.wav",
        speaker_turns=[SpeakerTurn(speaker="SPEAKER_00", start=0, end=1, text="hello world")],
    )
    monkeypatch.setattr(
        "local_transcript_max.cleanup.request_local_cleanup",
        lambda endpoint, model, scope, text: "Hello world.",
    )
    report = run_cleanup(transcript, tmp_path, config)
    assert report["accepted"] is True
    assert (tmp_path / "cleanup_diff.json").exists()
    assert (tmp_path / "transcript.cleaned.json").exists()
    assert not (tmp_path / "transcript.raw.json").exists()
