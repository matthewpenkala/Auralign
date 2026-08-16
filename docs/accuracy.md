<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Accuracy

Normal 0.3.1 presets map to tested exact local packs: `max` uses
`large-v3`, `balanced` and `cpu` use `turbo`, and `fast` uses the English-only
`distil-large-v3.5`. `distil-large-v3` and all twelve MLX models remain
available as optional advanced selections after their exact packs are
installed. A model choice changes quality, language coverage, speed, and
memory behavior; the GUI describes those tradeoffs and invalidates stale
readiness when the effective model changes. Denoising and vocal separation
remain opt-in because they can help or hurt ASR and diarization depending on
source audio.

The optional faster-whisper Silero VAD asset is not in the verified 0.3.1 model
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

## Presentation Proposal Evidence

Punctuation, casing, and sentence boundaries are derived presentation, not ASR
ground truth. The global decoder conserves the selected lexical sequence and
combines canonical source surfaces, deterministic grammar/timing/speaker
evidence, and explicitly configured local proposal sources. Apple Foundation
Models, a local OpenAI-compatible backend, and exact-pinned SaT may propose;
none may replace canonical words or bypass validation and receipt checks.

SaT exposes an optional/required source/headless boundary proposal source. One
retained 2026-08-16 exact-model smoke accepted the required offline runtime
path: 16 synthetic words, 128 punctuation-neutral proposals, succeeded status,
no runtime network access, 11.9414556250 seconds cold elapsed time, and
2,151,104,512 bytes maximum RSS on macOS 26.5 arm64/Python 3.11.13. That is
runtime and receipt-integrity evidence, not a boundary-quality or superiority
result. A second scale smoke processed 3,068 synthetic words into 24,544
punctuation-neutral proposals in 6.3970266250 seconds (479.597816
words/second), with no runtime network access and 2,219,900,928 bytes maximum
RSS. This establishes scale-path viability only. SaT supplies boundary
probabilities—no token-level punctuation or casing—and remains outside `full`,
default presets, model packs, and the macOS 14+ standalone product. The
superseded 2026-08-15 receipts encoded an invalid NONE/PERIOD punctuation
preference and remain historical rejected evidence only.

A separate retained boundary proxy (`bounded_quality_report.json`, SHA-256
`e17c40c091a9ae10ffe6cf6eb30178f64f5357beb06b6b2ad2ef34622131673a`)
measured the same exact scorer at the neutral 0.5 threshold. On a 59-word,
ten-sentence punctuation-free synthetic fixture it reached 1.000 precision,
0.700 recall, and 0.824 F1, materially improving the no-boundary baseline but
missing three reference boundaries. On the preserved 360-word private
conversational slice it reached 0.875 precision, 1.000 recall, and 0.933 F1
against existing source punctuation as a proxy—not human ground truth—while
still producing one sub-four-word fragment, three runs over forty words, and
five predicted stops at short same-speaker cross-turn continuations. Those
mixed results are why SaT is weighted evidence inside the global decoder, not
a standalone acceptance authority.

No token-level punctuation/case checkpoint cleared the 2026-08-15 adoption
thresholds. FullStop improved synthetic boundary F1 over felflare but performed
worse than the source-preserving baseline on the bounded conversational proxy,
missed a question, supplied no case signal, and imposed a 2.26 GB/4.33 GB
payload/RSS cost. The exact NeMo DistilBERT candidate missed all synthetic
questions, produced continuation stops, and failed proper-name/acronym casing;
felflare remained below the boundary and casing gates. Treat those results as
bounded selection evidence, not a general leaderboard. Future promotion needs
an exact immutable checkpoint, license and file receipts, local-only behavior,
word-index conservation, and material quality superiority on the canonical
acceptance corpus.

`--forced-alignment auto` attempts local forced alignment when WhisperX support
is available for the detected or configured language. WhisperX interpolation is
disabled. A source segment that cannot be completely and monotonically CTC
aligned uses its preserved Whisper/MLX word timestamps while successfully
forced-aligned source segments remain forced. If the backend cannot run or the
mixed result cannot conserve the recognized text, the pipeline falls back to
Whisper word timestamps for the complete transcript, then estimated segment
timings only as a last resort. The exported words mark the actual
`alignment_source`; fallback and estimated timestamps are never reported as
forced alignment. Strict forced mode permits one narrow exception for a
single-word segment no longer than 250 ms when WhisperX returns no usable CTC
characters: a finite preserved ASR word must match the segment text and overlap
its authoritative source interval, and the published timing is clipped to that
interval and marked `forced_alignment_short_segment_repair`. Generic,
multiword, non-overlapping, text-mismatched, or unmarked mixed-source results
still fail closed.

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
