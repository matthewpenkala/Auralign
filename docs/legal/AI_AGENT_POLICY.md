<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# AI And Automated-Agent Policy

This policy is mandatory repository workflow for automated agents and the
humans operating them. It protects the repository but is not itself a software
license, contributor agreement, confidentiality agreement, or grant of rights.

## Before Editing

1. Read the [legal index](README.md), `AGENTS.md`, and the files governing the
   affected subsystem.
2. Inspect existing SPDX, copyright, provenance, and upstream notices.
3. Preserve unrelated work and sensitive-data boundaries.

## Required Conduct

Agents must:

- preserve all copyright, license, SPDX, attribution, and provenance notices;
- never infer, remove, replace, or change a license or rights holder;
- record source, immutable revision, license, compatibility, notices, and
  modifications before incorporating third-party material;
- update the component/external-asset registry for every dependency, model,
  dataset, base image, GitHub Action, or vendored asset change;
- treat AI output as untrusted candidate material requiring human IP,
  provenance, privacy, security, and test review;
- fail closed when license, provenance, redistribution status, or terms are
  unknown;
- keep tokens, private source, recordings, transcripts, identities, embeddings,
  profiles, caches, and generated output out of prompts to unapproved services;
- keep runtime local/offline-first and avoid adding telemetry, uploads, or
  network behavior without explicit approval;
- preserve archive and container publication blocks unless the exact artifact
  has completed its release gates;
- before an explicitly requested source push, dynamically verify that the
  existing GitHub repository is private, the authenticated collaborator has
  `WRITE`, `MAINTAIN`, or `ADMIN` permission, the checked-out default branch is
  clean and not behind, and the push URL matches that repository.

## Prohibited Conduct

Agents must never:

- paste or transform third-party code to hide its origin;
- claim an AI system authored, owns, licensed, approved, or signed anything;
- fabricate a human attestation, legal approval, review date, evidence URL, or
  license conclusion;
- expose private repository content or operational data to unapproved services;
- make a repository public, change its visibility, force-push, rewrite remote
  history, push tags, create a release, upload assets, mirror content, or
  distribute packages, containers, models, datasets, or validation evidence;
- push source unless the user explicitly requests it and the dynamic private-
  collaborator preflight in `scripts/check_private_push_authorization.py` passes;
- bypass a current compliance, security, privacy, provenance, or release
  contract merely to obtain a passing result. Obsolete or architecture-specific
  internal gates may be revised when the replacement tests the authorized
  current contract at equal or better fidelity.

## Owner-Authorized Private macOS Release For This Run

On 2026-07-20, the repository owner explicitly authorized this implementation
run to synchronize reviewed source to the existing private `main`, create and
push the private macOS `0.2.0` release tag, and publish the exact Developer ID
signed, Apple-notarized, stapled Auralign PKG plus its allowlisted provenance
artifacts to that same private repository. This narrow authorization supersedes
the general tag/release/package prohibition above only for that exact private
macOS release after every applicable gate passes.

It does not authorize public visibility, a different repository, force-push,
history rewrite, containers, models, datasets, media, transcripts, credentials,
validation evidence, or any artifact outside the reviewed release allowlist.
The private-collaborator preflight, clean exact-commit build, dependency and
license closure, Developer ID Application and Installer identities, shared Team
ID, hardened runtime, notarization acceptance, stapling, install verification,
hash parity, and private-visibility checks remain mandatory. Credentials must
come from the operator's Keychain or Apple tooling and must never be committed
or placed in command-line arguments.

## Owner-Authorized Private macOS 0.2.1 Patch Release

On 2026-07-25, the repository owner explicitly authorized this continuation run
to synchronize the reviewed patch source to the existing private `main`, create
and push the nonconflicting private macOS `macos-v0.2.1` tag, and publish the
exact Developer ID signed, Apple-notarized, stapled Auralign 0.2.1 PKG plus its
allowlisted provenance artifacts to that same private repository. The existing
`macos-v0.2.0` tag and release are immutable and must not be moved, overwritten,
or repurposed.

This authorization has the same fail-closed private-repository, artifact,
credential, model, media, history, visibility, signing, notarization,
installation, and hash-parity boundaries as the 0.2.0 authorization above. It
does not authorize public exposure or any artifact outside the reviewed macOS
release allowlist.

## Owner-Authorized Private macOS 0.3.0 Model-Inclusive Release

On 2026-07-25, repository owner Matthew Penkala made the project release
decision to authorize the successor private macOS `0.3.0` architecture and all
nineteen exact model entries recorded in `compliance/external_assets.toml`.
This is a project authorization and technical-scope decision, not legal advice,
a legal opinion, a waiver, or a change to any upstream term. The immutable
`macos-v0.2.0` and `macos-v0.2.1` tags, releases, artifacts, and historical
release documents remain unchanged.

After all applicable gates pass, the authorization permits reviewed source
synchronization to the existing private `main`, creation and normal push of a
new `macos-v0.3.0` tag, and publication of the exact Developer ID signed,
Apple-notarized, stapled product PKG plus only the privacy-safe artifacts
allowlisted by the 0.3.0 runbook.

The scriptless PKG contains:

- the sealed arm64 Auralign application, standalone Python engine, exact native
  dependency closure, and reviewed FFmpeg/PyAV closure; and
- root-owned bootstrap component
  `com.auralign.studio.models.bootstrap.v1` version `1.0.0`, containing only
  exact Community-1, English alignment, ECAPA, deterministic corpus-free Punkt,
  licenses, notices, provenance, CycloneDX SBOM, and completion records.

