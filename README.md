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

- FFmpeg inspection and canonical-audio preparation with explicit audio-stream selection
- four verified faster-whisper/CTranslate2 ASR choices and twelve verified
  MLX/Apple-Silicon ASR choices, all available through exact local packs
- actual WhisperX forced alignment; non-strict configurations report any timestamp fallback
- local pyannote diarization and deterministic candidate scoring
- word-to-speaker fusion, QA, targeted re-decode candidates, and speaker checks
- local speaker enrollment and conservative profile matching
- deterministic offline identity review, approved/safe apply, and SRT-first
  derived named-speaker exports
- opt-in, fail-closed punctuation and capitalization through either an explicit
  OpenAI-compatible endpoint or Apple's on-device system model in the supported
  macOS 26+ source/headless lane
- an optional/required exact-local SaT sentence-boundary proposal source in
  the source/headless lane, with no acquisition path and explicit abstention or
  fail-closed behavior; bounded exact-model runtime smoke accepted
- SRT, VTT, JSON, JSONL, Markdown, text, CSV, and RTTM exports
- native SwiftUI macOS workbench with source-development and standalone private-release lanes
- batch execution, private-corpus benchmarking, and a localhost FastAPI service
- model and dataset cache planning, auditing, verification, and offline checks

Real ASR, alignment, diarization, and speaker matching require their optional
dependencies and local model assets. Automated tests use synthetic or mocked
inputs and do not download models or private media.

## Source Development Requirements

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

Source-runtime installation entry points are `install-macos.sh` for macOS 14+ arm64
and `install-windows.ps1` for Windows x64. They consume the release wheel and
lock-derived, hash-pinned requirements; Python, FFmpeg, CUDA, cuDNN, and the
Visual C++ runtime are host prerequisites, not bundled payloads.

The source checkout also contains the native SwiftUI workbench under `macos/`.
Debug builds use the repository's reviewed `.venv`. The separately governed
private standalone PKG embeds the pinned arm64 Python 3.11 engine/runtime and
native media closure inside `Auralign.app`, so an installed user does not need
Python, Homebrew, Git, `uv`, Xcode, Command Line Tools, a source checkout, a
virtual environment, or shell configuration.

The current 0.3.2 release contract reuses the separate root-owned bootstrap
component, `com.auralign.studio.models.bootstrap.v2` version `2.0.0`, below
`/Library/Application Support/com.auralign.studio/Models/`. It contains only
the exact Community-1 diarization, English alignment, and ECAPA speaker models
plus deterministic corpus-free English Punkt resources. The app bundle remains
free of model weights.

ASR weights are too large for the private release host's per-asset limit, so
the signed application completes setup through a native, nonmodal model
manager. It downloads one of sixteen owner-cleared, revision-pinned public ASR
packs from URLs sealed in the app; verifies every path, size, SHA-256, license,
notice, provenance record, CycloneDX SBOM, and completion record; and
atomically publishes it under
`~/Library/Application Support/com.auralign.studio/Models/Packs/<pack-id>/<version>/`.
There are no Installer network scripts and these public packs require no token.
A recipient needs one connected setup pass unless the selected exact pack is
already installed or imported. Normal processing is strictly local and offline
only after that pack verifies.

Mutable user state remains separately under the user's Library. No compiled
app, PKG, model weight, model cache, or token is included in the source
wheel/sdist or Git repository. See the
[native macOS GUI runbook](docs/macos-gui.md) and
[private 0.3.2 PKG release runbook](docs/release/macos-v0.3.2-private-pkg.md).

## Source-Development Local Cache Setup

The private GitHub repository contains verified pointers, immutable revisions,
compliance metadata, and setup commands—not model weights or gated-model
rights. This section is for source development and maintainer-controlled model
preparation. Each operator acquires models under upstream terms; weights,
Hugging Face caches, tokens, and audit output remain local and ignored. It is
not the recipient path for the 0.3.2 product PKG.

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
```

> **Community-1 evidence:** the pinned model card and official repository API
> declare CC-BY-4.0, the pinned tree has no standalone `LICENSE`, and the
> Hugging Face gate is an acquisition/contact mechanism. A direct upstream pull
> still requires completing that flow and using a read token. Matthew Penkala's
> 2026-07-25 project decision narrowly clears the exact attributed bytes for the
> private 0.3.2 model component; it is not a legal opinion or a change to
> upstream terms.

Only after the committed digest contract is complete, acquire and verify the
exact strict selection:

```bash
uv run ltm models pull \
  --asr large-v3 --diarization community-1 --alignment en \
  --speaker-id ecapa --cache-dir models
unset LTM_HF_TOKEN HF_TOKEN
uv run ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en \
  --speaker-id ecapa \
  --cache-dir models
LTM_OFFLINE=1 uv run ltm doctor --startup \
  --config examples/config.strict-offline.yaml
