<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign 0.3.1 macOS Release Dossier

## Status And Authority

This document records the successor 0.3.1 design, governance, and acceptance
contract. It does not claim that a final candidate already exists or that
Developer ID signing, notarization, stapling, Gatekeeper, installation,
publication, or clean-recipient checks have passed. Only direct evidence from
the exact final no-cache candidate can establish those results.

On 2026-07-25, Matthew Penkala recorded the owner project decision for the
model delivery described below. On 2026-08-16, he authorized the private 0.3.1
security and product-quality successor and its new bootstrap v2 identity. Those
are release-scope decisions, not legal advice, an AI legal opinion, a waiver,
ownership of third-party material, or a change to upstream terms. The accepted
0.2.0 and 0.2.1 releases and the governed 0.3.0/v1 records, artifacts, hashes,
and historical documents remain immutable evidence.

## Successor Changes

0.3.1 is the security and transcript-quality successor. Its locked runtime
advances `aiohttp` from 3.14.1 to 3.14.3 and NLTK from 3.9.4 to 3.10.3, and
replaces the vulnerable Lightning 2.6.5 registry source with exact upstream
security commit `d710d689510d50e800f53b3cd773cbca20b1f86f` under its verified
archive hash. The governed vulnerability inputs, multi-profile audit, component
registry, notices, requirements, lockfile, and SBOM checks bind that closure.
The standalone builder additionally applies one hash-bound build-only
normalization to that exact archive's `setup.py`: it relocates upstream's
random aggregate-MANIFEST temporary file outside the wheel package tree and
removes the aggregate wheel's redundant dependency on separately distributed
`pytorch-lightning` because this wheel already contains the exact patched
`lightning.pytorch` namespace. Both independent builds verify every original
and patched file hash and must produce an identical wheel. The changes do not
modify installed Lightning runtime code and remain recorded as Auralign
modifications under Apache-2.0.
After both source wheels are built, the final offline runtime installation uses
a hash-recorded projection of the locked requirements that excludes only those
already installed source-built distributions. This prevents `pip` from
silently rebuilding the original source URL while retaining hash enforcement
for every remaining dependency. Because every runtime dependency is already an
explicit locked entry, that pass disables transitive resolution; `pip check`,
installed-package inventory, runtime smoke, and SBOM validation then verify the
complete environment independently.

The transcript lane preserves immutable canonical words, timings, alignment,
speaker evidence, hashes, and provenance while using typed, source-span-bound
semantic edits and a globally decoded presentation plan for the clean
transcript. The current plan schema records exact scorer outputs and typed
evidence roles (`EXACT_MARK`, `BOUNDARY_ONLY`, `CASE_ONLY`, and
`STRUCTURAL_VETO`), replays every decision independently, and fails closed on
lexical drift, unsupported punctuation, unsafe contractions, malformed model
windows, missing required local engines, cross-format disagreement, or identity
leakage. Apple Foundation Models and SaT remain local proposal sources, not
authorities; SaT is boundary-only and excluded from the macOS 14 standalone.

The release also carries the narrowly bounded WhisperX short-segment alignment
repair, effective per-turn identity composition across every export, protected
UNKNOWN/Unattributed behavior, cleaned subtitle overlays, and capability-aware
native configuration. These are source-level claims until the exact fresh
no-cache, signed, notarized, stapled, installed 0.3.1 artifact passes its own
acceptance matrix.

## Product Boundary

One scriptless product PKG is intended to install two independently versioned
components:

```text
/Applications/Auralign.app

/Library/Application Support/com.auralign.studio/Models/
  com.auralign.studio.models.bootstrap.v2/
    model-bundle-manifest.json
    model-bundle-verification.json
    model-bundle-provenance.json
    model-bundle-complete.json
    THIRD-PARTY-NOTICES.txt
    licenses/
    models/
      pyannote--speaker-diarization-community-1/
      facebook--wav2vec2-base-960h/
      speechbrain--spkrec-ecapa-voxceleb/
      runtime-assets/nltk_data/
```

