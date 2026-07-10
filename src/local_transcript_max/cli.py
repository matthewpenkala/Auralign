# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

"""Command-line interface for Local Transcript Max."""

from __future__ import annotations

import json
import os
import stat
import time
from collections.abc import Callable
from itertools import islice
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import typer

from .config import MODEL_MANIFEST, PipelineConfig, PresetName, default_model_cache, load_config
from .datasets import (
    DEFAULT_DATASET_PROFILE,
    DEFAULT_MAX_LOCAL_GB,
    DEFAULT_MIN_FREE_GB,
    DEFAULT_SAMPLE_MAX_GB,
    DatasetProfile,
    cloud_plan,
    cloud_sync,
    dataset_budget_plan,
    dataset_entries,
    pull_sample_datasets,
    status_for_dataset,
    write_dataset_manifest,
)
from .doctor import format_doctor, run_doctor
from .errors import LTMError
from .models import (
    all_statuses,
    audit_selected_models,
    ensure_pull_preconditions,
    import_mlx_model,
    prune_plan,
    pull_model,
    selected_entries,
    verify_models,
    write_model_audit,
)
from .pipeline import transcribe_file
from .redaction import privacy_safe_text

app = typer.Typer(no_args_is_help=True, help="Local speaker-aware transcription.")
models_app = typer.Typer(no_args_is_help=True, help="Model cache commands.")
datasets_app = typer.Typer(no_args_is_help=True, help="Dataset sample and mirror commands.")
speakers_app = typer.Typer(no_args_is_help=True, help="Speaker enrollment commands.")
app.add_typer(models_app, name="models")
app.add_typer(datasets_app, name="datasets")
app.add_typer(speakers_app, name="speakers")


_CANONICAL_JOB_ARTIFACTS = (
    "canonical.wav",
    "audio_metadata.json",
    "vad.json",
    "transcript.raw.json",
    "transcript.aligned.json",
    "alignment_report.json",
    "transcript.diarized.json",
    "transcript.json",
    "transcript.jsonl",
    "transcript.md",
    "transcript.txt",
    "transcript.srt",
    "transcript.vtt",
    "transcript.rttm",
    "diarization.json",
    "diarization.rttm",
    "exclusive_diarization.json",
    "transcript.words.jsonl",
    "transcript.segments.csv",
    "words.csv",
    "speaker_report.json",
    "qa_report.json",
    "run_metadata.json",
)

_STRICT_VALIDATION_ARTIFACTS: tuple[tuple[str, Literal["file", "directory"]], ...] = (
    ("debug_report.json", "file"),
    ("diarization_decision_report.json", "file"),
    ("diarization_candidates", "directory"),
    ("transcript.redecode_candidates.json", "file"),
    ("redecode_report.json", "file"),
    ("speaker_validation_report.json", "file"),
)

_IDENTITY_JOB_ARTIFACTS = (
    "speaker_identity_report.json",
    "speaker_identity_candidates.json",
    "speaker_identity_evidence.jsonl",
    "speaker_identity_attribution_audit.json",
    "speaker_identity_attribution_audit.csv",
    "speaker_identity_diarization_stability.json",
    "speaker_identity_media_review_queue.json",
    "speaker_identity_media_review_report.json",
    "speaker_identity_approved_map.json",
    "transcript.identity_mapped.json",
    "transcript.identity_mapped.srt",
    "transcript.identity_mapped.md",
    "transcript.identity_mapped.vtt",
    "transcript.identity_mapped.txt",
    "transcript.identity_mapped.segments.csv",
)

_MAX_INSPECT_QA_BYTES = 4 * 1024 * 1024
_MAX_INSPECT_CANDIDATE_ENTRIES = 1_000
_SAFE_QA_OVERALL_VALUES = frozenset({"ok", "review_warnings", "no_deterministic_warnings"})


def _artifact_status(path: Path, *, expected_kind: Literal["file", "directory"] = "file") -> str:
    """Classify an artifact without following symlinks or exposing its path."""
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe_inaccessible"
    if stat.S_ISLNK(mode):
        return "unsafe_symlink"
    if expected_kind == "directory":
        if not stat.S_ISDIR(mode):
            return "unsafe_non_directory"
        return "present" if stat.S_IMODE(mode) == 0o700 else "unsafe_permissions"
    if not stat.S_ISREG(mode):
        return "unsafe_non_regular"
    return "present" if stat.S_IMODE(mode) == 0o600 else "unsafe_permissions"


def _candidate_directory_status(path: Path) -> str:
    """Boundedly validate candidate children without following symlinks."""
    try:
        children = list(islice(path.iterdir(), _MAX_INSPECT_CANDIDATE_ENTRIES + 1))
    except OSError:
        return "unsafe_inaccessible"
    if len(children) > _MAX_INSPECT_CANDIDATE_ENTRIES:
        return "unsafe_entry_limit"
    for child in children:
        if _artifact_status(child) != "present":
            return "unsafe_candidate_contents"
    return "present"


