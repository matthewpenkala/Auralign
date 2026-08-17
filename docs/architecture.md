<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Architecture

Local Transcript Max uses explicit configuration, typed Pydantic schemas,
backend interfaces, and artifact writers. Raw inputs and every derived stage are
kept separately so a failed or experimental step cannot silently rewrite the
evidence it consumed.

## Canonical Repository Spine

| Area | Canonical paths | Role |
| --- | --- | --- |
| Package | `src/local_transcript_max/` | Runtime implementation |
| Entrypoint | `pyproject.toml` → `local_transcript_max.cli:app` | Public `ltm` CLI |
| Orchestration | `cli.py`, `pipeline.py` | Commands and transcription flow |
| Contracts | `config.py`, `schemas.py`, `errors.py` | Configuration, artifacts, failures |
| Assets | `models.py`, `datasets.py`, `doctor.py` | Local cache management and readiness |
| Backends | `asr/`, `alignment/`, `diarization/`, `vad/` | Replaceable local processing stages |
| Derived output | `fusion/`, `qa/`, `export/`, `speaker_id/` | Speaker turns, QA, exports, matching |
| Services | `batch.py`, `benchmark/`, `server/` | Batch, evaluation, and local API |
| Verification | `tests/`, `.github/workflows/tests.yml` | Unit/mocked integration tests and CI |
| Operations | `README.md`, `docs/`, `examples/`, `bootstrap.sh` | Maintainer and operator guidance |

`config.py`, `schemas.py`, and `utils.py` are the highest-connectivity internal
modules. Changes to their public shapes should be treated as migrations and
validated across the CLI, pipeline, API, benchmarks, and tests.

## Runtime Flow

1. Load a preset, optional YAML, and environment/CLI overrides.
2. Resolve offline behavior, devices, and required local model paths.
3. Inspect and normalize media with FFmpeg.
4. Generate local VAD metadata.
5. Run the selected ASR backend and preserve raw output.
6. Align words, recording the real backend and any fallback source.
7. Run the selected diarization backend.
8. Optionally score deterministic diarization candidates.
9. Assign words to speakers and build speaker turns.
10. Run deterministic QA.
11. Optionally write re-decode candidates and conservative speaker matches.
12. Optionally run guarded cleanup into separate artifacts. Cleanup may use a
    guarded HTTP endpoint or Apple's in-process on-device system model. HTTP is
    localhost-only by default; non-local use requires explicit opt-in and is
    forbidden in offline mode.
13. Write exports, reports, diagnostics, core resource usage, and immutable run
    metadata.
14. When explicitly enabled, run identity resolution as a locked post-process
    and write only its derived report/export family.
15. When accepted complete cleanup and identity mapping are both enabled,
    compose them into one hash-bound `polished/` derived family. The canonical
    anonymous and aligned evidence remains unchanged.
16. Write a separate completion-timing artifact after optional debug-report
    publication, preserving the immutable core metadata contract.

## Artifact Invariants

- Canonical local audio is the shared timeline for VAD, ASR, alignment, and
  diarization.
- Supported CTranslate2 and MLX ASR backends consume canonical audio from an
  exact verified local pack. Neither backend resolves a floating repository ID
  or downloads during transcription.
- The normal product also rejects faster-whisper's optional
  `asr.vad_filter=true` selector because the corresponding Silero asset is not
  in the verified model component. Auralign's local model-free EnergyVAD
  remains the supported canonical-audio gate.
- Core ASR, alignment, diarization, and speaker-matching inference stays local.
  Optional transcript cleanup may use an explicitly approved non-local endpoint
  outside offline mode and then sends transcript text across that trust boundary.
- Raw ASR, aligned transcript, raw/exclusive diarization, fused transcript,
  speaker maps, re-decode candidates, cleanup results, and exports remain
  separate artifacts.
- Estimated timestamps are labeled as estimated and are never presented as
  successful forced alignment.
- WhisperX operates with interpolation disabled and preserves original ASR
  source-segment identity even when its sentence tokenizer emits multiple
  aligned subsegments. Character-level CTC evidence is projected onto the
  authoritative ASR word sequence without upstream timestamp interpolation.
  `auto` may retain valid CTC segments alongside
  privacy-audited localized ASR-timestamp fallback. Strict forced mode rejects
  generic mixed-source results, but permits a marked source-bounded repair for
  a text-identical single-word segment no longer than 250 ms when WhisperX
  returns no usable CTC characters and the preserved ASR word has a finite,
  positive overlap with that exact source interval.
- Subtitle exports are rendered only after alignment, diarization, and
  speaker-turn fusion.
- Diagnostics and reports use counts, indexes, reason codes, and redacted paths
  rather than transcript excerpts wherever practical.
- CSV exporters neutralize formula-capable text and speaker cells before writing
  spreadsheet-readable artifacts.

## Output Contract

A completed transcription job writes canonical and derived artifacts into its
job directory. The normal contract includes:

```text
canonical.wav
audio_metadata.json
vad.json
transcript.raw.json
transcript.aligned.json
alignment_report.json
transcript.diarized.json
transcript.json
transcript.jsonl
transcript.md
transcript.txt
transcript.srt
transcript.vtt
transcript.rttm
diarization.json
diarization.rttm
exclusive_diarization.json
transcript.words.jsonl
transcript.segments.csv
words.csv
speaker_report.json
qa_report.json
run_metadata.json
completion_timing.json
```

Optional modes add separate sidecars such as diarization candidate/decision
reports, re-decode candidates, `speaker_map.json`,
`transcript.speaker_matched.json`, cleanup outputs, validation reports, and
`debug_report.json`. The opt-in resolver adds `speaker_identity_*` reports and
`transcript.identity_mapped.*` exports without changing canonical transcripts
or anonymous RTTM. Compatibility aliases remain part of the current file
contract and must not be removed without an explicit migration.

`run_metadata.json` declares `timing_scope: pipeline_core.v1`. Its
`pipeline_core` duration, real-time factor, and resource snapshot are immutable
core evidence and stop before the metadata is published and before optional
identity or debug-report work. `completion_timing.json` uses schema
`completion_timing.v1` to record the observed end-to-end duration, matching
real-time factor, final resource usage, invocation setup, and post-core stage
durations. Its required `invocation_setup` field names pre-core work, while
`run_metadata_sha256` binds the exact immutable `run_metadata.json` bytes.
Its `through_debug_publish_before_completion_artifact.v1` measurement scope
explicitly excludes publishing the completion artifact itself and invoking the
final completion progress callback. Benchmark readers prefer the completion
record for total time and final resources; core stage timings remain sourced
from run metadata.

SRT and VTT are rendered from fused speaker turns. Their line width, line count,
speaker labels, maximum cue duration, and related behavior are controlled by the
`subtitle_export` YAML section. Word-timed turns are partitioned globally rather
than greedily so the minimum feasible cue count can still avoid short orphan
tails and prefer pause or punctuation boundaries. The primary artifact is a
timestamped conversational transcript for human and machine comprehension, not
a broadcast caption track: defaults therefore allow 120 characters per line,
four lines, and a 30-second target maximum cue duration. The width applies to
the complete rendered line, including the speaker prefix, so first-line
wrapping and cue feasibility reserve the exact label width. An indivisible
speaker label or word that is itself wider than the configured limit remains a
truthful best-effort exception. Splitting requires complete, valid,
non-overlapping word timestamps so the exporter never fabricates timing. A turn
with unavailable timing treats the visual and duration limits as best-effort.
Pauses above 2.5 seconds are mandatory cue boundaries; shorter pauses are
graded evidence so ordinary telephone hesitations do not fragment an otherwise
coherent turn. Japanese and Chinese wrapping uses basic compact-script
character units instead of assuming whitespace-delimited words; complex
Unicode legal-break and code-switch cases remain best-effort without adding a
new segmentation dependency.
Stricter on-screen caption profiles can override the defaults in configuration;
see the example configurations and config schema before changing delivery
defaults.

The optional cleanup lane writes a derived clean transcript and never mutates
canonical aligned evidence. Its production contract has three explicit layers:

1. `semantic_cleanup_plan.json` binds every proposed lexical change to an exact
   canonical source span, speaker, timing, surface digest, allowlisted operation
   class, provenance, confidence, and semantic-safety checks. The safe default
   automatically accepts only isolated non-semantic filler removal and an
   explicitly marked immediate stutter collapse at very high thresholds.
   False-start removal, self-correction resolution, display normalization, and
   narrow grammatical correction exist as typed classes but remain disabled
   without a separately audited scorer and policy. Hedges, modality, negation,
   attribution, quantities, names, dates, meaningful repetition, and ambiguity
   are protected. `strict-verbatim-v1` selects no lexical edits.
2. Local Apple or OpenAI-compatible backends propose punctuation and casing for
   the already selected clean word stream. Every lexically valid overlapping
   window is retained for provenance and disagreement reporting, then all
   correlated windows from that one backend are reconciled into one normalized
   per-word distribution. Overlap can expose uncertainty but cannot multiply a
   backend's voting authority. Malformed, drifting, or unavailable required
   windows fail closed.
3. `presentation_plan.json` records one deterministic whole-sequence Viterbi
   decision for every clean word. Source surface, pause geometry, speaker
   continuity, boundary-scoped question evidence, proper-name casing, and the
   reconciled backend distribution are scored signals. The bounded sentence
   length prior may rank supported boundaries but cannot create a strong stop
   without local typed support. Narrow timing-bound syntactic continuations and
   sentence-initial casing are path constraints. A diarization turn and a
   subtitle cue are never assumed to be sentence boundaries. Scorers may
   abstain, and their provenance remains in the plan.

