<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Accuracy

`large-v3` is the max-accuracy default. Turbo and Distil profiles prioritize
speed and real-time factor. Denoising and vocal separation are opt-in because
they can help or hurt ASR and diarization depending on source audio.

For production meeting work, use `balanced` as the normal first pass when
throughput matters. Escalate to `max` for high-stakes excerpts, poor audio,
names, numbers, or any passage that QA/manual review flags as suspicious. Local
rough-reference benchmarks are useful for relative decisions, but YouTube
auto-caption rips are not ground truth and should not be used as absolute
accuracy proof.

For long files, the default ASR config disables `condition_on_previous_text` to
reduce runaway context loops in chunked transcription.

`--forced-alignment auto` attempts local forced alignment when WhisperX support
is available for the detected or configured language. If it is unavailable, the
pipeline falls back to faster-whisper word timestamps, then estimated segment
timings only as a last resort. The exported words mark the actual
`alignment_source`; estimated timestamps are never reported as forced alignment.

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
