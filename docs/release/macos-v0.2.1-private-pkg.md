<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign 0.2.1 Private macOS PKG Release

This is the owner-authorized release contract for the arm64, macOS 14+
standalone Auralign 0.2.1 application. It supersedes the 0.2.0 macOS contract
for new builds without moving, overwriting, or repurposing the immutable
`macos-v0.2.0` tag or release.

## Product Boundary

The release is one scriptless system-domain PKG installing
`/Applications/Auralign.app`. The app embeds CPython 3.11.15, the reviewed
`local-transcript-max` 0.2.0 engine API, the exact hash-locked CPU dependency
closure, source-built PyAV 15.1.0, and a custom shared FFmpeg 7.1.5 closure
under the sealed nested bundle
`Contents/Frameworks/AuralignEngineRuntime.bundle/Contents/Resources/Runtime`.
The app version and package receipt version are 0.2.1 and 0.2.1.1
respectively. The embedded engine API version remains 0.2.0 intentionally;
the immutable source revision and source-tree digest identify the patched
engine bytes.

The product contains no models, caches, tokens, media, transcripts,
user-generated configuration, telemetry, installer scripts, LaunchAgents,
privileged helpers, or runtime acquisition behavior. The reviewed canonical
configuration manifest and field catalog remain sealed app resources. Models
remain separately acquired and verified under
[`docs/models.md`](../models.md). Installation alone does not establish
real-media or model readiness.

## Secure Apple Prerequisites

The release operator provisions these only through Apple/Xcode and the login
Keychain:

- a valid `Developer ID Application` certificate and private key;
- a valid `Developer ID Installer` certificate and private key from the same
  Team ID; and
- a `notarytool` Keychain profile backed by an App Store Connect API key or an
  Apple app-specific password.

Never provide the primary Apple Account password to a build script, commit a
certificate or private key, or place a notarization secret in a command
argument. Only the Keychain profile name and public certificate labels are
passed to the builder.

## Source Gates

From clean private `main`, run the applicable Python, Swift/Xcode, compliance,
privacy, history, distribution, model, Installer-design, and standalone
contract gates serially. The minimum source sequence is:

```bash
uv lock --check
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B scripts/check_release_requirements.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pytest -q -p no:cacheprovider
uv run --frozen ruff check .
uv run --frozen mypy src/local_transcript_max
uv run --python 3.11 reuse lint
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B scripts/check_compliance.py
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B scripts/generate_third_party_notices.py --check
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B scripts/check_reachable_history.py
bash scripts/check_macos_gui.sh
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B \
  packaging/macos/installer-design/build.py --check-only

release_gate_root="$(mktemp -d /private/tmp/auralign-source-gates.XXXXXX)"
release_dist_dir="$release_gate_root/dist"
release_sbom_dir="$release_gate_root/sbom"
release_exported="$release_gate_root/exported.txt"
release_audit_input="$release_gate_root/audit-input.txt"
release_audit="$release_gate_root/audit.json"
mkdir "$release_dist_dir" "$release_sbom_dir"
PYTHONDONTWRITEBYTECODE=1 uv build --out-dir "$release_dist_dir"
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B \
  scripts/verify_distribution.py \
  --dist-dir "$release_dist_dir" \
  --sbom-dir "$release_sbom_dir"
uv export --locked --extra full --group cpu --no-dev --no-emit-project \
  --output-file "$release_exported"
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B \
  scripts/prepare_vulnerability_audit_input.py \
  "$release_exported" "$release_audit_input"
release_audit_status=0
PYTHONDONTWRITEBYTECODE=1 uv run --frozen pip-audit \
  -r "$release_audit_input" --disable-pip --no-deps --strict \
  --progress-spinner off -f json -o "$release_audit" \
  || release_audit_status=$?
test "$release_audit_status" -eq 0 || test "$release_audit_status" -eq 1
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B \
  scripts/check_vulnerability_audit.py "$release_audit"

PYTHONDONTWRITEBYTECODE=1 uv run --frozen ltm models verify \
  --asr large-v3 --diarization community-1 --alignment en --speaker-id ecapa
PYTHONDONTWRITEBYTECODE=1 LTM_OFFLINE=1 uv run --frozen ltm doctor \
  --startup --config examples/config.strict-offline.yaml
```

The temporary root makes wheel, sdist, SBOM, and vulnerability outputs
candidate-specific and initially absent. Preserve its privacy-safe hashes in
the release ledger, then remove it during final task-owned cleanup. The exact
four-model strict-offline verification must pass on the release host; no test
fixture substitutes for that model/runtime gate.

## Release-Source Commit And Private Main Synchronization

Review tracked, staged, unstaged, ignored, and untracked scope before committing.
The release-source commit must contain only the intentional source change and
must leave no media, transcripts, models, caches, credentials, build products,
or private evidence in the index. After committing on `main`, run:

