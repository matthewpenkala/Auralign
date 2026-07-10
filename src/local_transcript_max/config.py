# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Configuration, presets, and model manifest handling."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .errors import LTMError
from .utils import env_flag, safe_repo_dir

PresetName = Literal["max", "balanced", "fast", "cpu"]


@dataclass(frozen=True)
class ModelEntry:
    key: str
    role: str
    model_id: str
    gated: bool = False
    required_files: tuple[str, ...] = ()
    any_required_files: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    revision: str | None = None
    optional: bool = False
    pullable: bool = True
    directory_aliases: tuple[str, ...] = ()
    license_expression: str = "NOASSERTION"
    source_url: str = ""
    terms_url: str = ""
    attribution: str = ""
    acquisition_status: str = "blocked"
    runtime_status: str = "blocked"
    redistribution_status: str = "blocked"
    review_status: str = "unverified"
    reviewed_at: str | None = None

    @property
    def directory_name(self) -> str:
        return safe_repo_dir(self.model_id)

    @property
    def selection_flag(self) -> str:
        return {
            "asr": "--asr",
            "asr-mlx": "--asr-mlx",
            "diarization": "--diarization",
            "alignment": "--alignment",
            "speaker-embedding": "--speaker-id",
        }.get(self.role, f"--{self.role}")

    def pull_command(self, cache_dir: str = "models") -> str:
        if not self.pullable:
            return (
                f"place local model files for {self.model_id} under "
                f"{cache_dir}/{self.directory_name}"
            )
        return f"ltm models pull {self.selection_flag} {self.key} --cache-dir {cache_dir}"

    def verify_command(self, cache_dir: str = "models") -> str:
        if not self.pullable:
            return "ltm models audit --config CONFIG_WITH_REVIEWED_LOCAL_MODEL_PATH"
        return f"ltm models verify {self.selection_flag} {self.key} --cache-dir {cache_dir}"


MLX_REVISIONS = {
    "mlx-community/whisper-tiny-mlx": "6caf9c55601caafbe6508a8b0d216bdf4783c4e8",
    "mlx-community/whisper-tiny.en-mlx": "5f4dafbb28e62a53c1b10426ff7eed36ca733bf7",
    "mlx-community/whisper-base-mlx": "1e3e249fb8d01c655324bd6841b1deadffd6d04c",
    "mlx-community/whisper-base.en-mlx": "aa0678c3466ed62c5c6114ec600a0e1f96820089",
    "mlx-community/whisper-small-mlx": "45f3915923c7a79a5a5b5a7d909d39aeb0e5630e",
    "mlx-community/whisper-small.en-mlx": "52a88bf6e98b114a210c21bb83e22d6e1505cb73",
    "mlx-community/whisper-medium-mlx": "7fc08c4eac4c316526498f147dfdee6f6303f975",
    "mlx-community/whisper-medium.en-mlx": "8d0335069b551e8586b8a4680674403d9417dfe4",
    "mlx-community/whisper-large-mlx": "9310354911111f2406ead1478e0139d9c6ea3acc",
    "mlx-community/whisper-large-v2-mlx": "cce86229e2765266197fef869ce9f7e2550067ab",
    "mlx-community/whisper-large-v3-mlx": "49e6aa286ad60c14352c404340ded53710378a11",
    "mlx-community/whisper-large-v3-turbo": "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb",
}


def mlx_model_entry(key: str, model_id: str, aliases: tuple[str, ...] = ()) -> ModelEntry:
    verified = model_id == "mlx-community/whisper-large-v3-mlx"
    return ModelEntry(
        key=key,
        role="asr-mlx",
        model_id=model_id,
        required_files=("config.json",),
        any_required_files=("weights.safetensors", "weights.npz"),
        aliases=aliases,
        directory_aliases=(key, *(alias for alias in aliases if "/" not in alias)),
        optional=True,
        pullable=False,
        revision=MLX_REVISIONS[model_id],
        license_expression="MIT" if verified else "NOASSERTION",
        source_url=f"https://huggingface.co/{model_id}",
        terms_url=f"https://huggingface.co/{model_id}",
        attribution="MLX Community model conversion; upstream model terms also apply.",
        acquisition_status="manual_only",
        runtime_status="approved_local" if verified else "local_evaluation_only",
        redistribution_status="permitted_with_license_terms" if verified else "blocked",
        review_status="verified" if verified else "blocked_missing_publisher_license",
        reviewed_at="2026-07-10",
    )