def _read_bounded_regular(path: Path, *, max_bytes: int) -> bytes:
    """Read one regular file through a bounded no-follow descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("artifact is not a regular file")
        if metadata.st_size > max_bytes:
            raise ValueError("artifact exceeds the inspection size limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > max_bytes:
            raise ValueError("artifact exceeds the inspection size limit")
        return payload
    finally:
        os.close(descriptor)


def _config(preset: str, config_file: Path | None = None) -> PipelineConfig:
    if preset not in {"max", "balanced", "fast", "cpu"}:
        raise typer.BadParameter("preset must be one of: max, balanced, fast, cpu")
    return load_config(preset=cast(PresetName, preset), config_file=config_file)


def _emit_identity_failure(
    *,
    code: str,
    message: str,
    remediation: str,
    mode: str,
    machine_readable: bool,
) -> None:
    """Render a resolver failure without echoing paths or private inputs."""
    if machine_readable:
        from .speaker_id.identity_models import MachineReadableError

        error = MachineReadableError(
            code=code,
            message=message,
            remediation=remediation,
            safe_context={"mode": mode},
        )
        typer.echo(
            json.dumps(error.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            err=True,
        )
        return
    typer.echo(f"Identity resolution failed [{code}]: {message}", err=True)
    typer.echo(f"Remediation: {remediation}", err=True)


@app.command()
def doctor(
    preset: Annotated[str, typer.Option(help="Config preset to inspect.")] = "max",
    config_file: Annotated[Path | None, typer.Option("--config", exists=True)] = None,
) -> None:
    """Check local dependencies, model cache status, and offline readiness."""
    try:
        config = _config(preset, config_file)
        report = run_doctor(config)
        typer.echo(format_doctor(report))
        if not report.ok:
            raise typer.Exit(1)
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@app.command()
def transcribe(
    ctx: typer.Context,
    input_file: Annotated[Path, typer.Argument(exists=True, readable=True)],
    preset: Annotated[str, typer.Option(help="max, balanced, fast, or cpu")] = "max",
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("outputs"),
    config_file: Annotated[Path | None, typer.Option("--config", exists=True)] = None,
    language: Annotated[str | None, typer.Option("--language")] = None,
    asr_backend: Annotated[
        str,
        typer.Option("--asr-backend", help="faster-whisper or mlx-whisper"),
    ] = "faster-whisper",
    asr_mlx_model: Annotated[str | None, typer.Option("--asr-mlx-model")] = None,
    asr_mlx_model_path: Annotated[Path | None, typer.Option("--asr-mlx-model-path")] = None,
    asr_mlx_fp16: Annotated[
        bool,
        typer.Option("--asr-mlx-fp16/--no-asr-mlx-fp16"),
    ] = True,
    asr_mlx_vad_windowed: Annotated[
        bool,
        typer.Option(
            "--asr-mlx-vad-windowed/--no-asr-mlx-vad-windowed",
            help="Experimental: transcribe MLX ASR in local VAD windows.",
        ),
    ] = False,
    asr_mlx_vad_window_seconds: Annotated[
        float | None,
        typer.Option("--asr-mlx-vad-window-seconds", min=0.001),
    ] = None,
    asr_mlx_vad_window_padding_seconds: Annotated[
        float | None,
        typer.Option("--asr-mlx-vad-window-padding-seconds", min=0.0),
    ] = None,
    num_speakers: Annotated[int | None, typer.Option("--num-speakers")] = None,
    min_speakers: Annotated[int | None, typer.Option("--min-speakers")] = None,
    max_speakers: Annotated[int | None, typer.Option("--max-speakers")] = None,
    diarization_backend: Annotated[
        str,
        typer.Option("--diarization-backend", help="auto, pyannote, or off"),
    ] = "pyannote",
    diarization_device: Annotated[
        str | None,
        typer.Option("--diarization-device", help="auto, cpu, cuda, mps, or backend-specific."),
    ] = None,
    preserve_overlap: Annotated[
        bool,
        typer.Option("--preserve-overlap/--no-preserve-overlap"),
    ] = True,
    exclusive_diarization: Annotated[
        bool,
        typer.Option("--exclusive-diarization/--no-exclusive-diarization"),
    ] = True,
    agentic_diarization: Annotated[bool, typer.Option("--agentic-diarization")] = False,
    diarization_candidates: Annotated[
        str,
        typer.Option("--diarization-candidates", help="auto or an integer candidate count"),
    ] = "auto",
    diarization_candidate_range: Annotated[
        str,
        typer.Option("--diarization-candidate-range", help="Inclusive range like 2:8"),
    ] = "2:8",
    known_speakers: Annotated[int | None, typer.Option("--known-speakers")] = None,
    write_diarization_decision_report: Annotated[
        bool,
        typer.Option("--write-diarization-decision-report/--no-write-diarization-decision-report"),
    ] = True,
    forced_alignment: Annotated[
        str,
        typer.Option("--forced-alignment", help="auto, on, off, or whisper"),
    ] = "auto",
    alignment_language: Annotated[str | None, typer.Option("--alignment-language")] = None,
    alignment_model: Annotated[str | None, typer.Option("--alignment-model")] = None,
    alignment_device: Annotated[
        str | None,
        typer.Option("--alignment-device", help="auto, cpu, cuda, or experimental mps."),
    ] = None,
    no_alignment: Annotated[bool, typer.Option("--no-alignment")] = False,
    word_timestamps: Annotated[bool, typer.Option("--word-timestamps/--no-word-timestamps")] = True,
    initial_prompt: Annotated[Path | None, typer.Option("--initial-prompt", exists=True)] = None,
    normalize_loudness: Annotated[bool, typer.Option("--normalize-loudness")] = False,
    denoise: Annotated[str, typer.Option("--denoise", help="off, mild, or strong")] = "off",
    targeted_redecode: Annotated[
        str,
        typer.Option("--targeted-redecode", help="off, low-confidence, hallucination, all-suspect"),
    ] = "off",
    redecode_max_spans: Annotated[int, typer.Option("--redecode-max-spans", min=1)] = 5,
    redecode_pad_seconds: Annotated[float, typer.Option("--redecode-pad-seconds", min=0.0)] = 1.0,
    redecode_beam_size: Annotated[int, typer.Option("--redecode-beam-size", min=1)] = 5,
    redecode_temperature: Annotated[
        float,
        typer.Option("--redecode-temperature", min=0.0),
    ] = 0.0,
    write_redecode_report: Annotated[bool, typer.Option("--write-redecode-report")] = False,
    speaker_id: Annotated[str, typer.Option("--speaker-id", help="off or auto")] = "off",
    speaker_identity_resolver: Annotated[
        str,
        typer.Option(
            "--speaker-identity-resolver",
            help="off, review, apply-safe, or apply-approved; derived artifacts only.",
        ),
    ] = "off",
    speaker_roster: Annotated[
        Path | None,
        typer.Option("--speaker-roster", help="Strict local identity roster JSON."),
    ] = None,
    speaker_identity_approved_map: Annotated[
        Path | None,
        typer.Option(
            "--speaker-identity-approved-map",
            help="Current-job approved map; used only by apply-approved.",
        ),
    ] = None,
    speaker_identity_external_transcript: Annotated[
        Path | None,
        typer.Option(
            "--speaker-identity-external-transcript",
            help="Read-only external transcript for drift comparison.",
        ),
    ] = None,
    write_identity_mapped_exports: Annotated[
        bool,
        typer.Option(
            "--write-identity-mapped-exports",
            help="Write derived identity-mapped exports; never overwrite canonical exports.",
        ),
    ] = False,
    identity_deliverable_format: Annotated[
        str,
        typer.Option(
            "--identity-deliverable-format",
            help="srt for the primary deliverable, or all for optional sidecars.",
        ),
    ] = "srt",
    cleanup: Annotated[str, typer.Option("--cleanup", help="off or local-llm")] = "off",
    cleanup_endpoint: Annotated[
        str,
        typer.Option("--cleanup-endpoint"),
    ] = "http://127.0.0.1:11434/v1",
    cleanup_model: Annotated[str | None, typer.Option("--cleanup-model")] = None,
    cleanup_scope: Annotated[
        str,
        typer.Option("--cleanup-scope", help="punctuation or readability"),
    ] = "punctuation",
    allow_remote_cleanup: Annotated[bool, typer.Option("--allow-remote-cleanup")] = False,
    write_cleanup_diff: Annotated[bool, typer.Option("--write-cleanup-diff")] = False,
    debug_report: Annotated[bool, typer.Option("--debug-report")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Print stage progress.")] = False,
    infer_speakers: Annotated[
        bool,
        typer.Option("--infer-speakers", help="Infer N-way speaker count from filename."),
    ] = False,
    asr_cpu_threads: Annotated[
        int | None,
        typer.Option(
            "--asr-cpu-threads",
            min=1,
            help="Explicit faster-whisper CPU threads; unset uses library default behavior.",
        ),
    ] = None,
    asr_num_workers: Annotated[
        int | None,
        typer.Option("--asr-num-workers", min=1, help="faster-whisper worker count."),
    ] = None,
    asr_batched: Annotated[
        bool,
        typer.Option(
            "--asr-batched/--no-asr-batched",
            help="Use faster-whisper BatchedInferencePipeline for ASR.",
        ),
    ] = False,
    asr_batch_size: Annotated[
        int | None,
        typer.Option("--asr-batch-size", min=1, help="Batch size for --asr-batched."),
    ] = None,
) -> None:
    """Transcribe one local audio/video file into reproducible artifacts."""
    try:
        config = _config(preset, config_file)

        def use_cli_value(name: str) -> bool:
            source = ctx.get_parameter_source(name)
            return config_file is None or getattr(source, "name", None) == "COMMANDLINE"

        def use_cli_default_or_explicit(name: str, env_names: tuple[str, ...]) -> bool:
            source = ctx.get_parameter_source(name)
            if getattr(source, "name", None) == "COMMANDLINE":
                return True
            return config_file is None and not any(name in os.environ for name in env_names)

        if use_cli_value("language"):
            config.asr.language = language
        if asr_backend not in {"faster-whisper", "mlx-whisper"}:
            raise typer.BadParameter("asr-backend must be one of: faster-whisper, mlx-whisper")
        if use_cli_default_or_explicit("asr_backend", ("LTM_ASR_BACKEND",)):
            config.asr.backend = cast(Literal["faster-whisper", "mlx-whisper"], asr_backend)
        if use_cli_value("asr_mlx_model") and asr_mlx_model:
            config.asr.mlx_model = asr_mlx_model
        if use_cli_value("asr_mlx_model_path") and asr_mlx_model_path is not None:
            config.asr.mlx_model_path = asr_mlx_model_path
        if use_cli_default_or_explicit("asr_mlx_fp16", ("LTM_ASR_MLX_FP16",)):
            config.asr.mlx_fp16 = asr_mlx_fp16
        if use_cli_default_or_explicit("asr_mlx_vad_windowed", ("LTM_ASR_MLX_VAD_WINDOWED",)):
            config.asr.mlx_vad_windowed = asr_mlx_vad_windowed
        if asr_mlx_vad_window_seconds is not None:
            config.asr.mlx_vad_window_seconds = asr_mlx_vad_window_seconds
        if asr_mlx_vad_window_padding_seconds is not None:
            config.asr.mlx_vad_window_padding_seconds = asr_mlx_vad_window_padding_seconds
        if use_cli_value("word_timestamps"):
            config.asr.word_timestamps = word_timestamps
        if use_cli_value("initial_prompt"):
            config.asr.glossary_file = initial_prompt
        if asr_cpu_threads is not None:
            config.asr.cpu_threads = asr_cpu_threads
        if asr_num_workers is not None:
            config.asr.num_workers = asr_num_workers
        if use_cli_value("asr_batched"):
            config.asr.batched = asr_batched
        if asr_batch_size is not None:
            config.asr.batch_size = asr_batch_size
        if use_cli_value("num_speakers"):
            config.diarization.num_speakers = num_speakers
        if use_cli_value("min_speakers"):
            config.diarization.min_speakers = min_speakers
        if use_cli_value("max_speakers"):
            config.diarization.max_speakers = max_speakers
        if known_speakers is not None:
            config.diarization.num_speakers = known_speakers
            config.agentic_diarization.known_speakers = known_speakers
            config.speaker_identity.known_speakers = known_speakers
        if (
            infer_speakers
            and config.diarization.num_speakers is None
            and config.diarization.min_speakers is None
            and config.diarization.max_speakers is None
        ):
            from .benchmark.corpus import infer_speaker_count

            inferred = infer_speaker_count(input_file)
            config.diarization.num_speakers = inferred
            config.agentic_diarization.known_speakers = inferred
        if diarization_backend not in {"auto", "pyannote", "off"}:
            raise typer.BadParameter("diarization-backend must be one of: auto, pyannote, off")
        if use_cli_value("diarization_backend"):
            config.diarization.backend = cast(
                Literal["auto", "pyannote", "off"], diarization_backend
            )
            config.diarization.enabled = diarization_backend != "off"
        if use_cli_value("diarization_device") and diarization_device is not None:
            config.diarization.device = diarization_device
        if use_cli_value("preserve_overlap"):
            config.diarization.preserve_overlap = preserve_overlap
        if use_cli_value("exclusive_diarization"):
            config.diarization.exclusive_diarization = exclusive_diarization
        if use_cli_value("agentic_diarization"):
            config.agentic_diarization.enabled = agentic_diarization
        if use_cli_value("diarization_candidate_range"):
            config.agentic_diarization.candidate_range = diarization_candidate_range
        if use_cli_value("write_diarization_decision_report"):
            config.agentic_diarization.write_decision_report = write_diarization_decision_report
        if use_cli_value("diarization_candidates") and diarization_candidates != "auto":
            try:
                config.agentic_diarization.candidates = int(diarization_candidates)
            except ValueError as exc:
                raise typer.BadParameter(
                    "diarization-candidates must be auto or an integer"
                ) from exc
        if forced_alignment not in {"auto", "on", "off", "whisper"}:
            raise typer.BadParameter("forced-alignment must be one of: auto, on, off, whisper")
        if use_cli_value("forced_alignment"):
            config.alignment.backend = {
                "on": "forced",
                "off": "off",
                "whisper": "whisper",
            }.get(forced_alignment, forced_alignment)
            config.alignment.enabled = config.alignment.backend != "off"
        if use_cli_value("no_alignment") and no_alignment:
            config.alignment.enabled = False
        if use_cli_value("alignment_language"):
            config.alignment.language = alignment_language
        if use_cli_value("alignment_model"):
            config.alignment.model = alignment_model
        if use_cli_value("alignment_device") and alignment_device is not None:
            config.alignment.device = alignment_device
        if use_cli_value("normalize_loudness"):
            config.audio.normalize_loudness = normalize_loudness
        if denoise not in {"off", "mild", "strong"}:
            raise typer.BadParameter("denoise must be one of: off, mild, strong")
        if use_cli_value("denoise"):
            config.audio.denoise = cast(Literal["off", "mild", "strong"], denoise)
        if targeted_redecode not in {"off", "low-confidence", "hallucination", "all-suspect"}:
            raise typer.BadParameter(
                "targeted-redecode must be one of: off, low-confidence, hallucination, all-suspect"
            )
        if use_cli_value("targeted_redecode"):
            config.redecode.mode = cast(
                Literal["off", "low-confidence", "hallucination", "all-suspect"],
                targeted_redecode,
            )
        if use_cli_value("redecode_max_spans"):
            config.redecode.max_spans = redecode_max_spans
        if use_cli_value("redecode_pad_seconds"):
            config.redecode.pad_seconds = redecode_pad_seconds
        if use_cli_value("redecode_beam_size"):
            config.redecode.beam_size = redecode_beam_size
        if use_cli_value("redecode_temperature"):
            config.redecode.temperature = redecode_temperature
        if use_cli_value("write_redecode_report"):
            config.redecode.write_report = write_redecode_report
        if speaker_id not in {"off", "auto"}:
            raise typer.BadParameter("speaker-id must be off or auto")
        if use_cli_value("speaker_id"):
            config.speaker_id.mode = cast(Literal["off", "auto"], speaker_id)
        if speaker_identity_resolver not in {
            "off",
            "review",
            "apply-safe",
            "apply-approved",
        }:
            raise typer.BadParameter(
                "speaker-identity-resolver must be one of: off, review, apply-safe, apply-approved"
            )
        if identity_deliverable_format not in {"srt", "all"}:
            raise typer.BadParameter("identity-deliverable-format must be one of: srt, all")
        if use_cli_value("speaker_identity_resolver"):
            config.speaker_identity.mode = cast(
                Literal["off", "review", "apply-safe", "apply-approved"],
                speaker_identity_resolver,
            )
        if use_cli_value("speaker_roster") and speaker_roster is not None:
            config.speaker_identity.roster_path = speaker_roster
        if (
            use_cli_value("speaker_identity_approved_map")
            and speaker_identity_approved_map is not None
        ):
            config.speaker_identity.approved_map_path = speaker_identity_approved_map
        if (
            use_cli_value("speaker_identity_external_transcript")
            and speaker_identity_external_transcript is not None
        ):
            config.speaker_identity.external_transcript = speaker_identity_external_transcript
        if use_cli_value("write_identity_mapped_exports"):
            config.speaker_identity.write_identity_mapped_exports = write_identity_mapped_exports
        if use_cli_value("identity_deliverable_format"):
            config.speaker_identity.deliverable_format = cast(
                Literal["srt", "all"], identity_deliverable_format
            )
        if (
            config.speaker_identity.mode == "apply-approved"
            and config.speaker_identity.approved_map_path is None
        ):
            raise typer.BadParameter("speaker-identity-approved-map is required for apply-approved")
        if (
            config.speaker_identity.mode in {"apply-safe", "apply-approved"}
            and config.speaker_identity.known_speakers is not None
            and config.speaker_identity.roster_path is None
        ):
            raise typer.BadParameter(
                "speaker-roster is required for identity apply with --known-speakers"
            )
        if cleanup not in {"off", "local-llm"}:
            raise typer.BadParameter("cleanup must be off or local-llm")
        if cleanup_scope not in {"punctuation", "readability"}:
            raise typer.BadParameter("cleanup-scope must be punctuation or readability")
        if use_cli_value("cleanup"):
            config.cleanup.mode = cast(Literal["off", "local-llm"], cleanup)
        if use_cli_value("cleanup_endpoint"):
            config.cleanup.endpoint = cleanup_endpoint
        if use_cli_value("cleanup_model"):
            config.cleanup.model = cleanup_model
        if use_cli_value("cleanup_scope"):
            config.cleanup.scope = cast(Literal["punctuation", "readability"], cleanup_scope)
        if use_cli_value("allow_remote_cleanup"):
            config.cleanup.allow_remote_cleanup = allow_remote_cleanup
        if use_cli_value("write_cleanup_diff"):
            config.cleanup.write_diff = write_cleanup_diff
        debug_path = out / "debug_report.json" if debug_report else None
        if verbose:
            transcript = transcribe_file(
                input_file,
                out,
                config,
                debug_report_path=debug_path,
                progress=progress_printer(),
            )
        else:
            transcript = transcribe_file(
                input_file,
                out,
                config,
                debug_report_path=debug_path,
            )
        typer.echo(f"Wrote transcript with {len(transcript.speaker_turns)} speaker turns to {out}")
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:
        typer.echo(
            "Transcription failed unexpectedly: "
            f"{exc.__class__.__name__}: {privacy_safe_text(str(exc))}. "
            "Re-run with --debug-report for a redacted trace.",
            err=True,
        )
        raise typer.Exit(1) from exc


@models_app.command("list")
def models_list(
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Model cache directory.")] = Path(
        "models"
    ),
) -> None:
    """List known models and local cache readiness."""
    rows = []
    for status in all_statuses(cache_dir):
        rows.append(
            (
                status.entry.role,
                status.entry.key,
                status.entry.model_id,
                "yes" if status.present else "no",
                str(status.path),
            )
        )
    typer.echo(
        "role\tkey\tmodel_id\tpresent\tpath\trevision\tlicense\tcompliance_review\t"
        "redistribution"
    )
    for row in rows:
        entry = MODEL_MANIFEST[row[1]]
        typer.echo(
            "\t".join(
                (
                    *row,
                    entry.revision or "unpinned",
                    entry.license_expression,
                    entry.review_status,
                    entry.redistribution_status,
                )
            )
        )


@models_app.command("audit")
def models_audit(
    preset: Annotated[str, typer.Option(help="Config preset to audit.")] = "max",
    config_file: Annotated[Path | None, typer.Option("--config", exists=True)] = None,
) -> None:
    """Audit configured ASR and diarization model identity and cache readiness."""
    try:
        config = _config(preset, config_file)
        alignment_model = None
        if config.alignment.enabled and config.alignment.backend not in {
            "off",
            "whisper",
            "fallback",
        }:
            alignment_model = config.alignment.model or "en"
        if config.asr.backend == "mlx-whisper":
            from .asr.mlx_whisper_backend import mlx_model_status

            statuses = [mlx_model_status(config)]
            statuses.extend(
                audit_selected_models(
                    config.model_cache,
                    asr=config.asr.model,
                    diarization=config.diarization.model if config.diarization.enabled else None,
                    alignment=alignment_model,
                )[1:]
            )
        else:
            statuses = audit_selected_models(
                config.model_cache,
                asr=config.asr.model,
                diarization=config.diarization.model if config.diarization.enabled else None,
                alignment=alignment_model,
            )
        typer.echo(
            "role\tkey\tmodel_id\tlocal_path\texists\tcomplete\toffline_usable\t"
            "gated\ttoken_required\ttoken_present\tproblem\trevision\tlicense\t"
            "compliance_review\tredistribution"
        )
        for status in statuses:
            typer.echo(
                "\t".join(
                    [
                        status.entry.role,
                        status.entry.key,
                        status.entry.model_id,
                        str(status.path),
                        _yes(status.exists),
                        _yes(status.present),
                        _yes(status.offline_usable),
                        _yes(status.entry.gated),
                        _yes(status.token_required),
                        _yes(status.token_present),
                        status.problem_summary(config.model_cache),
                        status.entry.revision or "unpinned",
                        status.entry.license_expression,
                        status.entry.review_status,
                        status.entry.redistribution_status,
                    ]
                )
            )
        audit_path = write_model_audit(config.model_cache)
        typer.echo(f"wrote {audit_path}")
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@models_app.command("import-mlx")
def models_import_mlx(
    source_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, readable=True),
    ],
    model: Annotated[str, typer.Option("--model")] = "mlx-large-v3-turbo",
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Model cache directory.")] = Path(
        "models"
    ),
    symlink: Annotated[
        bool,
        typer.Option("--symlink/--copy", help="Symlink instead of copying the local MLX model."),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing cache entry for this MLX model."),
    ] = False,
) -> None:
    """Import an existing converted MLX Whisper directory into the local cache."""
    try:
        status = import_mlx_model(
            source_dir,
            cache_dir,
            model=model,
            symlink=symlink,
            overwrite=overwrite,
        )
        mode = "symlinked" if symlink else "copied"
        typer.echo(f"{mode} {status.entry.role}:{status.entry.key} to {status.path}")
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@models_app.command("pull")
def models_pull(
    all_models: Annotated[bool, typer.Option("--all", help="Pull every manifest entry.")] = False,
    include_optional: Annotated[
        bool,
        typer.Option(
            "--include-optional",
            help="Include optional alignment and speaker-ID assets.",
        ),
    ] = False,
    asr: Annotated[str | None, typer.Option("--asr")] = None,
    diarization: Annotated[str | None, typer.Option("--diarization")] = None,
    alignment: Annotated[str | None, typer.Option("--alignment")] = None,
    speaker_embedding: Annotated[
        str | None,
        typer.Option("--speaker-id", "--speaker-embedding"),
    ] = None,
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Model cache directory.")] = Path(
        "models"
    ),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be pulled.")] = False,
) -> None:
    """Explicitly download selected model assets into the local cache."""
    try:
        entries = selected_entries(
            all_models=all_models,
            include_optional=include_optional,
            asr=asr,
            diarization=diarization,
            alignment=alignment,
            speaker_embedding=speaker_embedding,
        )
        if not entries:
            typer.echo(
                "No models selected. Example: ltm models pull --asr large-v3 --cache-dir models/"
            )
            raise typer.Exit(1)
        if dry_run:
            for entry in entries:
                typer.echo(
                    f"would pull {entry.role}:{entry.key} -> {entry.model_id} "
                    f"revision={entry.revision} license={entry.license_expression} "
                    f"compliance={entry.review_status}"
                )
            return
        ensure_pull_preconditions(entries, cache_dir)
        for entry in entries:
            path = pull_model(entry, cache_dir)
            typer.echo(f"pulled {entry.role}:{entry.key} to {path}")
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@models_app.command("verify")
def models_verify(
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Model cache directory.")] = Path(
        "models"
    ),
    include_optional: Annotated[
        bool,
        typer.Option("--include-optional", help="Verify optional alignment and speaker-ID assets."),
    ] = False,
) -> None:
    """Verify that selected model cache entries are complete and offline-usable."""
    try:
        write_model_audit(cache_dir, include_optional=True)
        statuses = verify_models(cache_dir, include_optional=include_optional)
        typer.echo(f"verified {len(statuses)} model cache entries")
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@models_app.command("prune")
def models_prune(
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Model cache directory.")] = Path(
        "models"
    ),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only print candidates.")] = False,
) -> None:
    """Identify cache directories that are not part of the model manifest."""
    plan = prune_plan(cache_dir)
    candidates = plan["candidates"]
    if dry_run:
        typer.echo(f"dry_run=true candidates={len(candidates)}")
        for candidate in candidates:
            typer.echo(f"{candidate['path']}\t{candidate['reason']}")
        return
    typer.echo("Prune is intentionally conservative. Re-run with --dry-run, then remove manually.")
    raise typer.Exit(1)


@datasets_app.command("list")
def datasets_list(
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Dataset cache directory.")] = Path(
        "datasets"
    ),
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_DATASET_PROFILE,
) -> None:
    """List known datasets and local/cloud policy."""
    try:
        dataset_profile = _dataset_profile(profile)
        typer.echo(
            "key\trepo_id\tclassification\tapprox_size_gb\tlocal_status\tpath\t"
            "revision\tlicense\tacquisition_approved\tmirror_approved\tcompliance_review"
        )
        for entry in dataset_entries(dataset_profile):
            status = status_for_dataset(entry, cache_dir)
            typer.echo(
                "\t".join(
                    [
                        entry.key,
                        entry.repo_id,
                        entry.classification,
                        f"{entry.approx_size_gb:.2f}",
                        status.local_status,
                        str(status.path),
                        entry.revision,
                        entry.license_expression,
                        _yes(entry.acquisition_approved),
                        _yes(entry.mirror_approved),
                        entry.review_status,
                    ]
                )
            )
        manifest = write_dataset_manifest(
            cache_dir,
            profile=dataset_profile,
            remote_configured=bool(os.getenv("LTM_DATASET_MIRROR_REMOTE")),
        )
        typer.echo(f"wrote {manifest}")
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@datasets_app.command("plan")
def datasets_plan(
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Dataset cache directory.")] = Path(
        "datasets"
    ),
    model_cache_dir: Annotated[
        Path | None,
        typer.Option("--model-cache-dir", help="Model cache directory for space checks."),
    ] = None,
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_DATASET_PROFILE,
    max_local_gb: Annotated[
        float,
        typer.Option("--max-local-gb", help="Maximum projected local bootstrap footprint."),
    ] = DEFAULT_MAX_LOCAL_GB,
    min_free_gb: Annotated[
        float,
        typer.Option("--min-free-gb", help="Minimum free space to preserve after bootstrap."),
    ] = DEFAULT_MIN_FREE_GB,
    allow_over_budget: Annotated[
        bool,
        typer.Option("--allow-over-budget", help="Report but do not fail budget blockers."),
    ] = False,
) -> None:
    """Plan local sample hydration and cloud-only large datasets against a disk budget."""
    try:
        dataset_profile = _dataset_profile(profile)
        plan = dataset_budget_plan(
            cache_dir,
            model_cache_dir=model_cache_dir or default_model_cache(),
            profile=dataset_profile,
            max_local_gb=max_local_gb,
            min_free_gb=min_free_gb,
            allow_over_budget=allow_over_budget or os.getenv("LTM_ALLOW_OVER_BUDGET") == "1",
        )
        typer.echo(json.dumps(plan, indent=2, sort_keys=True))
        if not plan["ok"]:
            raise typer.Exit(1)
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@datasets_app.command("pull-samples")
def datasets_pull_samples(
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Dataset cache directory.")] = Path(
        "datasets"
    ),
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_DATASET_PROFILE,
    max_sample_gb: Annotated[
        float,
        typer.Option("--max-sample-gb", help="Maximum size for any sample dataset pull."),
    ] = DEFAULT_SAMPLE_MAX_GB,
) -> None:
    """Request reviewed sample assets; fail closed when acquisition is blocked."""
    try:
        result = pull_sample_datasets(
            cache_dir,
            profile=_dataset_profile(profile),
            max_sample_gb=max_sample_gb,
        )
        for row in result["results"]:
            if row["status"] == "fetched":
                typer.echo(f"fetched {row['repo_id']} -> {row['local_path']}")
            elif row["status"] == "blocked":
                typer.echo(f"blocked {row['repo_id']}: {row['reason']}")
            else:
                typer.echo(f"skipped {row['repo_id']}: {row['reason']}")
        typer.echo(f"wrote {result['manifest_path']}")
        if any(row["status"] == "blocked" for row in result["results"]):
            raise typer.Exit(1)
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@datasets_app.command("cloud-plan")
def datasets_cloud_plan(
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Dataset cache directory.")] = Path(
        "datasets"
    ),
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_DATASET_PROFILE,
    remote: Annotated[str | None, typer.Option("--remote")] = None,
) -> None:
    """Print rclone-compatible cloud mirror commands for large public datasets."""
    try:
        plan = cloud_plan(cache_dir, profile=_dataset_profile(profile), remote=remote)
        typer.echo(json.dumps(plan, indent=2, sort_keys=True))
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@datasets_app.command("cloud-sync")
def datasets_cloud_sync(
    cache_dir: Annotated[Path, typer.Option("--cache-dir", help="Dataset cache directory.")] = Path(
        "datasets"
    ),
    profile: Annotated[str, typer.Option("--profile")] = DEFAULT_DATASET_PROFILE,
    remote: Annotated[str | None, typer.Option("--remote")] = None,
    yes_i_understand_bulk_transfer: Annotated[
        bool,
        typer.Option(
            "--yes-i-understand-bulk-transfer",
            help="Required before any large rclone dataset mirror sync.",
        ),
    ] = False,
) -> None:
    """Sync already-local large dataset mirrors to object/cloud storage."""
    try:
        result = cloud_sync(
            cache_dir,
            profile=_dataset_profile(profile),
            remote=remote,
            yes_i_understand_bulk_transfer=yes_i_understand_bulk_transfer,
        )
        typer.echo(json.dumps(result, indent=2, sort_keys=True))
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@speakers_app.command("enroll")
def speakers_enroll(
    name: Annotated[str, typer.Option("--name")],
    audio: Annotated[Path | None, typer.Option("--audio", exists=True, readable=True)] = None,
    audio_dir: Annotated[
        Path | None,
        typer.Option("--audio-dir", exists=True, file_okay=False),
    ] = None,
    allow_fallback: Annotated[
        bool,
        typer.Option("--allow-fallback-embedding", help="Use deterministic test fallback."),
    ] = False,
) -> None:
    """Enroll a local speaker profile."""
    from .speaker_id.enrollment import enroll_speaker

    enrollment = enroll_speaker(
        name=name,
        audio_path=audio,
        audio_dir=audio_dir,
        allow_fallback=allow_fallback,
    )
    typer.echo(f"enrolled {enrollment.name}: {enrollment.embedding_path}")


@speakers_app.command("list")
def speakers_list() -> None:
    """List local speaker profiles."""
    from .speaker_id.enrollment import list_enrollments

    for enrollment in list_enrollments():
        typer.echo(f"{enrollment.name}\t{enrollment.embedding_path}")


@speakers_app.command("inspect")
def speakers_inspect(name: str) -> None:
    """Inspect one local speaker profile."""
    from .speaker_id.enrollment import inspect_speaker

    try:
        enrollment = inspect_speaker(name)
        typer.echo(enrollment.model_dump_json(indent=2))
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@speakers_app.command("delete")
def speakers_delete(
    name: str,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm destructive delete.")] = False,
) -> None:
    """Delete one local speaker profile."""
    from .speaker_id.enrollment import delete_speaker

    try:
        path = delete_speaker(name, yes=yes)
        typer.echo(f"deleted speaker profile: {path}")
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@speakers_app.command("rename")
def speakers_rename(job_id: str, speaker: str, name: str) -> None:
    """Record a post-transcription speaker rename mapping."""
    from .speaker_id.enrollment import rename_speaker

    path = rename_speaker(job_id, speaker, name)
    typer.echo(f"wrote rename mapping: {path}")


@speakers_app.command("match")
def speakers_match(
    job_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    threshold: Annotated[float, typer.Option("--threshold")] = 0.75,
    ambiguity_margin: Annotated[float, typer.Option("--ambiguity-margin")] = 0.05,
) -> None:
    """Match diarized speaker clusters to local enrolled profiles."""
    from .speaker_id.enrollment import match_job_speakers

    payload = match_job_speakers(
        job_dir,
        threshold=threshold,
        ambiguity_margin=ambiguity_margin,
    )
    typer.echo(f"wrote {job_dir / 'speaker_map.json'}")
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@speakers_app.command("resolve-identities")
def speakers_resolve_identities(
    job_dir: Annotated[
        Path,
        typer.Argument(help="Existing local Auralign job directory."),
    ],
    mode: Annotated[
        str,
        typer.Option("--mode", help="review, apply-safe, or apply-approved."),
    ] = "review",
    roster: Annotated[
        Path | None,
        typer.Option("--roster", help="Strict versioned local roster JSON."),
    ] = None,
    known_speakers: Annotated[
        int | None,
        typer.Option(
            "--known-speakers",
            help="Expected cluster and complete-roster count (1-128).",
        ),
    ] = None,
    sibling_job: Annotated[
        list[Path] | None,
        typer.Option(
            "--sibling-job",
            help="Explicit related job; repeat for bounded review evidence.",
        ),
    ] = None,
    sibling_root: Annotated[
        Path | None,
        typer.Option("--sibling-root", help="Explicit bounded depth-one sibling root."),
    ] = None,
    sibling_manifest: Annotated[
        Path | None,
        typer.Option("--sibling-manifest", help="Strict bounded sibling manifest JSON."),
    ] = None,
    external_transcript: Annotated[
        Path | None,
        typer.Option("--external-transcript", help="Read-only transcript for drift comparison."),
    ] = None,
    approved_map: Annotated[
        Path | None,
        typer.Option("--approved-map", help="Current-run map required by apply-approved."),
    ] = None,
    write_identity_mapped_exports: Annotated[
        bool,
        typer.Option(
            "--write-identity-mapped-exports",
            help="Write derived exports in review mode; apply modes imply this.",
        ),
    ] = False,
    identity_deliverable_format: Annotated[
        str,
        typer.Option("--identity-deliverable-format", help="srt or all; SRT remains primary."),
    ] = "srt",
    audit_csv: Annotated[
        bool,
        typer.Option(
            "--audit-csv/--no-audit-csv",
            help="Write scan-friendly CSV; canonical JSON audit is always written.",
        ),
    ] = True,
    stability_config: Annotated[
        Path | None,
        typer.Option("--stability-config", help="Explicit bounded local sidecar configuration."),
    ] = None,
    write_media_review_queue: Annotated[
        bool,
        typer.Option(
            "--write-media-review-queue",
            help="Write timestamp-only disputed-region review tasks.",
        ),
    ] = False,
    media_review_report: Annotated[
        Path | None,
        typer.Option("--media-review-report", help="Strict reviewed conclusions; never media."),
    ] = None,
    unattributed_label: Annotated[
        str,
        typer.Option(
            "--unattributed-label",
            help="Display label for intentionally unattributed text.",
        ),
    ] = "Unattributed",
    min_apply_confidence: Annotated[
        float,
        typer.Option("--min-apply-confidence", help="Finite apply threshold in [0,1]."),
    ] = 0.90,
    profile_threshold: Annotated[
        float,
        typer.Option("--profile-threshold", help="Finite local profile threshold in [0,1]."),
    ] = 0.75,
    profile_ambiguity_margin: Annotated[
        float,
        typer.Option("--profile-ambiguity-margin", help="Finite ambiguity margin in [0,1]."),
    ] = 0.05,
    max_fragment_duration: Annotated[
        float,
        typer.Option("--max-fragment-duration", help="Bounded fragment duration in (0,60]."),
    ] = 4.0,
    max_fragment_words: Annotated[
        int,
        typer.Option("--max-fragment-words", help="Bounded fragment word count (1-500)."),
    ] = 12,
    max_sibling_jobs: Annotated[
        int,
        typer.Option("--max-sibling-jobs", help="Hard related-job limit (1-100)."),
    ] = 20,
    source_media_review: Annotated[
        str,
        typer.Option(
            "--source-media-review",
            help="none, queue, or reviewed-report; never automatic media inspection.",
        ),
    ] = "queue",
    turn_boundary_repair: Annotated[
        str,
        typer.Option(
            "--turn-boundary-repair",
            help="report-only or label-only; structural splitting is unsupported.",
        ),
    ] = "report-only",
    machine_readable: Annotated[
        bool,
        typer.Option("--machine-readable", help="Emit compact privacy-safe result/error JSON."),
    ] = False,
) -> None:
    """Resolve speaker identities offline into reports and derived exports."""
    from pydantic import ValidationError

    from .speaker_id.identity_io import IdentityResolverError
    from .speaker_id.identity_models import (
        DeliverableFormat,
        ResolverMode,
        ResolverPolicy,
        SourceMediaReviewMode,
        StructuralRepairMode,
    )
    from .speaker_id.identity_resolver import ResolveIdentityOptions, resolve_identities

    remediation = (
        "Validate the local job and resolver inputs, then retry; canonical artifacts are not "
        "resolver outputs."
    )
    try:
        if mode not in {"review", "apply-safe", "apply-approved"}:
            raise IdentityResolverError(
                "invalid_mode", "Resolver mode must be review, apply-safe, or apply-approved."
            )
        if identity_deliverable_format not in {"srt", "all"}:
            raise IdentityResolverError(
                "invalid_deliverable_format", "Identity deliverable format must be srt or all."
            )
        if source_media_review not in {"none", "queue", "reviewed-report"}:
            raise IdentityResolverError(
                "invalid_media_review_mode",
                "Source media review must be none, queue, or reviewed-report.",
            )
        if turn_boundary_repair not in {"report-only", "label-only"}:
            raise IdentityResolverError(
                "invalid_turn_boundary_repair",
                "Turn boundary repair must be report-only or label-only.",
            )
        if known_speakers is not None and not 1 <= known_speakers <= 128:
            raise IdentityResolverError(
                "invalid_known_speakers", "Known speaker count must be between 1 and 128."
            )
        if source_media_review == "reviewed-report" and media_review_report is None:
            raise IdentityResolverError(
                "media_review_report_required",
                "reviewed-report mode requires --media-review-report.",
            )
        if media_review_report is not None and source_media_review != "reviewed-report":
            raise IdentityResolverError(
                "media_review_report_wrong_mode",
                "--media-review-report is consumed only in reviewed-report mode.",
            )

        policy = ResolverPolicy(
            mode=ResolverMode(mode),
            min_apply_confidence=min_apply_confidence,
            profile_threshold=profile_threshold,
            profile_ambiguity_margin=profile_ambiguity_margin,
            max_fragment_duration=max_fragment_duration,
            max_fragment_words=max_fragment_words,
            max_sibling_jobs=max_sibling_jobs,
            unattributed_label=unattributed_label,
            audit_csv=audit_csv,
            deliverable_format=DeliverableFormat(identity_deliverable_format),
            turn_boundary_repair=StructuralRepairMode(turn_boundary_repair),
            source_media_review=SourceMediaReviewMode(source_media_review),
        )
        result = resolve_identities(
            job_dir,
            ResolveIdentityOptions(
                policy=policy,
                known_speakers=known_speakers,
                roster_path=roster,
                external_transcript=external_transcript,
                approved_map_path=approved_map,
                sibling_jobs=tuple(sibling_job or ()),
                sibling_root=sibling_root,
                sibling_manifest=sibling_manifest,
                stability_config_path=stability_config,
                media_review_report_path=media_review_report,
                write_identity_mapped_exports=write_identity_mapped_exports,
                write_media_review_queue=write_media_review_queue,
            ),
        )
    except IdentityResolverError as exc:
        _emit_identity_failure(
            code=exc.code,
            message=str(exc),
            remediation=exc.remediation or remediation,
            mode=mode,
            machine_readable=machine_readable,
        )
        raise typer.Exit(1) from exc
    except ValidationError as exc:
        _emit_identity_failure(
            code="invalid_resolver_options",
            message="Resolver options failed strict bounded validation.",
            remediation=remediation,
            mode=mode,
            machine_readable=machine_readable,
        )
        raise typer.Exit(1) from exc
    except Exception as exc:
        _emit_identity_failure(
            code="resolver_failed",
            message="Identity resolution failed without publishing a complete artifact family.",
            remediation=remediation,
            mode=mode,
            machine_readable=machine_readable,
        )
        raise typer.Exit(1) from exc

    report = result.report
    payload = {
        "schema_version": "speaker_identity_cli_result.v1",
        "status": "complete",
        "mode": report.mode.value,
        "run_id": report.run_id,
        "artifact_count": len(result.written_artifacts),
        "artifacts": sorted(result.written_artifacts),
        "cluster_decision_count": len(report.cluster_decisions),
        "turn_decision_count": len(report.turn_decisions),
        "warning_count": len(report.warnings),
        "validation_valid": report.validation_summary.valid,
    }
    if machine_readable:
        typer.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return
    typer.echo(
        "Identity resolution complete: "
        f"mode={payload['mode']} run_id={payload['run_id']} "
        f"artifacts={payload['artifact_count']} validation_valid={payload['validation_valid']}"
    )
    typer.echo("Artifacts: " + ", ".join(sorted(result.written_artifacts)))


@speakers_app.command("export")
def speakers_export(
    output: Annotated[Path, typer.Option("--out", "-o")] = Path("speaker_profiles.json"),
) -> None:
    """Export local speaker profile metadata."""
    from .speaker_id.enrollment import export_profiles

    path = export_profiles(output)
    typer.echo(f"exported speaker profiles to {path}")


@speakers_app.command("import")
def speakers_import(
    input_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    """Import local speaker profile metadata."""
    from .speaker_id.enrollment import import_profiles

    count = import_profiles(input_path)
    typer.echo(f"imported {count} speaker profiles")


@app.command()
def validate_speakers(
    job_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    expected_speakers: Annotated[int, typer.Option("--expected-speakers", min=1)],
) -> None:
    """Validate diarization outputs against an expected speaker count."""
    from .validation.speakers import render_validation_report, validate_speaker_artifacts

    try:
        report = validate_speaker_artifacts(job_dir, expected_speakers)
        typer.echo(render_validation_report(report))
        if report.unique_speakers != expected_speakers:
            raise typer.Exit(1)
    except LTMError as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@app.command()
def inspect(
    job_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Inspect a completed local job directory without exposing raw secrets."""
    job_status = _artifact_status(job_dir, expected_kind="directory")
    typer.echo(f"job_directory={job_status}")
    if job_status != "present":
        raise typer.Exit(1)
    status_by_artifact: dict[str, str] = {}
    category_counts: dict[str, dict[str, int]] = {}
    categories: tuple[tuple[str, tuple[tuple[str, Literal["file", "directory"]], ...]], ...] = (
        ("canonical", tuple((artifact, "file") for artifact in _CANONICAL_JOB_ARTIFACTS)),
        ("strict_validation", _STRICT_VALIDATION_ARTIFACTS),
        ("identity", tuple((artifact, "file") for artifact in _IDENTITY_JOB_ARTIFACTS)),
    )
    unsafe_count = int(job_status.startswith("unsafe_"))
    for category, artifacts in categories:
        counts = {"present": 0, "missing": 0, "unsafe": 0}
        for artifact, expected_kind in artifacts:
            artifact_status = _artifact_status(job_dir / artifact, expected_kind=expected_kind)
            if artifact == "diarization_candidates" and artifact_status == "present":
                artifact_status = _candidate_directory_status(job_dir / artifact)
            status_by_artifact[artifact] = artifact_status
            counts[
                "present"
                if artifact_status == "present"
                else "missing"
                if artifact_status == "missing"
                else "unsafe"
            ] += 1
            unsafe_count += int(artifact_status.startswith("unsafe_"))
            typer.echo(f"{artifact}={artifact_status}")
        category_counts[category] = counts
        for status_name in ("present", "missing", "unsafe"):
            typer.echo(f"{category}_artifact_{status_name}_count={counts[status_name]}")
    typer.echo(f"identity_artifact_count={category_counts['identity']['present']}")
    typer.echo(f"artifact_contract_unsafe_count={unsafe_count}")

    qa_invalid = False
    qa_path = job_dir / "qa_report.json"
    if status_by_artifact["qa_report.json"] == "present":
        try:
            qa = json.loads(_read_bounded_regular(qa_path, max_bytes=_MAX_INSPECT_QA_BYTES))
            if not isinstance(qa, dict) or not isinstance(qa.get("summary"), dict):
                raise ValueError("QA report summary is invalid")
            summary = qa["summary"]
            flag_count = summary.get("flag_count")
            overall = summary.get("overall_quality_summary")
            if type(flag_count) is not int or flag_count < 0:
                raise ValueError("QA flag count is invalid")
            if overall not in _SAFE_QA_OVERALL_VALUES:
                raise ValueError("QA overall result is invalid")
            typer.echo("qa_report_parse=valid")
            typer.echo(f"qa_flag_count={flag_count}")
            typer.echo(f"qa_overall={overall}")
        except (OSError, ValueError, json.JSONDecodeError):
            qa_invalid = True
            typer.echo("qa_report_parse=invalid")
    if unsafe_count or qa_invalid:
        raise typer.Exit(1)


