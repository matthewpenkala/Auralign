# SPDX-FileCopyrightText: 2026 Matthew Penkala
#
# SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from local_transcript_max.benchmark.compare import run_benchmark_compare
from local_transcript_max.benchmark.corpus import (
    MediaProbe,
    build_excerpt_command,
    build_transcribe_command,
    compute_type_support_metadata,
    expand_job_matrix,
    infer_speaker_count,
    media_probe_from_ffprobe,
    paired_youtube_srt,
    parse_macos_time_l,
    render_corpus_markdown,
    resource_sampling_supported,
    run_corpus_harness,
    write_srt_reference_text,
)
from local_transcript_max.benchmark.runner import run_benchmark_batch, run_benchmark_target
from local_transcript_max.cli import app
from local_transcript_max.doctor import private_benchmark_artifact_warning
from local_transcript_max.utils import read_json, write_json


def write_job(job: Path) -> None:
    job.mkdir(parents=True)
    (job / "transcript.txt").write_text("hello brave world", encoding="utf-8")
    write_json(
        job / "transcript.json",
        {
            "input_path": "sample.wav",
            "segments": [{"id": 0, "start": 0, "end": 1, "text": "hello brave world"}],
            "words": [
                {"text": "hello", "start": 0, "end": 0.3, "speaker": "SPEAKER_00"},
                {"text": "world", "start": 0.4, "end": 0.8, "speaker": "SPEAKER_00"},
            ],
            "diarization_turns": [{"start": 0, "end": 1, "speaker": "SPEAKER_00"}],
        },
    )
    write_json(job / "qa_report.json", {"summary": {"overlap_spans": []}})
    write_json(
        job / "run_metadata.json",
        {
            "input_metadata": {
                "path": "sample.wav",
                "duration_seconds": 12.0,
                "sample_rate": 16000,
                "channels": 1,
                "codec": "pcm_s16le",
                "format_name": "wav",
            },
            "canonical_audio_metadata": {
                "path": "canonical.wav",
                "duration_seconds": 12.0,
                "sample_rate": 16000,
                "channels": 1,
                "codec": "pcm_s16le",
                "format_name": "wav",
            },
            "audio_preprocessing": {
                "action": "reused",
                "reused": True,
                "reason": "already_canonical_wav",
                "ffmpeg_invoked": False,
                "input_matched_canonical_requirements": True,
            },
            "real_time_factor": 0.5,
            "asr_cpu_threads": 8,
            "asr_num_workers": 2,
            "resource_usage": {
                "rss_peak_sampled_bytes": 1_073_741_824,
                "rss_peak_process_lifetime_bytes": 1_073_741_824,
            },
            "device": "cpu",
            "compute_type": "int8",
            "runtime_environment": {
                "machine": "arm64",
                "asr_device_compute": {
                    "resolved_device": "cpu",
                    "resolved_compute_type": "int8",
                    "requested_cpu_threads": 8,
                    "effective_cpu_threads": 8,
                    "requested_num_workers": 2,
                    "effective_num_workers": 2,
                    "supported": True,
                },
            },
            "timings": [
                {"name": "total", "duration_seconds": 6.0},
                {"name": "asr", "duration_seconds": 4.0},
            ],
        },
    )
    (job / "transcript.rttm").write_text(
        "SPEAKER file 1 0.000 1.000 <NA> <NA> SPEAKER_00 <NA> <NA>\n",
        encoding="utf-8",
    )


