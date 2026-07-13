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
- `examples/config.strict-offline.yaml`: the exact ASR, diarization, forced-
  alignment, and ECAPA selection used for strict offline startup verification;
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
| Speaker embedding / `ecapa` | [`speechbrain/spkrec-ecapa-voxceleb`](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) at `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286` | `models/speechbrain--spkrec-ecapa-voxceleb/` | `Apache-2.0`; `verified`; not gated | `approved`; installed separately and never bundled by Auralign |

For these entries, the source URL and terms URL are the linked upstream model
page. Upstream terms control the separately acquired assets. The exact
machine-readable dispositions remain in
`compliance/external_assets.toml`; unknown or conflicting metadata fails the
compliance gate closed.

Required-file contracts:

| Key | Required files | Accepted alternative weight files |
| --- | --- | --- |
| `large-v3` | `model.bin`, `config.json`, `tokenizer.json`, `preprocessor_config.json`, `vocabulary.json` | none |
| `community-1` | `config.yaml`, `segmentation/pytorch_model.bin`, `embedding/pytorch_model.bin`, `plda/plda.npz`, `plda/xvec_transform.npz` | none |
| `en` | `config.json`, `preprocessor_config.json`, `tokenizer_config.json`, `vocab.json`, `model.safetensors` | none |
| `ecapa` | `hyperparams.yaml`, `embedding_model.ckpt`, `classifier.ckpt`, `mean_var_norm_emb.ckpt`, `label_encoder.txt`, `config.json` | none |

An Auralign pull also writes `.ltm-model.json` beside the acquired files. That
private local v2 marker binds the cache directory to the manifest key, role,
upstream ID, immutable revision, repository-manifest digest, acquisition tool,
timestamp, and runtime-file SHA-256 values. Pullable models
are not offline-usable when the marker is missing, malformed, unsafe, or does
not exactly match the current manifest—even if expected filenames happen to
exist. Do not hand-author or edit this marker; refresh the cache with the
documented pinned pull on an authorized connected machine.

## Exact Connected Preparation And Offline Runbook

From a private source clone, first install dependencies and choose ignored
local caches:

```bash
uv sync --extra dev --extra asr --extra diarization --extra alignment \
  --extra speaker-id --group cpu --no-python-downloads
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
```

If an approved immutable pointer is intentionally awaiting its first committed
file hash, an authorized maintainer reviews exactly one revision before pull:

```bash
uv run ltm models review-digests \
  --diarization community-1 \
  --cache-dir "$LTM_MODEL_CACHE"
```

This command is permitted only after explicit acquisition authorization and
applicable gated acceptance. It downloads only allowlisted files into a locked
sibling temporary directory, returns their SHA-256 map, installs nothing, and
deletes the temporary bytes. Review and commit the missing hash before normal
acquisition; never use digest review to bypass provenance review or redistribute
model files.

After every selected entry has a complete committed digest contract:

```bash
uv run ltm models pull \
  --asr large-v3 \
  --diarization community-1 \
  --alignment en \
  --speaker-id ecapa \
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
  --speaker-id ecapa \
  --cache-dir "$LTM_MODEL_CACHE"
LTM_OFFLINE=1 uv run ltm models audit \
  --config examples/config.strict-offline.yaml
LTM_OFFLINE=1 uv run ltm doctor --startup \
  --config examples/config.strict-offline.yaml
```

`examples/config.strict-offline.yaml` explicitly selects faster-whisper
`large-v3`, pyannote Community-1, `alignment.backend: forced` with the English
CTC asset, and default-on local ECAPA. A successful lightweight/default doctor
is not a substitute for this exact four-backend startup check.

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

Set `LTM_MODEL_CACHE` to the destination and rerun the exact four-entry
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

Configured manifest pointers:

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

This list records identities and immutable revisions, not runtime readiness.
Only entries with a complete committed digest map can be pulled or verified.
The turbo and Distil faster-whisper alternatives above are therefore
pointer-only/hash-review-blocked in 0.2.0.
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

Useful strict model commands:

```bash
uv run ltm models list
uv run ltm models audit
uv run ltm models verify
uv run ltm models verify --asr large-v3 --diarization community-1 --alignment en --speaker-id ecapa
uv run ltm models prune --dry-run
uv run ltm models pull --asr large-v3
uv run ltm models pull --diarization community-1
uv run ltm models pull --speaker-id ecapa
uv run ltm models pull --alignment auto
uv run ltm models review-digests --diarization community-1
uv run ltm models import-mlx /path/to/converted-mlx-whisper --cache-dir models
```

`--all` includes required ASR and diarization entries. Add
`--include-optional` for alternative ASR, alignment, and speaker-ID entries.
Pullable entries whose repository digest contract is incomplete fail before
network access; an immutable revision and a self-written marker are not a
substitute for reviewed SHA-256 values.

The cache validator treats a model as not ready when:

