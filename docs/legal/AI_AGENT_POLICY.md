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
- weaken a compliance, security, privacy, provenance, or release gate to obtain
  a passing result.

## Escalation

Stop and request owner review when evidence conflicts, an upstream license is
missing, copied expression may be present, a contribution lacks an executed
agreement, or the requested action would distribute content. Record a precise
blocked reason and the evidence needed to resolve it; do not use vague future-
work language or silently continue.
