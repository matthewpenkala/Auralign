<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Offline Mode

Set `LTM_OFFLINE=1` to require local model files and block non-local runtime
services. Localhost cleanup endpoints remain local-only; remote cleanup
endpoints are rejected in offline mode even when remote cleanup is otherwise
explicitly allowed.

```bash
LTM_OFFLINE=1 ltm doctor
```

When required models are missing, doctor and transcribe fail fast with the exact
online pull command. Offline mode does not call Hugging Face metadata endpoints
or attempt opportunistic downloads. A pullable cache is also rejected when its
`.ltm-model.json` linkage marker is missing or does not match the manifest's
immutable model ID and revision.

Doctor evaluates dependencies and models required by the active configuration;
missing packages for a selected ASR, diarization, forced-alignment, or speaker-ID
backend are errors rather than optional warnings. When validating a stricter
lane than the preset default, pass a config that describes that exact lane. For
example, strict forced-alignment readiness requires `alignment.backend: forced`.

`ltm speakers resolve-identities` is deterministic local post-processing. It
reads only the selected job, explicitly supplied local inputs, configured local
speaker-profile/rename stores, and bounded sibling artifacts. It never downloads
a model or calls a service. ECAPA evidence is usable only when an existing
current-job `speaker_map.json` proves a healthy local production backend;
backend absence/failure remains non-evidence.

MLX ASR is local-only even when `LTM_OFFLINE` is unset. When using
`asr.backend: mlx-whisper`, set `asr.mlx_model_path`,
`LTM_ASR_MLX_MODEL_PATH`, or `--asr-mlx-model-path` to an existing converted MLX
model directory containing `config.json` and `weights.safetensors` or
`weights.npz`. The runtime refuses remote repo IDs so `mlx-whisper` cannot fall
back to Hugging Face downloads during transcription.

The experimental MLX VAD-windowed mode is also local-only. It uses `vad.json`
speech regions as window hints, writes temporary WAV chunks under the system temp
directory, restores timestamps to the canonical timeline, and still uses only
the resolved local MLX model path.

If the converted MLX directory is outside the cache, import it explicitly:

```bash
uv run ltm models import-mlx /path/to/converted-mlx-whisper --cache-dir models
```

Use `ltm models audit` before and after model pulls to confirm whether each
configured model is offline-usable:

```bash
uv run ltm models audit
uv run ltm models pull --asr large-v3 --diarization community-1 --cache-dir models/
uv run ltm models pull --alignment en --cache-dir models/
uv run ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en \
  --speaker-id ecapa \
  --cache-dir models/
LTM_OFFLINE=1 uv run ltm doctor --startup --config examples/config.strict-offline.yaml
```

If this machine has no Hugging Face token or accepted pyannote terms, real
diarization remains blocked. Resume after terms/token setup with:

```bash
export LTM_MODEL_CACHE="$PWD/models"
export HF_HOME="$PWD/.hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
export HF_TOKEN="REDACTED_USER_SUPPLIED_TOKEN"
uv run ltm models pull --asr large-v3 --diarization community-1 --cache-dir models
uv run ltm models pull --alignment en --cache-dir models
unset LTM_HF_TOKEN HF_TOKEN
uv run ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en \
  --speaker-id ecapa \
  --cache-dir models
LTM_OFFLINE=1 uv run ltm doctor --startup --config examples/config.strict-offline.yaml
```

For a full local procurement pass on a connected staging machine:

```bash
chmod +x bootstrap.sh
./bootstrap.sh
```

Bootstrap exits nonzero for any missing model, generated asset, digest, native
dependency, or backend startup failure. `model_audit.json` is ignored and can
contain machine-local paths; do not commit or share it. Copy only an authorized
reviewed model cache and the source tree to the production air-gapped machine,
preserving hidden
`.ltm-model.json` linkage files. Do not copy `.hf-cache`, `.env`, shell history,
token stores, media, or generated output. A different pyannote operator must
accept the upstream gate and acquire their own copy. See
[`models.md`](models.md#exact-connected-preparation-and-offline-runbook) for the
safe transfer sequence. Dataset sample acquisition remains blocked until exact
provenance and license are verified.

## Budget-Aware Dataset Handling

The default bootstrap profile is `runtime-samples`, capped by:

```bash
LTM_BOOTSTRAP_MAX_LOCAL_GB=65
LTM_BOOTSTRAP_MIN_FREE_GB=100
```

The budget preflight checks both `LTM_MODEL_CACHE` and `LTM_DATASET_CACHE` so
split-volume setups cannot pass based only on dataset-cache free space.

Full LibriSpeech, AMI, and VoxCeleb are optional research corpora, not runtime
dependencies. They are recorded as cloud-only in `datasets/dataset_manifest.json`
and are not downloaded by `./bootstrap.sh`.

Sample acquisition is intentionally disabled. The plan command reports the
blocked asset without downloading it:

```bash
uv run ltm datasets plan --profile runtime-samples --max-local-gb 65
```

`ltm datasets plan` checks `LTM_MODEL_CACHE` by default. Pass
`--model-cache-dir` only when planning a different model-cache path.

Optional cloud/object storage mirrors require a named `rclone` remote and
explicit bulk-transfer approval:

```bash
export LTM_DATASET_MIRROR_REMOTE="gdrive:auralign-datasets"
uv run ltm datasets cloud-plan --remote "$LTM_DATASET_MIRROR_REMOTE"
uv run ltm datasets cloud-sync --remote "$LTM_DATASET_MIRROR_REMOTE" --yes-i-understand-bulk-transfer
```

Cloud mirrors are storage-only for public datasets. They are not used for
inference and are not required during offline transcription.
