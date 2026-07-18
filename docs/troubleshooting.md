<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Troubleshooting

## `ltm doctor` says faster-whisper is missing

Install the ASR extra:

```bash
uv sync --extra asr --extra dev
```

## `ltm doctor` says mlx-whisper is missing

MLX ASR is optional and Apple Silicon-focused. Install it explicitly:

```bash
uv sync --extra dev --extra mlx --extra diarization --extra alignment \
  --extra speaker-id --group cpu --no-python-downloads
```

Then run with `--asr-backend mlx-whisper` and an existing
`--asr-mlx-model-path`.

MLX requires native macOS arm64. Check both the shell and Python process:

```bash
uname -m
sysctl -in sysctl.proc_translated 2>/dev/null || true
uv run python -c 'import platform; print(platform.machine())'
```

An x86 process or `sysctl.proc_translated=1` is running under Rosetta. Recreate
the environment with native arm64 Python; Auralign does not silently replace a
requested MLX backend with faster-whisper.

## Offline mode fails

That is expected until the model cache is prepared. Run the exact `ltm models
pull ...` command printed by doctor while online, then retry with
`LTM_OFFLINE=1`.

## Combined model pull refuses before ASR download

When `--diarization community-1` is selected and no token is present, the pull
fails before downloading public ASR assets. Accept the pyannote terms and export
`HF_TOKEN` or `LTM_HF_TOKEN`, then retry.

## Pyannote returns gated access errors

Accept the terms at Hugging Face, then export `HF_TOKEN` or `LTM_HF_TOKEN` in
your shell. Do not place tokens in committed files.

## Need a failed-run trace

Re-run transcription with `--debug-report`. The report includes a redacted
exception trace, model audit, device/compute selection, and FFmpeg metadata where
available.

Use `ltm inspect JOB_DIR` to check which expected artifacts exist before
debugging a completed or failed job directory.

## A config still selects `nemo-sortformer`

The old NeMo placeholder was never operational and is no longer a supported
CLI/config extra. Select `pyannote`, `auto`, or `off`. Reintroducing Sortformer
requires a separately implemented and tested local-assets contract.

## Bootstrap stops at the gated model preflight

This is expected when a token lacks access or Community-1 terms are not
accepted. Strict preparation fails before pulling unrelated assets; it never
skips the required model. Each operator must accept the upstream terms, expose a
read-scoped token only through `HF_TOKEN` or `LTM_HF_TOKEN`, rerun the bootstrap,
and unset both variables afterward. No persistent bootstrap log is written.

## Dataset planning reports a storage-budget blocker

The separate optional dataset planner uses laptop-safe defaults:

```bash
uv run ltm datasets plan --profile runtime-samples --max-local-gb 65 --min-free-gb 100
```

This dataset budget does not authorize model acquisition and is not a bootstrap
override. Resolve any model-cache storage preflight failure on the target volume
before rerunning `prepare-runtime`.

## Full datasets were not downloaded

That is the default. LibriSpeech, AMI, and VoxCeleb are optional research
corpora and are recorded as `cloud_only` in `datasets/dataset_manifest.json`.
Runtime transcription does not require them.

## Cloud dataset sync refuses to run

`ltm datasets cloud-sync` requires both a named `rclone` remote and the explicit
`--yes-i-understand-bulk-transfer` flag. This prevents accidental bulk transfers
of hundreds of gigabytes.

## Cleanup endpoint is rejected

Cleanup is localhost-only by default. Use `http://127.0.0.1:11434/v1` or pass
`--allow-remote-cleanup` only after accepting the privacy risk. Cleanup never
overwrites raw ASR or raw diarization.

## Speaker match says `UNKNOWN`

That is expected for missing cluster embeddings, ambiguous cosine scores, or
below-threshold matches. Use more local enrollment samples or inspect
`speaker_map.json`. Do not force names onto uncertain speakers. The current
`ltm speakers rename` command records legacy metadata only; it does not change
the matched transcript or exports.

## Identity resolver blocks apply

