<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Documentation

The README is the production entry point. These focused documents are the
canonical sources for implementation details and operational policy.

## Architecture and Runtime

- [Architecture](architecture.md): repository spine, runtime stages, and
  validation boundaries.
- [Apple Silicon](apple-silicon.md): arm64 standalone topology, native
  revision-pinned ASR packs, MLX support, and CPU/MPS evidence boundaries.
- [Alignment](alignment.md): forced-alignment routing and fallback semantics.
- [Diarization](diarization.md): backends, device policy, and candidate scoring.

## Operations

- [Models](models.md): canonical human and AI-agent runbook for all nineteen
  entries, bootstrap/native delivery, exact verification, and offline use.
- [Offline mode](offline.md): local cache requirements and failure behavior.
- [Docker](docker.md): container build and Compose profiles.
- [Troubleshooting](troubleshooting.md): common runtime and setup failures.

## Quality

- [Accuracy](accuracy.md): source-development preset history, re-decode, and
  evaluation boundaries; the authoritative 0.3.0 preset/model mapping is in
  [Models](models.md).
- [Benchmarking](benchmarking.md): private-corpus methodology and reports.

## Interfaces

- [Native macOS GUI](macos-gui.md): SwiftUI workbench, engine-bridge trust
  boundary, source-development and embedded-runtime lanes, privacy, and gates.
- [July 28, 2026 macOS Neural Audio Intelligence redesign](design/macos-ui-redesign-2026-07-28.md):
  reconstructed requirements, attached-bundle research, audit findings,
  implementation, architecture decision, closure ledger, and validation record.
- [API](api.md): localhost service and job lifecycle.
- [Speaker identification](speaker-id.md): local profiles, matching, and
  offline identity resolution and named-speaker delivery.
- [Speaker identity resolver specification](design/speaker-identity-resolver.md):
  supported v1 evidence, decision, validation, and artifact contracts.
- [Auralign 0.2.0 private runbook](release/v0.2.0-private-runbook.md): native
  installation, model preparation, trusted validation, and owner-only release.
- [Auralign 0.2.0 private macOS PKG](release/macos-v0.2.0-private-pkg.md):
  standalone runtime, Developer ID signing, notarization, installation, and
  private-release parity.
- [Auralign 0.2.1 private macOS PKG](release/macos-v0.2.1-private-pkg.md):
  immutable accepted predecessor runtime, signing, notarization, installation,
  and private-release parity contract.
- [Auralign 0.2.1 macOS release and redesign dossier](release/macos-v0.2.1-dossier.md):
  immutable predecessor workbench, accessibility and visual evidence
  boundaries, runtime layout, provenance, privacy, and final-candidate
  acceptance.
- [Auralign 0.3.0 private macOS PKG](release/macos-v0.3.0-private-pkg.md):
  successor model-inclusive private release, root-owned model component,
  signing/notarization, installation, and parity contract.
- [Auralign 0.3.0 macOS release dossier](release/macos-v0.3.0-dossier.md):
  successor bootstrap-plus-native-pack topology, all 19 model dispositions,
  recipient independence, privacy, provenance, and acceptance boundaries.

## Safety

- [Security and privacy](security-privacy.md): sensitive data, tokens, caches,
  offline behavior, and raw-artifact preservation.

## Legal And Compliance

- [Legal and compliance index](legal/README.md): controlling authority and
  repository legal map.
- [Licensing](legal/LICENSING.md): PolyForm internal-use scope and separate-
  agreement boundaries.
- [Provenance](legal/PROVENANCE.md): ownership record, upstream review, and AI
  assistance policy.
- [External assets](legal/EXTERNAL_ASSETS.md): dependencies, models, datasets,
  native libraries, and data-rights boundaries.
- [Distribution](legal/DISTRIBUTION.md): source/package release rules and the
  container-publication block.
- [AI agent policy](legal/AI_AGENT_POLICY.md): mandatory automated-tool rules.

## Production-Readiness Contract

- [Production-readiness gates](production-readiness/PRODUCTION_READINESS.md):
  implementation traceability, verification contract, and release boundaries.
