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

Each completed job also records `resource_usage` with sampled RSS, process
lifetime peak RSS from `getrusage`, process CPU seconds, and best-effort source
fields explaining whether a metric came from `ps`, `resource.getrusage`, or was
unavailable on the host. Treat RSS as practical local evidence, not a precise
allocator-level profile. Benchmark JSON, CSV, and Markdown carry those fields
forward for preset and compute comparisons.

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
  --asr-mlx-model mlx-large-v3-turbo \
  --asr-mlx-model-path models/mlx-community--whisper-large-v3-turbo
```

For a bounded production-readiness smoke, use one representative 60-second
excerpt before spending time on a larger matrix:

```bash
LTM_OFFLINE=1 ltm benchmark-corpus "/path/to/excerpt-folder" \
  --out benchmark_runs/mlx_smoke \
  --mode backend-matrix \
  --presets balanced \
  --asr-backends faster-whisper,mlx-whisper \
  --asr-mlx-model mlx-large-v3-turbo \
  --asr-mlx-model-path models/mlx-community--whisper-large-v3-turbo \
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
to a supported acceleration path requires repeated stability plus
artifact-equivalence checks covering required files, speaker count, forced
alignment/fallback evidence, timeline invariants, QA summaries, and preserved
source hashes. Keep candidate and baseline jobs in separate directories.

Machine-tuned examples under `examples/` are benchmark starting points, not
portable defaults. Re-run a same-media local matrix before adopting their
thread, worker, batch, or device settings. The generic `balanced`, `max`,
`fast`, and `cpu` examples remain the normal preset references.

The current harness measures single-file latency/RTF. Throughput and
concurrent-job scaling are a separate benchmark question because they change
CPU contention, memory pressure, and per-file timing interpretation.
