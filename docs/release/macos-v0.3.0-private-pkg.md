<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign 0.3.0 Private macOS PKG Release

## Release Authority And Evidence Status

This is the execution contract for a new private arm64 macOS 14+ Auralign
0.3.0 release. It does not claim that a final candidate has already been built,
signed, notarized, stapled, Gatekeeper-assessed, installed, or published.

The immutable 0.2.0 and 0.2.1 tags, releases, assets, hashes, and historical
documents must not be moved or reused as successor evidence. The authorized
new scope is the existing private repository, a normal reviewed `main` push, a
new nonconflicting `macos-v0.3.0` tag, the exact accepted product PKG, and only
privacy-safe allowlisted evidence. No public exposure, visibility change,
force-push, history rewrite, loose model upload, raw cache, credentials, media,
transcripts, or raw validation logs are authorized.

## Shipping Product

One scriptless product PKG installs:

- `/Applications/Auralign.app`; and
- `/Library/Application Support/com.auralign.studio/Models/com.auralign.studio.models.bootstrap.v1/`.

The app contains the arm64 standalone engine, all executable dependencies,
CTranslate2/MLX runtimes, FFmpeg, FFprobe, PyAV, Torch, TorchCodec, torchaudio,
and required native libraries. It contains no model weights.

The root-owned bootstrap component is version `1.0.0` and contains only exact
Community-1, English alignment, ECAPA, deterministic corpus-free Punkt,
licenses, notices, provenance, verification, CycloneDX SBOM, and completion
records. It contains no ASR weight or executable code.

Component receipt targets are:

- `com.auralign.studio.pkg.app` at `0.3.0.1`;
- `com.auralign.studio.pkg.models.bootstrap-v1` at `1.0.0`.

## Native ASR Packs

All sixteen public ASR choices are delivered after installation by the signed
app's native model manager because the exact bytes exceed the private release
host's per-asset limit. Each pack is version `1.0.0` under:

```text
~/Library/Application Support/com.auralign.studio/Models/Packs/
  <pack-id>/1.0.0/
```

The native flow:

1. reads only revision-bound URLs and contracts from the signed catalog;
2. uses `URLSession` in the user session, never Installer/root;
3. stages in a private task directory;
4. supports truthful progress, cancellation, and safe retry/resume;
5. rejects unknown redirects, traversal, missing/extra files, size/hash drift,
   symlinks, hardlinks, unsafe modes, malformed licenses/notices/provenance/
   CycloneDX/completion, or a wrong pack identity;
6. atomically publishes only the complete verified pack; and
7. runs Python-authoritative readiness against the exact effective config.

The public packs require no account or token. No download occurs in
`preinstall`, `postinstall`, doctor, readiness, or transcription. The user
needs one connected **Complete Offline Setup** pass unless the exact pack is
already installed/imported. The product must not be called immediately
offline-ready before that completes.