MODEL_MANIFEST: dict[str, ModelEntry] = {
    "large-v3": ModelEntry(
        key="large-v3",
        role="asr",
        model_id="Systran/faster-whisper-large-v3",
        required_files=("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"),
        aliases=("max", "whisper-large-v3"),
        revision="edaa852ec7e145841d8ffdb056a99866b5f0a478",
        license_expression="MIT",
        source_url="https://huggingface.co/Systran/faster-whisper-large-v3",
        terms_url="https://huggingface.co/Systran/faster-whisper-large-v3",
        attribution="SYSTRAN faster-whisper conversion of OpenAI Whisper large-v3.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified",
        reviewed_at="2026-07-10",
    ),
    "turbo": ModelEntry(
        key="turbo",
        role="asr",
        model_id="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        required_files=("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"),
        aliases=(
            "large-v3-turbo",
            "dropbox-dash/faster-whisper-large-v3-turbo",
        ),
        revision="0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        license_expression="MIT",
        source_url="https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        terms_url="https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        attribution="Mobius Labs faster-whisper conversion; upstream Whisper terms also apply.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified",
        reviewed_at="2026-07-10",
    ),
    "distil-large-v3": ModelEntry(
        key="distil-large-v3",
        role="asr",
        model_id="Systran/faster-distil-whisper-large-v3",
        required_files=("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"),
        revision="c3058b475261292e64a0412df1d2681c06260fab",
        license_expression="MIT",
        source_url="https://huggingface.co/Systran/faster-distil-whisper-large-v3",
        terms_url="https://huggingface.co/Systran/faster-distil-whisper-large-v3",
        attribution="SYSTRAN CTranslate2 conversion of Distil-Whisper.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified",
        reviewed_at="2026-07-10",
    ),
    "distil-large-v3.5": ModelEntry(
        key="distil-large-v3.5",
        role="asr",
        model_id="distil-whisper/distil-large-v3.5-ct2",
        required_files=("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"),
        revision="9793ccc07920e0f830e1dba0343efcdf0ef8c903",
        license_expression="MIT",
        source_url="https://huggingface.co/distil-whisper/distil-large-v3.5-ct2",
        terms_url="https://huggingface.co/distil-whisper/distil-large-v3.5-ct2",
        attribution="Distil-Whisper CTranslate2 conversion.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified",
        reviewed_at="2026-07-10",
    ),
    "mlx-tiny": mlx_model_entry(
        "mlx-tiny",
        "mlx-community/whisper-tiny-mlx",
        aliases=("tiny", "whisper-tiny-mlx", "mlx-community/whisper-tiny-mlx"),
    ),
    "mlx-tiny.en": mlx_model_entry(
        "mlx-tiny.en",
        "mlx-community/whisper-tiny.en-mlx",
        aliases=("tiny.en", "whisper-tiny.en-mlx", "mlx-community/whisper-tiny.en-mlx"),
    ),
    "mlx-base": mlx_model_entry(
        "mlx-base",
        "mlx-community/whisper-base-mlx",
        aliases=("base", "whisper-base-mlx", "mlx-community/whisper-base-mlx"),
    ),
    "mlx-base.en": mlx_model_entry(
        "mlx-base.en",
        "mlx-community/whisper-base.en-mlx",
        aliases=("base.en", "whisper-base.en-mlx", "mlx-community/whisper-base.en-mlx"),
    ),
    "mlx-small": mlx_model_entry(
        "mlx-small",
        "mlx-community/whisper-small-mlx",
        aliases=("small", "whisper-small-mlx", "mlx-community/whisper-small-mlx"),
    ),
    "mlx-small.en": mlx_model_entry(
        "mlx-small.en",
        "mlx-community/whisper-small.en-mlx",
        aliases=("small.en", "whisper-small.en-mlx", "mlx-community/whisper-small.en-mlx"),
    ),
    "mlx-medium": mlx_model_entry(
        "mlx-medium",
        "mlx-community/whisper-medium-mlx",
        aliases=("medium", "whisper-medium-mlx", "mlx-community/whisper-medium-mlx"),
    ),
    "mlx-medium.en": mlx_model_entry(
        "mlx-medium.en",
        "mlx-community/whisper-medium.en-mlx",
        aliases=("medium.en", "whisper-medium.en-mlx", "mlx-community/whisper-medium.en-mlx"),
    ),
    "mlx-large": mlx_model_entry(
        "mlx-large",
        "mlx-community/whisper-large-mlx",
        aliases=("large", "large-v1", "whisper-large-mlx", "mlx-community/whisper-large-mlx"),
    ),
    "mlx-large-v2": mlx_model_entry(
        "mlx-large-v2",
        "mlx-community/whisper-large-v2-mlx",
        aliases=("large-v2", "whisper-large-v2-mlx", "mlx-community/whisper-large-v2-mlx"),
    ),
    "mlx-large-v3": mlx_model_entry(
        "mlx-large-v3",
        "mlx-community/whisper-large-v3-mlx",
        aliases=("large-v3", "whisper-large-v3-mlx", "mlx-community/whisper-large-v3-mlx"),
    ),
    "mlx-large-v3-turbo": mlx_model_entry(
        "mlx-large-v3-turbo",
        "mlx-community/whisper-large-v3-turbo",
        aliases=(
            "mlx-turbo",
            "large-v3-turbo-mlx",
            "large-v3-turbo",
            "turbo",
            "mlx-community/whisper-large-v3-turbo",
        ),
    ),
    "community-1": ModelEntry(
        key="community-1",
        role="diarization",
        model_id="pyannote/speaker-diarization-community-1",
        gated=True,
        required_files=(
            "config.yaml",
            "segmentation/pytorch_model.bin",
            "embedding/pytorch_model.bin",
            "plda/plda.npz",
            "plda/xvec_transform.npz",
        ),
        aliases=("pyannote-community-1",),
        revision="3533c8cf8e369892e6b79ff1bf80f7b0286a54ee",
        license_expression="CC-BY-4.0",
        source_url="https://huggingface.co/pyannote/speaker-diarization-community-1",
        terms_url="https://huggingface.co/pyannote/speaker-diarization-community-1",
        attribution="pyannote speaker-diarization-community-1; attribution and gated terms apply.",
        acquisition_status="approved_after_gated_terms",
        runtime_status="approved_local_after_gated_terms",
        redistribution_status="permitted_with_attribution_and_gated_terms",
        review_status="verified_gated",
        reviewed_at="2026-07-10",
    ),
    "en": ModelEntry(
        key="en",
        role="alignment",
        model_id="facebook/wav2vec2-base-960h",
        required_files=(
            "config.json",
            "preprocessor_config.json",
            "tokenizer_config.json",
            "vocab.json",
        ),
        any_required_files=("pytorch_model.bin", "model.safetensors"),
        aliases=("auto", "english"),
        optional=True,
        revision="22aad52d435eb6dbaf354bdad9b0da84ce7d6156",
        license_expression="Apache-2.0",
        source_url="https://huggingface.co/facebook/wav2vec2-base-960h",
        terms_url="https://huggingface.co/facebook/wav2vec2-base-960h",
        attribution="Meta wav2vec2-base-960h.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified",
        reviewed_at="2026-07-10",
    ),
    "ecapa": ModelEntry(
        key="ecapa",
        role="speaker-embedding",
        model_id="speechbrain/spkrec-ecapa-voxceleb",
        required_files=("hyperparams.yaml",),
        aliases=("speechbrain-ecapa",),
        optional=True,
        revision="0f99f2d0ebe89ac095bcc5903c4dd8f72b367286",
        license_expression="Apache-2.0",
        source_url="https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb",
        terms_url="https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb",
        attribution="SpeechBrain ECAPA-TDNN model; upstream VoxCeleb data caveats apply.",
        acquisition_status="approved",
        runtime_status="approved_local",
        redistribution_status="permitted_with_license_terms",
        review_status="verified_with_training_data_caveat",
        reviewed_at="2026-07-10",
    ),
}