```

Pyannote Community-1 is gated. Each operator must personally accept its Hugging
Face acquisition/contact flow and supply a read-scoped `HF_TOKEN` or
`LTM_HF_TOKEN` only in the local shell for a direct upstream pull; never commit,
echo, log, or pass it as a CLI argument.
The combined model pull checks gated access before downloading unrelated assets
so it does not leave a half-prepared cache. Each pinned pull writes a local
`.ltm-model.json` v2 provenance marker with file hashes; verification rereads
every allowlisted runtime file and fails when bytes, revision, or repository
manifest identity differ.

The 0.3.2 product PKG never contains that private marker, raw cache metadata,
or a token. Its root-owned bootstrap component and each per-user ASR pack use
deterministic privacy-safe manifest/verification/provenance/completion records,
complete license texts and notices, CycloneDX SBOMs, and the same exact file
digests. Acquisition and build tools remain maintainer-only.

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
before preparing a maintainer/source-development machine.

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

Before starting inference, inspect the effective configuration with the same
flags plus `--print-effective-config`. This prints a privacy-safe JSON
projection and a digest of the complete configuration, then exits without
creating outputs. It does not establish model, media, or device readiness.
Environment variables override YAML; explicitly supplied CLI flags override
both. See [configuration preview](docs/configuration-preview.md).

For multi-stream media, `--audio-stream-index N` selects the absolute zero-based
FFprobe stream index. The default consistently inspects and decodes the first
audio stream. Select and audition the microphone separately; see
[media ingestion](docs/media-ingestion.md).

Normal 0.3.2 presets deliberately select tested local packs:

| Preset | ASR model | Intended tradeoff |
| --- | --- | --- |
| `max` | `large-v3` | highest-quality general-purpose CTranslate2 path |
| `balanced` | `turbo` | faster multilingual balance |
| `fast` | `distil-large-v3.5` | fastest normal English path |
| `cpu` | `turbo` | CPU-focused low-latency path |

The strict `community-1`, `en`, and `ecapa` assets remain installed for
diarization, forced alignment, and speaker embedding. All four CTranslate2 and
all twelve MLX entries have exact immutable revisions, file/size/SHA-256
contracts, license and redistribution records, local real-inference evidence,
and versioned pack identities. The four entries above are production preset
choices; `distil-large-v3` and the twelve MLX models are truthful optional
advanced choices. No production-visible selector is pointer-only.

The optional faster-whisper Silero asset is not part of the verified model
component. Normal CLI/bridge validation rejects `asr.vad_filter: true`, and the
standalone GUI does not offer it as an enabled product choice. Auralign's local
model-free EnergyVAD remains active.

The canonical job keeps raw and derived stages separate. Important artifacts
include:

- `canonical.wav`, `audio_metadata.json`, and `vad.json`
- `transcript.raw.json`, `transcript.aligned.json`, and
  `alignment_report.json`
- `diarization.json`, `exclusive_diarization.json`, and speaker reports
- `transcript.json`, `transcript.srt`, `transcript.vtt`, and review sidecars
- optional `semantic_cleanup_plan.json`, `presentation_plan.json`, and
  `transcript.cleaned.*` derived clean-transcript artifacts
- `qa_report.json`, `run_metadata.json`, `completion_timing.json`, and optional
  `debug_report.json`

Agentic diarization, re-decode, speaker matching, and local cleanup write
separate artifacts; they do not overwrite raw ASR or diarization. The opt-in
`adaptive-safe` re-decode policy spends a wider beam only on QA-flagged spans
and publishes any accepted result as a separate derived `adaptive/` lane; see
[accuracy](docs/accuracy.md#adaptive-safe-re-decode-opt-in). See
[architecture](docs/architecture.md), [alignment](docs/alignment.md), and
[diarization](docs/diarization.md) for the stage contracts.
The canonical transcript remains immutable. The default clean-transcript
profile may remove only explicitly allowlisted, high-confidence speech
artifacts through a source-span-bound semantic plan; punctuation and casing are
then selected by a separate global presentation plan. Every retained or removed
word remains traceable to canonical timing, alignment, speaker, and hash
evidence. The optional `strict-verbatim-v1` profile disables lexical cleanup.
Neither profile permits opaque free-form rewriting.

`--preset max` selects the highest-accuracy transcription configuration; it
does not, by itself, promise that every opt-in derived stage ran. Use the
explicit full contract when that is the intent:

```bash
LTM_OFFLINE=1 uv run ltm transcribe INPUT \
  --preset max \
  --entire-pipeline \
  --cleanup apple-foundation \
  --cleanup-scope punctuation \
  --cleanup-proper-noun Natalie \
  --cleanup-proper-noun Michael \
  --out outputs/fresh-job
