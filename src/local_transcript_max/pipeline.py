# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""End-to-end transcription pipeline orchestration."""

from __future__ import annotations

import importlib.metadata
import inspect
import math
import os
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .alignment.base import AlignmentBackend
from .alignment.fallback_word_timestamps import FallbackWordTimestampAlignment
from .alignment.forced_alignment import (
    WhisperXForcedAlignment,
    preflight_forced_alignment,
)
from .asr.base import ASRBackend
from .asr.faster_whisper_backend import (
    FasterWhisperBackend,
    resolve_compute_type,
    resolve_cpu_threads,
    resolve_device,
    resolve_num_workers,
    validate_compute_type,
)
from .asr.mlx_whisper_backend import MlxWhisperBackend, mlx_compute_type, resolve_mlx_model_path
from .audio.inspect import inspect_media
from .audio.preprocess import canonical_audio_decision, prepare_canonical_audio
from .cleanup import run_cleanup
from .config import PipelineConfig
from .diagnostics import (
    alignment_diagnostic_payload,
    base_debug_report,
    runtime_environment,
    write_debug_report,
)
from .diarization.base import DiarizationBackend, diarize_backend
from .diarization.pyannote_backend import PyannoteDiarizationBackend
from .errors import BackendUnavailableError, LTMError
from .export import (
    render_markdown,
    render_rttm,
    render_srt,
    render_txt,
    render_vtt,
    write_json_artifact,
    write_segments_csv,
    write_words_csv,
    write_words_jsonl,
)
from .fusion.diarization_refinement import decision_report, run_agentic_diarization
from .fusion.turn_builder import (
    build_speaker_report,
    build_speaker_turns,
    repair_unknown_fragment_labels,
)
from .fusion.word_speaker_assignment import assign_speakers_to_words
from .models import require_model
from .platform_security import ensure_private_directory
from .qa.heuristics import run_qa
from .qa.redecode import run_targeted_redecode
from .redaction import privacy_safe_text
from .resource_usage import ResourceMonitor
from .runtime_assets import configure_offline_environment, punkt_data_root
from .schemas import (
    AlignedWord,
    ASRResult,
    DiarizationResult,
    QAReport,
    RunMetadata,
    StageTiming,
    Transcript,
    VADRegion,
)
from .speaker_id.identity_io import JobIdentityLock
from .utils import atomic_text_write, sha256_file
from .vad.energy import EnergyVAD

PIPELINE_ARTIFACT_NAMES = frozenset(
    {
        "alignment_report.json",
        "audio_metadata.json",
        "canonical.wav",
        "cleanup_diff.json",
        "cleanup_report.json",
        "debug_report.json",
        "diarization.json",
        "diarization.rttm",
        "diarization_candidates",
        "diarization_decision_report.json",
        "exclusive_diarization.json",
        "qa_report.json",
        "redecode_report.json",
        "run_metadata.json",
        "speaker_cluster_embeddings.json",
        "speaker_embedding_report.json",
        "speaker_map.json",
        "speaker_report.json",
        "transcript.redecode_candidates.json",
        "vad.json",
        "words.csv",
    }
)
PIPELINE_ARTIFACT_PREFIXES = ("speaker_identity_", "transcript.")