def test_benchmark_from_job_directory(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_job(job)
    ref = tmp_path / "ref.txt"
    ref.write_text("hello world", encoding="utf-8")
    report = run_benchmark_target(job, reference_text=ref, out=tmp_path / "bench")
    assert report.files[0].wer is not None
    assert (tmp_path / "bench" / "benchmark.json").exists()
    assert (tmp_path / "bench" / "benchmark.csv").exists()
    assert (tmp_path / "bench" / "benchmark.md").exists()
    benchmark = read_json(tmp_path / "bench" / "benchmark.json")
    assert benchmark["schema_version"] == "benchmark_report.v3"
    assert benchmark["metadata"]["privacy_note"]
    assert benchmark["files"][0]["audio_duration_seconds"] == 12.0
    assert benchmark["files"][0]["wall_time_seconds"] == 6.0
    assert benchmark["files"][0]["model_device_compute"]["device"] == "cpu"
    assert benchmark["files"][0]["model_device_compute"]["asr_cpu_threads"] == 8
    assert benchmark["files"][0]["model_device_compute"]["asr_num_workers"] == 2
    assert benchmark["files"][0]["resource_usage"]["rss_peak_sampled_bytes"] == 1_073_741_824
    assert benchmark["files"][0]["audio_preprocessing"]["action"] == "reused"
    assert benchmark["files"][0]["artifact_counts"]["json_files"] >= 3
    assert benchmark["files"][0]["quality_checks"]["segments_monotonic"] is True
    assert benchmark["aggregate"]["max_rss_peak_sampled_bytes"] == 1_073_741_824
    assert benchmark["aggregate"]["mean_wall_time_seconds"] == 6.0
    csv_text = (tmp_path / "bench" / "benchmark.csv").read_text(encoding="utf-8")
    assert "model_device_compute" in csv_text
    assert "asr_cpu_threads" in csv_text
    markdown = (tmp_path / "bench" / "benchmark.md").read_text(encoding="utf-8")
    assert "RSS peak" in markdown
    assert "cpu threads" in markdown


def test_benchmark_compare_outputs_deltas_without_significance_claim(tmp_path: Path) -> None:
    baseline_job = tmp_path / "baseline" / "job"
    candidate_job = tmp_path / "candidate" / "job"
    write_job(baseline_job)
    write_job(candidate_job)
    candidate_metadata = read_json(candidate_job / "run_metadata.json")
    candidate_metadata["real_time_factor"] = 0.4
    candidate_metadata["timings"][0]["duration_seconds"] = 4.8
    candidate_metadata["resource_usage"]["rss_peak_sampled_bytes"] = 805_306_368
    write_json(candidate_job / "run_metadata.json", candidate_metadata)
    baseline_report = run_benchmark_target(baseline_job, out=tmp_path / "baseline_report")
    candidate_report = run_benchmark_target(candidate_job, out=tmp_path / "candidate_report")

    comparison = run_benchmark_compare(
        tmp_path / "baseline_report",
        tmp_path / "candidate_report",
        out=tmp_path / "compare",
    )

    assert len(baseline_report.files) == 1
    assert len(candidate_report.files) == 1
    assert comparison["matched_files"][0]["metrics"]["real_time_factor"]["delta"] == -0.1
    assert comparison["matched_files"][0]["metrics"]["total_seconds"]["delta"] == -1.2
    assert comparison["interpretation_note"].startswith("Deltas compare two reports only")
    markdown = (tmp_path / "compare" / "benchmark_comparison.md").read_text(encoding="utf-8")
    assert "not statistical significance claims" in markdown
    assert (tmp_path / "compare" / "benchmark_comparison.csv").exists()

    result = CliRunner().invoke(
        app,
        [
            "benchmark",
            "compare",
            str(tmp_path / "baseline_report"),
            str(tmp_path / "candidate_report"),
            "--out",
            str(tmp_path / "compare_cli"),
        ],
    )
    assert result.exit_code == 0
    assert "wrote benchmark comparison matches=1" in result.stdout
    assert (tmp_path / "compare_cli" / "benchmark_comparison.json").exists()


def test_benchmark_accepts_old_metadata_without_threading_fields(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_job(job)
    metadata = read_json(job / "run_metadata.json")
    metadata.pop("asr_cpu_threads")
    metadata.pop("asr_num_workers")
    metadata["runtime_environment"]["asr_device_compute"].pop("effective_cpu_threads")
    metadata["runtime_environment"]["asr_device_compute"].pop("effective_num_workers")
    write_json(job / "run_metadata.json", metadata)

    report = run_benchmark_target(job, out=tmp_path / "bench")

    assert report.files[0].model_device_compute["asr_cpu_threads"] is None
    assert report.files[0].model_device_compute["asr_num_workers"] is None


def test_der_mocked_equal_rttm_path(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_job(job)
    ref = tmp_path / "ref.rttm"
    ref.write_text((job / "transcript.rttm").read_text(encoding="utf-8"), encoding="utf-8")
    report = run_benchmark_target(job, reference_rttm=ref, out=tmp_path / "bench")
    assert report.files[0].der == 0.0
    assert report.files[0].der_metadata["status"] == "ok"
    assert report.files[0].der_metadata["contract"] == {
        "schema_version": "rttm_der.v1",
        "collar_seconds": 0.0,
        "overlap_scored": True,
        "evaluation_region": "union",
        "speaker_mapping": "optimal_one_to_one",
    }


def test_benchmark_batch_references(tmp_path: Path) -> None:
    batch = tmp_path / "batch"
    job = batch / "job1"
    write_job(job)
    refs = tmp_path / "refs"
    refs.mkdir()
    (refs / "job1.txt").write_text("hello world", encoding="utf-8")
    report = run_benchmark_batch(batch, references=refs, out=tmp_path / "bench")
    assert len(report.files) == 1


def test_benchmark_cli_missing_reference_is_allowed_for_coverage_metrics(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_job(job)
    result = CliRunner().invoke(app, ["benchmark", str(job), "--out", str(tmp_path / "bench")])
    assert result.exit_code == 0
    assert read_json(tmp_path / "bench" / "benchmark.json")["aggregate"]["file_count"] == 1


def test_benchmark_missing_target_errors(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["benchmark", str(tmp_path / "missing")])
    assert result.exit_code != 0


def test_speaker_count_inference_defaults_to_two_without_explicit_way() -> None:
    assert infer_speaker_count(Path("2026-06-16 12-05-19_most-recent_4-way_call.mp4")) == 4
    assert infer_speaker_count(Path("2026-06-16 10-55-49_most-recent_5_way_call.mp4")) == 5
    assert infer_speaker_count(Path("2026-06-15 12-09-59_meeting.mp4")) == 2
    assert (
        infer_speaker_count(
            Path("2026-06-15 12-09-59_meeting.mp4"),
            slug="custom-meeting",
            overrides={"custom-meeting": 6},
        )
        == 6
    )


def test_media_probe_from_ffprobe_payload(tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"1234")
    probe = media_probe_from_ffprobe(
        media,
        {
            "format": {
                "duration": "12.5",
                "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                "size": "4",
            },
            "streams": [
                {"codec_type": "video", "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac", "duration": "12.0"},
            ],
        },
    )

    assert probe.duration_seconds == 12.0
    assert probe.container == "mov,mp4,m4a,3gp,3g2,mj2"
    assert probe.audio_codec == "aac"
    assert probe.video_codec == "h264"
    assert probe.size_bytes == 4


def test_paired_youtube_srt_and_srt_reference_text_skip_bad_timestamps(
    tmp_path: Path,
) -> None:
    media = tmp_path / "2026-06-15 12-09-59__2-way_call.mp4"
    media.write_bytes(b"fake")
    srt = tmp_path / "2026-06-15 12-09-59__youtube-transcript-rip.srt"
    srt.write_text(
        "\n\n".join(
            [
                "1\n00:00:01,000 --> 00:00:03,000\nhello <b>world</b>",
                "2\nNaN --> 00:00:04,000\nbad timestamp",
                "3\n00:00:10,000 --> 00:00:12,000\noutside window",
            ]
        ),
        encoding="utf-8",
    )

    assert paired_youtube_srt(media) == srt
    reference = write_srt_reference_text(
        srt,
        tmp_path / "ref.txt",
        window_start_seconds=0,
        window_end_seconds=5,
    )

    assert reference.captions_kept == 1
    assert reference.malformed_caption_blocks_skipped == 1
    assert reference.kind == "rough_reference"
    assert reference.words == 2
    assert (tmp_path / "ref.txt").read_text(encoding="utf-8") == "hello world\n"


def test_excerpt_command_construction_uses_stream_copy_by_default(tmp_path: Path) -> None:
    command = build_excerpt_command(
        tmp_path / "input.mp4",
        tmp_path / "excerpt.mp4",
        start_seconds=60,
        duration_seconds=30,
    )

    assert command[:7] == ["ffmpeg", "-hide_banner", "-y", "-ss", "60", "-t", "30"]
    assert "-c" in command
    assert "copy" in command
    assert "-reset_timestamps" in command


def test_dry_run_transcribe_command_generation() -> None:
    command = build_transcribe_command(
        Path("excerpt.mp4"),
        Path("jobs/example"),
        preset="max",
        speaker_count=4,
        config_file=None,
        max_depth=True,
        verbose=True,
        asr_cpu_threads=None,
        asr_num_workers=None,
        asr_batched=True,
        asr_batch_size=6,
    )

    assert command[:4] == ["uv", "run", "ltm", "transcribe"]
    assert "--num-speakers" in command
    assert "4" in command
    assert "--agentic-diarization" in command
    assert "--write-redecode-report" in command
    assert command[command.index("--asr-backend") + 1] == "faster-whisper"
    assert "--asr-batched" in command
    assert command[command.index("--asr-batch-size") + 1] == "6"


def test_compute_matrix_expansion_keeps_other_settings_constant(tmp_path: Path) -> None:
    rows = [
        {
            "source": "source.mp4",
            "input": "excerpt.mp4",
            "slug": "sample",
            "inferred_speakers": 2,
            "reference": "ref.txt",
            "reference_kind": "rough_reference",
        }
    ]

    jobs = expand_job_matrix(
        rows,
        out=tmp_path,
        mode="compute-matrix",
        presets=["max", "fast"],
        compute_types=["int8", "float32"],
        asr_backends=["faster-whisper"],
        asr_mlx_model=None,
        asr_mlx_model_path=None,
        diarization_devices=[],
        alignment_devices=[],
        infer_speakers=True,
        max_depth=False,
        verbose=False,
        asr_cpu_threads=None,
        asr_num_workers=None,
    )

    assert [job["preset"] for job in jobs] == ["max", "max"]
    assert [job["asr_backend"] for job in jobs] == ["faster-whisper", "faster-whisper"]
    assert [job["asr_compute_type"] for job in jobs] == ["int8", "float32"]
    assert [job["asr_device"] for job in jobs] == ["cpu", "cpu"]
    assert all(job["speaker_count"] == 2 for job in jobs)
    assert jobs[1]["float32_memory_warning"] == "float32 compute can be memory-heavy"


def test_compute_matrix_records_unsupported_compute_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "local_transcript_max.benchmark.corpus.supported_compute_types",
        lambda device: {"int8"},
    )
    rows = [
        {
            "source": "source.mp4",
            "input": "excerpt.mp4",
            "slug": "sample",
            "inferred_speakers": 2,
            "reference": "ref.txt",
            "reference_kind": "rough_reference",
        }
    ]

    jobs = expand_job_matrix(
        rows,
        out=tmp_path,
        mode="compute-matrix",
        presets=["max"],
        compute_types=["int8", "float32"],
        asr_backends=["faster-whisper"],
        asr_mlx_model=None,
        asr_mlx_model_path=None,
        diarization_devices=[],
        alignment_devices=[],
        infer_speakers=True,
        max_depth=False,
        verbose=False,
        asr_cpu_threads=None,
        asr_num_workers=None,
    )

    assert jobs[0]["compute_type_support"]["supported"] is True
    assert jobs[1]["benchmark_status"] == "skipped"
    assert jobs[1]["skipped_reason"] == "unsupported_ct2_compute_type"
    assert jobs[1]["compute_type_support"]["supported_compute_types"] == ["int8"]


def test_compute_type_support_unknown_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "local_transcript_max.benchmark.corpus.supported_compute_types",
        lambda device: None,
    )

    support = compute_type_support_metadata("float32", device="cpu")

    assert support["supported"] is None
    assert support["supported_compute_types"] is None


def test_compute_matrix_skips_mlx_backend(tmp_path: Path) -> None:
    rows = [
        {
            "source": "source.mp4",
            "input": "excerpt.mp4",
            "slug": "sample",
            "inferred_speakers": 2,
            "reference": "ref.txt",
            "reference_kind": "rough_reference",
        }
    ]

    jobs = expand_job_matrix(
        rows,
        out=tmp_path,
        mode="compute-matrix",
        presets=["max"],
        compute_types=["int8"],
        asr_backends=["faster-whisper", "mlx-whisper"],
        asr_mlx_model="mlx-large-v3-turbo",
        asr_mlx_model_path=Path("models/mlx-community--whisper-large-v3-turbo"),
        diarization_devices=[],
        alignment_devices=[],
        infer_speakers=True,
        max_depth=False,
        verbose=False,
        asr_cpu_threads=None,
        asr_num_workers=None,
    )

    assert [job["asr_backend"] for job in jobs] == ["faster-whisper", "mlx-whisper"]
    assert jobs[1]["benchmark_status"] == "skipped"
    assert jobs[1]["skipped_reason"] == "unsupported_backend_for_compute_matrix"
    assert "--asr-backend" in jobs[1]["transcribe_command"]
    assert "mlx-whisper" in jobs[1]["transcribe_command"]


def test_backend_matrix_expands_mlx_and_device_flags(tmp_path: Path) -> None:
    rows = [
        {
            "source": "source.mp4",
            "input": "excerpt.mp4",
            "slug": "sample",
            "inferred_speakers": 2,
            "reference": "ref.txt",
            "reference_kind": "rough_reference",
        }
    ]

    jobs = expand_job_matrix(
        rows,
        out=tmp_path,
        mode="backend-matrix",
        presets=["max", "fast"],
        compute_types=["int8"],
        asr_backends=["faster-whisper", "mlx-whisper"],
        asr_mlx_model="mlx-large-v3-turbo",
        asr_mlx_model_path=Path("models/mlx-community--whisper-large-v3-turbo"),
        diarization_devices=["mps"],
        alignment_devices=["mps"],
        infer_speakers=True,
        max_depth=False,
        verbose=False,
        asr_cpu_threads=None,
        asr_num_workers=None,
    )

    assert [job["preset"] for job in jobs] == ["max", "max"]
    assert [job["asr_backend"] for job in jobs] == ["faster-whisper", "mlx-whisper"]
    assert [job["benchmark_status"] for job in jobs] == ["planned", "planned"]
    mlx_command = jobs[1]["transcribe_command"]
    assert mlx_command[mlx_command.index("--asr-backend") + 1] == "mlx-whisper"
    assert mlx_command[mlx_command.index("--asr-mlx-model") + 1] == "mlx-large-v3-turbo"
    assert mlx_command[mlx_command.index("--diarization-device") + 1] == "mps"
    assert mlx_command[mlx_command.index("--alignment-device") + 1] == "mps"
    assert jobs[1]["alignment_device"] == "mps"
    assert jobs[1]["diarization_device"] == "mps"


def test_thread_matrix_expansion_uses_env_overrides(tmp_path: Path) -> None:
    rows = [
        {
            "source": "source.mp4",
            "input": "excerpt.mp4",
            "slug": "sample",
            "inferred_speakers": 2,
            "reference": "ref.txt",
            "reference_kind": "rough_reference",
        }
    ]

    jobs = expand_job_matrix(
        rows,
        out=tmp_path,
        mode="run-single",
        presets=["max"],
        compute_types=["int8"],
        asr_backends=["faster-whisper"],
        asr_mlx_model=None,
        asr_mlx_model_path=None,
        diarization_devices=[],
        alignment_devices=[],
        infer_speakers=True,
        max_depth=False,
        verbose=False,
        asr_cpu_threads=None,
        asr_num_workers=None,
        asr_cpu_thread_values=[None, 4, 8],
        asr_num_worker_values=[None, 2],
    )

    assert len(jobs) == 6
    assert jobs[0]["asr_cpu_threads"] is None
    assert jobs[0]["asr_num_workers"] is None
    assert jobs[0]["env_overrides"] == {}
    assert jobs[-1]["asr_cpu_threads"] == 8
    assert jobs[-1]["asr_num_workers"] == 2
    assert jobs[-1]["asr_batched"] is False
    assert jobs[-1]["asr_batch_size"] is None
    assert jobs[-1]["env_overrides"] == {
        "LTM_ASR_CPU_THREADS": "8",
        "LTM_ASR_NUM_WORKERS": "2",
    }


def test_corpus_harness_rejects_single_reference_for_multiple_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "first_2-way.mp4").write_bytes(b"fake")
    (corpus / "second_2-way.mp4").write_bytes(b"fake")
    reference = tmp_path / "rough_reference.txt"
    reference.write_text("rough local reference\n", encoding="utf-8")
    monkeypatch.setattr(
        "local_transcript_max.benchmark.corpus.probe_media",
        lambda path: MediaProbe(
            duration_seconds=30.0,
            container="mov,mp4",
            audio_codec="aac",
            video_codec="h264",
            size_bytes=4,
        ),
    )

    with pytest.raises(ValueError, match="--reference-text"):
        run_corpus_harness(
            corpus,
            out=tmp_path / "out",
            mode="inventory-only",
            presets=["max"],
            compute_types=["int8"],
            asr_backends=["faster-whisper"],
            asr_mlx_model=None,
            asr_mlx_model_path=None,
            diarization_devices=[],
            alignment_devices=[],
            recursive=True,
            excerpt_seconds=30.0,
            start_seconds=0.0,
            full=False,
            max_files=None,
            max_depth=False,
            infer_speakers=True,
            dry_run=True,
            verbose=False,
            speaker_overrides=None,
            asr_cpu_threads=None,
            asr_num_workers=None,
            reference_text=reference,
            sample_interval_seconds=2.0,
        )


def test_parse_macos_time_l_resource_output() -> None:
    parsed = parse_macos_time_l(
        "\n".join(
            [
                "       37.52 real        101.25 user         4.50 sys",
                " 3905642496 maximum resident set size",
            ]
        )
    )

    assert parsed["wall_seconds"] == 37.52
    assert parsed["user_cpu_seconds"] == 101.25
    assert parsed["system_cpu_seconds"] == 4.5
    assert parsed["max_rss_bytes"] == 3_905_642_496


def test_resource_sampling_unsupported_fallback(monkeypatch) -> None:
    monkeypatch.setattr("local_transcript_max.benchmark.corpus.which", lambda name: None)
    assert resource_sampling_supported() is False


def test_corpus_report_omits_transcript_text() -> None:
    report = {
        "privacy_note": "private",
        "settings": {
            "mode": "preset-matrix",
            "dry_run": True,
            "full": False,
            "presets": ["max"],
            "compute_types": ["int8"],
            "asr_batched": False,
            "asr_batch_size": None,
        },
        "inventory": [
            {
                "slug": "sample",
                "duration_seconds": 30.0,
                "size_bytes": 123,
                "container": "mp4",
                "audio_codec": "aac",
                "video_codec": "h264",
                "inferred_speakers": 2,
                "paired_srt_status": "paired",
                "benchmark_status": "planned",
                "reference_summary": {
                    "kind": "rough_reference",
                    "words": 3,
                },
            }
        ],
        "jobs": [
            {
                "job_slug": "sample-max",
                "preset": "max",
                "asr_compute_type": None,
                "validate_command": ["uv", "run", "ltm", "validate-speakers"],
                "benchmark_status": "planned",
            }
        ],
    }

    markdown = render_corpus_markdown(report)

    assert "hello private words" not in markdown
    assert "rough_reference" in markdown
    assert "transcript/SRT text" in markdown


def test_benchmark_cli_accepts_reference_srt(tmp_path: Path) -> None:
    job = tmp_path / "job"
    write_job(job)
    srt = tmp_path / "ref.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello world\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "benchmark",
            str(job),
            "--reference-srt",
            str(srt),
            "--out",
            str(tmp_path / "bench"),
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "bench" / "references" / "ref.txt").exists()
    benchmark = read_json(tmp_path / "bench" / "benchmark.json")
    assert benchmark["files"][0]["wer"] is not None


def test_private_benchmark_artifact_warning_detects_unignored_dir(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is required for this safety check")
    subprocess.run(["git", "init"], cwd=tmp_path, text=True, capture_output=True, check=True)
    (tmp_path / "benchmark_runs").mkdir()

    warning = private_benchmark_artifact_warning(tmp_path)

    assert warning is not None
    assert "benchmark_runs" in warning

    (tmp_path / ".gitignore").write_text("/benchmark_runs/\n", encoding="utf-8")
    assert private_benchmark_artifact_warning(tmp_path) is None
