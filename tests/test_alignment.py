# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

from pathlib import Path

import pytest

from local_transcript_max.alignment.forced_alignment import WhisperXForcedAlignment
from local_transcript_max.config import load_config
from local_transcript_max.errors import BackendUnavailableError
from local_transcript_max.pipeline import align_words
from local_transcript_max.schemas import ASRResult, ASRSegment


class MissingForcedAlignment:
    name = "missing-forced"

    def align(self, audio_path: Path, asr_result: ASRResult, config):
        raise BackendUnavailableError("mock forced alignment unavailable")


class SuccessfulForcedAlignment:
    name = "mock-forced"

    def __init__(self) -> None:
        self.last_device: str | None = None

    def align(self, audio_path: Path, asr_result: ASRResult, config):
        self.last_device = config.alignment.device
        return asr_result.segments[0].words


def test_auto_alignment_falls_back_to_estimated_words(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "auto"
    asr = ASRResult(segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello world")])
    outcome = align_words(tmp_path / "audio.wav", asr, config, MissingForcedAlignment())
    assert outcome.backend == "whisper-word-timestamps"
    assert outcome.warnings == ["mock forced alignment unavailable"]
    assert [word.alignment_source for word in outcome.words] == ["estimated", "estimated"]


def test_forced_alignment_records_explicit_mps_device(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    config.alignment.device = "mps"
    asr = ASRResult(
        segments=[
            ASRSegment(
                id=0,
                start=0.0,
                end=1.0,
                text="hello",
                words=[],
            )
        ]
    )

    outcome = align_words(tmp_path / "audio.wav", asr, config, SuccessfulForcedAlignment())

    assert outcome.backend == "mock-forced"
    assert outcome.device == "mps"


def test_forced_alignment_mode_does_not_fake_success(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    asr = ASRResult(segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")])
    with pytest.raises(BackendUnavailableError):
        align_words(tmp_path / "audio.wav", asr, config, MissingForcedAlignment())


def test_whisperx_alignment_requires_local_cache_before_loading(tmp_path: Path) -> None:
    config = load_config(cwd=tmp_path)
    config.offline = True
    config.alignment.backend = "forced"
    asr = ASRResult(
        language="en",
        segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")],
    )

    with pytest.raises(BackendUnavailableError, match="ltm models pull --alignment en"):
        WhisperXForcedAlignment().align(tmp_path / "audio.wav", asr, config)


def test_whisperx_alignment_rejects_non_ctc_local_path(tmp_path: Path) -> None:
    model_dir = tmp_path / "facebook--wav2vec2-large-xlsr-53"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    (model_dir / "preprocessor_config.json").write_text("{}", encoding="utf-8")
    config = load_config(cwd=tmp_path)
    config.alignment.backend = "forced"
    config.alignment.model = str(model_dir)
    asr = ASRResult(
        language="en",
        segments=[ASRSegment(id=0, start=0.0, end=1.0, text="hello")],
    )

    with pytest.raises(BackendUnavailableError, match="not a WhisperX-compatible CTC"):
        WhisperXForcedAlignment().align(tmp_path / "audio.wav", asr, config)