def preflight_required_models(config: PipelineConfig) -> None:
    validate_requested_device(config)
    if config.asr.backend == "faster-whisper":
        validate_compute_type(
            resolve_compute_type(config.asr.compute_type, resolve_device(config.asr.device)),
            resolve_device(config.asr.device),
        )
        require_model("asr", config.asr.model, config.model_cache, offline=config.offline)
    elif config.asr.backend == "mlx-whisper":
        resolve_mlx_model_path(config)
    else:
        raise BackendUnavailableError(
            f"Unknown ASR backend '{config.asr.backend}'. Use faster-whisper or mlx-whisper."
        )
    if config.diarization.enabled and config.diarization.backend in {"auto", "pyannote"}:
        require_model(
            "diarization",
            config.diarization.model,
            config.model_cache,
            offline=config.offline,
        )
    if config.alignment.enabled and config.alignment.backend == "forced":
        preflight_forced_alignment(config)
    if config.speaker_id.mode == "auto":
        model_path = require_model(
            "speaker-embedding",
            config.speaker_id.model,
            config.model_cache,
            offline=config.offline,
        )
        try:
            from .speaker_id.cluster_embeddings import _load_classifier, _resolve_device

            device = _resolve_device(config.speaker_id.device)
            _load_classifier(str(model_path), device)
        except Exception as exc:
            raise BackendUnavailableError(f"ECAPA speaker-ID preflight failed: {exc}") from exc


