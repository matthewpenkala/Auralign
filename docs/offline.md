<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Offline Mode

Set `LTM_OFFLINE=1` to require local model files and block non-local runtime
services. Localhost cleanup endpoints remain local-only; remote cleanup
endpoints are rejected in offline mode even when remote cleanup is otherwise
explicitly allowed.

In the explicit source/headless lane on Apple Intelligence-compatible Apple
Silicon Macs running macOS 26+ with Xcode 26+, Python 3.10+, and Apple
Intelligence enabled,
`cleanup.mode: apple-foundation` is also offline-compatible: it invokes
Apple's `SystemLanguageModel` in-process through the pinned `apple-fm-sdk`
bridge and has no cleanup endpoint. Auralign does not acquire or redistribute
the system-model weights. Availability is checked at runtime and a required
cleanup contract fails closed when the model or SDK is unavailable. The SDK is
not installed by the generic `full` extra and is excluded from the macOS 14+
standalone app/PKG; offline-compatible describes network behavior, not broader
platform or product availability.

The exact-pinned SaT boundary source is offline-only in its explicit
source/headless lane. A bounded required-mode smoke verified the exact local
model/tokenizer with Hugging Face and Transformers forced offline and recorded
`network_access=false`; this establishes runtime viability, not model quality.
`presentation-sat` is not included by `full` or the standalone product. The
operator must pre-provision the exact allowlisted
`sat-3l-sm` and XLM-R tokenizer directories and configure both paths; Auralign
has no acquisition or auto-download path. Verification occurs before the lazy
backend import. Optional mode records an abstention for missing, malformed, or
mismatched inputs; required mode fails closed. Offline compatibility does not
change the assets' unbundled, not-cleared-for-product-release disposition.

```bash
LTM_OFFLINE=1 ltm doctor
```

Offline mode does not call Hugging Face metadata endpoints or attempt
opportunistic downloads. In a source-development cache lane, a missing required
model fails fast with the exact maintainer pull command, and a pullable cache is
rejected when its private `.ltm-model.json` linkage marker does not match the
manifest's immutable model ID and revision.

The installed 0.3.2 product has two fail-closed resource contracts:

- the root-owned `com.auralign.studio.models.bootstrap.v2` version `2.0.0`
  component, containing Community-1, English alignment, ECAPA, and deterministic
  corpus-free Punkt; and
- the selected exact per-user ASR pack below
  `~/Library/Application Support/com.auralign.studio/Models/Packs/<pack-id>/<version>/`.

The signed native model manager may perform an explicit connected setup before
offline processing. It downloads only revision-bound public files from its
sealed catalog with no token, verifies exact paths, sizes, hashes, licenses,
notices, provenance, CycloneDX SBOM, permissions, and completion marker in a
private stage, then atomically publishes the pack. It never downloads during
doctor, readiness, or transcription. Missing or altered bootstrap content
requires package repair/reinstall; a missing or altered ASR pack is repaired
through **Complete Offline Setup** or an exact offline import. Arbitrary
directories and floating repository IDs are never trusted.

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

`ltm polish-job` is likewise local post-processing. It accepts only a completed
hash-bound job, a fresh destination, punctuation cleanup with zero fallback
windows, and a validated identity report. It rehashes its sources before
atomic publication and never reruns ASR, alignment, or diarization.

MLX ASR is supported on Apple Silicon only through one of the twelve exact
verified MLX packs. Selecting `mlx-whisper` without the matching valid pack
fails before pipeline execution; it never triggers a runtime download or
silently falls back to CTranslate2. See
[Apple Silicon](apple-silicon.md#mlx-and-mps-runtime-boundary).

Use `ltm models audit` before and after model pulls to confirm whether each
configured model is offline-usable:

```bash
uv run ltm models audit
```

The source contains the owner-reviewed SHA-256 contract for all five
Community-1 runtime files, including `config.yaml` at immutable revision
`3533c8cf8e369892e6b79ff1bf80f7b0286a54ee`. Gated access remains personal:
an authorized operator must accept the terms and expose a read-scoped token
only through the local environment before normal acquisition:

```bash
export LTM_MODEL_CACHE="$PWD/models"
export HF_HOME="$PWD/.hf-cache"
export HF_HUB_CACHE="$HF_HOME/hub"
export LTM_HF_TOKEN="REDACTED_USER_SUPPLIED_READ_TOKEN"
uv run ltm models pull \
  --asr large-v3 \
  --diarization community-1 \
  --alignment en \
  --speaker-id ecapa \
  --cache-dir "$LTM_MODEL_CACHE"
unset LTM_HF_TOKEN HF_TOKEN HUGGING_FACE_HUB_TOKEN
uv run ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en \
  --speaker-id ecapa \
  --cache-dir "$LTM_MODEL_CACHE"
LTM_OFFLINE=1 uv run ltm doctor --startup --config examples/config.strict-offline.yaml
```

The committed `config.yaml` digest was produced by the repository's ephemeral
five-file review command and approved by the owner on 2026-07-20. The review
installed nothing, its temporary model bytes were deleted, and the other four
returned hashes matched the existing manifest exactly. Do not edit the local
provenance marker or substitute an unreviewed revision.

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
complete the upstream gate for a direct maintainer pull. This cache-transfer
workflow is not the installed 0.3.2 recipient path: the product PKG installs
the exact cleared, attributed bootstrap component, and the native app installs
or imports the selected verified ASR pack. No recipient token is needed, but
the first native pack setup needs a connection unless an exact pack is already
installed or imported. See
[`models.md`](models.md#exact-connected-preparation-and-offline-runbook) for the
safe transfer sequence. Dataset sample acquisition remains blocked until exact
provenance and license are verified.

## Budget-Aware Dataset Handling

Dataset planning is separate from strict runtime preparation. Use the explicit
planner to inspect optional dataset storage without implying that datasets are
runtime dependencies:

```bash
uv run ltm datasets plan --profile runtime-samples --max-local-gb 65 --min-free-gb 100
```

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
