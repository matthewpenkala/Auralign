<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign

Auralign's transcription engine is packaged as Local Transcript Max
(`local-transcript-max`) and exposes the `ltm` command. It is a local-first,
speaker-aware pipeline for long-form audio and video with explicit model caches,
word timestamps, diarization, deterministic QA, benchmarks, and inspectable
exports.

> **Source-available license:** Auralign-owned material is licensed under the
> [PolyForm Internal Use License 1.0.0](LICENSE). It is shared-source/source-
> available software, not open source. Redistribution, sublicensing, customer
> deployment, hosted-service use, and additional commercial rights require a
> separate written agreement. External components and assets keep their own
> terms; see the [legal and compliance index](docs/legal/README.md).

Core transcription inference—ASR, alignment, diarization, and speaker
matching—is local. Network actions are explicit: reviewed model acquisition,
optional approved dataset-mirror sync, and remote transcript cleanup when
the operator supplies a non-local endpoint and `--allow-remote-cleanup`.
`LTM_OFFLINE=1` forbids non-local cleanup and supports fully local operation
after caches are prepared.

## Current Capabilities

- FFmpeg inspection and canonical-audio preparation
- faster-whisper ASR and opt-in Apple Silicon MLX ASR
- WhisperX-style forced alignment with honest timestamp fallbacks
- local pyannote diarization and deterministic candidate scoring
- word-to-speaker fusion, QA, targeted re-decode candidates, and speaker checks
- local speaker enrollment and conservative profile matching
- deterministic offline identity review, approved/safe apply, and SRT-first
  derived named-speaker exports
- SRT, VTT, JSON, JSONL, Markdown, text, CSV, and RTTM exports
- batch execution, private-corpus benchmarking, and a localhost FastAPI service
- model and dataset cache planning, auditing, verification, and offline checks

Real ASR, alignment, diarization, and speaker matching require their optional
dependencies and local model assets. Automated tests use synthetic or mocked
inputs and do not download models or private media.

## Requirements

