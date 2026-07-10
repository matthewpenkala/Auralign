<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# External Assets And Rights Boundaries

External assets are not licensed under Auralign's first-party license. A local
cache, access token, public URL, package declaration, model card, or successful
download is not proof that acquisition or redistribution is authorized.

The machine-readable external-asset registry is the authoritative inventory for
immutable revisions, source and terms URLs, SPDX expressions, acquisition
status, redistribution status, privacy classification, review evidence, and
review dates. Acquisition and release operations must fail closed when a
required record is missing or unverified.

## Models

| Asset family | Repository disposition |
| --- | --- |
| Whisper and Distil Whisper derivatives | Require verified MIT evidence and an immutable revision before acquisition or supported-use claims |
| `facebook/wav2vec2-base-960h` | Require verified Apache-2.0 evidence and an immutable revision |
| `speechbrain/spkrec-ecapa-voxceleb` | Require verified Apache-2.0 evidence and an immutable revision; training-data rights remain a separate concern |
| `pyannote/speaker-diarization-community-1` | CC-BY-4.0 attribution plus gated terms; every downstream user must independently satisfy access terms |
| `mlx-community/*` entries lacking publisher license metadata | `unverified` and blocked from redistribution or production-supported claims until upstream evidence is recorded |

Model weights are separately downloaded runtime assets. They must not enter the
source repository, wheel, sdist, container publication, SBOM scope by accident,
or validation evidence committed to Git.

## Datasets And Media

| Asset | Repository disposition |
| --- | --- |
| LibriSpeech | CC-BY-4.0 attribution must be recorded; full corpus remains optional and external |
| AMI Meeting Corpus | CC-BY-4.0 attribution must be recorded; full corpus remains optional and external |
| `hf-internal-testing/librispeech_asr_dummy` | Automated acquisition disabled until exact sample provenance and license are verified |
| VoxCeleb and mirrors | Reference-only; mirror synchronization disabled by default and blocked without explicit human legal approval for purpose, jurisdiction, access, and retention |
| User recordings and validation corpora | Private operator-controlled data; never repository or package content |

Dataset licenses do not answer recording-consent, publicity/voice, biometric,
privacy, contractual, or jurisdiction-specific research-use questions. Those
must be reviewed independently.

## Python And Native Dependencies

Python packages declared in `pyproject.toml` and resolved in `uv.lock` are not
vendored into Auralign's source or pure-Python wheel merely because they are
declared. Each installed package remains governed by its upstream terms. Wheels
such as PyAV and SoundFile may include native libraries with distinct notices
or source obligations; lock metadata is not sufficient evidence for release.

FFmpeg, codecs, Torch/TorchCodec, CUDA, cuDNN, NCCL, base images, and operating-
system packages are external components. Availability under a software license
does not establish patent, codec, export, or redistribution clearance.

## Secrets And Private Material

Tokens, `.env` files, model/dataset caches, recordings, transcripts,
diarization, embeddings, speaker profiles, QA/debug reports, local paths, and
generated run output are sensitive operational material. They must remain
outside the repository and all distribution archives.
