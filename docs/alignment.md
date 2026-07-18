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
LTM_OFFLINE=1 uv run ltm doctor --startup --config examples/config.strict-offline.yaml
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
uv run ltm transcribe input.wav --forced-alignment on --alignment-device cpu
uv run ltm transcribe input.wav --no-alignment
```

Use `--forced-alignment on` only when missing forced-alignment support should be
a hard failure. Use `auto` only when a clearly reported Whisper-timestamp
fallback is acceptable.

Alignment device resolution is independent from ASR and diarization. On Apple
Silicon, an unset or `auto` alignment device resolves to CPU; MLX ASR never
moves WhisperX to MPS. The runtime and reports distinguish the requested,
resolved, and actual alignment device.

MPS alignment requires both explicit device selection and the separate
experimental-approval control. For the CLI, pass both flags:

```bash
uv run ltm transcribe input.wav \
  --alignment-device mps \
  --experimental-alignment-mps
```

For environment configuration, set both
`LTM_ALIGNMENT_DEVICE=mps` and
`LTM_ALIGNMENT_ALLOW_EXPERIMENTAL_MPS=1`. For YAML, set both fields:

```yaml
alignment:
  device: mps
  allow_experimental_mps: true
```

Use that paired approval only for an explicit isolated Apple/Torch experiment.
This path is outside the normal MPS diarization canary and acceptance. Complete
the same strict alignment case successfully on CPU first, keep the CPU artifacts
unchanged, and run MPS in a separate directory. Promotion would require repeated
same-machine measurements, actual-device verification, equivalent forced-
alignment coverage and provenance, no additional missing or non-monotonic
timestamps, and no end-to-end QA regression. The production default remains
CPU; see the [Apple Silicon runbook](apple-silicon.md).
