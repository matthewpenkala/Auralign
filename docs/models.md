<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Models

Models are never downloaded during installation. Use `ltm models pull` for
explicit acquisition. Every supported acquisition is pinned to an immutable
publisher revision and carries a reviewed license disposition. `ltm models
audit` reports that compliance metadata without printing tokens.

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

`ltm models audit` reports configured model IDs, local paths, accepted aliases,
accepted directory aliases, whether directories exist, whether required files
are complete, whether offline mode can use them, and whether gated/token-
dependent access is required. It also writes a redacted machine-readable audit
to `models/model_audit.json`.

Useful model commands:

```bash
uv run ltm models list
uv run ltm models audit
uv run ltm models verify
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
`ltm models verify` and `LTM_OFFLINE=1 ltm doctor` as the readiness checks.

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

## Required vs Optional

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
LTM_OFFLINE=1 uv run ltm doctor
```

After successful model pulls:

```bash
uv run ltm models audit
uv run ltm doctor
LTM_OFFLINE=1 uv run ltm doctor
```

## Gated Model Caveats

`pyannote/speaker-diarization-community-1` requires both terms acceptance and a
read token. A missing token, unaccepted terms, or denied access produces an
actionable error. The combined pull command checks gated-model preconditions
before downloading public ASR assets, avoiding accidental partial cache setup.