The sixteen exact owner-cleared public ASR choices—four CTranslate2 and twelve
MLX—are delivered after installation by the signed app's native model manager
because their bytes cannot fit the private release host's per-asset limit. They
use revision-bound URLs from a sealed catalog, require no token, stage
privately, verify complete path/size/hash/license/notice/provenance/SBOM
contracts, and publish atomically below the current user's Application
Support. No Installer script or normal transcription path performs a download.

The normal preset mapping is `max=large-v3`, `balanced=turbo`,
`fast=distil-large-v3.5`, and `cpu=turbo`. `distil-large-v3` and all twelve
MLX entries are optional advanced choices. Every visible choice must remain
fully exact and operational; no pointer-only selector may be represented as
supported.

This authorization does not permit public visibility, loose model release
assets, raw Hugging Face or development caches, access tokens, `.env` files,
operator-local `.ltm-model.json` or `model_audit.json` records, private paths,
media, transcripts, raw validation evidence, containers, datasets, a different
repository, force-push, or history rewrite. Model bytes must never be committed
to Git. Acquisition/build tools remain maintainer-only. Recipients need no
Python, `uv`, Git, Xcode, Command Line Tools, Homebrew, checkout, virtual
environment, compiler, package manager, token copying, or Terminal; one
connected pack setup is required unless the exact selected pack is already
installed/imported, after which processing is strictly offline.

The exact external-asset records, attribution, notices, SBOMs, installation
topology, private visibility, signing, notarization, stapling,
install/reinstall, model integrity, strict-offline startup, real inference, and
local/published hash-parity gates remain mandatory. Unknown, mismatched,
linked, partial, extra, or unreviewed content fails closed.

## Owner-Authorized Private macOS 0.3.1 Security And Quality Successor

On 2026-08-16, repository owner Matthew Penkala explicitly authorized the
private macOS `0.3.1` successor, reviewed synchronization to the existing
private `main`, creation and normal push of a new `macos-v0.3.1` tag, and
publication of the exact accepted private release allowlist. This is a project
authorization and technical-scope decision, not legal advice, a legal opinion,
a waiver, or a change to upstream terms. All earlier tags, release artifacts,
hashes, and historical release records remain immutable and must not be moved,
overwritten, or relabeled.

The 0.3.1 scope reuses the exact nineteen model identities, revisions, file
contracts, licenses, and owner-clearance boundary recorded for 0.3.0. It gives
the changed bootstrap payload a new identity:
`com.auralign.studio.models.bootstrap.v2` version `2.0.0`, installed by receipt
`com.auralign.studio.pkg.models.bootstrap-v2`. The v2 component contains the
same three exact bootstrap models plus a freshly generated, hash-bound,
corpus-free English Punkt tree for locked NLTK `3.10.3`; it must never reuse or
relabel v1 bytes or receipts.

The authorization also covers the reviewed dependency-security remediation,
typed semantic cleanup and presentation-plan architecture, forced-alignment,
identity/export, privacy, offline, and native-app repairs in the 0.3.1 source.
Publication still requires the exact clean pushed commit, a fresh no-cache
build, real Developer ID signatures, Apple notarization and stapling,
Gatekeeper and install/reinstall acceptance, model/runtime closure, complete
notices/SBOM/provenance, and private download/hash parity. It does not authorize
public visibility, force-push, loose model assets, raw caches, credentials,
media, transcripts, private validation logs, or any artifact outside the
0.3.1 runbook allowlist.

## Owner-Authorized Private macOS 0.3.2 Presentation Quality Successor

On 2026-08-16, repository owner Matthew Penkala explicitly authorized the
private macOS `0.3.2` successor requested in this task, reviewed synchronization
to the existing private `main`, creation and normal push of the new
`macos-v0.3.2` tag, and publication of the exact accepted private release
allowlist. The published `macos-v0.3.1` tag, release, package, hashes, and
acceptance records remain immutable and must not be moved, overwritten, or
relabeled.

The 0.3.2 release reuses without modification the exact nineteen model
identities, revisions, files, licenses, owner-clearance boundary, native pack
catalog, and bootstrap v2 component authorized for 0.3.1. The bootstrap remains
`com.auralign.studio.models.bootstrap.v2` version `2.0.0`, installed by receipt
`com.auralign.studio.pkg.models.bootstrap-v2`; it is not rebuilt under a new
model identity merely because the application version advances.

The successor scope is the reviewed presentation-decoder, cleanup-report,
polish/replay, and QA changes that preserve canonical lexical, timing,
alignment, speaker, privacy, and provenance invariants while representing
ordinary punctuation uncertainty as reversible editorial presentation. The
authorization permits the exact clean pushed source commit, fresh no-cache
Developer ID build, Apple notarization and stapling, Gatekeeper and native
install/reinstall acceptance, new tag, private release, and independent private
download/hash parity required by the 0.3.2 runbook. It does not authorize
public visibility, force-push, loose model assets, raw caches, credentials,
media, transcripts, private validation logs, or any artifact outside that
runbook's allowlist.

## Escalation

Stop and request owner review when evidence conflicts, an upstream license is
missing, copied expression may be present, a contribution lacks an executed
agreement, or the requested action would distribute content. Record a precise
blocked reason and the evidence needed to resolve it; do not use vague future-
work language or silently continue.
