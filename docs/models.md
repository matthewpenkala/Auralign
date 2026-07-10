<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Models

Models are never downloaded during installation. Use `ltm models pull` for
explicit acquisition. Every supported acquisition is pinned to an immutable
publisher revision and carries a reviewed license disposition. `ltm models
audit` reports that compliance metadata without printing tokens.

## Canonical Runbook For Humans And AI Agents

This document is the first stop for both authorized operators and AI agents
working on model setup, inspection, troubleshooting, or code changes. Follow it
in order; do not substitute remembered model names, mutable upstream defaults,
or a successful default-preset doctor for the checked-in contract.

The authoritative records are:

- `src/local_transcript_max/config.py`: `MODEL_MANIFEST`, including exact IDs,
  immutable revisions, cache directories, and required-file contracts;
- `compliance/external_assets.toml`: license, provenance, access, acquisition,
  and redistribution dispositions;
- `examples/config.strict-offline.yaml`: the exact ASR, diarization, and forced-
  alignment selection used for strict offline doctor verification;
- this document: the canonical operator and agent sequence, safety boundaries,
  and actionable commands.

An agent must inspect those records before proposing or executing model work.
It must not download an asset simply to complete development, testing,
documentation, or repository validation. Connected acquisition requires an
explicit user instruction for that machine plus the operator's separate review
and acceptance of applicable upstream terms. When real packages or caches are
absent, report the strict offline doctor as blocked; synthetic tests prove the
contract, not real transcription readiness.

## Repository And Asset Boundary

The private GitHub repository contains model identifiers, immutable revisions,
required-file contracts, compliance metadata, and setup tooling. It does not
contain or transfer model weights, gated-model acceptance, access tokens, or a
right to redistribute any model. Dependencies and model assets are separate:
installing Auralign does not acquire either the models or the rights needed to
use them.

Every operator must review the applicable upstream source and terms, acquire
assets on an explicitly connected authorized machine, verify the resulting
local cache, and then run offline. Pyannote Community-1 requires each operator
to accept its gated conditions personally and use an appropriately scoped
token for acquisition. Repository access does not transfer that acceptance.

## Strict Production Pointer Matrix

The checked-in manifest and external-asset registry define this strict CPU
lane. The revisions below are immutable 40-character upstream commits.

