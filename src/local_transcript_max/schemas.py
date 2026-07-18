# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Pydantic schemas used across the pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LTMBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class StageTiming(LTMBaseModel):
    name: str
    duration_seconds: float
    started_at: datetime | None = None
    ended_at: datetime | None = None


class AudioMetadata(LTMBaseModel):
    path: str
    duration_seconds: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None
    bit_rate: int | None = None
    format_name: str | None = None
    loudness_estimate: float | None = None
    clipping_risk: bool | None = None
    silence_ratio: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelMetadata(LTMBaseModel):
    role: str
    model_id: str
    local_path: str | None = None
    revision: str | None = None
    revision_verified: bool | None = None
    checkpoint_sha256: str | None = None
    gated: bool = False
    present: bool = False


class VADRegion(LTMBaseModel):
    start: float
    end: float
    confidence: float = 1.0
    source: str = "unknown"


class Word(LTMBaseModel):
    text: str
    start: float | None = None
    end: float | None = None
    confidence: float | None = None
    speaker: str | None = None
    uncertain_speaker: bool = False
    speaker_overlap_seconds: float | None = None
    speaker_overlap_ratio: float | None = None
    speaker_candidates: list[dict[str, Any]] = Field(default_factory=list)
    assignment_source: str | None = None
    speaker_match_label: str | None = None
    speaker_match_confidence: float | None = None
    speaker_match_alternatives: list[dict[str, Any]] = Field(default_factory=list)
    speaker_match_ambiguous: bool = False
    speaker_profile_id: str | None = None


class AlignedWord(Word):
    alignment_source: Literal["forced", "whisper", "estimated", "missing"] = "missing"
    warnings: list[str] = Field(default_factory=list)


class ASRSegment(LTMBaseModel):
    id: int
    start: float
    end: float
    text: str
    words: list[AlignedWord] = Field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None


class ASRResult(LTMBaseModel):
    segments: list[ASRSegment]
    language: str | None = None
    language_probability: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    model: ModelMetadata | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    timing: StageTiming | None = None


class DiarizationTurn(LTMBaseModel):
    start: float
    end: float
    speaker: str
    confidence: float | None = None
    overlap: bool = False
    source: str = "unknown"


class DiarizationResult(LTMBaseModel):
    turns: list[DiarizationTurn]
    exclusive_turns: list[DiarizationTurn] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    model: ModelMetadata | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    timing: StageTiming | None = None


class SpeakerTurn(LTMBaseModel):
    speaker: str
    start: float
    end: float
    text: str
    words: list[AlignedWord] = Field(default_factory=list)
    mapped_name: str | None = None
    confidence: float | None = None
    speaker_match_label: str | None = None
    speaker_match_confidence: float | None = None
    speaker_match_alternatives: list[dict[str, Any]] = Field(default_factory=list)
    speaker_match_ambiguous: bool = False
    speaker_profile_id: str | None = None


class SpeakerMap(LTMBaseModel):
    speaker: str
    mapped_name: str | None = None
    confidence: float | None = None
    competing_matches: list[dict[str, Any]] = Field(default_factory=list)


class SpeakerAuditRow(LTMBaseModel):
    speaker: str
    mapped_name: str | None = None
    total_speaking_time: float
    turn_count: int
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)


class SpeakerReport(LTMBaseModel):
    speakers: list[SpeakerAuditRow]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Transcript(LTMBaseModel):
    input_path: str
    language: str | None = None
    segments: list[ASRSegment] = Field(default_factory=list)
    words: list[AlignedWord] = Field(default_factory=list)
    diarization_turns: list[DiarizationTurn] = Field(default_factory=list)
    exclusive_diarization_turns: list[DiarizationTurn] = Field(default_factory=list)
    speaker_turns: list[SpeakerTurn] = Field(default_factory=list)
    speaker_map: list[SpeakerMap] = Field(default_factory=list)
    cleaned_text: str | None = None


class QAFlag(LTMBaseModel):
    code: str
    severity: Literal["info", "warning", "error"]
    message: str
    start: float | None = None
    end: float | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class QAReport(LTMBaseModel):
    flags: list[QAFlag] = Field(default_factory=list)
    changed_spans: list[dict[str, Any]] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class SpeakerEnrollment(LTMBaseModel):
    name: str
    embedding_path: str
    sample_paths: list[str]
    backend: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SpeakerEmbeddingClusterReport(LTMBaseModel):
    selected_duration_seconds: float = Field(ge=0.0)
    embedding_dimensions: int | None = Field(default=None, gt=0)
    skip_reason: Literal["no_eligible_speech"] | None = None


class SpeakerEmbeddingReport(LTMBaseModel):
    schema_version: Literal["speaker_embedding_report.v1"] = "speaker_embedding_report.v1"
    model_revision: str
    device: Literal["cpu", "cuda"]
    speakers: dict[str, SpeakerEmbeddingClusterReport] = Field(default_factory=dict)


