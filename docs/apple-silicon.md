<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Apple Silicon Runbook

This runbook defines Auralign's conservative native macOS arm64 topology:

```text
ASR             MLX Whisper large-v3, FP16, native Apple Silicon
Alignment       real WhisperX forced alignment, CPU
Diarization     pyannote Community-1, CPU
Speaker ID      SpeechBrain ECAPA, CPU
Runtime posture local and offline
```

MLX accelerates the workload it is designed for. It does not make every Torch
operation faster, and Torch MPS availability is not evidence that pyannote or
WhisperX should use MPS. ASR, alignment, and diarization devices are independent
decisions.

The canonical configuration is
[`examples/config.apple-silicon.yaml`](../examples/config.apple-silicon.yaml).
The release acceptance lane remains the separate CPU-only
`examples/config.strict-macos-arm64-cpu.yaml`; an MLX/MPS benchmark is not a
substitute for that release gate.

## Support Status And Current Evidence

Auralign's standalone native macOS PKG requires macOS 14 or later and Apple
Silicon. It embeds Python 3.11 and its reviewed CPU runtime; Python is a host
prerequisite only for the source-development lane. The experimental MLX lane
additionally requires the optional `mlx-whisper`
dependency and a complete operator-supplied local MLX model directory. Model
weights are not bundled and transcription never downloads them.

As of the 2026-07-18 implementation pass, the validation host was native arm64,
Torch MPS was available, and the locked `mlx`/`mlx-whisper` packages imported
successfully. The local MLX large-v3 model and Community-1 cache were still
missing, so this guide does not claim a measured MLX or MPS speedup or a
real-media end-to-end Apple Silicon run. Host state is mutable; `ltm doctor`
and exact local cache checks, not this dated note, are the current source of
readiness truth.

## Native Prerequisites And Rosetta Detection

Required source-development host components:

- macOS 14 or later on Apple Silicon;
- native arm64 Python 3.11 and `uv`;
- native or universal FFmpeg and FFprobe on `PATH`;
- the locked CPU Torch dependency group;
- the `mlx`, `diarization`, `alignment`, and `speaker-id` extras;
- enough disk and unified-memory headroom for the selected local models.

The private standalone PKG replaces the host Python, `uv`, FFmpeg, and dependency
requirements with its signed embedded runtime. It still requires separately
acquired local models and enough disk and memory for the selected configuration.

Check the process architecture before installing or benchmarking:

```bash
uname -m
sysctl -in sysctl.proc_translated 2>/dev/null || true
uv run python -c 'import platform, sys; print(platform.machine(), sys.executable)'
file "$(command -v ffmpeg)"
file "$(command -v ffprobe)"
uv run python -c 'import torch; print(torch.backends.mps.is_built(), torch.backends.mps.is_available())'
```

`uname -m` and Python's `platform.machine()` should both report `arm64`.
`sysctl.proc_translated=1` means the process is running under Rosetta. An absent
value or `0`, together with `arm64`, is the native path. Do not benchmark MLX
under Rosetta; rebuild the environment with an arm64 Python and native `uv`.
For Torch MPS, `is_built()` means the installed Torch has the backend while
`is_available()` also reflects whether the current host can execute it. An MPS
request requires both; neither value proves a workload is faster on MPS.

The extended doctor is the repository-consistent check for macOS version,
process and hardware architecture, Rosetta state, Python executable, package
versions, MPS built/available state, FFmpeg/FFprobe suitability, physical
memory, power state when obtainable without privileges, cache locations, and
independent device resolution:

```bash
LTM_OFFLINE=1 uv run ltm doctor \
  --config examples/config.apple-silicon.yaml
```

Add `--startup` only when all packages and exact model caches are ready. Startup
loads every selected backend and is expected to fail for a missing or incomplete
asset:

```bash
LTM_OFFLINE=1 uv run ltm doctor --startup \
  --config examples/config.apple-silicon.yaml
```

