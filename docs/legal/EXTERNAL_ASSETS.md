<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# External Assets And Rights Boundaries

External assets are not licensed under Auralign's first-party license. A local
cache, access token, public URL, package declaration, model card, or successful
download is not proof that acquisition, use, or redistribution is authorized.

`compliance/external_assets.toml` is the machine-readable authority for
immutable revisions, exact files/sizes/SHA-256 values, source and terms URLs,
SPDX expressions, acquisition/use/redistribution status, product
classification, backend evidence, modification status, package destination,
human clearance, and review date. Unknown or conflicting required fields fail
closed.

## 0.3.0 Model Disposition

On 2026-07-25, Matthew Penkala recorded the project decision represented in
the registry for all nineteen exact model entries. This is a release-scope
decision, not legal advice, an AI legal opinion, ownership of the external
assets, or a change to upstream terms.

| Asset family | Exact 0.3.0 disposition |
| --- | --- |
| Four CTranslate2 ASR entries | MIT evidence, immutable revisions, complete file/size/SHA-256 contracts, real network-denied local inference, owner-cleared native-download packs |
| Twelve MLX Whisper entries | MIT chain from pinned OpenAI Whisper and mlx-whisper/MLX sources, immutable revisions, two-file size/SHA-256 contracts, real network-denied local inference, owner-cleared native-download packs |
| `pyannote/speaker-diarization-community-1` | Pinned card/API declare CC-BY-4.0; exact files owner-cleared with attribution for the scriptless bootstrap component |
| `facebook/wav2vec2-base-960h` | Apache-2.0 evidence; exact `en` files owner-cleared for the bootstrap component |
| `speechbrain/spkrec-ecapa-voxceleb` | Apache-2.0 evidence; exact `ecapa` files owner-cleared for the bootstrap component; VoxCeleb training-data caveat retained |
| Deterministic English Punkt | First-party build output from locked NLTK 3.9.4 empty parameters; no downloaded corpus; exact marker/files verified in the bootstrap component |

The scriptless PKG contains no ASR weights. It installs the app/runtime plus
root-owned bootstrap component
`com.auralign.studio.models.bootstrap.v1` version `1.0.0` below
`/Library/Application Support/com.auralign.studio/Models/`.

The sixteen ASR packs are independently versioned and published by the signed
native app below
`~/Library/Application Support/com.auralign.studio/Models/Packs/<pack-id>/<version>/`.
This native delivery addresses the private release host's per-asset size limit.
It does not authorize loose release assets, raw caches, floating URLs, or
runtime downloads during transcription. Public packs require no token, stage
privately, verify exact path/size/hash/license/notice/provenance/CycloneDX and
completion contracts, and publish atomically.

Normal preset mappings are `max=large-v3`, `balanced=turbo`,
`fast=distil-large-v3.5`, and `cpu=turbo`. `distil-large-v3` and all twelve
MLX models are optional advanced choices. All are exact and operational; none
is a pointer-only production selector.

## 0.3.1 Successor Disposition

On 2026-08-16, Matthew Penkala authorized the private 0.3.1 successor to reuse
the exact nineteen immutable model/file/license contracts above. No model
identity, revision, runtime file, ASR pack identity, redistribution evidence,
or native-download destination changes. The changed system payload receives a
new bootstrap identity, `com.auralign.studio.models.bootstrap.v2` version
`2.0.0`, and a new component receipt. It contains a freshly generated
corpus-free Punkt tree for NLTK `3.10.3` with its own marker, manifest,
verification, notices, SBOM, provenance, and completion hashes. The historical
0.3.0/v1 record remains unchanged and is not release evidence for v2.

## 0.3.2 Successor Disposition

On 2026-08-16, Matthew Penkala authorized the private 0.3.2 presentation-quality
successor to reuse the exact 0.3.1 external-asset contract without changing any
model identity, revision, file, license, native-pack destination, bootstrap
byte, or redistribution boundary. The app/product version advances; the
independently versioned bootstrap remains v2 `2.0.0`. The published 0.3.1
artifact and its hashes remain immutable historical evidence and do not
establish acceptance of the new application bytes.

