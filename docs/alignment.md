<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Forced Alignment

Auralign distinguishes forced alignment from Whisper word timestamps and
estimated timestamps. Estimated timestamps are never reported as forced
alignment.

## Model

| Key | Model ID | Required | Gated | Purpose |
| --- | --- | --- | --- | --- |
| `auto` / `en` | `facebook/wav2vec2-base-960h` | Optional | No | Local Wav2Vec2 CTC alignment support |

The alignment model is optional. The default runtime behavior is:

1. Try local forced alignment when dependencies and language support are present.
2. Fall back to Whisper word timestamps.
3. Use estimated segment-derived timestamps only as a last resort.

The English alignment asset must be a CTC ASR model with tokenizer/vocabulary
files and PyTorch or safetensors weights. Generic Wav2Vec2 pretraining
checkpoints are not considered forced-alignment ready.

## Report

Every transcription run writes `alignment_report.json`. It records:

- requested alignment backend and actual backend
- device and local model metadata when forced alignment runs
- whether fallback was used
- word counts, timed word counts, and missing timestamp counts
- alignment source counts and percentages
- word warning counts and backend warnings
- missing timestamp spans by word index only

The report deliberately omits transcript text. Use it with `qa_report.json` and
`run_metadata.json` to decide whether an export is timing-grade or should be
reviewed.

## Pull

```bash
export LTM_MODEL_CACHE="$PWD/models"
uv run ltm models pull --alignment auto --cache-dir models
uv run ltm models verify --alignment en --cache-dir models
LTM_OFFLINE=1 uv run ltm doctor --config examples/config.strict-offline.yaml
```

Expected cache path:

```text
models/facebook--wav2vec2-base-960h/
```

Strict verification also requires the `.ltm-model.json` immutable-revision
linkage marker written by the pinned Auralign pull. The exact strict doctor
configuration sets `alignment.backend: forced`; the normal `max` example can
fall back and therefore does not prove forced-alignment startup readiness.

## CLI Controls

```bash
uv run ltm transcribe input.wav --forced-alignment auto
uv run ltm transcribe input.wav --forced-alignment on --alignment-language en
uv run ltm transcribe input.wav --forced-alignment whisper
uv run ltm transcribe input.wav --forced-alignment on --alignment-device mps
uv run ltm transcribe input.wav --no-alignment
```

Use `--forced-alignment on` only when missing forced-alignment support should be
a hard failure. Use `auto` for production-friendly fallback.

`alignment.device` is optional. When unset, forced alignment inherits the ASR
device policy used by the existing pipeline. Set `--alignment-device mps`,
`LTM_ALIGNMENT_DEVICE=mps`, or `alignment.device: mps` only for explicit
Apple/Torch MPS experiments, then check `run_metadata.json` and benchmark
reports for the recorded alignment device.

These experiments are outside acceptance. Complete the same strict alignment
case successfully on CPU first, keep the CPU artifacts unchanged, and run MPS
as a separate benchmark job. Treat MPS as a candidate rather than a supported
acceleration path until repeated runs are stable and the alignment report,
timestamp coverage, fallback state, and derived artifacts are equivalent to
the CPU baseline.