The signed redirect policy is bounded to the current HTTPS endpoints listed by
Hugging Face's official
[model download/firewall guidance](https://huggingface.co/docs/hub/models-downloading).
Before source freeze, release-machine HEAD probes of the largest exact file in
all sixteen packs must succeed through that policy without downloading payload
bytes or supplying credentials. An endpoint change requires a reviewed catalog
and app update; it is never accepted dynamically.

Normal presets are `max=large-v3`, `balanced=turbo`,
`fast=distil-large-v3.5`, and `cpu=turbo`. `distil-large-v3` and all twelve
MLX packs are optional advanced choices. Exact revisions, files, sizes,
SHA-256 values, license records, pack IDs, and classifications are in
[`docs/models.md`](../models.md) and the two machine-readable authorities.

## Maintainer-Only Build Preparation

`scripts/build_macos_standalone.py` is a release-machine orchestrator.
Maintainers may need Xcode, Command Line Tools, compilers, `uv`, Python,
package tools, network access, an ignored verified acquisition cache, and Apple
Keychain identities. Recipients need none of those.

The authoritative build must:

1. use the exact reviewed clean source SHA already pushed to private
   `origin/main`;
2. begin with a new absent uniquely named output directory;
3. use `--no-cache` so diagnostic cache entries cannot influence or receive
   production output;
4. verify locked runtime/dependency/source inputs;
5. build and first-execute the reviewed LGPL FFmpeg closure;
6. assemble the purpose-built relocatable Python/MLX/native runtime rather than
   copy `.venv`;
7. assemble only the exact three-model/Punkt bootstrap from an ignored
   controlled source cache;
8. generate deterministic bootstrap manifest, verification, provenance,
   notices, license texts, CycloneDX SBOM, and completion records;
9. embed the runtime and signed model-delivery catalog into the Release app;
10. prove no checkout, Homebrew, pyenv, `.venv`, `$PATH`, temporary, user, or
    private path escapes;
11. sign nested code inside-out, then app, component packages, and product PKG;
12. notarize, staple, validate, expand, and audit the exact final PKG; and
13. emit privacy-safe manifests, hashes, inventories, notices, SBOM, and
    provenance evidence.

Every embedded-Python probe must use `-B` and
`PYTHONDONTWRITEBYTECODE=1`. No payload may mutate after its signing boundary.

## Community-1 Boundary

Community-1 revision
`3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` is bundled in the bootstrap
component. Its pinned official model card/API declare CC-BY-4.0; the pinned
tree has no standalone `LICENSE`; and the automatic Hugging Face gate records
contact/use-case fields without a separately recorded use-restriction
checkbox. A direct acquirer must complete that upstream flow.

Matthew Penkala separately recorded the project decision to redistribute the
exact pinned attributed bytes in this private product. Therefore the recipient
does not need a Hugging Face account or token. Installer acceptance is not
represented as Hugging Face/pyannote consent. Complete attribution, CC-BY-4.0
text, model-card evidence, and provenance must remain in the component.

## Source Freeze And Serialized Gates

Before the release-source commit:

1. finish all current documentation, manifests, notices, and policies;
2. inventory tracked, staged, unstaged, ignored, and untracked scope;
3. exclude weights, caches, tokens, private evidence, media, transcripts,
   DerivedData, temporary downloads, and unrelated files;
4. run the complete Python, Ruff, mypy, Swift, signed UI, Analyze, Release,
   accessibility, compliance, REUSE, notices, provenance, SBOM, privacy,
   history, secret, model, standalone, Installer, and package gates serially;
5. review and create the intentional source commit;
6. fetch the private origin;
7. run `scripts/check_private_push_authorization.py` on the clean committed
   tree; and
8. push normally to private `origin/main`, proving local/remote/live parity.

Any later source/runtime-layout/signing/package/model/compliance/privacy repair
invalidates only dependent evidence, but the new source must be reviewed,
committed, normally pushed, and used for a fresh authoritative build.

Close the native-pack inference matrix from the exact controlled acquisition
roots before accepting the candidate:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --frozen --python 3.11 \
  --extra full --extra mlx --group cpu \
  python -B scripts/run_native_model_acceptance.py \
  --source-root /OWNER-LOCAL/strict-model-cache \
  --source-root /OWNER-LOCAL/ctranslate2-model-cache \
  --source-root /OWNER-LOCAL/mlx-model-cache \
  --work-dir /ABSENT/PRIVATE/native-model-acceptance \
  --audio /OWNER-LOCAL/bounded-synthetic-speech.wav
```

The runner must resolve all sixteen native-download entries exactly once,
assemble and reverify every pack, and pass sixteen model plus four normal-preset
real backend cases with Python IP networking denied. Retain only its final
privacy-safe JSON: it excludes local paths, transcript text, audio hashes,
credentials, acquisition markers, cache metadata, and raw backend output.
Those source roots and the bounded audio fixture remain ignored owner-local
inputs and are never staged or published.

## Signing, Notarization, And Package Acceptance

Use only real Keychain Developer ID Application and Developer ID Installer
identities from the same Team ID plus an Apple-supported `notarytool` Keychain
profile. Never request the primary Apple password in chat or place credentials
in arguments, files, logs, or reports.

Required final checks include:

```bash
codesign --verify --strict --deep /path/to/Auralign.app
pkgutil --check-signature /path/to/Auralign-0.3.0-1-macos-arm64.pkg
spctl --assess --type execute --verbose=4 /path/to/Auralign.app
spctl --assess --type install --verbose=4 /path/to/Auralign-0.3.0-1-macos-arm64.pkg
xcrun stapler validate -v /path/to/Auralign-0.3.0-1-macos-arm64.pkg
```

Also inspect Developer ID authorities/team, Hardened Runtime, entitlements,
notary submission result, ticket, expanded product/components, BOMs, receipts,
Distribution, Installer resources, script absence, app/runtime topology,
bootstrap contents/modes/hashes, model exclusion from the app, executable-code
exclusion from the bootstrap component, and private/credential/path scans.

## Native Installation And User Acceptance

Install the exact stapled PKG through native Installer. A Touch ID or standard
macOS authorization sheet may satisfy local admin authorization; package
identity must still be proven independently.

Verify:

- app and bootstrap paths plus both exact receipts;
- root-owned readable/non-writable bootstrap tree;
- app signature, Gatekeeper status, ticket, and embedded runtime;
- Installer Light and Dark presentation with native text/controls;
- Finder launch, app identity/icon, single-instance behavior, bridge handshake,
  process ownership, cancellation, shutdown, and reaping;
- safe same-version reinstall and 0.2.1-to-0.3.0 upgrade without user-data loss;
- truthful blocked readiness before an ASR pack exists;
- keyboard/VoiceOver-accessible native setup without Terminal or token;
- exact download verification and atomic publication;
- each normal preset's packaged-engine real inference;
- bounded real local inference for every optional model;
- strict offline readiness and processing after setup; and
- no Python, Xcode, Command Line Tools, Homebrew, Git, checkout, `.venv`,
  compiler, package manager, shell, token, or network dependency during normal
  processing.

Clean-recipient evidence must be reported separately and never inferred. If a
separate clean supported Mac/new user/quarantine-bearing download is
unavailable, name that precise limitation.

## Private Publication And Parity

Only after all acceptance gates pass:

1. confirm the GitHub repository is still private;
2. confirm `macos-v0.3.0` is new locally/remotely;
3. create the immutable tag at the exact accepted source SHA and push normally;
4. create the private release;
5. upload only the exact allowlisted final PKG, `SHA256SUMS`, complete-product
   SBOM, FFmpeg corresponding source, and privacy-safe approved provenance
   records;
6. never upload loose model packs/weights, caches, raw logs, screenshots,
   expanded payloads, credentials, media, transcripts, private-path evidence,
   or local-only evidence; and
7. independently download the published PKG and prove byte-for-byte SHA-256,
   signature, ticket, tag, and source parity with the locally install-tested
   artifact.

Generated final artifact hashes and notarization/package evidence belong in
the accepted release/evidence directory, not in this pre-build tracked
runbook.

## Terminal Conditions

The release is incomplete for any dirty/unpushed source, cache-influenced
authoritative output, unresolved rights conflict, model/catalog/manifest drift,
wrong or partial pack, Installer script, credential/private-path leakage,
developer-tool runtime dependency, signing/team mismatch, notarization issue,
unstapled ticket, failed Gatekeeper/install/reinstall/runtime/inference check,
public exposure, tag/source drift, or local/published package hash mismatch.