def resolve_model_entry(role: str, value: str | None) -> ModelEntry | None:
    if not value:
        return None
    normalized = value.strip()
    for entry in MODEL_MANIFEST.values():
        if entry.role != role:
            continue
        if normalized in {entry.key, entry.model_id, *entry.aliases}:
            return entry
    return None


class ASRConfig(BaseModel):
    backend: Literal["faster-whisper", "mlx-whisper"] = "faster-whisper"
    model: str = "large-v3"
    mlx_model: str = "mlx-large-v3-turbo"
    mlx_model_path: Path | None = None
    mlx_fp16: bool = True
    mlx_vad_windowed: bool = False
    mlx_vad_window_seconds: float = Field(default=30.0, gt=0.0)
    mlx_vad_window_padding_seconds: float = Field(default=0.0, ge=0.0)
    device: str = "auto"
    compute_type: str = "auto"
    cpu_threads: int | None = Field(default=None, gt=0)
    num_workers: int | None = Field(default=None, gt=0)
    batched: bool = False
    beam_size: int = 5
    best_of: int = 5
    patience: float | None = None
    temperature: float | list[float] = Field(default_factory=lambda: [0.0, 0.2, 0.4])
    compression_ratio_threshold: float = 2.4
    logprob_threshold: float = -1.0
    no_speech_threshold: float = 0.6
    condition_on_previous_text: bool = False
    language: str | None = None
    task: str = "transcribe"
    initial_prompt: str | None = None
    prefix: str | None = None
    glossary_file: Path | None = None
    word_timestamps: bool = True
    vad_filter: bool = False
    batch_size: int = Field(default=8, gt=0)


