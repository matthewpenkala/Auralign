<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Security Policy

## Reporting A Vulnerability

Do not report vulnerabilities in a public issue, discussion, pull request, or
commit message. When the private GitHub remote is available, use its **Security**
tab and private vulnerability-reporting/advisory workflow. Before then, contact
the repository owner through an already established private channel.

Do not include raw audio/video, transcripts, speaker identities, embeddings,
profiles, tokens, `.env` content, private source, customer data, model/dataset
caches, generated job output, or unredacted local paths in an initial report.
Provide the minimum redacted reproduction. The owner will arrange an approved
private transfer if sensitive evidence is necessary.

Include:

- affected revision and component;
- impact and required preconditions;
- minimal, redacted reproduction steps;
- whether exploitation requires network access or untrusted media;
- suggested mitigation, if known.

Do not test against systems, repositories, media, accounts, or data you are not
authorized to use. Do not publish details or request a CVE on the project's
behalf before coordinating with the owner.

## Supported Versions

Until formal releases exist, only the current approved `main` revision is in
scope for security fixes. Older commits, local branches, development artifacts,
unverified models/datasets, and externally modified builds are unsupported.

## Operational Boundary

Auralign is local/offline-first but processes untrusted media and invokes
FFmpeg, ML frameworks, model files, and optional API/cleanup services. Keep
dependencies patched, run the API on loopback unless protected externally, use
read-scoped model tokens, validate acquired assets, and follow
[`docs/security-privacy.md`](docs/security-privacy.md).