def transcribe_file(
    input_file: Path,
    out_dir: Path,
    config: PipelineConfig,
    *,
    asr_backend: ASRBackend | None = None,
    alignment_backend: AlignmentBackend | None = None,
    diarization_backend: DiarizationBackend | None = None,
    precheck_models: bool = True,
    debug_report_path: Path | None = None,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> Transcript:
    if config.offline:
        configure_offline_environment(nltk_data=punkt_data_root(config.model_cache))
    input_file = input_file.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    canonical_destination = (out_dir / "canonical.wav").resolve()
    if input_file == canonical_destination:
        raise LTMError(
            "Input media resolves to the job's canonical.wav destination; choose a fresh "
            "output directory so the source cannot be replaced."
        )
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing_artifacts = existing_pipeline_artifacts(out_dir)
    if existing_artifacts:
        preview = ", ".join(existing_artifacts[:3])
        suffix = "" if len(existing_artifacts) <= 3 else ", ..."
        raise LTMError(
            "Output directory already contains Auralign pipeline artifacts "
            f"({preview}{suffix}); choose a fresh output directory."
        )
    if any(out_dir.iterdir()):
        raise LTMError(
            "Output directory must be empty before transcription; choose a fresh output "
            "directory."
        )
    ensure_private_directory(out_dir)
    debug_path = debug_report_path or out_dir / "debug_report.json"
    debug_report = base_debug_report(input_file, out_dir, config)
    resource_monitor = ResourceMonitor()
    identity_lock = JobIdentityLock(out_dir)
    try:
        identity_lock.__enter__()
        resource_monitor.start()
        emit_progress(progress, "preflight_start", {"input": str(input_file), "out": str(out_dir)})
        if precheck_models:
            preflight_required_models(config)
        emit_progress(progress, "preflight_done", {})

        started = time.perf_counter()
        emit_progress(progress, "inspect_start", {"path": str(input_file)})
        input_metadata = inspect_media(input_file)
        emit_progress(
            progress,
            "inspect_done",
            {"duration_seconds": input_metadata.duration_seconds, "codec": input_metadata.codec},
        )
        debug_report["input_metadata"] = input_metadata.model_dump(mode="json")
        input_file_hash = sha256_file(input_file)
        emit_progress(progress, "canonical_audio_start", {})
        canonical_path, ffmpeg_command = prepare_canonical_audio(
            input_file,
            out_dir,
            config.audio,
            input_metadata=input_metadata,
        )
        canonical_metadata = inspect_media(canonical_path)
        audio_preprocessing = canonical_audio_decision(
            input_file,
            canonical_path,
            input_metadata,
            config.audio,
            ffmpeg_command,
        )
        emit_progress(
            progress,
            "canonical_audio_done",
            {
                "path": str(canonical_path),
                "duration_seconds": canonical_metadata.duration_seconds,
                "ffmpeg": bool(ffmpeg_command),
                "action": audio_preprocessing["action"],
            },
        )
        write_json_artifact(
            out_dir / "audio_metadata.json",
            {
                "input": input_metadata.model_dump(mode="json"),
                "canonical": canonical_metadata.model_dump(mode="json"),
                "ffmpeg_command": ffmpeg_command,
                "preprocessing": audio_preprocessing,
            },
        )
        debug_report["canonical_wav"] = {
            "path": str(canonical_path),
            "ffmpeg_command": ffmpeg_command,
            "metadata": canonical_metadata.model_dump(mode="json"),
            "preprocessing": audio_preprocessing,
        }

        vad_started = time.perf_counter()
        emit_progress(progress, "vad_start", {})
        vad_result = EnergyVAD().detect_with_metadata(canonical_path)
        vad_regions = list(vad_result.regions)
        vad_timing = StageTiming(name="vad", duration_seconds=time.perf_counter() - vad_started)
        emit_progress(
            progress,
            "vad_done",
            {"duration_seconds": vad_timing.duration_seconds, "regions": len(vad_regions)},
        )
        write_json_artifact(
            out_dir / "vad.json",
            vad_result.to_artifact(),
        )

        asr_backend = asr_backend or select_asr_backend(config)
        emit_progress(progress, "asr_start", {"backend": getattr(asr_backend, "name", "asr")})
        asr_result = transcribe_with_context(asr_backend, canonical_path, config, vad_regions)
        emit_progress(
            progress,
            "asr_done",
            {
                "duration_seconds": asr_result.timing.duration_seconds
                if asr_result.timing
                else None,
                "segments": len(asr_result.segments),
                "language": asr_result.language,
            },
        )
        write_json_artifact(out_dir / "transcript.raw.json", asr_result)

        alignment_started = time.perf_counter()
        emit_progress(progress, "alignment_start", {"backend": config.alignment.backend})
        alignment = align_words(canonical_path, asr_result, config, alignment_backend)
        alignment_timing = StageTiming(
            name="alignment", duration_seconds=time.perf_counter() - alignment_started
        )
        alignment_report = alignment_diagnostic_payload(
            requested_backend=config.alignment.backend,
            backend=alignment.backend,
            device=alignment.device,
            words=alignment.words,
            warnings=alignment.warnings,
            duration_seconds=alignment_timing.duration_seconds,
            metadata=alignment.metadata,
        )
        write_json_artifact(out_dir / "alignment_report.json", alignment_report)
        emit_progress(
            progress,
            "alignment_done",
            {
                "duration_seconds": alignment_timing.duration_seconds,
                "backend": alignment.backend,
                "words": len(alignment.words),
                "warnings": len(alignment.warnings),
            },
        )
        aligned_transcript = Transcript(
            input_path=str(input_file),
            language=asr_result.language,
            segments=asr_result.segments,
            words=alignment.words,
        )
        write_json_artifact(out_dir / "transcript.aligned.json", aligned_transcript)

        diarization_backend = diarization_backend or select_diarization_backend(config)
        emit_progress(
            progress,
            "diarization_start",
            {"backend": getattr(diarization_backend, "name", "diarization")},
        )
        if config.agentic_diarization.enabled:
            diarization_result, diarization_decision = run_agentic_diarization(
                canonical_path,
                config,
                diarization_backend,
                words=alignment.words,
                vad_regions=vad_regions,
                out_dir=out_dir,
                progress=diarization_progress_callback(progress),
            )
        else:
            diarization_result = diarize_backend(
                diarization_backend,
                canonical_path,
                config,
                progress=diarization_progress_callback(progress),
            )
            diarization_decision = decision_report(diarization_result)
        emit_progress(
            progress,
            "diarization_done",
            {
                "duration_seconds": diarization_result.timing.duration_seconds
                if diarization_result.timing
                else None,
                "turns": len(diarization_result.turns),
                "exclusive_turns": len(diarization_result.exclusive_turns),
                "device": diarization_result.metadata.get("device"),
            },
        )
        fusion_turns = (
            diarization_result.exclusive_turns
            if config.diarization.exclusive_diarization and diarization_result.exclusive_turns
            else diarization_result.turns
        )
        assigned_words = assign_speakers_to_words(alignment.words, fusion_turns)
        speaker_turns = repair_unknown_fragment_labels(build_speaker_turns(assigned_words))
        emit_progress(
            progress,
            "fusion_done",
            {"speaker_turns": len(speaker_turns), "words": len(assigned_words)},
        )
        transcript = Transcript(
            input_path=str(input_file),
            language=asr_result.language,
            segments=asr_result.segments,
            words=assigned_words,
            diarization_turns=diarization_result.turns,
            exclusive_diarization_turns=diarization_result.exclusive_turns,
            speaker_turns=speaker_turns,
        )
        write_json_artifact(out_dir / "transcript.diarized.json", transcript)
        write_json_artifact(out_dir / "diarization.json", diarization_result)
        write_json_artifact(
            out_dir / "exclusive_diarization.json",
            {
                "turns": [
                    turn.model_dump(mode="json") for turn in diarization_result.exclusive_turns
                ]
            },
        )

        qa_report = run_qa(transcript) if config.qa.enabled else QAReport()
        speaker_report = build_speaker_report(speaker_turns)
        write_json_artifact(out_dir / "qa_report.json", qa_report)
        write_json_artifact(out_dir / "speaker_report.json", speaker_report)
        if config.agentic_diarization.write_decision_report:
            write_json_artifact(
                out_dir / "diarization_decision_report.json",
                diarization_decision,
            )
        if config.redecode.mode != "off" or config.redecode.write_report:
            emit_progress(progress, "redecode_start", {"mode": config.redecode.mode})
            run_targeted_redecode(
                canonical_path,
                transcript,
                qa_report,
                config,
                asr_backend,
                out_dir,
            )
            emit_progress(progress, "redecode_done", {"mode": config.redecode.mode})

        emit_progress(progress, "export_start", {})
        write_exports(out_dir, transcript, config)
        emit_progress(progress, "export_done", {})
        if config.speaker_id.mode == "auto":
            from .speaker_id.cluster_embeddings import build_cluster_embeddings
            from .speaker_id.enrollment import match_job_speakers

            cluster_result = build_cluster_embeddings(
                canonical_path,
                diarization_result.exclusive_turns,
                cache_dir=config.model_cache,
                device=config.speaker_id.device,
                minimum_segment_seconds=config.speaker_id.minimum_segment_seconds,
                maximum_speech_seconds=config.speaker_id.maximum_speech_seconds,
            )
            write_json_artifact(
                out_dir / "speaker_cluster_embeddings.json", cluster_result.embeddings
            )
            write_json_artifact(
                out_dir / "speaker_embedding_report.json", cluster_result.report
            )
            match_job_speakers(
                out_dir,
                threshold=config.speaker_id.threshold,
                ambiguity_margin=config.speaker_id.ambiguity_margin,
                profile_dir=config.speaker_id.profile_dir,
            )
        if config.cleanup.mode != "off":
            cleanup_report = run_cleanup(transcript, out_dir, config)
            write_json_artifact(out_dir / "cleanup_report.json", cleanup_report)
        total_duration = time.perf_counter() - started
        timings = [vad_timing, alignment_timing]
        if asr_result.timing:
            timings.append(asr_result.timing)
        if diarization_result.timing:
            timings.append(diarization_result.timing)
        resource_usage = resource_monitor.stop()
        asr_runtime = asr_runtime_metadata(config, asr_result, asr_backend)
        diarization_runtime = diarization_runtime_metadata(config, diarization_result)
        if sha256_file(input_file) != input_file_hash:
            raise LTMError(
                "Input media changed while processing; refusing to publish completion metadata."
            )
        run_metadata = RunMetadata(
            package_version=__version__,
            input_file_hash=input_file_hash,
            input_metadata=input_metadata,
            canonical_audio_metadata=canonical_metadata,
            ffmpeg_command=ffmpeg_command,
            models=[
                model for model in (asr_result.model, diarization_result.model) if model is not None
            ],
            asr_backend=getattr(asr_backend, "name", asr_backend.__class__.__name__),
            diarization_backend=getattr(
                diarization_backend, "name", diarization_backend.__class__.__name__
            ),
            alignment_backend=alignment.backend,
            alignment_device=alignment.device,
            cuda_available=cuda_available(),
            dependency_versions=dependency_versions(),
            runtime_environment=runtime_environment(config),
            environment_overrides=environment_overrides(),
            resource_usage=resource_usage,
            audio_preprocessing=audio_preprocessing,
            compute_type=asr_runtime["compute_type"],
            device=asr_runtime["device"],
            asr_cpu_threads=asr_runtime["asr_cpu_threads"],
            asr_num_workers=asr_runtime["asr_num_workers"],
            asr_batched=bool(asr_runtime["asr_batched"]),
            asr_batch_size=asr_runtime["asr_batch_size"],
            diarization_requested_device=diarization_runtime["diarization_requested_device"],
            diarization_resolved_device=diarization_runtime["diarization_resolved_device"],
            diarization_device_fallback=bool(diarization_runtime["diarization_device_fallback"]),
            diarization_warnings=diarization_runtime["diarization_warnings"],
            timings=timings + [StageTiming(name="total", duration_seconds=total_duration)],
            real_time_factor=(
                total_duration / canonical_metadata.duration_seconds
                if canonical_metadata.duration_seconds
                else None
            ),
        )
        write_json_artifact(out_dir / "run_metadata.json", run_metadata)
        if config.speaker_identity.mode != "off":
            emit_progress(
                progress,
                "speaker_identity_start",
                {"mode": config.speaker_identity.mode},
            )
            identity_result = run_identity_resolution(out_dir, config)
            debug_report["speaker_identity"] = {
                "mode": identity_result.report.mode.value,
                "run_id": identity_result.report.run_id,
                "artifact_count": len(identity_result.written_artifacts),
                "validation_valid": identity_result.report.validation_summary.valid,
            }
            emit_progress(
                progress,
                "speaker_identity_done",
                debug_report["speaker_identity"],
            )
        emit_progress(
            progress,
            "complete",
            {
                "duration_seconds": total_duration,
                "real_time_factor": run_metadata.real_time_factor,
                "rss_peak_sampled_bytes": resource_usage.get("rss_peak_sampled_bytes"),
                "rss_end_bytes": resource_usage.get("rss_end_bytes"),
                "out": str(out_dir),
            },
        )
        debug_report["status"] = "complete"
        debug_report["run_metadata"] = run_metadata.model_dump(mode="json")
        debug_report["alignment_warnings"] = alignment.warnings
        debug_report["alignment_report"] = alignment_report
        if debug_report_path:
            write_debug_report(debug_path, debug_report)
        return transcript
    except Exception as exc:
        resource_usage = resource_monitor.stop()
        emit_progress(
            progress,
            "failed",
            {
                "type": exc.__class__.__name__,
                "message": privacy_safe_text(str(exc)),
                "rss_peak_sampled_bytes": resource_usage.get("rss_peak_sampled_bytes"),
            },
        )
        if debug_report_path:
            debug_report["status"] = "failed"
            debug_report["resource_usage"] = resource_usage
            debug_report["exception"] = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "trace": traceback.format_exc(),
            }
            write_debug_report(debug_path, debug_report)
        raise
    finally:
        identity_lock.__exit__(None, None, None)