class DiarizationConfig(BaseModel):
    backend: Literal["auto", "pyannote", "off"] = "pyannote"
    model: str = "community-1"
    num_speakers: int | None = Field(default=None, ge=1, le=128)
    min_speakers: int | None = Field(default=None, ge=1, le=128)
    max_speakers: int | None = Field(default=None, ge=1, le=128)
    device: str = "auto"
    enabled: bool = True
    preserve_overlap: bool = True
    exclusive_diarization: bool = True

    @model_validator(mode="after")
    def validate_speaker_range(self) -> DiarizationConfig:
        if (
            self.min_speakers is not None
            and self.max_speakers is not None
            and self.min_speakers > self.max_speakers
        ):
            raise ValueError("min_speakers must be less than or equal to max_speakers")
        return self


class AgenticDiarizationConfig(BaseModel):
    enabled: bool = False
    candidates: int | None = Field(default=None, ge=1, le=128)
    candidate_range: str = "2:8"
    known_speakers: int | None = Field(default=None, ge=1, le=128)
    write_decision_report: bool = True

    @field_validator("candidate_range")
    @classmethod
    def validate_candidate_range(cls, value: str) -> str:
        try:
            left, right = value.split(":", maxsplit=1)
            start = int(left)
            end = int(right)
        except ValueError as exc:
            raise ValueError("candidate_range must be an inclusive range like 2:8") from exc
        if start < 1 or end < start or end > 128:
            raise ValueError("candidate_range must satisfy 1 <= start <= end <= 128")
        return value