`Auralign.app` contains the standalone arm64 Python 3.11 engine, CTranslate2
and MLX runtimes, and reviewed native media closure, but no model weights. The
bootstrap component is `com.auralign.studio.models.bootstrap.v2` version
`2.0.0`, root-owned, read-only, and contains no executable code or ASR weight.

Mutable settings, logs, caches, output, and native-download packs stay in the
signed-in user's Library. The app and bootstrap component are never writable
runtime caches.

Recipients need no Python, `uv`, Git, Xcode, Command Line Tools, Homebrew,
source checkout, virtual environment, compiler, package manager, token copying,
shell configuration, or Terminal.

## Native ASR-Pack Delivery

The private release host limits each release asset to less than 2 GiB, while
individual exact ASR weights can exceed that limit even after compression.
The limit is recorded in GitHub's official
[release documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases);
the current Hugging Face storage/CDN endpoints are taken from its official
[model download documentation](https://huggingface.co/docs/hub/models-downloading).
The selected design follows established native macOS application-resource
delivery principles:

- no `preinstall` or `postinstall` network script;
- no privileged credential or download context;
- one signed, native, nonmodal **Complete Offline Setup** surface;
- `URLSession` downloads from revision-bound URLs sealed in the app catalog;
- private staging with resumable/retryable progress;
- exact allowlist, byte-count, SHA-256, license, notice, provenance,
  deterministic CycloneDX SBOM, permission, and completion checks; and
- atomic publish below
  `~/Library/Application Support/com.auralign.studio/Models/Packs/<pack-id>/<version>/`.

All sixteen ASR sources are public and owner-cleared; no recipient token or
Hugging Face account is required. Auralign may launch before an ASR pack is
present, but it must remain visibly blocked for transcription. Installation is
therefore not described as immediate offline readiness. One connected exact
pack setup is needed unless that pack is already installed/imported. Normal
processing is strictly local and offline afterward.

## Complete Model Disposition

The machine-readable authority and exact hashes are in
`src/local_transcript_max/config.py` and
`compliance/external_assets.toml`; the human exact-file tables are in
[`docs/models.md`](../models.md).

| Classification | Entries |
| --- | --- |
| Production ASR | `large-v3`, `turbo`, `distil-large-v3.5` |
| Optional ASR | `distil-large-v3`; `mlx-tiny`, `mlx-tiny.en`, `mlx-base`, `mlx-base.en`, `mlx-small`, `mlx-small.en`, `mlx-medium`, `mlx-medium.en`, `mlx-large`, `mlx-large-v2`, `mlx-large-v3`, `mlx-large-v3-turbo` |
| Production bootstrap | `community-1`, `en`, `ecapa` |
| Evaluation-only or blocked | none |

Normal presets deliberately map as follows:

| Preset | Model | Tradeoff |
| --- | --- | --- |
| `max` | `large-v3` | highest-quality general-purpose path |
| `balanced` | `turbo` | fast multilingual balance |
| `fast` | `distil-large-v3.5` | fastest normal English-only path |
| `cpu` | `turbo` | CPU-focused low-latency path |

Every model has an immutable revision, exact required-file allowlist,
size/SHA-256 map, license and attribution evidence, acquisition/use/
redistribution record, modification status, local and product destination,
backend compatibility, real local inference evidence, and package identity.
No production-visible selector is pointer-only.

## License And Clearance Boundary

- CTranslate2 and MLX Whisper model packs are recorded under their exact MIT
  evidence chains.
- Community-1 is declared CC-BY-4.0 by its pinned card/API; its pinned tree has
  no standalone `LICENSE`; its Hugging Face gate is recorded as an acquisition/
  contact mechanism.
- English Wav2Vec2 and ECAPA are Apache-2.0; the ECAPA/VoxCeleb training-data
  caveat remains.
- The deterministic Punkt resource is generated from empty NLTK 3.10.3
  parameters and contains no downloaded corpus.

The owner clearance record does not erase model-card omissions or convert the
AI into a legal approver. Complete applicable license text, attribution,
notices, provenance, hashes, and SBOM records must accompany every installed
bootstrap or native pack.

The Community-1 copy is bundled because the owner separately cleared
redistribution of those exact attributed bytes. A recipient does not need to
repeat the upstream acquisition gate, and the Installer license is not
represented as upstream Hugging Face consent.

## Runtime Resolution And Readiness

Release resolution accepts only:

- the exact root-owned bootstrap ID/version and its deterministic records; and
- the expected selected per-user pack ID/version from the signed catalog.

Wrong revisions, wrong pack IDs, missing or extra files, size/hash drift,
symlinks, hardlinks, unsafe permissions, malformed or missing licenses/notices/
provenance/SBOM/completion, or partial staging remain blockers. The app does
not select an arbitrary directory because expected filenames exist.

Changing backend/model/preset invalidates readiness. The bridge handshake may
be healthy while transcription remains blocked; **Ready** requires the exact
effective pack plus selected bootstrap backends to pass Python-authoritative
verification and startup.

## Package And Security Topology

Planned component identities are:

- app receipt `com.auralign.studio.pkg.app`, version `0.3.1.1`;
- bootstrap receipt `com.auralign.studio.pkg.models.bootstrap-v2`, version
  `2.0.0`.

Both component packages must be scriptless. Nested Mach-O objects are signed
inside-out with a real Developer ID Application identity, followed by the
runtime helper/container and outer app. The final product PKG is signed with a
Developer ID Installer identity from the same Team ID. `--deep` is for
recursive verification, not primary signing. No payload may mutate after its
applicable signing boundary.

The outer app has an exact empty entitlement dictionary. The purpose-built
runtime grants only its nested `bin/python3.11` executable
`com.apple.security.cs.allow-unsigned-executable-memory`, the narrow exception
required by the locked Numba/llvmlite JIT path used by MLX Whisper. Other
executables and libraries receive no runtime exception; library validation,
Gatekeeper, SIP, and the remaining Hardened Runtime protections stay enabled.

Notarization acceptance alone is insufficient. The exact PKG must be stapled
and pass `codesign`, `pkgutil`, `spctl` execute/install, `stapler`, expansion,
BOM, receipt, Distribution, scriptless-payload, model, privacy, and native
runtime checks.

## Privacy And Provenance

The app/bootstrap/native-pack payloads must exclude tokens, `.env`, raw
Hugging Face caches, download locks, private `.ltm-model.json` or
`model_audit.json`, user names, private paths, acquisition timestamps, media,
transcripts, profiles, embeddings, validation logs, and unrelated files.
Model bytes never enter Git or appear as loose GitHub release assets.

The Installer uses the imported Light and Dark Aqua presentation kit with
native text and controls. Its backgrounds remain text-free scaffolding; they
must not rasterize legal copy, fake controls, progress, security, or status.

## Acceptance Boundary

Repository tests, a structure-only PKG, a diagnostic cache, an ad-hoc
signature, local model directories, or 0.2.1 evidence cannot establish 0.3.1
acceptance. The exact clean pushed source and fresh `--no-cache` candidate must
directly pass:

- full Python, Ruff, strict mypy, Swift unit/signed UI, Xcode Analyze/Release,
  accessibility, bridge, lifecycle, privacy, compliance, notices, SBOM, and
  package-contract gates;
- exact app/bootstrap expansion and all sixteen signed-catalog/manifest
  consistency checks;
- real local backend load/inference for every model and every normal preset;
- native download interruption, corruption, unexpected-file, link,
  wrong-revision, and atomic-publication tests;
- Developer ID, Hardened Runtime, notarization, stapling, Gatekeeper, native
  install, same-version reinstall, and upgrade checks;
- Finder launch, single instance, bridge handshake, process reaping, and strict
  offline readiness after selected-pack setup;
- source/local/remote/tag parity and local/published PKG SHA-256 parity; and
- final private visibility, secret/history scan, clean worktree, and stale-
  process absence.

A separate clean supported Mac without developer tools remains the strongest
recipient evidence. If unavailable, the final report must name that limitation
rather than infer success.