def existing_pipeline_artifacts(out_dir: Path) -> list[str]:
    """Return existing names that prove ``out_dir`` is already an Auralign job."""

    return sorted(
        child.name
        for child in out_dir.iterdir()
        if child.name in PIPELINE_ARTIFACT_NAMES
        or child.name.startswith(PIPELINE_ARTIFACT_PREFIXES)
    )


def run_identity_resolution(out_dir: Path, config: PipelineConfig) -> Any:
    """Run the opt-in post-processor while the pipeline owns the job lock."""
    from .speaker_id.identity_models import (
        DeliverableFormat,
        ResolverMode,
        ResolverPolicy,
        SourceMediaReviewMode,
        StructuralRepairMode,
    )
    from .speaker_id.identity_resolver import ResolveIdentityOptions, resolve_identities

    identity = config.speaker_identity
    if identity.mode == "off":
        raise ValueError("identity resolver is disabled")
    policy = ResolverPolicy(
        mode=ResolverMode(identity.mode),
        min_apply_confidence=identity.min_apply_confidence,
        profile_threshold=identity.profile_threshold,
        profile_ambiguity_margin=identity.profile_ambiguity_margin,
        max_fragment_duration=identity.max_fragment_duration,
        max_fragment_words=identity.max_fragment_words,
        max_sibling_jobs=identity.max_sibling_jobs,
        unattributed_label=identity.unattributed_label,
        audit_csv=identity.audit_csv,
        deliverable_format=DeliverableFormat(identity.deliverable_format),
        turn_boundary_repair=StructuralRepairMode(identity.turn_boundary_repair),
        source_media_review=SourceMediaReviewMode(identity.source_media_review),
    )
    return resolve_identities(
        out_dir,
        ResolveIdentityOptions(
            policy=policy,
            known_speakers=identity.known_speakers,
            roster_path=identity.roster_path,
            external_transcript=identity.external_transcript,
            approved_map_path=identity.approved_map_path,
            sibling_jobs=tuple(identity.sibling_jobs),
            sibling_root=identity.sibling_root,
            sibling_manifest=identity.sibling_manifest,
            stability_config_path=identity.stability_config_path,
            media_review_report_path=identity.media_review_report_path,
            write_identity_mapped_exports=identity.write_identity_mapped_exports,
            write_media_review_queue=identity.write_media_review_queue,
            job_lock_already_held=True,
        ),
    )


