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
2. In `auto`, retain every complete source segment that CTC aligned successfully
   and use the original ASR word timestamps only for a failed source segment.
3. If the forced backend cannot run or its output cannot be safely reconciled,
   fall back to Whisper word timestamps for the complete transcript.
4. Use estimated segment-derived timestamps only as a last resort.

WhisperX is called once per original ASR source segment after loading the model
and waveform once. This preserves source identity when WhisperX splits one input
segment into multiple sentence-level outputs. Auralign passes
`interpolate_method: ignore`; missing acoustic evidence is never filled inside
WhisperX and then mislabeled as forced. Each source segment must preserve the
recognized text and contain complete, finite, bounded, monotonic timestamps.
WhisperX character alignments are aggregated onto Auralign's authoritative ASR
word sequence, so different WhisperX word tokenization cannot silently merge or
split recognized words. A word remains forced only when every non-whitespace
character carries a complete, valid acoustic start/end pair.

`alignment.backend: forced` remains strict. It permits only one bounded repair
when WhisperX returns no usable CTC characters for a text-identical single-word
segment no longer than 250 ms: the preserved ASR word must have finite positive
timing that overlaps that exact source interval, its timing is clipped to the
source and prior-word bounds, and it remains labeled `alignment_source: whisper`
with `forced_alignment_short_segment_repair`. Any other failed, missing,
estimated, or Whisper-timed source segment is a hard failure. `auto` may publish
a broader mixed result, but every word retains its real `alignment_source`.

The English alignment asset must be a CTC ASR model with tokenizer/vocabulary
files and PyTorch or safetensors weights. Generic Wav2Vec2 pretraining
checkpoints are not considered forced-alignment ready.

The pinned Wav2Vec2 model is loaded through an inference-only in-memory
configuration. Auralign disables SpecAugment and its time/feature masking
probabilities before model construction, moves the model to the selected
device, calls `eval()`, and fails if training mode or the training-only
`masked_spec_embed` parameter remains. The immutable cached `config.json` and
weights are never rewritten. Auralign does not broadly suppress Transformers
model-loading warnings. The former randomly initialized `masked_spec_embed`
warning was inference-loading hygiene only: the parameter is inactive in
evaluation mode and was not the cause of invalid MPS alignment timestamps.

## Report

Every transcription run writes `alignment_report.json`. It records:

- requested alignment backend and actual backend
- device and local model metadata when forced alignment runs
- whether fallback was used
- whether fallback was complete or localized
- forced, localized-fallback, and missing segment coverage with privacy-safe
  indexes, segment IDs, and reason codes
- word counts, timed word counts, and missing timestamp counts
- alignment source counts and percentages
- upstream interpolation method and explicit interpolated count, which is zero
  for the supported `ignore` contract
- inference-hygiene evidence for evaluation mode, disabled SpecAugment, zero
  mask probabilities, and absence of `masked_spec_embed`
- word warning counts and backend warnings
- missing timestamp spans by word index only
- raw-ASR-to-published timestamp displacement and a separate displacement
  summary limited to words actually changed by timing repair
- up to 100 indexed review spans for endpoint displacements of at least one
  second or published word durations of at least two seconds, with total count,
  truncation state, signed deltas and both timing coordinate pairs
- preserved review cues for finite raw ASR intervals that were negative,
  zero-duration or reversed, even if forced alignment later gives them positive
  duration; this can expose a segment-edge duplicate for source review without
  asserting that a repeated word is false

Review spans contain no word text. Their `asr_confidence` comes from the raw ASR
word; `published_confidence` retains the published word's existing score, whose
kind is explicitly `ctc_alignment_score` for forced words. These are different
model measurements, not interchangeable estimates of lexical correctness.
The existing transcript `confidence` field keeps its compatibility semantics.

A long or displaced forced word can cross a pause, omitted restart or competing
take while still satisfying lexical conservation and monotonic timing. These
thresholds are review cues, not acoustic limits: a sustained word or a correct
alignment repair can also trigger one. Listen to the surrounding source and
inspect raw ASR before using such a boundary in an edit. No word is deleted,
re-timed, re-decoded or accepted automatically by this diagnostic.

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

Use `--forced-alignment on` when anything short of complete, lexically conserved
alignment—or the narrowly marked, source-bounded short-segment repair described
above—should be a hard failure. Use `auto` only when a clearly reported broader
localized or complete Whisper-timestamp fallback is acceptable.

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