@app.command()
def batch(
    input_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    preset: Annotated[str, typer.Option(help="max, balanced, fast, or cpu")] = "max",
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("out/batch"),
    recursive: Annotated[bool, typer.Option("--recursive")] = False,
    include: Annotated[str, typer.Option("--include")] = "*",
    exclude: Annotated[str | None, typer.Option("--exclude")] = None,
    resume: Annotated[bool, typer.Option("--resume")] = False,
    skip_completed: Annotated[bool, typer.Option("--skip-completed")] = False,
    expected_speakers: Annotated[int | None, typer.Option("--expected-speakers", min=1)] = None,
    debug_report: Annotated[bool, typer.Option("--debug-report")] = False,
    aggregate_qa: Annotated[bool, typer.Option("--aggregate-qa")] = True,
) -> None:
    """Transcribe a directory of local media files with isolated failures."""
    from .batch import run_batch

    config = _config(preset)
    summary = run_batch(
        input_dir,
        out,
        config,
        recursive=recursive,
        include=include,
        exclude=exclude,
        resume=resume,
        skip_completed=skip_completed,
        expected_speakers=expected_speakers,
        debug_report=debug_report,
    )
    if not aggregate_qa:
        (out / "aggregate_qa.csv").unlink(missing_ok=True)
    typer.echo(f"batch complete={summary['complete']} failed={summary['failed']} out={out}")