| Role / key | Upstream ID and immutable revision | Expected local directory | License / access review | Acquisition / redistribution |
| --- | --- | --- | --- | --- |
| ASR / `large-v3` | [`Systran/faster-whisper-large-v3`](https://huggingface.co/Systran/faster-whisper-large-v3) at `edaa852ec7e145841d8ffdb056a99866b5f0a478` | `models/Systran--faster-whisper-large-v3/` | `MIT`; `verified`; not gated; no token required | `approved`; `permitted_with_license_terms`; never bundled by Auralign |
| Diarization / `community-1` | [`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1) at `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` | `models/pyannote--speaker-diarization-community-1/` | `CC-BY-4.0`; `verified_gated`; personal terms acceptance and read token required for acquisition | `approved_after_gated_terms`; `permitted_with_attribution_and_gated_terms`; no Auralign mirroring or bundling |
| English forced alignment / `en` (`auto`) | [`facebook/wav2vec2-base-960h`](https://huggingface.co/facebook/wav2vec2-base-960h) at `22aad52d435eb6dbaf354bdad9b0da84ce7d6156` | `models/facebook--wav2vec2-base-960h/` | `Apache-2.0`; `verified`; not gated; no token required | `approved`; `permitted_with_license_terms`; never bundled by Auralign |

For these entries, the source URL and terms URL are the linked upstream model
page. Upstream terms control the separately acquired assets. The exact
machine-readable dispositions remain in
`compliance/external_assets.toml`; unknown or conflicting metadata fails the
compliance gate closed.

Required-file contracts:

| Key | Required files | Accepted alternative weight files |
| --- | --- | --- |
| `large-v3` | `model.bin`, `config.json`, `tokenizer.json`, `preprocessor_config.json` | none |
| `community-1` | `config.yaml`, `segmentation/pytorch_model.bin`, `embedding/pytorch_model.bin`, `plda/plda.npz`, `plda/xvec_transform.npz` | none |
| `en` | `config.json`, `preprocessor_config.json`, `tokenizer_config.json`, `vocab.json` | exactly one usable `pytorch_model.bin` or `model.safetensors` |

An Auralign pull also writes `.ltm-model.json` beside the acquired files. That
private local marker binds the cache directory to the manifest key, role,
upstream ID, and immutable revision used by the explicit pull. Pullable models
are not offline-usable when the marker is missing, malformed, unsafe, or does
not exactly match the current manifest—even if expected filenames happen to
exist. Do not hand-author or edit this marker; refresh the cache with the
documented pinned pull on an authorized connected machine.

## Exact Connected Preparation And Offline Runbook

From a private source clone, first install dependencies and choose ignored
local caches:

```bash
uv sync --extra dev --extra asr --extra diarization --extra alignment \
  --group cpu --no-python-downloads
export LTM_MODEL_CACHE="$PWD/models"
export HF_HOME="$PWD/.hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
uv run ltm models list --cache-dir "$LTM_MODEL_CACHE"
uv run ltm models audit --config examples/config.strict-offline.yaml
```

Review the matrix and upstream terms before enabling network access. Then, on
the authorized connected machine only:

```bash
# Redacted example only; never save or pass a real token as a CLI argument.
export LTM_HF_TOKEN="REDACTED_USER_SUPPLIED_READ_TOKEN"
uv run ltm models pull \
  --asr large-v3 \
  --diarization community-1 \
  --alignment en \
  --cache-dir "$LTM_MODEL_CACHE"
unset LTM_HF_TOKEN HF_TOKEN
```

The pull uses only the manifest's immutable revisions. It is an explicit
connected setup operation, not runtime auto-download behavior. Verify the exact
strict selection locally, with no token required:

```bash
uv run ltm models verify \
  --asr large-v3 \
  --diarization community-1 \
  --alignment en \
  --cache-dir "$LTM_MODEL_CACHE"
LTM_OFFLINE=1 uv run ltm models audit \
  --config examples/config.strict-offline.yaml
LTM_OFFLINE=1 uv run ltm doctor \
  --config examples/config.strict-offline.yaml
```

`examples/config.strict-offline.yaml` explicitly selects faster-whisper
`large-v3`, pyannote Community-1, and `alignment.backend: forced` with the
English CTC asset. This is intentionally stricter than `config.max.yaml`, whose
alignment behavior can fall back. A successful default-preset doctor is not a
substitute for this exact forced-alignment check.

After doctor succeeds, transcription with that same configuration remains
local and offline:

```bash
LTM_OFFLINE=1 uv run ltm transcribe input.wav \
  --config examples/config.strict-offline.yaml \
  --num-speakers <N> \
  --out outputs/job_001
```

If a reviewed cache must move to another machine, first confirm that the same
operator/machine is authorized under every upstream term. A different pyannote
operator must accept the gate and acquire their own authorized copy. When a
same-operator transfer is permitted, copy the `LTM_MODEL_CACHE` directory with
metadata and dotfiles intact over an approved encrypted channel; do not copy
`.env`, tokens, shell history, the Hugging Face token store, media, or output:

```bash
rsync -a --protect-args "$LTM_MODEL_CACHE/" \
  authorized-offline-host:/approved/path/models/
```

Set `LTM_MODEL_CACHE` to the destination and rerun the exact three-entry
`models verify` and strict offline doctor there. If the transfer method omits
`.ltm-model.json`, verification must fail and the cache must not be used.

Failure behavior is deliberately actionable and closed:

- missing directory: pull the exact manifest key on an authorized connected machine;
- missing, empty, or unreadable required file: treat the cache as partial and refresh it;
- missing or mismatched `.ltm-model.json`: revision linkage is unverified; do not edit the marker;
- unknown model ID or revision override: acquisition and runtime resolution refuse it;
- missing gated acceptance/token: Community-1 acquisition stops before unrelated pulls;
- offline missing or incomplete asset: doctor and transcription fail without network access;
- missing active backend package or startup preflight: doctor reports an error and cannot pass.

Confirmed manifest entries:

- `large-v3`: `Systran/faster-whisper-large-v3`
- `turbo`: `mobiuslabsgmbh/faster-whisper-large-v3-turbo` with `dropbox-dash/faster-whisper-large-v3-turbo` accepted as a redirect/alias
- `distil-large-v3`: `Systran/faster-distil-whisper-large-v3`
- `distil-large-v3.5`: `distil-whisper/distil-large-v3.5-ct2`
- MLX audit-only family:
  - `mlx-tiny`: `mlx-community/whisper-tiny-mlx`
  - `mlx-tiny.en`: `mlx-community/whisper-tiny.en-mlx`
  - `mlx-base`: `mlx-community/whisper-base-mlx`
  - `mlx-base.en`: `mlx-community/whisper-base.en-mlx`
  - `mlx-small`: `mlx-community/whisper-small-mlx`
  - `mlx-small.en`: `mlx-community/whisper-small.en-mlx`
  - `mlx-medium`: `mlx-community/whisper-medium-mlx`
  - `mlx-medium.en`: `mlx-community/whisper-medium.en-mlx`
  - `mlx-large`: `mlx-community/whisper-large-mlx`
  - `mlx-large-v2`: `mlx-community/whisper-large-v2-mlx`
  - `mlx-large-v3`: `mlx-community/whisper-large-v3-mlx`
  - `mlx-large-v3-turbo`: `mlx-community/whisper-large-v3-turbo`
- `community-1`: `pyannote/speaker-diarization-community-1`
- `en` / `auto`: `facebook/wav2vec2-base-960h`
- `ecapa`: `speechbrain/spkrec-ecapa-voxceleb`

Do not use `Systran/faster-whisper-large-v3-turbo`; it is not a supported entry
in Auralign's model manifest. Use the listed turbo entry or accepted alias.

Pyannote community diarization is gated. Accept the Hugging Face terms and
export `HF_TOKEN` or `LTM_HF_TOKEN` before pulling it.

Only `mlx-community/whisper-large-v3-mlx` currently has verified MIT publisher
metadata. The other configured MLX conversions remain usable only from an
operator-supplied local cache for evaluation; they are not acquisition- or
redistribution-cleared and must not be described as production-cleared.

## Cache Behavior

By default, model assets are expected under `models/` using a safe directory name
derived from the model ID, for example:

- `models/Systran--faster-whisper-large-v3`
- `models/mobiuslabsgmbh--faster-whisper-large-v3-turbo`
- `models/mlx-community--whisper-large-v3-turbo`
- `models/pyannote--speaker-diarization-community-1`
- `models/facebook--wav2vec2-base-960h`
- `models/speechbrain--spkrec-ecapa-voxceleb`

`ltm models audit` reports configured model IDs, shareable cache-relative paths,
accepted aliases, accepted directory aliases, whether directories exist,
whether required files
are complete, whether immutable revision linkage is verified, whether offline
mode can use them, and whether gated/token-dependent access is required. It
also writes a token-redacted machine-local audit to
`models/model_audit.json`. That ignored file can contain local paths and should
not be shared or committed.

Useful model commands:

```bash
uv run ltm models list
uv run ltm models audit
uv run ltm models verify
uv run ltm models verify --asr large-v3 --diarization community-1 --alignment en
uv run ltm models prune --dry-run
uv run ltm models pull --all
uv run ltm models pull --all --include-optional
uv run ltm models pull --asr large-v3
uv run ltm models pull --asr large-v3-turbo
uv run ltm models pull --asr distil-large-v3
uv run ltm models pull --asr distil-large-v3.5
uv run ltm models pull --diarization community-1
uv run ltm models pull --speaker-id ecapa
uv run ltm models pull --alignment auto
uv run ltm models import-mlx /path/to/converted-mlx-whisper --cache-dir models
```

`--all` includes required ASR and diarization entries. Add
`--include-optional` for alignment and speaker-ID entries.

The cache validator treats a model as not ready when:

- the model directory is missing
- required files are missing
- required files exist but are zero bytes
- a pullable model lacks the exact Auralign immutable-revision linkage marker
- offline mode is enabled and the required model is incomplete

For ASR CTranslate2 models, `model.bin` and `config.json` are required. For
MLX ASR models, `config.json` and either `weights.safetensors` or `weights.npz`
are required. MLX entries are audit-only: `ltm models pull` does not download
them, and transcription refuses to pass remote repo IDs to `mlx-whisper`.
Short aliases like `turbo`, `large-v3`, and `small` resolve only within the
`asr-mlx` manifest role and still require a complete local cache directory. Use
`ltm models import-mlx` to copy or symlink an already converted local MLX
directory into the canonical cache path after validating those files. For
pyannote community diarization, `config.yaml` is required, along with the rest
of the snapshot downloaded by Hugging Face. For English forced alignment, Auralign
requires a CTC Wav2Vec2 ASR checkpoint with tokenizer/vocabulary files and
weights; generic Wav2Vec2 pretraining checkpoints are not alignment-ready.

## Local Bootstrap

`bootstrap.sh` prepares the project-local cache variables, logs only safe status
messages to `auralign_bootstrap.log`, runs a storage-budget preflight against
both the model cache and dataset cache, pulls required ASR cache entries, pulls
pyannote only when `HF_TOKEN` or `LTM_HF_TOKEN` is present, and tries optional
alignment and speaker-ID assets as best-effort steps. Dataset sample acquisition
is currently blocked because the configured dummy sample does not publish
adequate license metadata. The bootstrap never downloads full LibriSpeech, AMI,
or VoxCeleb corpora to the laptop by default.

```bash
chmod +x bootstrap.sh
./bootstrap.sh
```

Default bootstrap budget knobs:

```bash
LTM_BOOTSTRAP_PROFILE=runtime-samples
LTM_BOOTSTRAP_MAX_LOCAL_GB=65
LTM_BOOTSTRAP_MIN_FREE_GB=100
LTM_SAMPLE_MAX_GB=1
LTM_ALLOW_OVER_BUDGET=0
```

The script does not store tokens and does not print token values. It exits
nonzero when required public model pulls fail, or when pyannote fails after a
token was supplied. Missing pyannote token/terms and optional alignment,
speaker-ID, audit, or cloud-plan failures are logged as caveats. Dataset sample
acquisition is skipped while its provenance/license record is blocked. Treat
the exact selected `ltm models verify` command and strict configured offline
doctor above as the production-lane readiness checks.

## Dataset Policy

Datasets are not runtime requirements. The default `runtime-samples` profile
keeps local dataset storage small and records full corpora as cloud-only
research assets:

| Key | Repo ID | Policy | Approx size | Local default |
| --- | --- | --- | --- | --- |
| `librispeech-dummy` | `hf-internal-testing/librispeech_asr_dummy` | License/provenance unverified | `<1 GB` | Acquisition blocked |
| `librispeech-full` | `openslr/librispeech_asr` | Cloud-only | `124 GB` | Not downloaded locally |
| `ami-full` | `edinburghcstr/ami` | Cloud-only | `29 GB` | Not downloaded locally |
| `voxceleb-full` | `ProgramComputer/voxceleb` | Underlying voice/AV/biometric rights unresolved | `745 GB` | Reference-only; mirror blocked |

Useful dataset commands:

```bash
uv run ltm datasets list
uv run ltm datasets plan --profile runtime-samples --max-local-gb 65
uv run ltm datasets cloud-plan --remote "$LTM_DATASET_MIRROR_REMOTE"
uv run ltm datasets cloud-sync --remote "$LTM_DATASET_MIRROR_REMOTE" --yes-i-understand-bulk-transfer
```

`cloud-sync` only syncs already-local, compliance-approved cloud-mirror
directories with `rclone`. LibriSpeech and AMI may appear in a plan; VoxCeleb
is excluded and reported as blocked. The command never performs transcription,
inference, telemetry, or runtime uploads.

## Default Cache-Set Classification

The `Required` column below describes the default full manifest cache set used
by `models pull --all` and unselected `models verify`. A runtime configuration
requires only its active ASR choice plus its enabled diarization, alignment,
and speaker-ID lanes. Use explicit verification selectors for that exact set.

| Key | Model ID | Required | Gated | Cache path |
| --- | --- | --- | --- | --- |
| `large-v3` | `Systran/faster-whisper-large-v3` | Yes | No | `models/Systran--faster-whisper-large-v3` |
| `turbo` | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | Yes | No | `models/mobiuslabsgmbh--faster-whisper-large-v3-turbo` or accepted alias cache `models/dropbox-dash--faster-whisper-large-v3-turbo` |
| `distil-large-v3` | `Systran/faster-distil-whisper-large-v3` | Yes | No | `models/Systran--faster-distil-whisper-large-v3` |
| `distil-large-v3.5` | `distil-whisper/distil-large-v3.5-ct2` | Yes | No | `models/distil-whisper--distil-large-v3.5-ct2` |
| `mlx-*` | `mlx-community/whisper-*-mlx` or `mlx-community/whisper-large-v3-turbo` | Optional audit-only | No | canonical escaped model-ID path, plus accepted alias directories such as `models/mlx-turbo` |
| `community-1` | `pyannote/speaker-diarization-community-1` | Yes | Yes | `models/pyannote--speaker-diarization-community-1` |
| `auto` | `facebook/wav2vec2-base-960h` | Optional | No | `models/facebook--wav2vec2-base-960h` |
| `ecapa` | `speechbrain/spkrec-ecapa-voxceleb` | Optional | No | `models/speechbrain--spkrec-ecapa-voxceleb` |

## Offline Behavior

`LTM_OFFLINE=1` disables opportunistic model/network access. Doctor and
transcribe inspect only local files and fail fast with the exact online pull
command needed to prepare the cache. Optional cleanup may use localhost, but
offline mode rejects non-local cleanup endpoints:

```bash
LTM_OFFLINE=1 uv run ltm doctor --config examples/config.strict-offline.yaml
```

After successful model pulls:

```bash
uv run ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en \
  --cache-dir models
LTM_OFFLINE=1 uv run ltm models audit --config examples/config.strict-offline.yaml
LTM_OFFLINE=1 uv run ltm doctor --config examples/config.strict-offline.yaml
```

## Gated Model Caveats

`pyannote/speaker-diarization-community-1` requires both terms acceptance and a
read token. A missing token, unaccepted terms, or denied access produces an
actionable error. The combined pull command checks gated-model preconditions
before downloading public ASR assets, avoiding accidental partial cache setup.
