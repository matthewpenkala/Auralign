<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Benchmarking

Benchmark existing transcript output directories:

```bash
ltm benchmark JOB_DIR --reference-text refs/transcript.txt --reference-rttm refs/diarization.rttm --out benchmark_report/
ltm benchmark JOB_DIR --reference-srt refs/youtube-transcript-rip.srt --out benchmark_report/
ltm benchmark batch OUT_DIR --references refs/ --out benchmark_report/
ltm benchmark compare benchmark_report/baseline benchmark_report/candidate --out benchmark_compare_report/
```

The benchmark runner computes WER/CER when text references are present and
dependency-free DER when reference and hypothesis RTTM are present. The fixed
`rttm_der.v1` contract uses a zero-second collar, scores overlap, evaluates the
union of reference/hypothesis speech, and chooses one global optimal one-to-one
speaker-label mapping. `der_metadata` records status and miss, false-alarm,
confusion, denominator, region, segment, speaker-count, and contract fields.
Malformed present RTTM fails rather than producing a misleading score; missing
RTTM and zero reference speech have typed unavailable statuses. Without
references the report still includes coverage, speaker uncertainty, overlap
count, runtime factor, and model/device metadata.

`run_metadata.json` is carried into benchmark JSON so Apple Silicon comparisons
can be tied to the resolved ASR device, CTranslate2 compute type, supported
compute-type matrix, Torch MPS availability, dependency versions, per-stage
timings, canonical-audio reuse/normalization decisions, artifact counts, and
basic output-quality checks. Use those fields before claiming a speedup; compare
wall-clock, real-time factor, memory pressure, and output quality for the same
media.

The timing scopes are intentionally split. `run_metadata.json` is immutable
core evidence with `timing_scope: pipeline_core.v1`; its `pipeline_core` timing,
real-time factor, and resource snapshot stop before publishing that metadata
and before later identity or debug-report work. `completion_timing.json` uses
schema `completion_timing.v1` and records `observed_end_to_end_seconds`, its
corresponding real-time factor, final resource usage, and named durations for
invocation setup, metadata publication, optional speaker-identity resolution,
and optional debug publication. Model/cache audit is inside the timed core
preflight. The required `invocation_setup` duration covers the earlier
successful-call setup, and `run_metadata_sha256` binds the exact immutable
`run_metadata.json` bytes it measures. Its measurement scope,
`through_debug_publish_before_completion_artifact.v1`, necessarily excludes
publishing `completion_timing.json` itself and sending the final completion
progress callback; those exclusions are explicit in the artifact.

For current jobs, `ltm benchmark` validates that binding, then prefers the
completion artifact for wall time, real-time factor, and resource usage while
retaining core stage timings from `run_metadata.json`. It falls back to the
legacy metadata fields only when evaluating an older job without
`completion_timing.json`. The dedicated Apple
Silicon canary does not use that compatibility fallback: every completed canary
run must contain and hash-bind a valid completion artifact so end-to-end timing
and final peak-memory evidence have one unambiguous scope.

Current `run_metadata.json` declares
`completion_timing_artifact: completion_timing.json`; a missing, malformed, or
mismatched declared artifact is an incomplete current job, never a legacy
fallback. Generic benchmark and batch resume compatibility applies only to
pre-contract metadata with no `completion_timing_artifact` field. Batch resume
then requires the legacy transcript, metadata, and a finite nonnegative
`total` timing before treating that older job as complete.

The resource records include sampled RSS, process lifetime peak RSS, process
CPU seconds, stage samples, and feature-detected MLX and Torch MPS allocator
counters when those runtimes are already loaded. Source fields identify native-
process values or unavailable metrics. RSS and framework counters are
overlapping views of unified memory, so never add them together. Benchmark
JSON, CSV, and Markdown carry those fields forward for preset and compute
comparisons.