class BenchmarkFileMetric(LTMBaseModel):
    input_file: str
    audio_duration_seconds: float | None = None
    wall_time_seconds: float | None = None
    wer: float | None = None
    cer: float | None = None
    mer: float | None = None
    wil: float | None = None
    wip: float | None = None
    der: float | None = None
    der_metadata: dict[str, Any] = Field(default_factory=dict)
    speaker_count_accuracy: float | None = None
    words_with_timestamps_pct: float | None = None
    words_with_speaker_labels_pct: float | None = None
    uncertain_speaker_pct: float | None = None
    overlap_span_count: int | None = None
    real_time_factor: float | None = None
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    model_device_compute: dict[str, Any] = Field(default_factory=dict)
    timings: dict[str, float] = Field(default_factory=dict)
    artifact_counts: dict[str, Any] = Field(default_factory=dict)
    audio_preprocessing: dict[str, Any] = Field(default_factory=dict)
    quality_checks: dict[str, Any] = Field(default_factory=dict)


class BenchmarkReport(LTMBaseModel):
    schema_version: str = "benchmark_report.v3"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)
    files: list[BenchmarkFileMetric]
    aggregate: dict[str, float | int | None] = Field(default_factory=dict)


class RunMetadata(LTMBaseModel):
    package_version: str
    code_revision: str | None = None
    source_config_sha256: str | None = None
    source_config_sha256_scope: Literal["complete_runtime_configuration.v1"] | None = None
    source_configuration: dict[str, Any] = Field(default_factory=dict)
    source_configuration_schema: Literal["privacy_safe_configuration.v1"] | None = None
    input_file_hash: str | None = None
    input_metadata: AudioMetadata | None = None
    canonical_audio_metadata: AudioMetadata | None = None
    ffmpeg_command: list[str] = Field(default_factory=list)
    models: list[ModelMetadata] = Field(default_factory=list)
    asr_backend: str | None = None
    diarization_backend: str | None = None
    alignment_backend: str | None = None
    alignment_device: str | None = None
    asr_requested_device: str | None = None
    asr_resolved_device: str | None = None
    asr_actual_device: str | None = None
    alignment_requested_device: str | None = None
    alignment_resolved_device: str | None = None
    alignment_actual_device: str | None = None
    alignment_fallback_reason: str | None = None
    compute_type: str | None = None
    device: str | None = None
    asr_cpu_threads: int | None = None
    asr_num_workers: int | None = None
    asr_batched: bool = False
    asr_batch_size: int | None = None
    diarization_requested_device: str | None = None
    diarization_resolved_device: str | None = None
    diarization_actual_device: str | None = None
    diarization_device_fallback: bool = False
    diarization_fallback_reason: str | None = None
    diarization_warnings: list[str] = Field(default_factory=list)
    device_policy: dict[str, Any] = Field(default_factory=dict)
    cuda_available: bool | None = None
    dependency_versions: dict[str, str | None] = Field(default_factory=dict)
    runtime_environment: dict[str, Any] = Field(default_factory=dict)
    config_file: str | None = None
    environment_overrides: dict[str, str] = Field(default_factory=dict)
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    audio_preprocessing: dict[str, Any] = Field(default_factory=dict)
    processing_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    timings: list[StageTiming] = Field(default_factory=list)
    real_time_factor: float | None = None
    timing_scope: Literal["pipeline_core.v1"] = "pipeline_core.v1"
    completion_timing_artifact: str | None = "completion_timing.json"


class CompletionStageDurations(LTMBaseModel):
    """Named work outside the immutable pipeline-core timing scope."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
        allow_inf_nan=False,
    )

    invocation_setup: float = Field(strict=True, ge=0.0)
    run_metadata_preparation_and_publish: float = Field(strict=True, ge=0.0)
    speaker_identity: float = Field(strict=True, ge=0.0)
    debug_report_publish: float = Field(strict=True, ge=0.0)


class CompletionTimingReport(LTMBaseModel):
    """Observed end-to-end timing kept separate from immutable run metadata."""

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
        allow_inf_nan=False,
    )

    schema_version: Literal["completion_timing.v1"] = "completion_timing.v1"
    run_metadata_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pipeline_core_seconds: float = Field(strict=True, ge=0.0)
    observed_end_to_end_seconds: float = Field(strict=True, ge=0.0)
    real_time_factor: float | None = Field(default=None, strict=True, ge=0.0)
    stage_durations: CompletionStageDurations
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    measurement_scope: Literal[
        "through_debug_publish_before_completion_artifact.v1"
    ] = "through_debug_publish_before_completion_artifact.v1"
    excluded_operations: tuple[
        Literal["completion_timing_artifact_publish"],
        Literal["completion_progress_callback"],
    ] = (
        "completion_timing_artifact_publish",
        "completion_progress_callback",
    )

    @model_validator(mode="after")
    def _end_to_end_covers_core(self) -> CompletionTimingReport:
        if self.observed_end_to_end_seconds < self.pipeline_core_seconds:
            raise ValueError("observed end-to-end timing cannot be shorter than core")
        accounted = self.pipeline_core_seconds + sum(
            (
                self.stage_durations.invocation_setup,
                self.stage_durations.run_metadata_preparation_and_publish,
                self.stage_durations.speaker_identity,
                self.stage_durations.debug_report_publish,
            )
        )
        if self.observed_end_to_end_seconds + 1e-9 < accounted:
            raise ValueError(
                "observed end-to-end timing cannot be shorter than accounted stages"
            )
        return self


def jsonable(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def path_str(path: Path | str) -> str:
    return str(Path(path).expanduser())