def emit_progress(
    progress: Callable[[str, dict[str, Any]], None] | None,
    event: str,
    payload: dict[str, Any],
) -> None:
    if progress is None:
        return
    try:
        progress(event, payload)
    except Exception:
        return


def diarization_progress_callback(
    progress: Callable[[str, dict[str, Any]], None] | None,
) -> Callable[[dict[str, Any]], None] | None:
    if progress is None:
        return None

    def callback(payload: dict[str, Any]) -> None:
        emit_progress(progress, "pyannote_progress", payload)

    return callback


def write_exports(out_dir: Path, transcript: Transcript, config: PipelineConfig) -> None:
    write_json_artifact(out_dir / "transcript.json", transcript)
    atomic_text_write(out_dir / "transcript.txt", render_txt(transcript))
    atomic_text_write(out_dir / "transcript.md", render_markdown(transcript))
    atomic_text_write(out_dir / "transcript.srt", render_srt(transcript, config.subtitle_export))
    atomic_text_write(out_dir / "transcript.vtt", render_vtt(transcript, config.subtitle_export))
    rttm = render_rttm(transcript)
    atomic_text_write(out_dir / "transcript.rttm", rttm)
    atomic_text_write(out_dir / "diarization.rttm", rttm)
    write_words_jsonl(out_dir / "transcript.jsonl", transcript)
    write_words_jsonl(out_dir / "transcript.words.jsonl", transcript)
    write_words_csv(out_dir / "words.csv", transcript)
    write_segments_csv(out_dir / "transcript.segments.csv", transcript)


