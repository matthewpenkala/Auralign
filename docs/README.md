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
- [Apple Silicon](apple-silicon.md): native arm64 MLX/CPU topology, diagnostics,
  offline setup, QA, and evidence-gated MPS diarization.
- [Alignment](alignment.md): forced-alignment routing and fallback semantics.
- [Diarization](diarization.md): backends, device policy, and candidate scoring.

## Operations

- [Models](models.md): canonical human and AI-agent runbook for the manifest,
  connected acquisition, exact verification, and strict offline use.
- [Offline mode](offline.md): local cache requirements and failure behavior.
- [Docker](docker.md): container build and Compose profiles.
- [Troubleshooting](troubleshooting.md): common runtime and setup failures.

## Quality

- [Accuracy](accuracy.md): presets, re-decode, and evaluation boundaries.
- [Benchmarking](benchmarking.md): private-corpus methodology and reports.

## Interfaces

- [Native macOS GUI](macos-gui.md): SwiftUI workbench, engine-bridge trust
  boundary, source-development and embedded-runtime lanes, privacy, and gates.
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
  current patch-release runtime, signing, notarization, installation, and
  private-release parity contract.
- [Auralign 0.2.1 macOS release and redesign dossier](release/macos-v0.2.1-dossier.md):
  implemented workbench, accessibility and visual evidence boundaries, runtime
  layout, provenance, privacy, and final-candidate acceptance.

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