class AlignmentConfig(BaseModel):
    backend: str = "auto"
    language: str | None = None
    model: str | None = None
    device: str | None = None
    enabled: bool = True


class AudioConfig(BaseModel):
    sample_rate: int = 16000
    mono: bool = True
    normalize_loudness: bool = False
    denoise: Literal["off", "mild", "strong"] = "off"
    separate_vocals: Literal["off", "experimental"] = "off"
    preserve_channels: bool = False


class QAConfig(BaseModel):
    enabled: bool = True
    targeted_redecode: bool = False


class RedecodeConfig(BaseModel):
    mode: Literal["off", "low-confidence", "hallucination", "all-suspect"] = "off"
    max_spans: int = 5
    pad_seconds: float = 1.0
    beam_size: int = 5
    temperature: float = 0.0
    write_report: bool = False
    write_repaired_transcript: bool = False

    @field_validator("write_repaired_transcript")
    @classmethod
    def reject_repaired_transcript_output(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "write_repaired_transcript=true is no longer supported: targeted "
                "re-decode is candidate-only. Set it to false and review "
                "transcript.redecode_candidates.json instead."
            )
        return value


class SpeakerIDConfig(BaseModel):
    mode: Literal["off", "auto"] = "off"
    profile_dir: Path | None = None
    threshold: float = 0.75
    ambiguity_margin: float = 0.05
    allow_fallback_embeddings: bool = False


class IdentityResolverConfig(BaseModel):
    """Opt-in post-transcription speaker-identity resolver configuration."""

    mode: Literal["off", "review", "apply-safe", "apply-approved"] = "off"
    known_speakers: int | None = Field(default=None, ge=1, le=128)
    roster_path: Path | None = None
    external_transcript: Path | None = None
    approved_map_path: Path | None = None
    sibling_jobs: list[Path] = Field(default_factory=list, max_length=100)
    sibling_root: Path | None = None
    sibling_manifest: Path | None = None
    stability_config_path: Path | None = None
    media_review_report_path: Path | None = None
    write_identity_mapped_exports: bool = False
    write_media_review_queue: bool = False
    audit_csv: bool = True
    deliverable_format: Literal["srt", "all"] = "srt"
    source_media_review: Literal["none", "queue", "reviewed-report"] = "queue"
    turn_boundary_repair: Literal["report-only", "label-only"] = "report-only"
    unattributed_label: str = "Unattributed"
    min_apply_confidence: float = Field(default=0.90, ge=0.0, le=1.0)
    profile_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    profile_ambiguity_margin: float = Field(default=0.05, ge=0.0, le=1.0)
    max_fragment_duration: float = Field(default=4.0, gt=0.0, le=60.0)
    max_fragment_words: int = Field(default=12, ge=1, le=500)
    max_sibling_jobs: int = Field(default=20, ge=1, le=100)

    @field_validator("unattributed_label")
    @classmethod
    def validate_unattributed_label(cls, value: str) -> str:
        from .speaker_id.identity_models import normalize_display_label

        return normalize_display_label(value, allow_reserved=True)

    @model_validator(mode="after")
    def validate_mode_inputs(self) -> IdentityResolverConfig:
        if self.mode == "apply-approved" and self.approved_map_path is None:
            raise ValueError("apply-approved requires approved_map_path")
        if self.mode not in {"off", "apply-approved"} and self.approved_map_path is not None:
            raise ValueError("approved_map_path is consumed only by apply-approved")
        if (
            self.mode in {"apply-safe", "apply-approved"}
            and self.known_speakers is not None
            and self.roster_path is None
        ):
            raise ValueError("identity apply with known_speakers requires roster_path")
        if self.source_media_review == "reviewed-report" and self.media_review_report_path is None:
            raise ValueError("reviewed-report requires media_review_report_path")
        if (
            self.source_media_review != "reviewed-report"
            and self.media_review_report_path is not None
        ):
            raise ValueError(
                "media_review_report_path is consumed only by source_media_review=reviewed-report"
            )
        return self