def transcribe_with_context(
    asr_backend: ASRBackend,
    audio_path: Path,
    config: PipelineConfig,
    vad_regions: list[VADRegion],
) -> ASRResult:
    signature = inspect.signature(asr_backend.transcribe)
    if "vad_regions" in signature.parameters:
        return asr_backend.transcribe(audio_path, config, vad_regions=vad_regions)
    return asr_backend.transcribe(audio_path, config)


@dataclass(frozen=True)
class AlignmentOutcome:
    words: list[AlignedWord]
    backend: str
    warnings: list[str]
    device: str | None = None
    metadata: dict[str, Any] | None = None


def align_words(
    audio_path: Path,
    asr_result: ASRResult,
    config: PipelineConfig,
    alignment_backend: AlignmentBackend | None = None,
) -> AlignmentOutcome:
    fallback = FallbackWordTimestampAlignment()
    if not config.alignment.enabled or config.alignment.backend in {"off", "whisper", "fallback"}:
        return AlignmentOutcome(
            words=fallback.align(audio_path, asr_result, config),
            backend="whisper-word-timestamps",
            warnings=[],
            device=None,
            metadata={
                "source": "asr_word_timestamps",
                "requested_backend": config.alignment.backend,
            },
        )
    backend = alignment_backend or WhisperXForcedAlignment()
    try:
        words = backend.align(audio_path, asr_result, config)
        validate_forced_alignment_words(words)
        return AlignmentOutcome(
            words=words,
            backend=getattr(backend, "name", "forced"),
            warnings=[],
            device=_optional_metadata_str(getattr(backend, "last_device", None)),
            metadata={
                "language": config.alignment.language or asr_result.language,
                "model": _optional_metadata_str(getattr(backend, "last_model_name", None)),
                "model_path": _optional_metadata_str(getattr(backend, "last_model_path", None)),
            },
        )
    except BackendUnavailableError as exc:
        if config.alignment.backend == "forced":
            raise
        return AlignmentOutcome(
            words=fallback.align(audio_path, asr_result, config),
            backend="whisper-word-timestamps",
            warnings=[privacy_safe_text(str(exc))],
            device=None,
            metadata={
                "source": "asr_word_timestamps",
                "fallback_reason": privacy_safe_text(str(exc)),
                "requested_backend": config.alignment.backend,
            },
        )


