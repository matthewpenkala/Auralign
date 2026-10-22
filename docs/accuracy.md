<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Accuracy

Normal 0.3.2 presets map to tested exact local packs: `max` uses
`large-v3`, `balanced` and `cpu` use `turbo`, and `fast` uses the English-only
`distil-large-v3.5`. `distil-large-v3` and all twelve MLX models remain
available as optional advanced selections after their exact packs are
installed. A model choice changes quality, language coverage, speed, and
memory behavior; the GUI describes those tradeoffs and invalidates stale
readiness when the effective model changes. Denoising and vocal separation
remain opt-in because they can help or hurt ASR and diarization depending on
source audio.

The optional faster-whisper Silero VAD asset is not in the verified 0.3.2 model
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

## Recognition, timing and editorial review

Forced alignment times the supplied recognized words. It cannot recover speech
the recognizer omitted. Complete forced coverage, high confidence, lexical
conservation from raw to final output, or agreement between recognizers does
not establish that all source speech was recognized. Repeated takes can be
normalized into one fluent reading; even an independent recognizer can omit a
unique passage. Preserve raw outputs and challenge disputed passages with
bounded acoustic context rather than rewriting the script into ASR evidence.

QA now excludes nonfinite, negative, zero-duration and reversed word timestamps
from usable timestamp coverage and reports their indexes as errors. It also
flags words lasting at least two seconds for contextual review. The
`review_scope` summary explicitly separates deterministic timing checks from
unestablished lexical completeness, unevaluated take selection and unperformed
perceptual review. `no_deterministic_warnings` remains a narrow statement about
these checks, not an acceptance verdict. The
[alignment report](alignment.md#report) locates large raw-to-published shifts.

For source/export comparisons, record the actual media, selected microphone,
decoded duration and coordinate origin. An export may contain an opening offset
or valid speech missing from the source transcript. Word boundaries crossing a
cut need quarantine and review, not forced mapping or automatic deletion.
Neither whole-file text agreement nor a successful decode replaces listening to
the retained interiors and joins of the actual export. Automated timing cues
do not choose takes or authorize editorial changes.

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

Targeted re-decode has two selection policies, set with
`redecode.selection_policy`:

| Policy | Default | What it does |
| --- | --- | --- |
| `candidate-only` | yes | Decodes each suspect window once and records review candidates. It never changes the transcript. |
| `adaptive-safe` | opt-in | Decodes each suspect window twice — beam 5 and beam 10 — and publishes a separate derived transcript only when the wider beam wins on independent evidence. |

### Candidate-only re-decode

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

### Adaptive-safe re-decode (opt-in)

```bash
uv run ltm transcribe input.wav \
  --targeted-redecode low-confidence \
  --redecode-selection-policy adaptive-safe \
  --redecode-beam-size 5 \
  --redecode-wide-beam-size 10
```

#### Why beam 10 is not globally better

A wider beam is not reliably better for Whisper. Beam 10 sometimes recovers
ambiguous speech, but it also costs runtime and can introduce hallucinations,
omissions, and repetition loops that beam 5 avoided. Auralign therefore never
raises the beam for the whole file. It spends the wider search only on spans QA
already flagged, and promotes the result only when independent local evidence
says the wider result is both safer and better. `MAX` is not redefined as "use a
bigger beam everywhere".

#### What the lane does

1. The ordinary full-file first pass runs at beam 5 and is never modified.
2. Typed QA evidence selects bounded suspicious spans. Overlapping spans are
   merged deterministically and bound to whole first-pass segments, so a
   replacement can never start or end mid-segment.
3. One audio window per span is extracted once and hashed. Both candidates
   decode those exact bytes.
4. The window is decoded at beam 5 (the comparison control) and beam 10 (the
   challenger). Model, revision, backend, language, task, temperature, prompts,
   VAD behaviour, thresholds, context policy, and compute type stay identical.
   Beam width is the single deliberate difference.
5. Each candidate is measured against the same audio using forced alignment,
   compression and repetition metrics, no-speech probability, timestamp
   validity, language consistency, and word-rate plausibility.
6. A lexicographic policy decides. Only the beam-10 candidate can ever be
   promoted; the windowed beam-5 candidate is a control and never replaces the
   first pass on its own.

#### Selection and abstention rules

The evaluator applies, in order:

1. **Hard invariants** on the beam-10 candidate — it must exist, produce text,
   have present, valid, monotonic timestamps inside its window, keep
   `no_speech_prob` at or below 0.6 and compression ratio at or below 2.4, keep
   the adjacent-repeat ratio at or below 0.5, keep the language of the first
   pass, hold a plausible 0.2–8.0 words per second, and reach the configured
   forced-alignment coverage floor (0.90 by default). A span covering more than
   one speaker abstains outright.
2. **No protected regression** — the candidate may not lose ground against the
   control on alignment coverage, compression ratio, repetition ratio, or
   no-speech probability beyond a small tolerance.
3. **Improvement in an approved dimension** — at least one *independent*
   (non-model-score) dimension must improve by its margin.
4. **A versioned score margin** — the uncalibrated log-probability margin must
   reach `redecode.adaptive_min_logprob_margin`.
5. **Otherwise preserve the first pass.** Ties, ambiguity, candidate failure,
   missing alignment evidence, and identical beam-5/beam-10 text all abstain.

Model probabilities, beam scores, and local score margins are **uncalibrated**.
They rank candidates. They are never presented as semantic confidence, and a
better score alone never promotes anything.

**Protected material.** When a candidate would change negation, quantities or
digits, dates and times, names and proper nouns, modality, attribution,
contractions, emphatic repetition, or false starts, the required score margin is
multiplied by `redecode.adaptive_protected_margin_multiplier` (3.0 by default)
and two independent supporting dimensions are required instead of one. A
protected difference is not automatically wrong — the first pass may be the
mistaken side — but ambiguous evidence abstains.

#### Outputs, provenance, and replay

The original transcript and every top-level artifact stay exactly as the first
pass produced them. An accepted selection is published as a separate derived
lane:

```text
adaptive/redecode_plan.json                # sealed reconciliation plan
adaptive/transcript.selected.raw.json      # derived selected ASR artifact
adaptive/transcript.selected.json          # derived aligned, diarized transcript
adaptive/qa_report.selected.json
adaptive/alignment_report.selected.json
adaptive/transcript.{txt,md,srt,vtt,rttm,json,jsonl,csv}
adaptive/transcript.adaptive_candidates.json   # sensitive candidate surfaces
adaptive/adaptive_redecode_report.json         # privacy-safe summary
adaptive/adaptive_validation_report.json
```

Publication is transactional: the lane is staged in a private sibling directory
and atomically promoted only when every artifact validates together. A failed
check publishes nothing and leaves the first pass standing.

The plan (`adaptive_redecode_plan.v1`) binds the schema and policy versions, the
producer and source revision, the beam ladder, canonical audio and per-window
audio hashes, the first-pass ASR digest and language, model, backend, and
configuration provenance, every candidate metric, every hard-gate outcome, the
selection or abstention with its reason code, each score margin tagged
calibrated or uncalibrated, failure and fallback reasons, and the disposition of
every dependent stage. It is canonically serialized and sealed with SHA-256.
SHA-256 proves artifact integrity — not semantic correctness and not
authenticity. Loading rejects unknown, stale, extended, malformed, duplicate,
non-canonical, oversized, and opaque artifacts rather than ignoring them, and
every decision can be recomputed from the bound inputs.

Candidate transcript text lives only in the sensitive
`transcript.adaptive_candidates.json`. The privacy-safe summary carries hashes,
counts, metrics, statuses, and reason codes — never candidate excerpts and never
private absolute paths.

#### Dependent stages

Alignment, word timing, word/speaker fusion, QA, and exports are rerun from the
selected ASR text. Media preparation, VAD, diarization, and speaker embeddings
are reused because their exact inputs are unchanged and they are
text-independent; every reuse decision is recorded in the plan. Cleanup and the
speaker-identity resolver also depend on the selected text and are *not*
regenerated inside the derived lane, so adaptive-safe fails closed when either
is enabled rather than publishing a partially reconciled lane.

#### Resource bounds

`redecode.max_spans`, `redecode.pad_seconds`,
`redecode.adaptive_max_span_seconds`,
`redecode.adaptive_max_total_window_seconds`, and
`redecode.adaptive_max_plan_bytes` bound span count, context padding, per-span
duration, total re-decoded audio, and serialized plan size. Each span is decoded
at most once per beam; there is no recursion and no retry. Spans beyond a limit
are recorded with their reason code and skipped without decoding.

#### Configuration validation

Adaptive-safe is rejected unless the beam ladder strictly increases, the wide
beam is at most 10, `redecode.temperature` is 0.0, a re-decode mode is set, QA
is enabled, forced alignment is enabled (`alignment.backend` `auto` or
`forced` — Whisper word timestamps come from the decode being judged and are not
independent evidence), the ASR backend is `faster-whisper` (mlx-whisper does not
support beam search), and cleanup and the identity resolver are off.

#### Default status and remaining promotion evidence

**Adaptive-safe is opt-in. It is not enabled by default in `MAX`.**

Two classes of development evidence exist so far. Neither is held-out quality
evidence.

*Synthetic policy evidence.* `scripts/evaluate_adaptive_redecode.py` runs the
real policy against the scripted, non-private fixture corpus in
`tests/fixtures/adaptive_redecode_corpus.json` and compares three arms — beam 5,
global beam 10 for evaluation only, and adaptive 5→10. That harness measures the
policy's decisions, not Whisper's accuracy, and its corpus was authored
alongside the policy.

*Real-audio development run.* The lane has also been run end to end offline on a
private local conversational recording (about 22 minutes, two speakers) with
`large-v3`, forced alignment, and pyannote diarization, alongside a beam-5
baseline arm, an evaluation-only global-beam-10 arm, and a repeat run. That run
established three things and no more: the lane runs on real audio and real
models; its sealed plan and every span decision reproduced byte-identically on
replay; and raising the beam globally is not free — the whole-file beam-10 arm
spent about 36% more ASR time and diverged from beam 5 on roughly 5% of tokens,
touching seven protected classes. On that recording the lane decoded its single
selected span at both beams and abstained on a hard gate, so it published no
derived transcript. No verbatim reference transcript exists for that material,
so **WER and CER were not measurable**; cross-arm differences are divergence, not
error. `scripts/compare_adaptive_redecode_jobs.py` produces that privacy-safe
comparison and a separate sensitive review packet for local human adjudication.
Because the material is private, the run's artifacts stay in the ignored local
evaluation tree and are never committed, uploaded, or attached to a release.

Enabling adaptive-safe by default in `MAX` additionally requires:

- a held-out, representative conversational-ASR corpus with references;
- no threshold tuning on the final evaluation set;
- measured WER/CER on that corpus;
- false-promotion analysis on real decoder output;
- human review of every materially changed span;
- runtime and peak-memory measurements on real jobs;
- an independently recorded product acceptance decision.

Until that evidence exists, adaptive-safe stays opt-in.

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