Only the semantic plan may change lexical membership, and only through its
enumerated operations; the presentation decoder has no add/delete/replace,
merge/split, or reorder operation. Clean words map reversibly to canonical word
indices, while canonical word slots, timestamps, alignment sources, and speaker
ownership remain intact. Derived SRT and VTT freeze canonical cue ownership and
timing, overlay the accepted plan without dropping punctuation or casing under
width pressure, and may omit only a cue made empty by an accepted semantic
removal. Every requested format is rendered from the same cleanup and identity
display plans.

An isolated SaT scorer adapter can provide optional or required source/headless
sentence-boundary probabilities to the production presentation decoder. It
lazily loads
`wtpsplit` only after exact local model and tokenizer directories pass complete
path, file-type, size, and SHA-256 allowlists. Character scores map to the
final character of each exact space-joined source word; timing is irrelevant.
The adapter has no acquisition or network path, preserves explicit abstention
or failure provenance, and is excluded from `full`, default presets, product
model manifests/components, and the macOS 14+ standalone closure. Accepted
v2 receipts are canonically hash-bound, typed against the exact production
pins, and retained with the generic proposal-source receipts in every polished
manifest. A zero signal weight verifies the configured assets twice but does
not load the backend or enter decoder inputs, so its plan score, decisions,
abstentions, and contributing provenance are exactly SaT-off. Accepted
static/API and fake-backend tests establish the proposal interface. A retained
2026-08-16 bounded smoke additionally establishes exact pinned-model runtime
viability: required mode, forced Hugging Face/Transformers offline operation,
16 synthetic words, 128 punctuation-neutral proposals, succeeded status, and
no runtime network access. Its 11.9414556250-second cold elapsed time and
2,151,104,512-byte maximum RSS are environment-specific. This smoke is not
evidence of boundary quality or superiority. A second scale smoke processed
3,068 synthetic words into 24,544 punctuation-neutral proposals in
6.3970266250 seconds with no runtime network access and 2,219,900,928 bytes
maximum RSS. It establishes scale-path viability, not linguistic quality. The
superseded 2026-08-15 NONE/PERIOD receipts remain rejected historical evidence.
The decoder, canonical inputs, validation, and receipts remain authoritative.

No token-level punctuation/case checkpoint is registered as a production
proposal source. The bounded felflare, FullStop, and NeMo comparison did not
clear the combined conversational-quality, question/case, provenance, and
runtime thresholds. Apple and explicitly configured local-LLM windows remain
proposal sources, not transcript authorities.

Identity export derives one effective display-label plan from both cluster and
turn decisions. Explicit mapped or `Unattributed` turn decisions win. A safely
mapped global cluster name remains displayable when an otherwise ordinary turn
contains a word-level timing uncertainty, while canonical `UNKNOWN`, no-coverage,
mixed-speaker, conflict, rejected, and instability cases stay protected. SRT
and VTT render every canonical speaker-label line, fail on unmatched or
malformed cues, and validate all non-label payload bytes. Markdown, text, JSON
display fields, and CSV use the same effective-label plan.

## Dependency and Deployment Edges

- `pyproject.toml` and `uv.lock` are the package and dependency sources of
  truth.
- The `ltm` script resolves to `local_transcript_max.cli:app`.
- `Dockerfile` installs the locked project and uses `uv run ltm` as its
  entrypoint.
- `docker-compose.yml` invokes `doctor` or the local API through that entrypoint.
- The API app is intentionally loaded dynamically as
  `local_transcript_max.server.app:app`.
- GitHub Actions installs the development and API extras before running tests,
  Ruff, and strict mypy.

## Validation Boundaries

The automated suite covers configuration, cache auditing, artifact schemas,
exports, QA, batch, benchmark, API endpoints, local cleanup guardrails,
speaker-profile metadata/matching, diarization scoring, device policy, and
pipeline orchestration with synthetic or mocked data.

Real model inference is an operational validation boundary because model
weights and private media are deliberately excluded from the repository. ASR,
actual WhisperX alignment, pyannote diarization, and ECAPA matching therefore
fail with actionable cache/dependency messages when assets are unavailable.
The former NeMo Sortformer placeholder is not a supported CLI/config backend;
pyannote or `off` are the supported diarization choices.

The standard repository gate is:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/local_transcript_max
```

## Installed macOS Trust Topology

The 0.3.2 product separates executable code, shared bootstrap resources, and
per-user ASR packs:

```text
/Applications/Auralign.app
/Library/Application Support/com.auralign.studio/Models/
  com.auralign.studio.models.bootstrap.v2/
~/Library/Application Support/com.auralign.studio/Models/Packs/
  <pack-id>/<version>/
```

The scriptless PKG installs the signed app/runtime and the root-owned bootstrap
component. The app's native model manager uses its signed catalog and
`URLSession` to obtain one of sixteen public ASR packs, verifies the complete
immutable contract in a private staging directory, and atomically publishes
only a valid pack. Runtime resolution accepts only the expected bundle or pack
identity and version; wrong revisions, missing or extra files, hash/size
mismatches, links, unsafe permissions, malformed metadata, or partial
publication remain readiness blockers. The network is never part of normal
transcription.