def validate_forced_alignment_words(words: list[AlignedWord]) -> None:
    """Reject incomplete or invalid forced-alignment output before publication."""
    if not words:
        raise BackendUnavailableError("Forced alignment returned no word timestamps.")
    previous_start = -math.inf
    previous_end = -math.inf
    for index, word in enumerate(words):
        start = word.start
        end = word.end
        if (
            word.alignment_source != "forced"
            or start is None
            or end is None
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end < start
            or start < previous_start
            or end < previous_end
        ):
            raise BackendUnavailableError(
                f"Forced alignment returned an invalid timestamp at word index {index}."
            )
        previous_start = start
        previous_end = end


class DisabledDiarizationBackend:
    name = "off"

    def diarize(self, audio_path: Path, config: PipelineConfig) -> DiarizationResult:
        return DiarizationResult(
            turns=[],
            raw={"disabled": True},
            metadata={"backend": "off", "overlap_support": False},
        )


def select_asr_backend(config: PipelineConfig) -> ASRBackend:
    if config.asr.backend == "faster-whisper":
        return FasterWhisperBackend()
    if config.asr.backend == "mlx-whisper":
        return MlxWhisperBackend()
    raise BackendUnavailableError(
        f"Unknown ASR backend '{config.asr.backend}'. Use faster-whisper or mlx-whisper."
    )


