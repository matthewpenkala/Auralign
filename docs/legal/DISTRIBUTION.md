<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Distribution And Delivery Policy

## Governing Rule

The PolyForm Internal Use License 1.0.0 does not grant recipients permission to
distribute Auralign. The rights holder may provide an authorized copy under the
controlling license or a separate written agreement; a recipient may not
redistribute that copy merely because it was available from a private remote or
installed from an archive.

No automated agent, CI job, contributor, or collaborator may publish source,
packages, containers, releases, or model/data artifacts without an explicit
owner-authorized release action and completion of the applicable gates below.
Private-repository dependency review uses GitHub's pinned official action only
when the repository has the required GitHub security entitlement and the owner
sets `AURALIGN_ENABLE_DEPENDENCY_REVIEW=true`; the portable locked dependency,
SBOM, and vulnerability gates run regardless.

## Source Repository

An authorized source release must contain `LICENSE`, `NOTICE`,
`THIRD_PARTY_NOTICES.md`, REUSE/SPDX data, and required upstream notices. It
must exclude secrets, private data, local paths, caches, weights, media,
generated outputs, internal handoffs, and unreachable historical objects. Only
the approved sanitized `main` may be pushed; never use `--all` or `--mirror`.

## Wheel And Source Distribution

An owner-authorized wheel or sdist must:

- carry the exact license expression, owner notice, and required legal files;
- contain only intended first-party package/source files and identified notices;
- exclude models, datasets, media, transcripts, embeddings, caches, secrets,
  local paths, native binaries, and generated run output;
- have a scope-specific SBOM and extracted-archive inspection;
- distinguish dependencies that are merely declared from material actually
  included in the archive.

The package is private and must not be uploaded to PyPI or another public index.
Possession of an archive does not authorize redistribution.

## Containers

Container images are internal-only and non-distributable until the exact image
digest is separately cleared. A source/wheel clearance does not clear base
images, OS packages, Python wheels, FFmpeg/codecs, Torch/TorchCodec, NVIDIA
components, native libraries, notices, or corresponding-source obligations.
Container builds and SBOMs may be used internally, but registry publication and
external image delivery must be hard-blocked.

## Models, Datasets, And User Data

Models and datasets are acquired separately under upstream licenses and terms.
They are never included by implication in an Auralign license or release.
Gated access must be accepted by each user; one maintainer's acceptance cannot
be transferred. Unverified or disabled assets must not be mirrored, bundled, or
described as cleared.

Recordings, transcripts, speaker identities, embeddings, profiles, benchmarks,
and validation evidence are not release artifacts. Their handling requires
operator authorization and the controls in [security and privacy](../security-privacy.md).

## Customer, Hosted-Service, And Commercial Use

Customer deployment, hosted/managed service, sublicensing, redistribution, and
commercial licensing outside the controlling internal-use permission require a
separate written agreement. That agreement must define the exact artifact,
recipient, use, support, data handling, third-party obligations, and any
additional license grant. Repository documentation cannot create those rights.

## Human Legal Gates

Counsel or qualified human review remains required for contributor agreements,
customer/commercial terms, public branding, patent-publication strategy, and
disputed external-asset or native-library rights. These gates do not permit
technical metadata or release safeguards to be omitted.