```bash
git fetch origin main
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B \
  scripts/check_private_push_authorization.py
git push origin main
release_source_sha="$(git rev-parse HEAD)"
release_remote_sha="$(git ls-remote --exit-code origin refs/heads/main | cut -f1)"
test "$release_source_sha" = "$(git rev-parse origin/main)"
test "$release_source_sha" = "$release_remote_sha"
test -z "$(git status --porcelain)"
```

The authorization check is post-commit and fails closed on a dirty tree or
missing current write access. Push normally; never force, mirror, or rewrite
history. Build only from the exact clean synchronized SHA.

## Build, Sign, Notarize, And Staple

Run the deterministic builder from the exact clean pushed source commit with
the Keychain-provisioned identity labels and profile:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen python -B \
  scripts/build_macos_standalone.py \
  --work-dir "$PWD/build/macos-standalone-v0.2.1" \
  --no-cache \
  --developer-application-identity \
    "Developer ID Application: OWNER (TEAMID)" \
  --developer-installer-identity \
    "Developer ID Installer: OWNER (TEAMID)" \
  --notary-profile "auralign-notary"
```

Release mode refuses a dirty worktree, a non-`main` branch, an existing build
destination, or omission of `--no-cache`. The production lane never reads or
publishes diagnostic cache entries. It verifies immutable source archives and
FFmpeg's signing key, builds an arm64-only runtime, validates every dyld edge
and macOS deployment target, and removes reviewed production-unreachable
test/fixture/sample-data trees, the pyannote example-media fixture, and nine
upstream package model weights from the copied runtime. It also removes
exact-hash-bound upstream documentation, recipes, demos, templates, tools,
benchmarks, and test examples. The exact test, fixture, development-content,
and file inventories are hash-bound; only four reviewed NumPy, SymPy, and Torch
public or lazy-compatibility testing API directories needed by traced or
conservative compatibility paths remain. The removed weights are outside
Auralign's external model manifest and include the optional faster-whisper
Silero VAD model; the standalone therefore fails clearly if
`asr.vad_filter=true`, while the default model-free EnergyVAD path remains
available. The prune evidence records the original file/tree hashes,
content-addressed absent result states, and the unchanged source/wheel/archive
boundary. The builder then performs privacy and quarantine scans, generates
the exact runtime SBOM, smokes the supported native and Python stack plus
bridge, signs nested code inside-out, builds the scriptless product archive
with native Installer presentation resources, and requires an accepted,
issue-free Apple notarization log before stapling, re-expanding, and
reverifying the final package.

Diagnostic cache entries are content-addressed development aids only. They are
fully revalidated, restored only into absent destinations, and labeled as
non-authoritative. Structure-only output and any ad-hoc signature are never
release evidence.

## Allowlisted Private Release Assets

Only these seven files from the final work directory are macOS 0.2.1 release
assets:

- `Auralign-0.2.1-1-macos-arm64.pkg`;
- `ffmpeg-7.1.5.tar.xz`;
- `Auralign-0.2.1-1-macos-arm64.cdx.json`;
- `runtime-manifest.json`;
- `signed-app-manifest.json`;
- `macos-pkg-release.json`; and
- `SHA256SUMS`.

Notarization logs, build logs, result bundles, local model evidence,
screenshots, expanded packages, certificates, and install receipts stay
owner-local.

## Installation Acceptance

Before publication, verify the PKG signature, Gatekeeper decision, stapled
ticket, expanded scriptless payload, and exact signed-app inventory. Install
the same accepted PKG through the native Installer path and verify:

- `/Applications/Auralign.app` and receipt
  `com.auralign.studio.pkg.app` version 0.2.1.1;
- the installed app's strict nested signatures and Gatekeeper acceptance;
- Finder launch, single-instance behavior, bridge handshake, and owned-process
  reaping;
- the embedded Python, PyAV, Torch, TorchCodec, FFmpeg, and FFprobe smoke;
- truthful external-model readiness and safe same-version reinstall; and
- user data outside the sealed application bundle.

A separate clean macOS 14 Apple Silicon host or VM is still required to prove
the no-Python, no-Homebrew, no-Git fresh-machine claim.

## Private Publication And Parity

After the release-source commit is pushed and local/remote SHA parity is
proven, first verify private visibility and prove that the tag is absent both
locally and remotely:

```bash
test "$(gh repo view --json visibility --jq .visibility)" = "PRIVATE"
test -z "$(git tag --list macos-v0.2.1)"
git ls-remote --exit-code origin refs/heads/main >/dev/null
test -z "$(git ls-remote --tags origin refs/tags/macos-v0.2.1)"
```

Only then create the annotated tag `macos-v0.2.1` at the exact synchronized
source SHA and push it normally. Create the private GitHub release only after
the final package has passed signing, notarization, stapling, Gatekeeper, and
installation acceptance. Upload exactly the seven allowlisted files. Download
the release to a new owner-local directory and verify filenames, sizes,
SHA-256 values, tag SHA, package signature, notarization ticket, and
`SHA256SUMS` parity.

No force-push, history rewrite, public release, visibility change, model
upload, or extra asset is authorized.
