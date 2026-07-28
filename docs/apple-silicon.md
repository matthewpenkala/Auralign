<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Apple Silicon Runbook

## Supported 0.3.0 Product Topology

Auralign 0.3.0 supports macOS 14 or later on Apple Silicon. The signed
standalone application embeds Python 3.11, the Auralign engine, its locked CPU
dependency closure, MLX 0.31.2, mlx-whisper 0.4.3, and the reviewed
FFmpeg/PyAV closure. Recipients do not need Python, Xcode, Command Line Tools,
Homebrew, Git, `uv`, a checkout, `.venv`, token, compiler, package manager, or
Terminal.

The scriptless PKG installs:

```text
/Applications/Auralign.app

/Library/Application Support/com.auralign.studio/Models/
  com.auralign.studio.models.bootstrap.v1/
```

The bootstrap component is version `1.0.0`, root-owned, and read-only to
ordinary users. It contains only Community-1 diarization, English forced
alignment, ECAPA speaker embedding, and deterministic corpus-free English
Punkt resources. It contains no ASR weights or executable code.

All ASR choices are independently versioned per-user packs:

```text
~/Library/Application Support/com.auralign.studio/Models/Packs/
  <pack-id>/<version>/
```

The signed native model manager obtains public, owner-cleared pack files from
revision-bound URLs in its sealed catalog. It uses `URLSession`, stages
privately, verifies every relative path, byte count, SHA-256, license, notice,
provenance record, CycloneDX SBOM, mode, and completion record, and atomically
publishes only a complete pack. It never uses an Installer script, token, shell,
Python package manager, or floating repository reference.

Auralign can launch before an ASR pack is present, but transcription readiness
remains blocked. The user completes one connected **Complete Offline Setup**
pass unless the exact selected pack is already installed or imported. Normal
processing is local and offline after verification.

## Model And Preset Contract

All sixteen ASR entries have exact immutable revisions, complete file/size/hash
contracts, reviewed license/redistribution records, real network-denied local
inference evidence, and a versioned pack identity. Four are CTranslate2
choices; twelve use MLX:

| Backend | Product choices |
| --- | --- |
| faster-whisper/CTranslate2 | `large-v3`, `turbo`, `distil-large-v3`, `distil-large-v3.5` |
| MLX/Apple Silicon | `mlx-tiny`, `mlx-tiny.en`, `mlx-base`, `mlx-base.en`, `mlx-small`, `mlx-small.en`, `mlx-medium`, `mlx-medium.en`, `mlx-large`, `mlx-large-v2`, `mlx-large-v3`, `mlx-large-v3-turbo` |

The normal presets deliberately map to tested production choices:

| Preset | Model | Reader-facing tradeoff |
| --- | --- | --- |
| `max` | `large-v3` | highest-quality general-purpose CTranslate2 path |
| `balanced` | `turbo` | fast multilingual quality/speed balance |
| `fast` | `distil-large-v3.5` | fastest normal English-only path |
| `cpu` | `turbo` | CPU-focused low-latency path |

`distil-large-v3` and the twelve MLX entries are optional advanced selections,
not pointer-only or evaluation-only records. Changing the model or backend
invalidates readiness and requires verification of that exact effective
configuration.

The complete nineteen-entry authority—including Community-1, English
alignment, and ECAPA—is in [Models](models.md),
`src/local_transcript_max/config.py`, and
`compliance/external_assets.toml`.

## Source Development

Source development uses native arm64 Python 3.11 and the locked dependency
environment. Verify that the shell and Python process are native before
release-relevant checks:

```bash
uname -m
sysctl -in sysctl.proc_translated 2>/dev/null || true
PYTHONDONTWRITEBYTECODE=1 uv run python -B -c \
  'import platform; print(platform.machine())'
```

`uname -m` and `platform.machine()` must report `arm64`.
`sysctl.proc_translated=1` identifies Rosetta and blocks native acceptance.

The strict source-cache lane remains:

```bash
uv run ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en --speaker-id ecapa
LTM_OFFLINE=1 uv run ltm doctor --startup \
  --config examples/config.strict-offline.yaml
```

Source caches use private `.ltm-model.json` linkage records. The installed
bootstrap component and native ASR packs use deterministic public package
records instead. Neither trust contract may be substituted for the other.

## MLX And MPS Runtime Boundary

### MLX ASR

MLX is a supported 0.3.0 ASR backend on Apple Silicon when the selected exact
pack is valid. It must not:

- resolve or download a remote repository ID during readiness or transcription;
- accept an arbitrary directory because familiar filenames exist;
- silently fall back to faster-whisper;
- treat an unverified local conversion as a product pack; or
- report readiness from imports or fixtures without real local backend load.

The native catalog and Python manifest must agree on model identity, revision,
files, sizes, hashes, pack ID, pack version, and delivery. A missing, wrong,
partial, linked, mutable, or extra pack remains visibly blocked.

### Torch MPS experiments

CPU remains the release acceptance baseline for diarization, alignment, and
speaker embedding. Explicit Torch MPS experiments are separate and may not be
described as Neural Engine acceleration. Promotion requires same-media repeated
evidence, no fallback, adequate speed improvement, and no QA regression.

For a bounded explicit canary:

```bash
LTM_OFFLINE=1 ltm apple-silicon-canary \
  /path/to/private/canary.wav \
  --num-speakers 2 \
  --out benchmark_runs/apple_silicon_canary
```

Keep private media, model packs, transcripts, and generated benchmark evidence
outside Git.

## Benchmarking

Compare exact installed/local packs only after each one verifies:

```bash
LTM_OFFLINE=1 ltm benchmark-corpus "/path/to/excerpt-folder" \
  --out benchmark_runs/asr_matrix \
  --mode backend-matrix \
  --presets max,balanced,fast,cpu \
  --asr-backends faster-whisper,mlx-whisper \
  --excerpt-seconds 60 \
  --standard-depth
```

Same-media benchmarks can compare speed, peak RSS, WER/CER, timestamps, and
speaker QA. They do not replace license clearance, pack integrity, signed
payload verification, or clean-recipient acceptance.

## Release Acceptance

The exact 0.3.0 candidate must prove:

- arm64-only application and embedded native closure;
- exact bootstrap component ID/version, file sizes, SHA-256 values, modes,
  ownership, notices, license texts, CycloneDX SBOM, completion marker, and
  receipt;
- no model weights inside `Auralign.app`;
- no ASR weights in the bootstrap component;
- all sixteen catalog packs have exact signed-catalog/Python-manifest parity;
- native download staging, cancellation/retry, corruption rejection, and atomic
  publication work without Installer scripts or tokens;
- packaged-engine local inference for every production preset and bounded real
  inference evidence for every optional model;
- strict offline readiness after the selected pack is installed;
- no checkout, `.venv`, Homebrew, pyenv, `$PATH`, Xcode, Command Line Tools,
  shell-profile, compiler, or package-manager dependency;
- Finder launch, bridge handshake, lifecycle/reaping, and single-instance
  behavior;
- Developer ID, Hardened Runtime, notarization, stapling, Gatekeeper, install,
  and reinstall acceptance; and
- an honest clean-recipient limitation if a separate clean supported Mac is
  unavailable.

Synthetic and mocked tests validate orchestration and rejection behavior, not
acoustic accuracy, real backend execution, or clean-recipient readiness.
