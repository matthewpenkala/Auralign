<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Accuracy

Normal 0.3.0 presets map to tested exact local packs: `max` uses
`large-v3`, `balanced` and `cpu` use `turbo`, and `fast` uses the English-only
`distil-large-v3.5`. `distil-large-v3` and all twelve MLX models remain
available as optional advanced selections after their exact packs are
installed. A model choice changes quality, language coverage, speed, and
memory behavior; the GUI describes those tradeoffs and invalidates stale
readiness when the effective model changes. Denoising and vocal separation
remain opt-in because they can help or hurt ASR and diarization depending on
source audio.

The optional faster-whisper Silero VAD asset is not in the verified 0.3.0 model
component, so `asr.vad_filter: true` is rejected rather than silently
downloading or approximating that model. The normal pipeline continues to use
Auralign's local model-free EnergyVAD.

For multilingual meeting work, use `balanced` as the normal first pass when
throughput matters. Use `fast` only for English material. Escalate to `max`
for high-stakes excerpts, poor audio,
names, numbers, or any passage that QA/manual review flags as suspicious. Local
rough-reference benchmarks are useful for relative decisions, but YouTube
auto-caption rips are not ground truth and should not be used as absolute
accuracy proof.

For long files, the default ASR config disables `condition_on_previous_text` to
reduce runaway context loops in chunked transcription.

`--forced-alignment auto` attempts local forced alignment when WhisperX support
is available for the detected or configured language. WhisperX interpolation is
disabled. A source segment that cannot be completely and monotonically CTC
aligned uses its preserved Whisper/MLX word timestamps while successfully
forced-aligned source segments remain forced. If the backend cannot run or the
mixed result cannot conserve the recognized text, the pipeline falls back to
Whisper word timestamps for the complete transcript, then estimated segment
timings only as a last resort. The exported words mark the actual
`alignment_source`; fallback and estimated timestamps are never reported as
forced alignment. Strict forced mode permits no mixed-source result.

## Targeted Re-Decode

Real re-decode requires the selected local model cache and source audio.

Use:

```bash
uv run ltm transcribe input.wav \
  --targeted-redecode low-confidence \
  --redecode-max-spans 5 \
  --redecode-pad-seconds 1.0 \
  --write-redecode-report
```

Outputs:

```text
transcript.redecode_candidates.json
redecode_report.json
```

Raw ASR is preserved. Candidate text is recorded separately and is not silently
merged into the final transcript. Re-decode windows are created only for QA
flags with known timestamps; transcript-wide aggregate warnings remain in the
QA report for review and never trigger an implicit full-recording ASR retry.
Each candidate records first-pass and repair-candidate backend/model/config
bindings, canonical and window audio hashes, both timestamp coordinate systems,
selection reasons, and the one-attempt/non-recursive limits.

## Benchmarking

Benchmark reports support job directories, batch outputs, dependency-light
WER/CER, deterministic RTTM DER, and CSV, JSON, and Markdown output. DER uses a
zero-second collar, scores overlap over the union of speech regions, and finds
one global optimal one-to-one anonymous-speaker mapping. Reports include the
contract, status, and miss/false-alarm/confusion components; synthetic tests do
not establish acoustic accuracy.

```bash
uv run ltm benchmark JOB_DIR --reference-text ref.txt
uv run ltm benchmark JOB_DIR --reference-json ref.json
uv run ltm benchmark JOB_DIR --reference-rttm ref.rttm
uv run ltm benchmark batch OUT_DIR --references refs/
```

Outputs:

```text
benchmark.json
benchmark.csv
benchmark.md
```