## Model License Evidence

The four CTranslate2 conversions are recorded as MIT. The twelve MLX
conversions rely on the exact pinned model identity/card plus the corresponding
OpenAI Whisper MIT source/weights and the MIT mlx-whisper/MLX
conversion/runtime record. Eleven pinned MLX repositories omit a standalone
license field or file; that absence is disclosed in their row-specific
evidence rather than hidden. The owner's revision-specific project decision is
the human clearance boundary; it is not an AI inference that absence alone
creates permission.

For Community-1 revision
`3533c8cf8e369892e6b79ff1bf80f7b0286a54ee`, the official model card and
Hugging Face API metadata declare `CC-BY-4.0`; the pinned file tree contains no
standalone `LICENSE`. The official automatic gate requests default
username/email, `Company/university`, and a use-case field. Its recorded schema
contains no additional use-restriction checkbox. Auralign records the gate as
an upstream acquisition/contact mechanism. A direct acquirer must complete it;
the separately owner-cleared attributed copy in the bootstrap component does
not require a recipient Hugging Face account or token. Installer acceptance is
not represented as upstream consent.

The bootstrap component and each native pack contain complete applicable
license text, attribution, `THIRD-PARTY-NOTICES.txt`, deterministic provenance,
verification, CycloneDX SBOM, and completion records. They exclude tokens,
user names, private paths, acquisition timestamps, `.ltm-model.json`,
`model_audit.json`, cache metadata, locks, partial files, media, transcripts,
and validation logs.

## Repository And Release Boundaries

Model weights must not enter Git, Git LFS, wheels, sdists, source archives,
containers, package registries, standalone model uploads, or raw GitHub release
assets. The owner-authorized private product flow is the narrow exception:
exact bootstrap bytes may be inside the signed/notarized/stapled PKG and exact
ASR bytes may be published only as verified native per-user packs by the signed
application.

The native model manager never uses `preinstall` or `postinstall`, never runs
as root, never embeds a shared credential, and never treats an Auralign
checkbox as upstream consent. Normal readiness and processing are offline-only
and cannot download or select arbitrary directories.

## Datasets And Media

| Asset | Repository disposition |
| --- | --- |
| LibriSpeech | CC-BY-4.0 attribution must be recorded; full corpus remains optional and external |
| AMI Meeting Corpus | CC-BY-4.0 attribution must be recorded; full corpus remains optional and external |
| `hf-internal-testing/librispeech_asr_dummy` | Automated acquisition disabled until exact sample provenance and license are verified |
| VoxCeleb and mirrors | Reference-only; mirror synchronization blocked without explicit human review for purpose, jurisdiction, access, and retention |
| User recordings and validation corpora | Private operator-controlled data; never repository or product content |

Dataset licenses do not answer recording-consent, publicity/voice, biometric,
privacy, contractual, or jurisdiction-specific questions. Those require
separate review.

## Python And Native Dependencies

Python packages declared in `pyproject.toml` and resolved in `uv.lock` are not
vendored into source merely because they are declared. The standalone app's
purpose-built runtime contains only the exact reviewed closure; each component
retains its upstream terms and notices.

FFmpeg, codecs, PyAV, Torch, TorchCodec, torchaudio, MLX, native libraries,
CUDA/cuDNN/NCCL, base images, and operating-system packages are external
components. Availability under a software license does not by itself establish
patent, codec, export, or redistribution clearance.

## Secrets And Private Material

Tokens, `.env` files, raw caches, recordings, transcripts, diarization,
embeddings, profiles, QA/debug reports, private paths, and generated run output
are sensitive operational material. They remain outside Git, the PKG, native
packs, and release attachments except for the exact public model bytes and
privacy-safe deterministic records explicitly authorized above.