`ltm benchmark compare` accepts either report directories or report JSON files.
It writes JSON, Markdown, and CSV deltas for RTF, stage timings, RSS, WER/CER/DER,
speaker-label coverage, and uncertainty. It labels missing metrics and does not
make statistical-significance or speedup claims automatically.

For private local corpora, use `benchmark-corpus` to run a reproducible matrix
without moving media into the repository:

```bash
ltm benchmark-corpus "/path/to/RECORDED MEETINGS" \
  --out benchmark_runs/recorded_meetings \
  --mode preset-matrix \
  --presets max,balanced,fast,cpu \
  --excerpt-seconds 120 \
  --start-seconds 60 \
  --verbose
```

The corpus runner discovers local media recursively, infers speaker count from
explicit `N-way` filename markers, defaults unmarked files to two speakers,
creates local excerpts, derives local text references from paired
`youtube-transcript-rip.srt` files, and writes per-preset benchmark reports.
Derived SRT references are rough comparison aids only; do not treat YouTube
transcript rips as WER/DER ground truth.
The `--reference-text` override is for a single selected media item only; use a
single-file corpus or `--max-files 1` so one rough reference cannot be applied to
unrelated jobs.
Use `--mode backend-matrix` for a bounded same-media ASR backend comparison,
such as faster-whisper versus MLX; use `--mode compute-matrix` only for
faster-whisper/CTranslate2 compute-type sweeps.

`benchmark_runs/`, `benchmark_report/`, and `benchmark_reports/` are ignored
because they can contain private generated transcripts, rough local references,
media excerpts, logs, speaker artifacts, and benchmark outputs. Keep those
artifacts local. Do not commit, upload, or paste transcript/SRT text, raw media,
excerpts, speaker embeddings, model weights, cache contents, tokens, or `.env`
values into benchmark reports or issue/PR text. Local references and excerpts
are private evaluation assets, not repo fixtures.

Use `--dry-run` first to inspect the inventory and planned jobs without running
transcription. Use `--full` only when wall time and memory headroom are known.
Verbose runs print stage transitions for long media so ASR, diarization, and
re-decode plateaus are visible while the job is active.

Useful modes:

- `inventory-only`: write media inventory and a privacy-safe local report.
- `excerpt-only`: create local excerpts and rough references without transcription.
- `run-single`: run one planned job for a small smoke test.
- `preset-matrix`: compare `max`, `balanced`, `fast`, and `cpu` while keeping the media window and speaker count fixed.
- `compute-matrix`: compare CPU `int8`, `int8_float32`, and `float32`; unsupported CTranslate2 CPU combinations are recorded as skipped before model load, and `float32` can be memory-heavy.
- `full-corpus`: requires `--full` and should only be used after explicit approval for long media.

The harness records exact commands, repo state, environment summary, inventory,
matrix settings, subprocess wall time, `/usr/bin/time -l` metrics on macOS when
available, live CPU/RSS samples when supported, `ltm benchmark` outputs, and
`validate-speakers` results. The report distinguishes measured values from
inferred values such as filename-derived speaker counts and artifact-based stage
hints. ASR thread/worker matrices can be run with positive integer values while
including `unset` as the current constructor-default baseline:

```bash
ltm benchmark-corpus "/path/to/excerpt-folder" \
  --out benchmark_runs/asr_cpu_threading \
  --mode run-single \
  --presets max \
  --asr-cpu-thread-values unset,4,8,12,16
```

`LTM_ASR_CPU_THREADS`, `LTM_ASR_NUM_WORKERS`, `--asr-cpu-threads`, and
`--asr-num-workers` only affect faster-whisper/CTranslate2 CPU execution. They
do not enable MPS/GPU ASR. Higher values can increase CPU contention, memory
pressure, or reduce responsiveness. Keep defaults unset unless same-media
benchmark evidence justifies a documented recommendation.

Batched faster-whisper inference can be tested with `--asr-batched` and
`--asr-batch-size`. It remains local faster-whisper/CTranslate2 execution, not
MPS/GPU ASR, and should be evaluated against the same media and rough references
before becoming a default:

```bash
ltm benchmark-corpus "/path/to/excerpt-folder" \
  --out benchmark_runs/asr_batched \
  --mode run-single \
  --presets balanced \
  --asr-batched \
  --asr-batch-size 8
```

MLX ASR can be compared as an opt-in backend on Apple Silicon. The MLX model
path must already exist locally; the transcription runtime will not download it:

```bash
ltm benchmark-corpus "/path/to/excerpt-folder" \
  --out benchmark_runs/asr_mlx \
  --mode backend-matrix \
  --presets balanced \
  --asr-backends faster-whisper,mlx-whisper \
  --asr-mlx-model mlx-large-v3 \
  --asr-mlx-model-path models/mlx-community--whisper-large-v3-mlx
```

`mlx-large-v3` is the reviewed Apple Silicon production candidate.
`mlx-large-v3-turbo` remains local-evaluation-only and must not be substituted
as the production default. The command above is an ASR backend exploration; it
is not the evidence-gated MPS diarization canary.

For a bounded local ASR smoke, use one representative 60-second excerpt before
spending time on a larger matrix:

```bash
LTM_OFFLINE=1 ltm benchmark-corpus "/path/to/excerpt-folder" \
  --out benchmark_runs/mlx_smoke \
  --mode backend-matrix \
  --presets balanced \
  --asr-backends faster-whisper,mlx-whisper \
  --asr-mlx-model mlx-large-v3 \
  --asr-mlx-model-path models/mlx-community--whisper-large-v3-mlx \
  --excerpt-seconds 60 \
  --start-seconds 60 \
  --standard-depth
```

Read that smoke as a go/no-go check, not a final accuracy claim. Continue only
if both jobs complete, `speaker_validation_status` is acceptable,
`words_with_timestamps_pct` remains high, and WER/CER or manual review does not
show a material same-excerpt regression. Use `--max-depth` and more windows only
after the short smoke passes.

`--mode compute-matrix` is specific to faster-whisper/CTranslate2 compute types.
MLX backend jobs in that mode are recorded as skipped with
`unsupported_backend_for_compute_matrix`.

Use `--diarization-devices mps` only to add an explicit Torch MPS pyannote
diarization experiment; Apple Silicon `auto` defaults to CPU for pyannote.
Use `--alignment-devices mps` only for explicit WhisperX alignment experiments.
Corpus summaries include requested/resolved diarization device, diarization
fallback state, and alignment device when completed benchmark artifacts provide
them.

MPS matrix entries are non-acceptance optimization candidates. Establish a
successful CPU acceptance baseline on the same media before running them, and
never substitute an MPS result for a failed or incomplete CPU gate. Promotion
to a supported acceleration path requires one excluded warmup and at least
three measured warm runs for both variants, identical bindings, actual MPS
execution without fallback, at least the configured minimum 10% median
diarization-stage improvement, no material median end-to-end regression, no
individual candidate total-time outlier beyond the accepted baseline envelope,
and no alignment or speaker-QA regression. Keep every run manifest and keep
candidate and baseline jobs in separate directories.

The complete gate and its explicit outcomes are documented in the
[Apple Silicon runbook](apple-silicon.md). A normal `benchmark-corpus` device
matrix does not by itself satisfy that gate because its generic path does not
provide the canary's required binding and warmup/repetition contract. Use the
dedicated `ltm apple-silicon-canary` command for promotion evidence; do not
promote MPS from a one-off matrix.

Machine-tuned examples under `examples/` are benchmark starting points, not
portable defaults. Re-run a same-media local matrix before adopting their
thread, worker, batch, or device settings. The generic `balanced`, `max`,
`fast`, and `cpu` examples remain the normal preset references.

The current harness measures single-file latency/RTF. Throughput and
concurrent-job scaling are a separate benchmark question because they change
CPU contention, memory pressure, and per-file timing interpretation.
