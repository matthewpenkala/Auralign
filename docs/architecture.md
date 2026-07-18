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
12. Optionally run guarded cleanup into separate artifacts. Cleanup is
    localhost-only by default; non-local use requires explicit opt-in and is
    forbidden in offline mode.
13. Write exports, reports, diagnostics, core resource usage, and immutable run
    metadata.
14. When explicitly enabled, run identity resolution as a locked post-process
    and write only its derived report/export family.
15. Write a separate completion-timing artifact after optional debug-report
    publication, preserving the immutable core metadata contract.

## Artifact Invariants

- Canonical local audio is the shared timeline for VAD, ASR, alignment, and
  diarization.
- Default ASR consumes canonical audio. Experimental MLX VAD-windowed ASR may
  use VAD regions as local window hints but must restore canonical timestamps.
- Core ASR, alignment, diarization, and speaker-matching inference stays local.
  Optional transcript cleanup may use an explicitly approved non-local endpoint
  outside offline mode and then sends transcript text across that trust boundary.
- Raw ASR, aligned transcript, raw/exclusive diarization, fused transcript,
  speaker maps, re-decode candidates, cleanup results, and exports remain
  separate artifacts.
- Estimated timestamps are labeled as estimated and are never presented as
  successful forced alignment.
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
speaker labels, cue duration, and related behavior are controlled by the
`subtitle_export` YAML section; see the example configurations and config schema
before changing delivery defaults.

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
