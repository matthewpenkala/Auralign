<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Third-Party Notices

This document distinguishes material included in Auralign artifacts from
software and assets that Auralign only declares, invokes, or downloads. It is a
notice inventory, not a grant of rights and not a substitute for the exact
license text and evidence attached to each component record or SBOM.

The deterministic effective component/asset inventory is generated at
[`compliance/THIRD_PARTY_COMPONENTS.generated.md`](compliance/THIRD_PARTY_COMPONENTS.generated.md).
Run `uv run python scripts/generate_third_party_notices.py --check` to detect
registry/notice drift.

## Included In The Source Repository

No third-party runtime library, model weight, dataset, recording, media sample,
base image, operating-system package, FFmpeg binary, or codec is intentionally
vendored in the source tree.

The standardized PolyForm Internal Use License 1.0.0 text is reproduced in
`LICENSE` and `LICENSES/LicenseRef-PolyForm-Internal-Use-1.0.0.txt` from the
[PolyForm Project's official text](https://polyformproject.org/licenses/internal-use/1.0.0).
The Auralign required notice is separate from the standard license wording.

If an archive or scan identifies any other third-party content as included, the
artifact is not cleared until its source, exact version, copyright, license,
notices, modifications, and distribution obligations are recorded.

## Declared Python And Build Dependencies

Dependencies declared by `pyproject.toml` or resolved by `uv.lock` are installed
separately and retain their upstream licenses. They are not relicensed under
Auralign's PolyForm terms. Direct dependency families include:

- Hugging Face Hub, jiwer, Pydantic, PyYAML, and Typer;
- faster-whisper, mlx-whisper, pyannote.audio, WhisperX, and SpeechBrain;
- security-pinned aiohttp and NLTK dependencies, plus Lightning built from an
  exact upstream security-fix commit;
- the explicit source/headless-only `apple-fm-sdk` cleanup bridge;
- the explicit source/headless-only `wtpsplit` SaT boundary-scoring adapter;
- FastAPI and Uvicorn;
- Hatchling, mypy, pytest, Ruff, and types-PyYAML.

The reviewed dependency closure is predominantly MIT, BSD-family, Apache-2.0,
PSF, and ISC. Conditional/multi-license records include MPL-covered metadata
for `certifi`/`pathspec` and an `MPL-2.0 AND MIT` record for `tqdm`; the exact
selected distribution and evidence control. PyAV and SoundFile wheels may carry
native libraries under additional terms. A package name, classifier, or lock
entry alone is not enough to clear redistribution. Consult the scope-specific
SBOM and compliance registry for exact versions and expressions.

The supported locked environments use `aiohttp==3.14.3` and `nltk==3.10.3`.
Lightning is built under Apache-2.0 from exact upstream commit
`d710d689510d50e800f53b3cd773cbca20b1f86f`, whose source metadata reports
version `2.6.2`; the downloaded archive is pinned by SHA-256 in `uv.lock`.
That commit contains the upstream `_instantiator` allowlist repair for
CVE-2026-58659. This source-level remediation is revision- and hash-bound and
is recorded separately from owner-approved vulnerability risk exceptions.
Auralign also applies one narrowly reviewed build-only normalization to the
pinned `setup.py`: the upstream random aggregate-MANIFEST temporary filename is
replaced by a fixed root-level temporary filename so it cannot enter the wheel.
The original and patched file hashes and exact patch identifier are recorded in
the standalone build receipt; installed Lightning runtime code is otherwise
unchanged by this deterministic-build normalization.

`apple-fm-sdk==0.2.1` is pinned to upstream tag `v0.2.1`, commit
`84841bb71b22996c00bb67e3b4c7ae1f985315ea`, under Apache-2.0. Upstream
requires macOS 26+, Xcode 26+, Python 3.10+, and Apple Intelligence on a
compatible Mac. Auralign exposes it only through the explicit `cleanup-apple`
source/headless extra. The SDK, its native Foundation Models bridge, and its
build-helper closure are excluded from the generic `full` extra and the
macOS 14+ standalone app/PKG. The Apple-managed system-model weights are not
acquired, bundled, or redistributed by Auralign.

`wtpsplit==2.2.1` is pinned to commit
`e48922c0ad240c7d2cb1ef8afbc0cd413372bc83` under MIT. It is available only
through the explicit `presentation-sat` source/headless extra and is excluded
from `full`, the macOS 14+ standalone app/PKG, and every product model
component. The adapter accepts only exact pre-provisioned local directories for
`segment-any-text/sat-3l-sm` and the `facebookAI/xlm-roberta-base` tokenizer;
it contains no download or auto-download path. Both assets are pinned and
hash/size allowlisted in `compliance/external_assets.toml`, remain unbundled,
and completed a bounded exact-model source/headless smoke on 2026-08-16. The
required boundary-only adapter ran with Hugging Face and Transformers forced
offline over 16 synthetic words, produced 128 punctuation-neutral proposals
with `status=succeeded` and `network_access=false`, and retained a canonical
receipt with SHA-256
`a2718924ee45be86ba63b313904edada3e3a2fc2709618d692d868217903228f`.
A second scale smoke processed 3,068 synthetic words into 24,544
punctuation-neutral proposals in 6.3970266250 seconds with
`network_access=false`; its canonical receipt SHA-256 is
`9db53b1d16a9a25f97175b68f0f23621bbabc5b3c893b7a63ded835847e7dc69`.
The superseded 2026-08-15 receipts remain rejected historical evidence of an
invalid NONE/PERIOD projection.
These prove exact runtime-path, scale-path, and receipt viability only—not
sentence-boundary quality, superiority, product bundling, or redistribution
clearance.

## Private Standalone macOS PKGs

The owner-authorized private macOS products are distinct artifacts from the
source, wheel, and sdist lanes. The sealed app component embeds CPython 3.11.15
from the immutable
python-build-standalone 20260510 asset, the exact hash-locked CPU Python
dependency environment, native Torch-family libraries, source-built PyAV 15.1.0,
and a custom FFmpeg 7.1.5 shared-library build. The immutable 0.2.0 and 0.2.1
products contain no models or datasets.

The standalone application does not include `apple-fm-sdk`, its native
Foundation Models library, or its package build helpers. Consequently the
Apple cleanup backend is not a capability of the macOS 14+ standalone product;
it remains an explicit macOS 26+ source/headless option.

The standalone application also excludes `wtpsplit`, its source/headless-only
dependency closure, SaT ONNX weights, and the XLM-R tokenizer files. SaT
scoring is not a capability or readiness requirement of the 0.3.0 product.

The successor private 0.3.0 product PKG adds one separate, root-owned,
read-only bootstrap component at
`/Library/Application Support/com.auralign.studio/Models/com.auralign.studio.models.bootstrap.v1/`.
It contains the exact Community-1 diarization, English forced-alignment, and
ECAPA speaker-embedding directories below `models/`, plus the deterministic
`nltk-punkt-tab-english-corpus-free` runtime-data record. The latter is four
empty English `punkt_tab` parameter files generated by locked NLTK 3.9.4 with
`training_data: none`, plus a canonical marker; it is not a copied corpus or a
fourth model.

The sixteen cleared CTranslate2 and MLX ASR selections are delivered after
installation by Auralign's native model manager because the largest exact
weight file cannot fit the private release host's per-asset limit. Each public
pack is downloaded from revision-bound URLs sealed in the signed app catalog,
staged privately, verified by exact path/size/SHA-256, and atomically promoted
below
`~/Library/Application Support/com.auralign.studio/Models/Packs/<pack-id>/<version>/`.
This flow uses no token and no Installer script. A selected pack must be
complete before transcription is ready; processing remains local and offline
after setup.

Models remain outside `Auralign.app`, Git, source/wheel/sdist archives, and
loose release assets. Raw caches, cache locks, partial downloads, tokens,
operator-local linkage/audit records, acquisition timestamps, and private
paths are not included.

The application carries the complete python-build-standalone runtime-license
metadata, Python and PyAV notices, FFmpeg LGPL/configuration evidence, Auralign's
generated registry notice, an exact installed-distribution CycloneDX SBOM, and a
signed-byte runtime inventory. The private release also provides the exact
unmodified FFmpeg 7.1.5 source archive as corresponding-source evidence. FFmpeg
is built with network, GPL, nonfree, and version3 code disabled; its actual
configure string and native closure are release gates. Every remaining Python
distribution and native object is governed by the exact artifact SBOM and its
upstream terms. No package name or source-workspace declaration alone clears a
shipped byte.

## Models And The Private 0.3.0 Delivery Contract

| Key | Upstream model | Exact revision | License | Delivery |
| --- | --- | --- | --- | --- |
| `large-v3` | `Systran/faster-whisper-large-v3` | `edaa852ec7e145841d8ffdb056a99866b5f0a478` | MIT | Native pack |
| `turbo` | `dropbox-dash/faster-whisper-large-v3-turbo` | `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` | MIT | Native pack |
| `distil-large-v3` | `Systran/faster-distil-whisper-large-v3` | `c3058b475261292e64a0412df1d2681c06260fab` | MIT | Native pack |
| `distil-large-v3.5` | `distil-whisper/distil-large-v3.5-ct2` | `9793ccc07920e0f830e1dba0343efcdf0ef8c903` | MIT | Native pack |
| `mlx-tiny` | `mlx-community/whisper-tiny-mlx` | `6caf9c55601caafbe6508a8b0d216bdf4783c4e8` | MIT chain | Native pack |
| `mlx-tiny.en` | `mlx-community/whisper-tiny.en-mlx` | `5f4dafbb28e62a53c1b10426ff7eed36ca733bf7` | MIT chain | Native pack |
| `mlx-base` | `mlx-community/whisper-base-mlx` | `1e3e249fb8d01c655324bd6841b1deadffd6d04c` | MIT chain | Native pack |
| `mlx-base.en` | `mlx-community/whisper-base.en-mlx` | `aa0678c3466ed62c5c6114ec600a0e1f96820089` | MIT chain | Native pack |
| `mlx-small` | `mlx-community/whisper-small-mlx` | `45f3915923c7a79a5a5b5a7d909d39aeb0e5630e` | MIT chain | Native pack |
| `mlx-small.en` | `mlx-community/whisper-small.en-mlx` | `52a88bf6e98b114a210c21bb83e22d6e1505cb73` | MIT chain | Native pack |
| `mlx-medium` | `mlx-community/whisper-medium-mlx` | `7fc08c4eac4c316526498f147dfdee6f6303f975` | MIT chain | Native pack |
| `mlx-medium.en` | `mlx-community/whisper-medium.en-mlx` | `8d0335069b551e8586b8a4680674403d9417dfe4` | MIT chain | Native pack |
| `mlx-large` | `mlx-community/whisper-large-v1-mlx` | `e2cb9fbf9c7aefad760be1dc9b48c075b21288c8` | MIT chain | Native pack |
| `mlx-large-v2` | `mlx-community/whisper-large-v2-mlx` | `cce86229e2765266197fef869ce9f7e2550067ab` | MIT chain | Native pack |
| `mlx-large-v3` | `mlx-community/whisper-large-v3-mlx` | `49e6aa286ad60c14352c404340ded53710378a11` | MIT | Native pack |
| `mlx-large-v3-turbo` | `mlx-community/whisper-large-v3-turbo` | `a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb` | MIT chain | Native pack |
| `community-1` | `pyannote/speaker-diarization-community-1` | `3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` | CC-BY-4.0 | Bootstrap component |
| `en` | `facebook/wav2vec2-base-960h` | `22aad52d435eb6dbaf354bdad9b0da84ce7d6156` | Apache-2.0 | Bootstrap component |
| `ecapa` | `speechbrain/spkrec-ecapa-voxceleb` | `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286` | Apache-2.0 | Bootstrap component |

The pinned `mlx-large-v3` model card directly declares MIT. Eleven other pinned
MLX conversion repositories omit a standalone license file and direct license
metadata; their recorded disposition relies on the reviewed OpenAI Whisper and
locked `mlx-whisper` MIT chain, exact unmodified bytes, and the owner's private
release decision. That omission is disclosed rather than silently inferred.
Community-1's pinned card/API declare CC-BY-4.0 and the repository has no
standalone `LICENSE` at that revision. Its official gate is recorded as an
upstream acquisition/contact mechanism; the bundled cleared copy does not
claim to register a recipient's Hugging Face consent.

Matthew Penkala recorded the revision-specific 19-model private-project release
decision on 2026-07-25. It is not legal advice, a legal opinion, or a change to
upstream terms. Exact runtime-file SHA-256 and size values, card/gate evidence,
attribution, modification status, backend evidence, and package identity are
recorded in `compliance/external_assets.toml` and `docs/models.md`. Any missing,
changed, linked, mutable, or extra file fails closed.

Model licenses do not necessarily grant rights in training data, names, voices,
publicity, or biometric processing. Model weights and caches are prohibited
from source and Python package archives. The curated bootstrap component and
each native pack are not raw maintainer caches. They carry deterministic
manifest, verification, provenance, completion, notice, license, and
CycloneDX SBOM records, and the final accepted release inventory records the
complete product relationship.

## Separately Acquired Datasets And Media

| Asset | Recorded disposition | Inclusion |
| --- | --- | --- |
| LibriSpeech | CC-BY-4.0 attribution | External optional research corpus |
| AMI Meeting Corpus | CC-BY-4.0 attribution | External optional research corpus |
| `hf-internal-testing/librispeech_asr_dummy` | Exact sample license/provenance missing; acquisition blocked | Not included |
| VoxCeleb/mirror | Underlying audiovisual, voice, publicity, privacy, and research-use rights unresolved; mirror sync blocked | Reference-only; not included |
| User/validation media | Private operator-controlled content | Never included |

## Containers, Native Libraries, And System Components

Docker/OCI artifacts are internal-only and non-distributable until each exact
image digest is cleared. Base images, Debian packages, FFmpeg/codecs,
Torch/TorchCodec, CUDA, cuDNN, NCCL, NVIDIA components, and native libraries
retain their own terms. Container SBOMs are evidence inventories, not release
authorization. Codec availability does not establish patent clearance.

Portable Windows CI downloads the checksum-pinned Gyan FFmpeg 8.1.2 essentials
build (GPL-3.0-or-later) into ephemeral job storage. Auralign verifies the exact
archive and executable version, uses `ffmpeg`/`ffprobe` only for tests, and never
includes that native build in source, wheel, sdist, native bundles, or releases.

## Notice Maintenance

Generated notices must be reproducible from immutable component records. A
dependency, model, dataset, base image, GitHub Action, or vendored-asset change
must update the registry, evidence, SBOM, and this document when its human-facing
disposition changes. Unknown, conflicting, or missing terms fail closed.
