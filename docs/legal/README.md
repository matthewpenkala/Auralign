<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Legal And Compliance Index

This directory is the canonical human-readable index for Auralign's licensing,
provenance, external-asset, distribution, and automated-agent rules. Auralign is
source-available/shared-source software, not open-source software.

## Controlling Terms By Scope

- Terms governing an identified third-party component or external asset control
  that material and its use. No Auralign agreement waives or replaces them.
- [`LICENSE`](../../LICENSE), the PolyForm Internal Use License 1.0.0, controls
  Auralign-owned material unless the rights holder signs a separate written
  agreement for a specific recipient and use. Such an agreement can change only
  rights the Auralign rights holder is authorized to grant.
- [`NOTICE`](../../NOTICE), [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md),
  machine-readable component records, and package metadata identify scope and
  obligations. Plain-language documentation summarizes those records.

Summaries, repository access controls, SPDX identifiers, package metadata, and
agent instructions do not replace, waive, or expand controlling license terms.

## Documents

- [Licensing](LICENSING.md): permissions, restrictions, first-party scope, and
  separate-agreement boundaries.
- [Provenance](PROVENANCE.md): ownership, Git-history limits, AI assistance,
  upstream-source review, and publication conditions.
- [External assets](EXTERNAL_ASSETS.md): dependencies, models, datasets, native
  libraries, and data-rights boundaries.
- [Distribution](DISTRIBUTION.md): source, Python archives, containers, external
  assets, customer delivery, and release controls.
- [AI agent policy](AI_AGENT_POLICY.md): mandatory repository rules for
  automated tools and agents.
- [Security and privacy](../security-privacy.md): sensitive data and operational
  trust boundaries.

## Scope

Unless a file carries a different notice, Auralign-owned source code, tests,
documentation, configuration, workflows, and examples use SPDX identifier
`LicenseRef-PolyForm-Internal-Use-1.0.0` and the required notice `Copyright 2026
Matthew Penkala`. External components and assets keep their own terms.

Questions about additional rights must be handled by the repository owner under
a separate written agreement. Do not open an issue containing confidential
terms, private source, security details, recordings, transcripts, credentials,
speaker identity material, or other sensitive information.
