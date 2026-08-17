<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign 0.3.2 Private macOS PKG Release

## Authority And Scope

This is the execution contract for the private arm64 macOS 14+ Auralign 0.3.2
presentation-quality successor authorized by Matthew Penkala on 2026-08-16.
The published `macos-v0.3.1` tag, release, package, hashes, and acceptance
records are immutable historical evidence and must not be reused or altered.

0.3.2 ships the reviewed presentation decoder v13, reversible hard96 editorial
escalation, receipt-bound sentence-span placement, typed reviewed-question
handling, exact replay, and retained-evidence composition. Canonical lexical
content, word order, timestamps, alignment, speaker evidence, privacy, and
provenance remain hard invariants. Ordinary punctuation uncertainty is derived
presentation and must remain reversible, hash-bound, and honestly reported.

## Product Identity

The scriptless product PKG installs:

- `/Applications/Auralign.app`, version `0.3.2` build `1`;
- app receipt `com.auralign.studio.pkg.app`, version `0.3.2.1`; and
- the unchanged bootstrap v2 component at
  `/Library/Application Support/com.auralign.studio/Models/com.auralign.studio.models.bootstrap.v2/`,
  receipt `com.auralign.studio.pkg.models.bootstrap-v2`, version `2.0.0`.

The bootstrap and sixteen native-download ASR packs reuse the exact 0.3.1
model/file/license/clearance contract. No loose model file, cache, token,
acquisition marker, media, transcript, identity evidence, or private validation
log is a release asset.

## Source Freeze And Build

The authoritative release must use a clean reviewed `main` commit already
pushed to the private origin, a new absent work directory, and
`scripts/build_macos_standalone.py --no-cache`. It must use the exact verified
bootstrap acquisition cache, real Developer ID Application and Installer
identities from Team `9JKXQ94GK6`, and the `auralign-notary` Keychain profile.

Before committing, run the complete Python, Ruff, strict mypy, Swift unit/UI,
accessibility, Analyze, Release, compliance, REUSE, notices, SBOM, provenance,
privacy, secret/history, model, strict-offline, standalone-contract, and
Installer-design gates. After the source commit, fetch the origin, run
`scripts/check_private_push_authorization.py`, push normally, and prove exact
local/remote parity before building.

## Package Acceptance

The exact final PKG must pass all builder audits plus:

```bash
codesign --verify --strict --deep /path/to/Auralign.app
pkgutil --check-signature /path/to/Auralign-0.3.2-1-macos-arm64.pkg
spctl --assess --type execute --verbose=4 /path/to/Auralign.app
spctl --assess --type install --verbose=4 /path/to/Auralign-0.3.2-1-macos-arm64.pkg
xcrun stapler validate -v /path/to/Auralign-0.3.2-1-macos-arm64.pkg
```

Inspect both independent component packages, BOMs, scripts, Distribution,
Installer Light/Dark resources, app/runtime topology, bootstrap contents,
permissions, hashes, notices, SBOM, provenance, private-path exclusions,
entitlements, Team ID, Hardened Runtime, and ticket. The installed-facing Read
Me must say Auralign 0.3.2 and bootstrap resources 2.0.0.

Install the exact stapled PKG through native Installer, verify both receipts and
installed paths, launch from Finder, prove embedded-engine handshake and local
readiness behavior, and run a safe same-version reinstall. Native authorization
is a separate human macOS action; reaching the authorization sheet is not an
installation result. Clean-recipient evidence remains a separately reported
external gate if no second clean supported Mac is available.

## Private Publication

Only after local acceptance:

1. re-confirm the repository is private and the collaborator has write access;
2. confirm `macos-v0.3.2` is new locally and remotely;
3. create the immutable annotated tag at the exact accepted source commit and
   push it normally;
4. create the private GitHub release; and
5. upload only the final PKG, `SHA256SUMS`, complete-product CycloneDX SBOM,
   FFmpeg corresponding-source archive, and the privacy-safe package/model
   manifests and provenance records named by the accepted release allowlist.

Independently download the published PKG into a new private directory and prove
byte-for-byte SHA-256, signature, stapled ticket, tag, source, and local
install-tested artifact parity. Never change repository visibility, move an
existing tag, force-push, rewrite history, or publish raw validation evidence.

## Terminal Conditions

The release is incomplete for any dirty or unpushed source, reused authoritative
build directory, cache-influenced build, signing/team mismatch, notarization or
ticket failure, Installer-copy drift, failed install/reinstall/runtime gate,
unexpected package file, privacy/provenance failure, public exposure, or
local/published hash mismatch.
