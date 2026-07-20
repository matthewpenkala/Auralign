<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign 0.2.0 Private macOS PKG Release

This is the owner-authorized release contract for the arm64, macOS 14+
standalone Auralign application. It supplements the source/wheel/native-engine
runbook; it does not change those artifact allowlists.

## Product Boundary

The release is one scriptless system-domain PKG installing
`/Applications/Auralign.app`. The app embeds CPython 3.11.15, Auralign's Python
engine, the exact hash-locked CPU dependency closure, source-built PyAV 15.1.0,
and a custom shared FFmpeg 7.1.5 closure under the sealed nested bundle
`Contents/Frameworks/AuralignEngineRuntime.bundle/Contents/Resources/Runtime`.
It contains no models, caches, tokens, media, transcripts, configuration,
telemetry, installer scripts, LaunchAgents, privileged helpers, or runtime
acquisition behavior.

Models remain separately acquired and verified under
[`docs/models.md`](../models.md). Installation alone does not establish
real-media or model readiness.

## Secure Apple Prerequisites

The release operator provisions these through Apple/Xcode and the login
Keychain:

- a valid `Developer ID Application` certificate and private key;
- a valid `Developer ID Installer` certificate and private key from the same
  Team ID; and
- a `notarytool` Keychain profile backed by an App Store Connect API key or an
  Apple app-specific password.

Never provide the primary Apple Account password to a build script, commit a
certificate/private key, or place a notarization secret in a command argument.
Only the Keychain profile name and public certificate labels are passed to the
builder.

## Source Gates

From clean private `main`:

```bash
uv run --frozen python scripts/check_release_requirements.py
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen mypy src/local_transcript_max
uv run --python 3.11 reuse lint
uv run --frozen python scripts/check_compliance.py
uv run --frozen python scripts/generate_third_party_notices.py --check
bash scripts/check_macos_gui.sh
python3 packaging/macos/installer-design/build.py --check-only
```

The exact four-model strict-offline verification is separate and must also pass
on the release host. No test fixture substitutes for that model/runtime gate.

## Build, Sign, Notarize, And Staple

List the installed public identity labels without exporting credentials:

```bash
security find-identity -v
```

Then run the deterministic builder with the exact labels and Keychain profile:

```bash
uv run --frozen python scripts/build_macos_standalone.py \
  --work-dir "$PWD/build/macos-standalone-v0.2.0" \
  --no-cache \
  --developer-application-identity \
    "Developer ID Application: OWNER (TEAMID)" \
  --developer-installer-identity \
    "Developer ID Installer: OWNER (TEAMID)" \
  --notary-profile "auralign-notary"
```

Release mode refuses a dirty worktree, a non-`main` branch, or omission of
`--no-cache`. It verifies immutable source archives and FFmpeg's signing key,
builds an arm64-only runtime, validates every dyld edge and macOS deployment
target, performs privacy and quarantine scans, generates the exact runtime
SBOM, smokes the native stack and bridge, signs nested code inside-out, builds
the scriptless product archive with the native Installer presentation
resources, notarizes, staples, re-expands, and reverifies the final package.

For disposable structure-only diagnostics, the builder may reuse
content-addressed download, native, and runtime stages only after their input
manifests and complete payload hashes match. Cache entries are published
atomically and fail closed on missing, stale, modified, or ambiguous content.
Those ad-hoc-signed outputs are labeled `UNSIGNED-TEST` and are never release
evidence. The authoritative Developer ID build always uses `--no-cache`.

## Allowlisted Private Release Assets

Only these seven files from the final work directory are macOS release assets:

- `Auralign-0.2.0-1-macos-arm64.pkg`;
- `ffmpeg-7.1.5.tar.xz`;
- `Auralign-0.2.0-1-macos-arm64.cdx.json`;
- `runtime-manifest.json`;
- `signed-app-manifest.json`;
- `macos-pkg-release.json`; and
- `SHA256SUMS`.

Notarization logs, build logs, result bundles, local model evidence, screenshots,
expanded packages, certificates, and install receipts stay owner-local.

## Installation Acceptance

Before publication:

```bash
pkgutil --check-signature build/macos-standalone-v0.2.0/Auralign-0.2.0-1-macos-arm64.pkg
spctl --assess --type install --verbose=4 \
  build/macos-standalone-v0.2.0/Auralign-0.2.0-1-macos-arm64.pkg
xcrun stapler validate -v \
  build/macos-standalone-v0.2.0/Auralign-0.2.0-1-macos-arm64.pkg
sudo installer -pkg \
  build/macos-standalone-v0.2.0/Auralign-0.2.0-1-macos-arm64.pkg \
  -target /
```

Verify the `/Applications/Auralign.app` signature, launch from Finder, embedded
bridge handshake, single-instance behavior, app-owned process reaping, reinstall
of the same version, receipt identifier/version, local/offline posture, and user
data preservation. A separate clean macOS 14 Apple Silicon host or VM is required
to prove the no-Python/no-Homebrew/no-Git fresh-machine claim.

## Private Publication And Parity

After the source commit is pushed and local/remote SHA parity is proven, create
the annotated tag `macos-v0.2.0` at that exact commit and push it normally.
Confirm repository visibility is `PRIVATE`, then create a private GitHub release
containing only the seven allowlisted files. Download the release to a new local
directory and verify filenames, sizes, SHA-256 values, tag SHA, package signature,
notarization ticket, and `SHA256SUMS` parity.

No force-push, history rewrite, public release, visibility change, model upload,
or extra asset is authorized.