@app.command("benchmark", context_settings={"allow_extra_args": True})
def benchmark(
    ctx: typer.Context,
    target: Annotated[str | None, typer.Argument()] = None,
    reference_text: Annotated[Path | None, typer.Option("--reference-text", exists=True)] = None,
    reference_srt: Annotated[Path | None, typer.Option("--reference-srt", exists=True)] = None,
    reference_json: Annotated[Path | None, typer.Option("--reference-json", exists=True)] = None,
    reference_rttm: Annotated[Path | None, typer.Option("--reference-rttm", exists=True)] = None,
    references: Annotated[Path | None, typer.Option("--references", exists=True)] = None,
    out: Annotated[Path, typer.Option("--out")] = Path("benchmark_report"),
) -> None:
    """Benchmark a job directory, completed output directory, or batch output."""
    if target is None:
        typer.echo("Provide a JOB_DIR or completed output directory.", err=True)
        raise typer.Exit(1)
    from .benchmark.runner import run_benchmark_batch, run_benchmark_target

    try:
        if target == "compare":
            if len(ctx.args) != 2:
                typer.echo(
                    "Usage: ltm benchmark compare BASELINE_REPORT_OR_DIR "
                    "CANDIDATE_REPORT_OR_DIR --out benchmark_compare_report",
                    err=True,
                )
                raise typer.Exit(1)
            from .benchmark.compare import run_benchmark_compare

            comparison = run_benchmark_compare(Path(ctx.args[0]), Path(ctx.args[1]), out=out)
            typer.echo(
                f"wrote benchmark comparison matches={len(comparison['matched_files'])} to {out}"
            )
            return
        if reference_srt is not None:
            from .benchmark.corpus import write_srt_reference_text

            reference_text = out / "references" / f"{reference_srt.stem}.txt"
            write_srt_reference_text(reference_srt, reference_text)
        if target == "batch":
            if not ctx.args:
                typer.echo("Usage: ltm benchmark batch OUT_DIR --references refs/", err=True)
                raise typer.Exit(1)
            report = run_benchmark_batch(Path(ctx.args[0]), references=references, out=out)
            typer.echo(f"wrote batch benchmark report for {len(report.files)} files to {out}")
            return
        report = run_benchmark_target(
            Path(target),
            reference_text=reference_text,
            reference_json=reference_json,
            reference_rttm=reference_rttm,
            out=out,
        )
        typer.echo(f"wrote benchmark report for {len(report.files)} files to {out}")
    except (OSError, ValueError) as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc


@app.command("benchmark-corpus")
def benchmark_corpus(
    corpus_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("benchmark_runs/corpus"),
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help=(
                "inventory-only, excerpt-only, run-single, preset-matrix, "
                "backend-matrix, compute-matrix, or full-corpus"
            ),
        ),
    ] = "preset-matrix",
    presets: Annotated[
        str,
        typer.Option("--presets", help="Comma-separated presets: max,balanced,fast,cpu"),
    ] = "max,balanced,fast,cpu",
    compute_types: Annotated[
        str,
        typer.Option("--compute-types", help="Comma-separated ASR compute matrix values."),
    ] = "int8,int8_float32,float32",
    asr_backends: Annotated[
        str,
        typer.Option("--asr-backends", help="Comma-separated ASR backends."),
    ] = "faster-whisper",
    asr_mlx_model: Annotated[str | None, typer.Option("--asr-mlx-model")] = None,
    asr_mlx_model_path: Annotated[Path | None, typer.Option("--asr-mlx-model-path")] = None,
    diarization_devices: Annotated[
        str,
        typer.Option(
            "--diarization-devices",
            help="Optional comma-separated diarization device matrix values.",
        ),
    ] = "",
    alignment_devices: Annotated[
        str,
        typer.Option(
            "--alignment-devices",
            help="Optional comma-separated forced-alignment device matrix values.",
        ),
    ] = "",
    recursive: Annotated[bool, typer.Option("--recursive/--no-recursive")] = True,
    excerpt_seconds: Annotated[
        float | None,
        typer.Option("--excerpt-seconds", help="Create local excerpts before transcription."),
    ] = 120.0,
    start_seconds: Annotated[float, typer.Option("--start-seconds", min=0.0)] = 60.0,
    full: Annotated[
        bool,
        typer.Option("--full", help="Transcribe full media instead of excerpts."),
    ] = False,
    max_files: Annotated[int | None, typer.Option("--max-files", min=1)] = None,
    max_depth: Annotated[
        bool,
        typer.Option(
            "--max-depth/--standard-depth",
            help="Enable agentic diarization and re-decode.",
        ),
    ] = True,
    infer_speakers: Annotated[
        bool,
        typer.Option("--infer-speakers/--no-infer-speakers"),
    ] = True,
    speaker_overrides: Annotated[
        str | None,
        typer.Option("--speaker-overrides", help="Comma-separated slug=count speaker overrides."),
    ] = None,
    reference_text: Annotated[
        Path | None,
        typer.Option(
            "--reference-text",
            exists=True,
            help="Use one private local rough reference text file for planned jobs.",
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    verbose: Annotated[bool, typer.Option("--verbose/--quiet")] = True,
    sample_interval_seconds: Annotated[
        float,
        typer.Option("--sample-interval-seconds", min=0.1),
    ] = 2.0,
    asr_cpu_threads: Annotated[
        int | None,
        typer.Option("--asr-cpu-threads", min=1),
    ] = None,
    asr_num_workers: Annotated[
        int | None,
        typer.Option("--asr-num-workers", min=1),
    ] = None,
    asr_batched: Annotated[
        bool,
        typer.Option("--asr-batched/--no-asr-batched"),
    ] = False,
    asr_batch_size: Annotated[
        int | None,
        typer.Option("--asr-batch-size", min=1),
    ] = None,
    asr_cpu_thread_values: Annotated[
        str | None,
        typer.Option(
            "--asr-cpu-thread-values",
            help="Comma-separated CPU thread matrix values, e.g. unset,4,8,12,16.",
        ),
    ] = None,
    asr_num_worker_values: Annotated[
        str | None,
        typer.Option(
            "--asr-num-worker-values",
            help="Comma-separated worker matrix values, e.g. unset,1,2,4.",
        ),
    ] = None,
) -> None:
    """Run a local private-corpus benchmark with excerpts, presets, and SRT refs."""
    from .benchmark.corpus import (
        ASR_BACKEND_MATRIX,
        ASR_COMPUTE_MATRIX,
        PRESET_MATRIX,
        BenchmarkMode,
        parse_csv_values,
        parse_optional_positive_int_csv,
        parse_speaker_overrides,
        run_corpus_harness,
    )

    valid_modes = {
        "inventory-only",
        "excerpt-only",
        "run-single",
        "preset-matrix",
        "backend-matrix",
        "compute-matrix",
        "full-corpus",
    }
    try:
        if mode not in valid_modes:
            raise typer.BadParameter(
                "mode must be one of: inventory-only, excerpt-only, run-single, "
                "preset-matrix, backend-matrix, compute-matrix, full-corpus"
            )
        preset_names = parse_csv_values(
            presets,
            valid=set(PRESET_MATRIX),
            label="presets",
        )
        compute_names = parse_csv_values(
            compute_types,
            valid=set(ASR_COMPUTE_MATRIX),
            label="compute-types",
        )
        asr_backend_names = parse_csv_values(
            asr_backends,
            valid=set(ASR_BACKEND_MATRIX),
            label="asr-backends",
        )
        diarization_names = [
            part.strip() for part in diarization_devices.split(",") if part.strip()
        ]
        alignment_names = [part.strip() for part in alignment_devices.split(",") if part.strip()]
        overrides = parse_speaker_overrides([speaker_overrides] if speaker_overrides else None)
        cpu_thread_values = parse_optional_positive_int_csv(
            asr_cpu_thread_values,
            label="asr-cpu-thread-values",
        )
        worker_values = parse_optional_positive_int_csv(
            asr_num_worker_values,
            label="asr-num-worker-values",
        )
        report = run_corpus_harness(
            corpus_dir,
            out=out,
            mode=cast(BenchmarkMode, mode),
            presets=preset_names,
            compute_types=compute_names,
            asr_backends=asr_backend_names,
            asr_mlx_model=asr_mlx_model,
            asr_mlx_model_path=asr_mlx_model_path,
            diarization_devices=diarization_names,
            alignment_devices=alignment_names,
            recursive=recursive,
            excerpt_seconds=excerpt_seconds,
            start_seconds=start_seconds,
            full=full,
            max_files=max_files,
            max_depth=max_depth,
            infer_speakers=infer_speakers,
            dry_run=dry_run,
            verbose=verbose,
            speaker_overrides=overrides,
            asr_cpu_threads=asr_cpu_threads,
            asr_num_workers=asr_num_workers,
            asr_batched=asr_batched,
            asr_batch_size=asr_batch_size,
            asr_cpu_thread_values=cpu_thread_values,
            asr_num_worker_values=worker_values,
            reference_text=reference_text,
            sample_interval_seconds=sample_interval_seconds,
            emit=lambda message: typer.echo(message, err=True),
        )
    except (OSError, ValueError, typer.BadParameter) as exc:
        typer.echo(privacy_safe_text(str(exc)), err=True)
        raise typer.Exit(1) from exc

    typer.echo(
        "wrote corpus benchmark report "
        f"media={len(report['inventory'])} jobs={len(report['jobs'])} out={out}"
    )


def parse_preset_csv(value: str) -> list[PresetName]:
    presets = [part.strip() for part in value.split(",") if part.strip()]
    valid = {"max", "balanced", "fast", "cpu"}
    invalid = [preset for preset in presets if preset not in valid]
    if invalid:
        raise typer.BadParameter(
            "presets must be comma-separated values from: max, balanced, fast, cpu"
        )
    return [cast(PresetName, preset) for preset in presets] or ["max"]


def progress_printer() -> Callable[[str, dict[str, Any]], None]:
    started = time.perf_counter()

    def _print(event: str, payload: dict[str, Any]) -> None:
        elapsed = time.perf_counter() - started
        detail = " ".join(
            f"{key}={value}"
            for key, value in payload.items()
            if value is not None and key not in {"message"}
        )
        typer.echo(f"[{elapsed:8.2f}s] {event} {detail}".rstrip(), err=True)

    return _print


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8765,
    allow_network_bind: Annotated[
        bool,
        typer.Option(
            "--allow-network-bind",
            help="Allow binding the unauthenticated local API outside loopback.",
        ),
    ] = False,
) -> None:
    """Run the local FastAPI service."""
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if host not in loopback_hosts and not allow_network_bind:
        typer.echo(
            "Refusing to bind the unauthenticated local API outside loopback. "
            "Use --allow-network-bind only behind external access controls.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        import uvicorn
    except ImportError as exc:
        typer.echo("FastAPI service dependencies are missing. Run: uv sync --extra api", err=True)
        raise typer.Exit(1) from exc
    network_bound = host not in loopback_hosts
    if network_bound:
        typer.echo(
            "Warning: binding the unauthenticated local API outside loopback.",
            err=True,
        )
    previous_network_bound = os.environ.get("LTM_API_NETWORK_BOUND")
    if network_bound:
        os.environ["LTM_API_NETWORK_BOUND"] = "1"
    try:
        uvicorn.run("local_transcript_max.server.app:app", host=host, port=port)
    finally:
        if network_bound:
            if previous_network_bound is None:
                os.environ.pop("LTM_API_NETWORK_BOUND", None)
            else:
                os.environ["LTM_API_NETWORK_BOUND"] = previous_network_bound


def known_model_keys() -> list[str]:
    return sorted(MODEL_MANIFEST)


def _dataset_profile(value: str) -> DatasetProfile:
    if value != "runtime-samples":
        raise typer.BadParameter("dataset profile must be runtime-samples")
    return "runtime-samples"


def _yes(value: bool) -> str:
    return "yes" if value else "no"
