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
- FastAPI and Uvicorn;
- Hatchling, mypy, pytest, Ruff, and types-PyYAML.

The reviewed dependency closure is predominantly MIT, BSD-family, Apache-2.0,
PSF, and ISC. Conditional/multi-license records include MPL-covered metadata
for `certifi`/`pathspec` and an `MPL-2.0 AND MIT` record for `tqdm`; the exact
selected distribution and evidence control. PyAV and SoundFile wheels may carry
native libraries under additional terms. A package name, classifier, or lock
entry alone is not enough to clear redistribution. Consult the scope-specific
SBOM and compliance registry for exact versions and expressions.

## Separately Downloaded Models

| Asset | Recorded license/terms disposition | Inclusion |
| --- | --- | --- |
| Whisper and verified Distil Whisper derivatives | MIT; immutable revision required | Downloaded separately; never source/wheel/sdist content |
| `facebook/wav2vec2-base-960h` | Apache-2.0 | Downloaded separately |
| `speechbrain/spkrec-ecapa-voxceleb` | Apache-2.0; VoxCeleb training-data caveat remains | Downloaded separately |
| `pyannote/speaker-diarization-community-1` | CC-BY-4.0 attribution plus gated access terms; each user must independently accept | Downloaded separately |
| `mlx-community/whisper-large-v3-mlx` | MIT evidence verified | Imported separately |
| Other configured `mlx-community/*` entries | Publisher license metadata missing; blocked/unverified | Must not be redistributed or represented as production-cleared |

Model licenses do not necessarily grant rights in training data, names, voices,
publicity, or biometric processing. Model weights and caches are prohibited from
source and Python package archives.

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