## Native Dependency Installation

For a source checkout, install the locked native dependency set:

```bash
uv sync --extra dev --extra mlx --extra diarization --extra alignment \
  --extra speaker-id --group cpu --no-python-downloads
```

The native installer provides the same guarded source-checkout route:

```bash
./install-macos.sh --source-checkout --experimental-mlx
source .auralign-runtime/auralign-env.sh
```

Do not combine `cpu`, `cu126`, and `cu128` groups. CUDA groups are not Apple
Silicon acceleration paths. The normal release wheel does not include the
experimental MLX extra; use the reviewed source-checkout route for this lane.

Re-run doctor after installation. `mlx-whisper` missing, an x86 Python under
Rosetta, or a requested MPS device that Torch cannot use is a blocker, not a
reason to silently switch backends.

## Local Model-Cache Preparation

Keep caches local and ignored:

```bash
export LTM_MODEL_CACHE="$PWD/models"
export HF_HOME="$PWD/.hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
```

For Apple Silicon ASR, use the reviewed `mlx-large-v3` manifest entry:

```text
model key:  mlx-large-v3
model ID:   mlx-community/whisper-large-v3-mlx
local path: models/mlx-community--whisper-large-v3-mlx
```

It is the MLX conversion with verified MIT publisher metadata. The
`mlx-large-v3-turbo` entry remains local-evaluation-only with an unresolved
license disposition and must not be the production default.

Auralign does not acquire MLX weights. After an authorized operator separately
obtains a complete converted directory under upstream terms, import a copy into
the canonical cache:

```bash
uv run ltm models import-mlx /path/to/converted-mlx-whisper \
  --model mlx-large-v3 \
  --cache-dir models
```

The source and imported directory must contain `config.json` and either
`weights.safetensors` or `weights.npz`. Symlink imports are prohibited.

Forced alignment, diarization, and speaker matching also require their exact
local assets:

```text
models/facebook--wav2vec2-base-960h
models/pyannote--speaker-diarization-community-1
models/speechbrain--spkrec-ecapa-voxceleb
```