```

`--entire-pipeline` requires `max`, an enabled local cleanup backend, and a
complete cleanup pass. It defaults to `semantically-faithful-clean-v1` while
honoring an explicit stricter `strict-verbatim-v1` request. Backend
unavailability, malformed or unsafe output, untraceable lexical drift,
projection failure, or even one fallback window fails the job rather than
allowing a broadly worded completion claim. The Apple backend uses the
operating system's on-device `SystemLanguageModel`; its internal model revision
is Apple-managed and opaque, so the cleanup report records the SDK version,
OS/build, canonical system-model identifier, locality, and availability rather
than inventing a weight revision.

When `local-llm` is the required backend, `--cleanup-model` must name the exact
locally served model. Auralign rejects an omitted or blank model instead of
accepting an endpoint-selected default that cannot be identified in the
cleanup receipt.

The Apple backend is an explicit source/headless option, not part of the
generic `full` extra or the macOS 14+ standalone application. It requires an
Apple Intelligence-compatible Apple Silicon Mac running macOS 26+, Xcode 26+
with its agreement accepted, and Apple Intelligence enabled. The upstream SDK
accepts Python 3.10+, while Auralign's supported source environment remains
Python 3.11 or 3.12:

```bash
uv sync --extra cleanup-apple --extra dev --no-python-downloads
```

The pinned SDK's native library targets macOS 26 and is therefore deliberately
excluded from the standalone runtime. On unsupported hosts, select another
explicit local cleanup backend; required cleanup still fails closed rather
than silently downgrading.

The independently configured SaT proposal interface is also source/headless-only.
Install it with `--extra presentation-sat`, then supply the exact pinned local
`sat-3l-sm` model and XLM-R tokenizer directories. Auralign never downloads
either asset, verifies every allowed file by size and SHA-256 before importing
the backend, and uses its probabilities only as weighted sentence-boundary
evidence under the deterministic global presentation decoder. Optional mode
records a reason-coded abstention; required mode fails closed. SaT, its model,
and its tokenizer are excluded from `full`, all product model packs, and the
macOS 14+ standalone app/PKG.

Polished manifests retain the canonical v2 SaT receipt and its matching
generic proposal-source receipt. Receipt validation rebinds the exact pins,
configuration, clean-word count, eight-class proposal coverage, and canonical
hash. A zero signal weight still verifies configured assets twice, but skips
backend inference and decoder participation so it is observably identical to
SaT-off.

The adapter and exact file contracts have static/API and fake-backend test
coverage plus retained bounded live smokes. On macOS 26.5 arm64 with Python
3.11.13, the required boundary-only adapter ran with Hugging Face and
Transformers forced offline over 16 synthetic words, emitted 128
punctuation-neutral proposals, and recorded no runtime network access. Cold
elapsed time was 11.9414556250 seconds and maximum RSS was 2,151,104,512 bytes.
The canonical receipt SHA-256 is
`a2718924ee45be86ba63b313904edada3e3a2fc2709618d692d868217903228f`;
the ignored evidence is under
`outputs/evaluations/2026-08-16-sat-boundary-neutral-smoke/`. This proves exact
runtime-path and receipt integrity only. A second scale smoke processed 3,068
synthetic words into 24,544 punctuation-neutral proposals in 6.3970266250
seconds (479.597816 words/second) with 2,219,900,928 bytes maximum RSS and no
runtime network access; receipt SHA-256
`9db53b1d16a9a25f97175b68f0f23621bbabc5b3c893b7a63ded835847e7dc69`.
The earlier 2026-08-15 receipts are preserved as superseded evidence of a
rejected NONE/PERIOD projection and are not current production evidence.
Neither current smoke establishes boundary quality or superiority.

No token-level punctuation/case checkpoint was adopted after the bounded
2026-08-15 bakeoff. Source punctuation, deterministic grammar/timing evidence,
SaT boundaries when explicitly configured, and the single overlap-reconciled
distribution from local Apple or OpenAI-compatible proposal windows remain
separate signals. The bounded global length prior cannot independently create
a strong stop, and required cleanup also fails closed on structural fragments,
locally adverse added stops, sentence-start casing defects, missing questions,
or materially under-punctuated long turns. Canonical
artifacts, hash-bound plans, validation reports, and acceptance receipts—not
any single model output—govern derived transcript publication.

For a completed canonical job, `ltm polish-job` reuses the hash-bound ASR,
alignment, diarization, and identity evidence and reruns only semantic cleanup,
presentation decoding, and export composition. It requires a fresh output
directory, publishes a manifest last, and never overwrites the source job:

```bash
uv run ltm polish-job outputs/completed-job \
  --cleanup apple-foundation \
  --cleanup-proper-noun Natalie \
  --cleanup-proper-noun Michael \
  --out outputs/completed-job-polished
```

`run_metadata.json` is the immutable core-run record and declares
`timing_scope: pipeline_core.v1`. Its real-time factor and `pipeline_core`
duration stop before metadata publication and later derived work.
`completion_timing.json` separately records the observed end-to-end duration
from invocation setup through model audit, pipeline work, metadata, identity,
and optional debug-report publication. Its required `invocation_setup` duration
names work before the immutable core begins, and `run_metadata_sha256` binds
the exact `run_metadata.json` bytes. Its own publication and the final progress
callback are listed as excluded operations.

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
configured endpoint; cleanup outputs remain sensitive, while the diff report
records counts and reason codes without transcript previews. See
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
Native macOS arm64 setup and performance validation are covered in the
[Apple Silicon runbook](docs/apple-silicon.md).