Start with `ltm speakers resolve-identities JOB_DIR --mode review` and inspect
the report/audit. Apply with a supplied `--known-speakers <N>` requires a strict
complete roster. `apply-approved` additionally requires an approved map whose
job/run/artifact hashes still match. Stale maps, unsafe drift, backend failure,
conflicting names, and concurrent job activity fail closed; canonical files are
left unchanged. `--machine-readable` emits a typed path-free remediation error.

## API export returns invalid format

The local API only allows known transcript formats and rejects path traversal.
Use `txt`, `md`, `srt`, `vtt`, `json`, `jsonl`, `rttm`, or `csv`.

## Model audit says a cache is partial

Delete or move the incomplete model directory only after confirming it is not a
valid local snapshot, then rerun the exact remediation command printed by
`uv run ltm models audit`. Do not edit files inside Hugging Face cache metadata
by hand.

For MLX ASR, the local directory must contain `config.json` and either
`weights.safetensors` or `weights.npz`. `ltm models pull` does not download MLX
entries; provide a converted local model directory and point
`--asr-mlx-model-path` at it, or import it into the canonical cache path:

```bash
uv run ltm models import-mlx /path/to/converted-mlx-whisper \
  --model mlx-large-v3 \
  --cache-dir models
```

The reviewed production-candidate path is
`models/mlx-community--whisper-large-v3-mlx`. The Turbo MLX conversion is
local-evaluation-only and is not the production default.

For Apple Silicon device experiments, pyannote `auto` stays on CPU unless CUDA
is available. Use `--diarization-device mps` only as an explicit same-media
canary/override. ASR `mps` is not valid for faster-whisper/CTranslate2, and
alignment is an independent CPU decision.

If a recognized pyannote MPS operation is unsupported, the backend may rebuild
a fresh CPU pipeline and retry once. Inspect requested/resolved/actual device
and the structured fallback reason. `requested=mps` with `actual=cpu` is
fallback evidence, not an MPS success. Complete the same media successfully on
CPU before any MPS experiment, keep the candidate in a separate job directory,
and do not change devices midstream to rescue a failed acceptance run.

Explicit alignment MPS use requires both `--alignment-device mps` and
`--experimental-alignment-mps`. Environment and YAML configuration likewise
require both the MPS device selection and the separate
`LTM_ALIGNMENT_ALLOW_EXPERIMENTAL_MPS=1` or
`alignment.allow_experimental_mps: true` approval. It is a separate WhisperX
experiment, never part of automatic MPS diarization promotion. See the
[Apple Silicon runbook](apple-silicon.md) for the repeated-run gate and the
conservative rollback.

## Apple Silicon runs out of unified memory or benchmark results vary

Run baseline and candidate jobs serially, close unrelated memory-heavy
applications, hold the power source and low-power state constant, and use a
short representative canary before full media. Leave faster-whisper-specific
thread/worker flags unset for MLX. Compare stage snapshots, total time, RSS, and
the separately labeled MLX/Torch allocator counters. Those counters overlap
with process RSS under unified memory and must not be summed.

Batch runs default to warm model reuse. If measured retained residency or swap
pressure is the limiting factor, test `--model-lifecycle per-job`; it releases
backend model references and already-loaded framework allocator caches between
files and records `model_release` timing. Restore `--model-lifecycle auto` for
the normal warm-throughput path. Do not use per-job cleanup merely because a
cache counter is nonzero.

Do not disable QA, force unsupported MPS operations, or mix cold-start runs into
warm medians to obtain a favorable result. Restore both
`--alignment-device cpu` and `--diarization-device cpu` when an experiment is
unstable.

## TorchCodec warns about an unsupported FFmpeg major version

TorchCodec builds may support a narrower FFmpeg major-version range than the
host installation. The pyannote backend avoids that decoder path by passing a
preloaded canonical WAV waveform through torchaudio. `ltm doctor` reports the
decoder strategy, but it does not pin, downgrade, or rewrite local
FFmpeg/TorchCodec installations. Treat the warning as a compatibility note
unless a run fails during audio decoding, then align the installed versions
using their upstream compatibility guidance.