def asr_runtime_metadata(
    config: PipelineConfig,
    asr_result: ASRResult,
    asr_backend: ASRBackend,
) -> dict[str, Any]:
    backend_name = getattr(asr_backend, "name", asr_backend.__class__.__name__)
    if backend_name == "mlx-whisper" or asr_result.config.get("backend") == "mlx-whisper":
        return {
            "device": "mlx",
            "compute_type": str(asr_result.config.get("compute_type") or mlx_compute_type(config)),
            "asr_cpu_threads": None,
            "asr_num_workers": None,
            "asr_batched": False,
            "asr_batch_size": None,
        }
    asr_device = resolve_device(config.asr.device)
    return {
        "device": asr_device,
        "compute_type": resolve_compute_type(config.asr.compute_type, asr_device),
        "asr_cpu_threads": resolve_cpu_threads(config.asr.cpu_threads, asr_device),
        "asr_num_workers": resolve_num_workers(config.asr.num_workers),
        "asr_batched": config.asr.batched,
        "asr_batch_size": config.asr.batch_size if config.asr.batched else None,
    }


def diarization_runtime_metadata(
    config: PipelineConfig,
    diarization_result: DiarizationResult,
) -> dict[str, Any]:
    metadata = diarization_result.metadata
    requested = _optional_metadata_str(metadata.get("requested_device"))
    if requested is None and config.diarization.enabled and config.diarization.backend != "off":
        requested = config.diarization.device
    resolved = _optional_metadata_str(metadata.get("device"))
    if resolved is None:
        resolved = _optional_metadata_str(metadata.get("resolved_device"))
    if resolved is None and (not config.diarization.enabled or config.diarization.backend == "off"):
        resolved = "off"
    warnings = [str(warning) for warning in diarization_result.warnings]
    fallback = any(warning.startswith("pyannote_device_fallback:") for warning in warnings)
    return {
        "diarization_requested_device": requested,
        "diarization_resolved_device": resolved,
        "diarization_device_fallback": fallback,
        "diarization_warnings": warnings,
    }


def _optional_metadata_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def select_diarization_backend(config: PipelineConfig) -> DiarizationBackend:
    if not config.diarization.enabled or config.diarization.backend == "off":
        return DisabledDiarizationBackend()
    if config.diarization.backend in {"auto", "pyannote"}:
        return PyannoteDiarizationBackend()
    raise BackendUnavailableError(
        f"Unknown diarization backend '{config.diarization.backend}'. Use auto, pyannote, or off."
    )


def dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in (
        "faster-whisper",
        "ctranslate2",
        "mlx-whisper",
        "mlx",
        "pyannote.audio",
        "torch",
        "torchaudio",
        "whisperx",
        "speechbrain",
    ):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def environment_overrides() -> dict[str, str]:
    allowed = (
        "LTM_MODEL_CACHE",
        "LTM_OFFLINE",
        "LTM_DEVICE",
        "LTM_ASR_BACKEND",
        "LTM_ASR_MLX_MODEL",
        "LTM_ASR_MLX_MODEL_PATH",
        "LTM_ASR_MLX_FP16",
        "LTM_ASR_DEVICE",
        "LTM_DIARIZATION_DEVICE",
        "LTM_ALIGNMENT_DEVICE",
        "LTM_ASR_CPU_THREADS",
        "LTM_ASR_NUM_WORKERS",
        "LTM_ASR_BATCHED",
        "LTM_ASR_BATCH_SIZE",
        "HF_HOME",
        "HF_HUB_CACHE",
    )
    return {name: os.environ[name] for name in allowed if name in os.environ}


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def validate_requested_device(config: PipelineConfig) -> None:
    asr_requests_cuda = config.asr.backend == "faster-whisper" and config.asr.device == "cuda"
    alignment_requests_cuda = config.alignment.device == "cuda"
    if (
        not asr_requests_cuda
        and config.diarization.device != "cuda"
        and not alignment_requests_cuda
    ):
        return
    try:
        import torch

        cuda_available = torch.cuda.is_available()
    except Exception:
        cuda_available = False
    if not cuda_available:
        from .errors import LTMError

        raise LTMError(
            "CUDA was requested through config or LTM_DEVICE, but CUDA is not available. "
            "Use --preset cpu, set LTM_DEVICE=cpu, or run in an NVIDIA/CUDA environment."
        )