- Python 3.11 for released runtime environments; Python 3.12 remains a portable-test target
- [`uv`](https://docs.astral.sh/uv/)
- FFmpeg and FFprobe on `PATH`
- enough local disk space for the selected models

Install the development environment and common runtime extras:

```bash
uv sync --extra dev --extra asr --extra diarization --extra alignment \
  --extra speaker-id --extra api --group cpu --no-python-downloads
uv run ltm --help
uv run ltm doctor
```

The source workspace uses mutually exclusive `cpu`, `cu126`, and `cu128`
dependency groups so `uv` can route Torch through the reviewed official index.
Use `cpu` for macOS arm64 and Windows CPU, `cu128` for Windows NVIDIA/Blackwell,
and retain `cu126` only for the secondary internal Linux lane. Never combine
accelerator groups.

Native installation entry points are `install-macos.sh` for macOS 14+ arm64
and `install-windows.ps1` for Windows x64. They consume the release wheel and
lock-derived, hash-pinned requirements; Python, FFmpeg, CUDA, cuDNN, and the
Visual C++ runtime are host prerequisites, not bundled payloads.

## Local Cache Setup

The private GitHub repository contains verified pointers, immutable revisions,
compliance metadata, and setup commands—not model weights or gated-model
rights. Each authorized operator acquires models separately under upstream
terms; weights, Hugging Face caches, tokens, and audit output remain local and
ignored.

Keep model and Hugging Face caches inside ignored project directories:

```bash
export LTM_MODEL_CACHE="$PWD/models"
export HF_HOME="$PWD/.hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
```

Inspect the manifest before acquiring assets:

```bash
uv run ltm models list
uv run ltm models audit
uv run ltm models pull --asr large-v3 --cache-dir models
uv run ltm models pull --diarization community-1 --cache-dir models
uv run ltm models pull --alignment auto --cache-dir models
uv run ltm models pull --speaker-id ecapa --cache-dir models
uv run ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en \
  --speaker-id ecapa \
  --cache-dir models
LTM_OFFLINE=1 uv run ltm doctor --startup \
  --config examples/config.strict-offline.yaml
```

Pyannote Community-1 is gated. Accept its Hugging Face terms and supply a
read-scoped `HF_TOKEN` or `LTM_HF_TOKEN` in the local shell; never commit it.
The combined model pull checks gated access before downloading unrelated assets
so it does not leave a half-prepared cache. Each pinned pull writes a local
`.ltm-model.json` v2 provenance marker with file hashes; verification rereads
every allowlisted runtime file and fails when bytes, revision, or repository
manifest identity differ.

`bootstrap.sh` and `bootstrap.ps1` are thin wrappers around the same
`ltm prepare-runtime` command. Preparation pulls exactly the four strict models,
generates corpus-free English Punkt parameters, verifies all file digests,
removes tokens from the validation environment, and runs heavyweight offline
backend startup. Alignment and ECAPA failures are fatal:

```bash
./bootstrap.sh
```

See the exact connected/offline runbook and authorized cache-transfer rules in
[model management](docs/models.md), plus [offline operation](docs/offline.md),
before preparing a production machine.

## Transcription

Run a normal high-accuracy job:

```bash
uv run ltm transcribe input.wav \
  --preset max \
  --num-speakers <N> \
  --out outputs/job_001 \
  --debug-report
```

Inspect and validate the result without printing transcript content:

```bash
uv run ltm inspect outputs/job_001
uv run ltm validate-speakers outputs/job_001 --expected-speakers <N>
```

`balanced` is the recommended first pass for routine long meetings. Escalate
selected high-stakes or QA-flagged material to `max`. Turbo, Distil, batched
faster-whisper, MLX, and explicit MPS device settings are opt-in performance
paths and should be validated against the same representative media before use.

The canonical job keeps raw and derived stages separate. Important artifacts
include:

- `canonical.wav`, `audio_metadata.json`, and `vad.json`
- `transcript.raw.json`, `transcript.aligned.json`, and
  `alignment_report.json`
- `diarization.json`, `exclusive_diarization.json`, and speaker reports
- `transcript.json`, `transcript.srt`, `transcript.vtt`, and review sidecars
- `qa_report.json`, `run_metadata.json`, and optional `debug_report.json`

Agentic diarization, re-decode, speaker matching, and local cleanup write
separate artifacts; they do not overwrite raw ASR or diarization. See
[architecture](docs/architecture.md), [alignment](docs/alignment.md), and
[diarization](docs/diarization.md) for the stage contracts.

## Speaker Identification And Identity Resolution

Current speaker matching applies a real name only when a local enrolled profile
produces a conservative, non-ambiguous match:

```bash
uv run ltm speakers enroll --name "Alice" --audio alice.wav
uv run ltm speakers match outputs/job_001
```

Ambiguous matches remain `UNKNOWN`. The offline identity resolver operates on a
completed job without rerunning ASR or changing canonical artifacts:

```bash
uv run ltm speakers resolve-identities outputs/job_001 --mode review
uv run ltm speakers resolve-identities outputs/job_001 \
  --mode apply-safe --roster roster.json --known-speakers <N>
```

`review` is the default and writes the report, candidates, deterministic
evidence ledger, and authoritative JSON attribution audit. Apply modes write a
derived `transcript.identity_mapped.srt` while preserving anonymous canonical
transcripts and RTTM. `apply-approved` additionally requires a current-job,
hash-bound approved map. Named-speaker delivery is SRT-first; Markdown and
other formats are review sidecars. See [speaker identification](docs/speaker-id.md).

`ltm speakers rename` records local metadata and does not rewrite a job. Only a
collision-safe, current-job hash-bound rename record can drive safe resolver
application; older unbound records remain metadata, not apply evidence.

## Batch, Benchmarks, and API

```bash
uv run ltm batch ./audio --preset balanced --out outputs/batch --recursive
uv run ltm benchmark outputs/job_001 --reference-text refs/transcript.txt
uv run ltm benchmark-corpus /path/to/private/media \
  --out benchmark_runs/smoke --mode run-single --presets balanced --dry-run
uv run ltm serve --host 127.0.0.1 --port 8765
```

Benchmark directories can contain private excerpts, transcripts, references,
logs, and speaker artifacts. They are ignored and must remain local. The API is
unauthenticated and loopback-only by default; non-loopback binding requires an
explicit flag and external access controls. See [benchmarking](docs/benchmarking.md)
and the [API guide](docs/api.md).

## Privacy Boundary

Never commit or publish:

- raw audio or video;
- transcripts, diarization, QA, or debug outputs;
- speaker embeddings, enrollment samples, or profiles;
- model and dataset caches;
- `.env` files, tokens, or generated logs containing local paths.

The relevant directories and artifact patterns are covered by `.gitignore`.
Offline mode rejects non-local cleanup endpoints and fails fast when required
local assets are missing. Explicit remote cleanup sends transcript text to the
configured endpoint; cleanup diff artifacts can contain short original and
cleaned text previews and must remain private. See
[security and privacy](docs/security-privacy.md).

## License, Contributions, And Security

Copyright 2026 Matthew Penkala. The controlling first-party terms are in
[`LICENSE`](LICENSE), with notices and external-component boundaries in
[`NOTICE`](NOTICE) and [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
Repository access does not transfer ownership or authorize redistribution.

Auralign does not accept unsolicited contributions. Invited contributions
require an executed confidentiality and contributor/IP agreement; see
[`CONTRIBUTING.md`](CONTRIBUTING.md). Report vulnerabilities through the private
process in [`SECURITY.md`](SECURITY.md), never a public issue or a report
containing recordings, transcripts, credentials, identities, or private output.

Container images are internal-only and non-distributable until each exact image
and its native/system dependency obligations receive separate clearance.

## Repository Structure

```text
src/local_transcript_max/   Python package and runtime implementation
tests/                      Unit and mocked integration tests
docs/                       Canonical operational and design documentation
examples/                   Generic and opt-in configuration examples
.github/workflows/          Continuous integration
bootstrap.sh                Connected-machine cache bootstrap
Dockerfile                  Container runtime image
docker-compose.yml          CPU, GPU, and local-server profiles
pyproject.toml              Package, dependency, lint, test, and type settings
uv.lock                     Locked Python dependency graph
```

Start with the [documentation index](docs/README.md) for focused guidance.

## Development and Validation

The repository is pinned to Python 3.11 through `.python-version`.

```bash
uv sync --extra dev --extra api --no-python-downloads
uv run pytest -q
uv run ruff check .
uv run mypy src/local_transcript_max
```

Tests must not require model downloads, remote services, private media, or
secrets. Docker usage is documented in [docs/docker.md](docs/docker.md), and
common failures are covered in [troubleshooting](docs/troubleshooting.md).