Community-1 is gated and its current manifest preparation gate is documented in
[Models](models.md#exact-connected-preparation-and-offline-runbook). Repository
access does not confer gated acceptance. Never place a token in a CLI argument,
configuration, report, or chat, and never hand-author `.ltm-model.json`.

Audit the exact configuration without network access:

```bash
LTM_OFFLINE=1 uv run ltm models audit \
  --config examples/config.apple-silicon.yaml
```

## Why The Topology Is Hybrid

MLX Whisper is the ASR choice because it provides a native Apple Silicon
Whisper runtime and an explicit FP16 path. Auralign binds it to a complete local
directory, so an unavailable package or model fails with an actionable error
instead of silently changing model/backend or attempting a download.

WhisperX forced alignment stays on CPU because correct timing provenance is the
acceptance criterion and MPS availability alone establishes neither support nor
speed. Historical project evidence found the alignment MPS path slower; only a
separate repeated same-media experiment could revisit it, and that experiment
is never part of automatic diarization promotion.

Pyannote diarization starts on CPU because it is the stable acceptance baseline.
Torch MPS can expose unsupported operations, and no valid same-machine benchmark
in this implementation pass established a production benefit. The canary
therefore changes only the diarization device and retains CPU unless the full
speed and QA gate passes.

## Independent Device Policy

The runtime records requested, resolved, and actual execution devices. These
terms are deliberately distinct:

- **requested** is the configuration or CLI value;
- **resolved** is the device selected before backend construction;
- **actual** is the device on which inference evidence says the backend ran;
- **fallback** records whether execution changed device and why.

| Stage | Conservative request | Apple Silicon resolution | Required actual evidence | Permitted override |
| --- | --- | --- | --- | --- |
| ASR with `mlx-whisper` | MLX backend, FP16 | MLX framework on native arm64 | reported MLX GPU/default device with Metal available | no backend fallback; fail if native MLX is unavailable |
| ASR with `faster-whisper` | `auto` or `cpu` | CUDA when genuinely available, otherwise CPU | resolved CUDA or CPU | CUDA or CPU only; CTranslate2 never uses MPS |
| forced alignment | `cpu` | CPU | CPU | explicit isolated MPS experiment only; never automatic |
| diarization | `cpu` | CPU | CPU | explicit MPS canary/override only; never automatic |
| speaker ID | `cpu` | CPU | CPU | supported non-MPS device only |

Alignment does not inherit the ASR device. Selecting MLX ASR never moves
WhisperX alignment to MPS. Selecting MPS diarization never changes alignment.
For MLX, `resolved_device=mlx` identifies the framework rather than proving GPU
execution. Inspect its recorded runtime/default device and Metal availability;
an unexpected CPU default is not valid evidence for the optimized lane.

An explicit pyannote MPS run may encounter an unsupported operation. A
recognized MPS compatibility failure may rebuild a fresh CPU pipeline and retry
once. The result must retain `requested=mps`, `resolved=mps`, `actual=cpu`,
`fallback_occurred=true`, and a structured reason. Such a run is
`mps_fell_back_to_cpu`; it is not valid MPS performance evidence. Unrecognized
backend errors propagate instead of being hidden by a generic retry.

Community-1 is a mixed workload even when its neural path uses MPS:
segmentation and embedding inference can execute on MPS while clustering,
array conversion, and post-processing remain on CPU. A top-level
`actual_device=mps` means verified MPS neural inference completed without
fallback; it does not mean every pyannote operation ran on the GPU. Inspect the
per-run `device_policy.diarization.component_devices` fields:
`segmentation`, `embedding`, `clustering_and_postprocessing`, and `overall`.
Both required neural rows must report the exact device observed from the
component's own device attribute after a successful pipeline call, with
`verification=component_device_attribute_and_successful_pipeline_call`. Missing
or partial component evidence makes the canary inconclusive. Do not treat
expected CPU clustering/post-processing as a fallback.

## Conservative Production Run

The configuration form keeps the complete topology in one reviewed file:

```bash
LTM_OFFLINE=1 uv run ltm transcribe INPUT \
  --config examples/config.apple-silicon.yaml \
  --num-speakers N \
  --out OUTPUT \
  --debug-report
```

The equivalent explicit command is:

```bash
LTM_OFFLINE=1 uv run ltm transcribe INPUT \
  --asr-backend mlx-whisper \
  --asr-mlx-model mlx-large-v3 \
  --asr-mlx-model-path models/mlx-community--whisper-large-v3-mlx \
  --asr-mlx-fp16 \
  --forced-alignment on \
  --alignment-device cpu \
  --diarization-device cpu \
  --num-speakers N \
  --model-lifecycle auto \
  --out OUTPUT \
  --debug-report
```

`INPUT`, `OUTPUT`, and `N` are operator-supplied values. Use an exact known
speaker count when one is available. Keep source media, transcripts, debug
reports, QA artifacts, model caches, and speaker material private and ignored.

`--forced-alignment on` is intentional: missing WhisperX support or a missing
alignment model is a hard failure. `--forced-alignment auto` permits a
Whisper-timestamp fallback and therefore does not prove timing-grade forced
alignment.

## Canary Workflow

The MPS diarization canary compares exactly two variants:

```text
CPU baseline:  MLX FP16 ASR + CPU WhisperX alignment + CPU pyannote
MPS candidate: MLX FP16 ASR + CPU WhisperX alignment + MPS pyannote
```

Media bytes, Git commit plus executable-source tree hash, model IDs plus
verified revisions or exact local checkpoint SHA-256 bindings, framework
package versions, canonical Torch MPS and CPU-thread environment hash, speaker
constraints, ASR, alignment, and every non-device configuration value must be
identical. Use deterministic separate output directories. Preserve failed and
inconclusive runs; never overwrite the CPU baseline with a candidate.

The minimum measurement sequence for each variant is:

1. one separately labeled cold-start run excluded from medians;
2. one unmeasured warmup excluded from medians;
3. at least three successful measured warm runs;
4. individual run manifests plus medians;
5. explicit requested/resolved/actual device and fallback evidence;
6. core stage timing from immutable `run_metadata.json`;
7. observed end-to-end timing and final peak-memory evidence from
   `completion_timing.json`;
8. alignment QA and speaker QA.

Before each first/excluded variant run, the runner explicitly clears all three
backend caches so the run is a real cold-start sample. Cold-start timing is
recorded separately and never mixed into warm medians. Warmup and measured
runs must report non-cold backend lifecycle telemetry or become inconclusive.
Prefer AC power, Low Power Mode off, and nominal thermal state. Hold
power source and low-power state constant, avoid concurrent jobs, and do not
compare runs made under materially different memory pressure. Per-run manifests
record the available power/thermal context before and after execution. A
power-source, Low Power Mode/power-mode, or recorded thermal-state change makes
the affected run inconclusive; so does serious or critical thermal pressure.

Inspect the deterministic plan before using private media or loading models:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=0 LTM_OFFLINE=1 \
  uv run ltm apple-silicon-canary INPUT \
  --out benchmark_runs/apple_silicon_canary \
  --num-speakers N \
  --config examples/config.apple-silicon.yaml \
  --roster ROSTER \
  --warmup-runs 1 \
  --measured-runs 3 \
  --alignment-model en \
  --minimum-diarization-speedup-ratio 0.10 \
  --maximum-total-time-regression-ratio 0.05 \
  --dry-run
```

`--roster` is optional; omit that line when no reviewed local roster is part of
the binding. Dry-run requires a native Apple Silicon host with available MPS,
but it does not require the media, model files, or backend packages and does not
create the output directory. Execution requires a fresh, nonexistent `--out`
path so older evidence cannot be reused.

Execute the same plan by removing `--dry-run`:

```bash
PYTORCH_ENABLE_MPS_FALLBACK=0 LTM_OFFLINE=1 \
  uv run ltm apple-silicon-canary INPUT \
  --out benchmark_runs/apple_silicon_canary \
  --num-speakers N \
  --config examples/config.apple-silicon.yaml \
  --roster ROSTER \
  --warmup-runs 1 \
  --measured-runs 3 \
  --alignment-model en \
  --minimum-diarization-speedup-ratio 0.10 \
  --maximum-total-time-regression-ratio 0.05
```

The runner enforces MLX FP16, forced CPU alignment, exact speaker count,
local/offline execution, enabled transcript QA, and CPU-versus-MPS pyannote as
the only comparison dimension. It forces deterministic `temperature=0`,
removes prompt, prefix, and glossary inputs, and forces persistent model
lifecycle for the controlled warm-run sequence. It disables agentic
diarization, re-decode, speaker
identification and identity application, and cleanup so they cannot confound
the device comparison. Backend instances are reused within each variant after
warmup, and the CPU/MPS execution order alternates by ordinal to reduce order
bias. An optional roster is schema-validated, must match the exact requested
speaker count, and contributes a content hash to the comparison binding; it
does not enable identity application.

The manual-only MLX cache is never represented as an upstream revision merely
because its directory is complete. Run evidence records
`revision_verified=false`, omits the unverified revision, and binds the exact
allowlisted local runtime files with `checkpoint_sha256`. Pullable alignment
and diarization models retain their verified immutable revisions. The runner
also binds `mlx`, `mlx-metal`, `mlx-whisper`, Torch, torchaudio, WhisperX, and
pyannote package versions and rechecks the MLX checkpoint after the canary.

Decisive evidence requires `PYTORCH_ENABLE_MPS_FALLBACK` to be unset or set to a
false value such as `0`. The command above uses `0` explicitly. The runner
refuses an enabled implicit Torch fallback because an unsupported operation
could otherwise execute on CPU while appearing to be an MPS candidate.
Keep other Torch MPS environment experiments such as fast math or alternate
Metal matrix kernels out of this baseline/candidate pair; benchmark each
separately with fresh processes and the same QA gate. The canonical runner
rejects non-empty MPS debug, allocator-watermark, fast-math, tracing, profiling,
or alternate-Metal-kernel variables. It records and binds the exact
`OMP_NUM_THREADS`, `MKL_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`, and
`OPENBLAS_NUM_THREADS` values so thread-policy drift cannot masquerade as a
device result.

Artifacts remain private under the selected output directory:

```text
canary_plan.json
runs/cpu_baseline/cold-start-001/canary_run_manifest.json
runs/cpu_baseline/warmup-001/canary_run_manifest.json
runs/cpu_baseline/measured-001/canary_run_manifest.json
runs/mps_candidate/cold-start-001/canary_run_manifest.json
runs/mps_candidate/warmup-001/canary_run_manifest.json
runs/mps_candidate/measured-001/canary_run_manifest.json
canary_evidence.json
canary_decision.json
canary_result.json
```

Additional measured manifests continue through the requested repetition count.
Each `runs/...` directory is a complete private pipeline job and contains the
normal raw, aligned, and diarized transcript plus QA and debug artifacts. Only
the top-level canary plan, evidence, decision, and result files and the nested
`canary_run_manifest.json` files are transcript-text-free summaries. Never
publish or copy the complete canary output tree as benchmark evidence. Every
failed or inconclusive run remains in the evidence. Do not substitute a one-off
`benchmark-corpus` matrix; it does not by itself provide this binding, warmup,
repetition, actual-device, and promotion contract.

Within each complete job, `run_metadata.json` declares
`timing_scope: pipeline_core.v1`. It is the immutable core record used by
downstream identity binding, so it is not rewritten to add work that happens
after its publication. Its real-time factor and `pipeline_core` row therefore
do not represent the full observed job. `completion_timing.json` uses schema
`completion_timing.v1` and supplies the canary's total duration and final
resource evidence. The measurement starts at pipeline invocation; invocation
setup is named separately and model/cache auditing is included in timed
preflight. Its required `invocation_setup` duration names the pre-core work,
and `run_metadata_sha256` binds the immutable `run_metadata.json` bytes. Its
scope,
`through_debug_publish_before_completion_artifact.v1`, includes metadata
publication, optional identity resolution, and optional debug-report
publication. It explicitly excludes writing itself and the final completion
progress callback so the measurement does not claim to time its own
serialization.

### MPS Diarization Evidence Gate

The initial automatic-promotion threshold is a configurable minimum 10% median
diarization-stage improvement. The default maximum end-to-end regression is 5%;
the default QA tolerances permit no regression. Passing the speed threshold
alone is insufficient. Promotion additionally requires:

- identical comparison bindings and CPU alignment throughout;
- actual MPS execution with no CPU fallback;
- synchronized MPS timing boundaries;
- reliable completion of all required candidate runs;
- no material median end-to-end time regression and no individual candidate
  warm run above the worst baseline warm-run envelope plus the configured
  regression allowance;
- no increase beyond accepted tolerance in missing or non-monotonic timestamps;
- no regression in forced-alignment coverage;
- no increase in estimated/interpolated timing;
- no regression in uncertain or unassigned speaker rates;
- no regression in speaker flapping;
- acceptable requested, detected, and validated speaker counts;
- no new Critical or Major QA defect.

The gate produces exactly one of:

```text
cpu_retained
mps_promoted
mps_explicit_override
benchmark_inconclusive
mps_failed
mps_fell_back_to_cpu
qa_regression
insufficient_evidence
```

`mps_promoted` records automatic-promotion eligibility for the bound
machine/configuration; it does not silently rewrite production configuration.
The operator must still explicitly select MPS after reviewing the private
decision artifact. `mps_explicit_override` records an operator experiment; it
does not change the default. Inconclusive, failed, fallback, or QA-regressing
results retain CPU.

Pass `--explicit-mps-override` only when the operator intentionally wants the
decision artifact to record the MPS lane as an operator experiment rather than
automatic promotion. Hard CPU-alignment, actual-device, fallback, and candidate
completion checks still apply; any failed speed, binding, or QA criteria remain
in the decision reasons. The outcome is not automatic-promotion eligibility and
does not alter the production default.

## QA Interpretation

Review `run_metadata.json`, `completion_timing.json`,
`alignment_report.json`, `qa_report.json`,
`speaker_validation_report.json`, and the debug report without publishing
transcript content. A canary should compare stable counts and rates, including:

- forced-aligned, Whisper-timestamp, estimated/interpolated, and missing words;
- timestamp coverage, missing timestamps, missing alignment segments, and
  non-monotonic timestamps;
- requested, detected, and validated speaker counts;
- assigned, unassigned, and uncertain word counts and rates;
- overlap, excessively short turns, and speaker-flapping rate;
- Critical and Major QA defect identities;
- requested, resolved, and actual device plus fallback reason;
- preprocessing, model-load, ASR, alignment, and diarization core timings;
- observed end-to-end timing plus final resource and peak-memory evidence.

Each completed run manifest also records the processing timestamp and SHA-256
of exactly these seven private evidence artifacts:
`completion_timing.json`, `run_metadata.json`, `transcript.raw.json`,
`alignment_report.json`, `diarization.json`, `qa_report.json`, and
`speaker_validation_report.json`. Missing, extra, duplicate, malformed, or
unbound evidence makes a purported completed run invalid. This binds the
private timing and QA inputs without copying their transcript text into canary
summaries. The canary always takes total timing and peak memory from
`completion_timing.json`, verifies its internal run-metadata SHA-256 binding,
and does not fall back to the core metadata contract.

Estimated timing is never forced-alignment success. A successful process exit
with an MPS-to-CPU fallback is not a successful MPS canary. Rough SRT or
auto-caption references can support relative review but are not timing or
speaker ground truth.

## Unified-Memory And Performance Discipline

Apple Silicon CPU and GPU share unified memory. Optimize end-to-end wall time
and peak pressure, not an isolated kernel:

- run baseline and candidate serially;
- close unrelated memory-heavy applications;
- keep the machine on the same power source and low-power state;
- preserve cold/model-load time separately from measured warm inference;
- avoid re-decoding or normalizing the same audio merely for a device change;
- compare canonical-audio reuse and every stage timing, not just diarization;
- restart a process between variants when isolation is required;
- compare RSS/process peaks and the separately labeled MLX/Torch allocator
  counters; these overlap under unified memory and must never be summed.

ASR CPU thread and worker controls apply to faster-whisper/CTranslate2, not MLX.
Leave them unset for the MLX topology. Machine-tuned M3 examples are benchmark
starting points only; validate them on the same media before adoption. More
workers can increase contention and unified-memory pressure.

Keep the production MLX precision fixed at FP16. Run alternate-precision
experiments in fresh processes so library-managed model caches cannot reuse
state loaded for a different precision.

`runtime.model_lifecycle` and `--model-lifecycle` expose three deliberate
residency policies:

- `auto` is the default: batch orchestration reuses backend/model instances for
  warm throughput, while a one-shot CLI process naturally ends after the job;
- `persistent` explicitly requests the same warm-reuse behavior for batch or
  caller-managed backend instances;
- `per-job` constructs fresh batch backends and, after each job, drops
  backend-owned model/audio caches, runs garbage collection, and clears already
  loaded MLX and Torch MPS allocator caches.

Use `per-job` only after observed retained-memory or swap pressure; its cleanup
time is recorded as `model_release` and it intentionally trades throughput for
lower residency. Auralign does not run accelerator-cache cleanup after every
operation under `auto` or `persistent`.

If a run stalls, swaps heavily, or is killed under memory pressure, reduce
concurrency, use a shorter representative canary, restore the CPU diarization
baseline, and inspect stage snapshots before changing model precision or QA
policy. Do not disable safety checks or force unsupported MPS operations to
obtain a result.

## Long Or Problem-File Opt-Ins

Whole-file MLX ASR is the default. VAD-windowed MLX and targeted re-decode are
bounded repair tools, not global performance defaults.

For an unusually long or degraded file, enable VAD windows explicitly:

```bash
LTM_OFFLINE=1 uv run ltm transcribe INPUT \
  --config examples/config.apple-silicon.yaml \
  --num-speakers N \
  --asr-mlx-vad-windowed \
  --asr-mlx-vad-window-seconds 30 \
  --asr-mlx-vad-window-padding-seconds 0 \
  --out OUTPUT \
  --debug-report
```

The mode uses local `vad.json` regions, restores window timestamps to the
canonical timeline, and must still use the same local MLX model. Long continuous
speech regions use bounded overlapping decode context (one second by default,
capped to one quarter of a short configured window). Each decoded word is
assigned by timestamp midpoint to exactly one non-overlapping ownership
interval, so boundary context is not emitted twice. `vad_windows`,
`chunk_payloads`, `vad_provenance`, and `vad_window_qa` retain decode bounds,
ownership bounds, VAD sources/parameters, and context-drop counts. Automatic
whole-file double transcription is deliberately not performed; compare
coverage and timeline integrity against a separate whole-file ASR job when
validating this opt-in on representative media.

The window duration has a one-second minimum so millisecond-scale values cannot
collapse the rounded ownership intervals recorded in artifacts.

For bounded suspicious regions, generate candidates without rewriting the
canonical transcript:

```bash
LTM_OFFLINE=1 uv run ltm transcribe INPUT \
  --config examples/config.apple-silicon.yaml \
  --num-speakers N \
  --targeted-redecode low-confidence \
  --redecode-max-spans 5 \
  --redecode-pad-seconds 1 \
  --redecode-beam-size 5 \
  --redecode-temperature 0 \
  --write-redecode-report \
  --out OUTPUT \
  --debug-report
```

Review `transcript.redecode_candidates.json` and `redecode_report.json`.
Candidate text is never silently merged into the final transcript. Each bounded
candidate binds the canonical audio, extracted window, first-pass and repair
configuration, backend, model revision or checkpoint, device, reason codes,
coordinate systems, and text hashes. The policy permits one attempt per span,
caps span count and padding, and prohibits recursive repair; these limits are
recorded in the report.

## Recovery And Rollback

To return an experiment to the conservative topology, rerun into a new output
directory with:

```text
--alignment-device cpu
--diarization-device cpu
```

Do not reuse a partially initialized MPS pipeline as the CPU baseline and do
not change devices inside an acceptance run. If MLX itself is unavailable,
stop with the actionable MLX diagnostic. Selecting `mlx-whisper` never silently
falls back to faster-whisper.

Faster-whisper remains a separately configured local backend for portability
and the strict CPU release lane. Its `auto` device resolves to CUDA when
available and otherwise CPU; it never routes CTranslate2 through MPS.

## Known Limits

- No MLX or MPS speedup is established without real same-machine measurements.
- The current MLX and Torch MPS backends do not establish Neural Engine use;
  do not label this topology as ANE-accelerated.
- Torch reporting MPS available does not prove every pyannote operation is
  supported or fast.
- Forced-alignment MPS is outside the normal canary and never auto-promoted.
- Community-1 remains subject to its gated terms and exact cache-integrity gate.
- MLX model acquisition is operator-managed; Auralign validates/imports local
  files but does not pull them.
- Synthetic and mocked tests validate orchestration and policy, not acoustic
  accuracy or production readiness.
- Representative private media must remain local and outside the repository.