class CleanupConfig(BaseModel):
    mode: Literal["off", "local-llm"] = "off"
    endpoint: str = "http://127.0.0.1:11434/v1"
    model: str | None = None
    scope: Literal["punctuation", "readability"] = "punctuation"
    allow_remote_cleanup: bool = False
    write_diff: bool = False
    max_word_delta_ratio: float = 0.15


class SubtitleExportConfig(BaseModel):
    split_long_cues: bool = True
    max_line_width: int | None = Field(default=45, gt=0)
    max_line_count: int | None = Field(default=2, gt=0)
    speaker_labels: bool = True
    use_mapped_names: bool = True
    highlight_words: bool = False


class PipelineConfig(BaseModel):
    preset: PresetName = "max"
    model_cache: Path = Path("models")
    offline: bool = False
    audio: AudioConfig = Field(default_factory=AudioConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    diarization: DiarizationConfig = Field(default_factory=DiarizationConfig)
    agentic_diarization: AgenticDiarizationConfig = Field(default_factory=AgenticDiarizationConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    qa: QAConfig = Field(default_factory=QAConfig)
    redecode: RedecodeConfig = Field(default_factory=RedecodeConfig)
    speaker_id: SpeakerIDConfig = Field(default_factory=SpeakerIDConfig)
    speaker_identity: IdentityResolverConfig = Field(default_factory=IdentityResolverConfig)
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)
    subtitle_export: SubtitleExportConfig = Field(default_factory=SubtitleExportConfig)


def default_model_cache(cwd: Path | None = None) -> Path:
    value = os.getenv("LTM_MODEL_CACHE")
    if value:
        return Path(value).expanduser()
    return (cwd or Path.cwd()) / "models"


def default_hf_home(cwd: Path | None = None) -> Path:
    return Path(os.getenv("HF_HOME", str((cwd or Path.cwd()) / ".hf-cache"))).expanduser()


def default_hf_hub_cache(cwd: Path | None = None) -> Path:
    return Path(os.getenv("HF_HUB_CACHE", str(default_hf_home(cwd) / "hub"))).expanduser()


def hf_token() -> str | None:
    return os.getenv("LTM_HF_TOKEN") or os.getenv("HF_TOKEN")


def token_present() -> bool:
    return bool(hf_token())


def _env_positive_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise LTMError(f"{name} must be a positive integer when set.") from exc
    if parsed <= 0:
        raise LTMError(f"{name} must be a positive integer when set.")
    return parsed


def _env_non_negative_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise LTMError(f"{name} must be a non-negative number when set.") from exc
    if parsed < 0:
        raise LTMError(f"{name} must be a non-negative number when set.")
    return parsed


def preset_config(preset: PresetName, cwd: Path | None = None) -> PipelineConfig:
    model_cache = default_model_cache(cwd)
    offline = env_flag("LTM_OFFLINE")
    asr_backend_override = os.getenv("LTM_ASR_BACKEND")
    asr_mlx_model_override = os.getenv("LTM_ASR_MLX_MODEL")
    asr_mlx_model_path_override = os.getenv("LTM_ASR_MLX_MODEL_PATH")
    asr_device_override = os.getenv("LTM_ASR_DEVICE") or os.getenv("LTM_DEVICE")
    diarization_device_override = os.getenv("LTM_DIARIZATION_DEVICE") or os.getenv("LTM_DEVICE")
    alignment_device_override = os.getenv("LTM_ALIGNMENT_DEVICE")
    config = PipelineConfig(preset=preset, model_cache=model_cache, offline=offline)
    if preset == "balanced":
        config.asr.model = "turbo"
        config.asr.beam_size = 5
        config.asr.compute_type = "int8_float32"
    elif preset == "fast":
        config.asr.model = "distil-large-v3.5"
        config.asr.beam_size = 1
        config.alignment.backend = "fallback"
    elif preset == "cpu":
        config.asr.model = "turbo"
        config.asr.device = "cpu"
        config.asr.compute_type = "int8"
        config.asr.beam_size = 1
    if asr_backend_override:
        config.asr.backend = _validate_asr_backend(asr_backend_override)
    if asr_mlx_model_override:
        config.asr.mlx_model = asr_mlx_model_override
    if asr_mlx_model_path_override:
        config.asr.mlx_model_path = Path(asr_mlx_model_path_override).expanduser()
    if os.getenv("LTM_ASR_MLX_FP16") is not None:
        config.asr.mlx_fp16 = env_flag("LTM_ASR_MLX_FP16", config.asr.mlx_fp16)
    if os.getenv("LTM_ASR_MLX_VAD_WINDOWED") is not None:
        config.asr.mlx_vad_windowed = env_flag(
            "LTM_ASR_MLX_VAD_WINDOWED", config.asr.mlx_vad_windowed
        )
    mlx_vad_window_seconds = _env_non_negative_float("LTM_ASR_MLX_VAD_WINDOW_SECONDS")
    if mlx_vad_window_seconds is not None:
        if mlx_vad_window_seconds <= 0:
            raise LTMError("LTM_ASR_MLX_VAD_WINDOW_SECONDS must be greater than 0 when set.")
        config.asr.mlx_vad_window_seconds = mlx_vad_window_seconds
    mlx_vad_window_padding_seconds = _env_non_negative_float(
        "LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS"
    )
    if mlx_vad_window_padding_seconds is not None:
        config.asr.mlx_vad_window_padding_seconds = mlx_vad_window_padding_seconds
    if asr_device_override:
        config.asr.device = asr_device_override
    if diarization_device_override:
        config.diarization.device = diarization_device_override
    if alignment_device_override:
        config.alignment.device = alignment_device_override
    asr_cpu_threads = _env_positive_int("LTM_ASR_CPU_THREADS")
    if asr_cpu_threads is not None:
        config.asr.cpu_threads = asr_cpu_threads
    asr_num_workers = _env_positive_int("LTM_ASR_NUM_WORKERS")
    if asr_num_workers is not None:
        config.asr.num_workers = asr_num_workers
    if os.getenv("LTM_ASR_BATCHED") is not None:
        config.asr.batched = env_flag("LTM_ASR_BATCHED")
    asr_batch_size = _env_positive_int("LTM_ASR_BATCH_SIZE")
    if asr_batch_size is not None:
        config.asr.batch_size = asr_batch_size
    return config


def _deep_update(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _normalize_yaml_literals(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            child_key: _normalize_yaml_literals(child_value, child_key)
            for child_key, child_value in value.items()
        }
    if value is False and key in {"backend", "mode", "denoise"}:
        return "off"
    return value


def load_config(
    preset: PresetName = "max",
    config_file: Path | None = None,
    cwd: Path | None = None,
) -> PipelineConfig:
    config = preset_config(preset, cwd=cwd)
    if config_file:
        with config_file.expanduser().open("r", encoding="utf-8") as handle:
            loaded = _normalize_yaml_literals(yaml.safe_load(handle) or {})
        merged = _deep_update(config.model_dump(mode="python"), loaded)
        try:
            config = PipelineConfig.model_validate(merged)
        except ValidationError as exc:
            raise LTMError(f"Invalid config file {config_file}: {exc}") from exc
    if os.getenv("LTM_MODEL_CACHE"):
        config.model_cache = default_model_cache(cwd)
    config.offline = env_flag("LTM_OFFLINE", config.offline)
    if config.qa.targeted_redecode and config.redecode.mode == "off":
        config.redecode.mode = "low-confidence"
    if os.getenv("LTM_ASR_DEVICE") or os.getenv("LTM_DEVICE"):
        config.asr.device = os.environ.get("LTM_ASR_DEVICE", os.environ.get("LTM_DEVICE", "auto"))
    if os.getenv("LTM_ASR_BACKEND"):
        config.asr.backend = _validate_asr_backend(os.environ["LTM_ASR_BACKEND"])
    if os.getenv("LTM_ASR_MLX_MODEL"):
        config.asr.mlx_model = os.environ["LTM_ASR_MLX_MODEL"]
    if os.getenv("LTM_ASR_MLX_MODEL_PATH"):
        config.asr.mlx_model_path = Path(os.environ["LTM_ASR_MLX_MODEL_PATH"]).expanduser()
    if os.getenv("LTM_ASR_MLX_FP16") is not None:
        config.asr.mlx_fp16 = env_flag("LTM_ASR_MLX_FP16", config.asr.mlx_fp16)
    if os.getenv("LTM_ASR_MLX_VAD_WINDOWED") is not None:
        config.asr.mlx_vad_windowed = env_flag(
            "LTM_ASR_MLX_VAD_WINDOWED", config.asr.mlx_vad_windowed
        )
    mlx_vad_window_seconds = _env_non_negative_float("LTM_ASR_MLX_VAD_WINDOW_SECONDS")
    if mlx_vad_window_seconds is not None:
        if mlx_vad_window_seconds <= 0:
            raise LTMError("LTM_ASR_MLX_VAD_WINDOW_SECONDS must be greater than 0 when set.")
        config.asr.mlx_vad_window_seconds = mlx_vad_window_seconds
    mlx_vad_window_padding_seconds = _env_non_negative_float(
        "LTM_ASR_MLX_VAD_WINDOW_PADDING_SECONDS"
    )
    if mlx_vad_window_padding_seconds is not None:
        config.asr.mlx_vad_window_padding_seconds = mlx_vad_window_padding_seconds
    if os.getenv("LTM_DIARIZATION_DEVICE") or os.getenv("LTM_DEVICE"):
        config.diarization.device = os.environ.get(
            "LTM_DIARIZATION_DEVICE",
            os.environ.get("LTM_DEVICE", "auto"),
        )
    if os.getenv("LTM_ALIGNMENT_DEVICE"):
        config.alignment.device = os.environ["LTM_ALIGNMENT_DEVICE"]
    asr_cpu_threads = _env_positive_int("LTM_ASR_CPU_THREADS")
    if asr_cpu_threads is not None:
        config.asr.cpu_threads = asr_cpu_threads
    asr_num_workers = _env_positive_int("LTM_ASR_NUM_WORKERS")
    if asr_num_workers is not None:
        config.asr.num_workers = asr_num_workers
    if os.getenv("LTM_ASR_BATCHED") is not None:
        config.asr.batched = env_flag("LTM_ASR_BATCHED", config.asr.batched)
    asr_batch_size = _env_positive_int("LTM_ASR_BATCH_SIZE")
    if asr_batch_size is not None:
        config.asr.batch_size = asr_batch_size
    if os.getenv("LTM_CLEANUP_ENDPOINT"):
        config.cleanup.endpoint = os.environ["LTM_CLEANUP_ENDPOINT"]
    return config


def _validate_asr_backend(value: str) -> Literal["faster-whisper", "mlx-whisper"]:
    if value not in {"faster-whisper", "mlx-whisper"}:
        raise LTMError("LTM_ASR_BACKEND must be one of: faster-whisper, mlx-whisper.")
    return cast(Literal["faster-whisper", "mlx-whisper"], value)


def model_local_path(entry: ModelEntry, cache_dir: Path) -> Path:
    return cache_dir.expanduser() / entry.directory_name


def model_present(entry: ModelEntry, cache_dir: Path) -> bool:
    path = model_local_path(entry, cache_dir)
    if not path.exists():
        return False
    required_ready = all((path / required).exists() for required in entry.required_files)
    if entry.any_required_files:
        return required_ready and any(
            (path / required).exists() for required in entry.any_required_files
        )
    return required_ready