- the model directory is missing
- required files are missing
- required files exist but are zero bytes
- a pullable model lacks the exact Auralign immutable-revision linkage marker
- offline mode is enabled and the required model is incomplete
- any allowlisted runtime file lacks a committed repository-reviewed SHA-256

The strict four-model lane has a complete reviewed digest contract only after
Community-1's gated `config.yaml` digest is acquired and committed by the owner.
Other configured faster-whisper alternatives remain pointer-only until their
complete runtime-file hashes are independently acquired and reviewed. They must
not be represented as operational pulls merely because an ID and immutable
revision are present.

For ASR CTranslate2 models, every allowlisted runtime file is required. For
MLX ASR models, `config.json` and either `weights.safetensors` or `weights.npz`
are required. MLX entries are audit-only: `ltm models pull` does not download
them, and transcription refuses to pass remote repo IDs to `mlx-whisper`.
Short aliases like `turbo`, `large-v3`, and `small` resolve only within the
`asr-mlx` manifest role and still require a complete local cache directory. Use
`ltm models import-mlx` to copy an already converted local MLX directory into
the canonical cache path after validating those files. Symlink imports are
prohibited because strict cache verification rejects linked content. For
pyannote community diarization, exactly the allowlisted `config.yaml`,
segmentation, embedding, and PLDA runtime files are required. For English forced alignment, Auralign
requires a CTC Wav2Vec2 ASR checkpoint with tokenizer/vocabulary files and
weights; generic Wav2Vec2 pretraining checkpoints are not alignment-ready.

## Local Bootstrap

`bootstrap.sh` and `bootstrap.ps1` are thin wrappers around `ltm
prepare-runtime`. The command performs storage and environment preflight, pulls
exactly the selected four-model runtime, generates local corpus-free English
Punkt parameters, verifies immutable revisions and runtime-file hashes, removes
token variables from the validation environment, and runs strict offline
backend startup. It never acquires datasets.

```bash
chmod +x bootstrap.sh
./bootstrap.sh
```

The wrappers do not store or print token values. Missing Community-1 access,
alignment, ECAPA, digest integrity, generated Punkt, native libraries, or
backend construction is a fatal preparation failure, never a caveat.

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

The `Status` column distinguishes the exact strict runtime from configured
pointers that intentionally fail closed until a complete digest review. A
runtime configuration requires its active ASR choice plus enabled diarization,
alignment, and speaker-ID lanes. Use explicit verification selectors for that
exact set.

| Key | Model ID | Status | Gated | Cache path |
| --- | --- | --- | --- | --- |
| `large-v3` | `Systran/faster-whisper-large-v3` | Strict ASR; digest-complete | No | `models/Systran--faster-whisper-large-v3` |
| `turbo` | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | Pointer-only; hash review blocked | No | `models/mobiuslabsgmbh--faster-whisper-large-v3-turbo` or accepted alias cache `models/dropbox-dash--faster-whisper-large-v3-turbo` |
| `distil-large-v3` | `Systran/faster-distil-whisper-large-v3` | Pointer-only; hash review blocked | No | `models/Systran--faster-distil-whisper-large-v3` |
| `distil-large-v3.5` | `distil-whisper/distil-large-v3.5-ct2` | Pointer-only; hash review blocked | No | `models/distil-whisper--distil-large-v3.5-ct2` |
| `mlx-*` | `mlx-community/whisper-*-mlx` or `mlx-community/whisper-large-v3-turbo` | Optional audit-only | No | canonical escaped model-ID path, plus accepted alias directories such as `models/mlx-turbo` |
| `community-1` | `pyannote/speaker-diarization-community-1` | Strict diarization pointer; `config.yaml` digest review blocked | Yes | `models/pyannote--speaker-diarization-community-1` |
| `auto` | `facebook/wav2vec2-base-960h` | Required in strict/full mode | No | `models/facebook--wav2vec2-base-960h` |
| `ecapa` | `speechbrain/spkrec-ecapa-voxceleb` | Required in strict/full mode | No | `models/speechbrain--spkrec-ecapa-voxceleb` |

## Offline Behavior

`LTM_OFFLINE=1` disables opportunistic model/network access. Doctor and
transcribe inspect only local files and fail fast with the exact online pull
command needed to prepare the cache. Optional cleanup may use localhost, but
offline mode rejects non-local cleanup endpoints:

```bash
LTM_OFFLINE=1 uv run ltm doctor --startup --config examples/config.strict-offline.yaml
```

After successful model pulls:

```bash
uv run ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en \
  --speaker-id ecapa \
  --cache-dir models
LTM_OFFLINE=1 uv run ltm models audit --config examples/config.strict-offline.yaml
LTM_OFFLINE=1 uv run ltm doctor --startup --config examples/config.strict-offline.yaml
```

## Gated Model Caveats

`pyannote/speaker-diarization-community-1` requires both terms acceptance and a
read token. A missing token, unaccepted terms, or denied access produces an
actionable error. The combined pull command checks gated-model preconditions
before downloading public ASR assets, avoiding accidental partial cache setup.
