<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Provenance And Rights Record

## First-Party Record

The intended licensor and rights holder for Auralign-owned material is Matthew
Penkala. The private pre-publication Git history was preserved outside the
publication repository before history sanitization. Its Git identities are
evidence of repository attribution only: names and email addresses do not prove
copyright ownership, employment clearance, authorization to license, or the
provenance of every contribution.

The repository classifies tracked paths as follows:

| Path class | Provenance disposition |
| --- | --- |
| `src/`, `tests/`, `docs/`, `examples/`, scripts, configuration, and workflows | Auralign-first-party unless a more specific in-file or registry record applies |
| `uv.lock` and generated manifests/notices | Machine-maintained output derived from first-party configuration and upstream metadata |
| `LICENSE` and `LICENSES/` | Standard license text from the PolyForm Project plus Auralign's required notice; not Auralign application code |
| Declared Python packages and build tools | Referenced, not vendored into the source tree; governed by upstream terms |
| Models, datasets, media, caches, profiles, and run output | External/private material; excluded from source and package archives |
| Base images, OS packages, FFmpeg/codecs, and native libraries | External container/runtime material; governed by their own terms |

`REUSE.toml`, file-level SPDX tags, `THIRD_PARTY_NOTICES.md`, and the external
asset registry together form the path and component rights ledger. A more
specific upstream notice always overrides the first-party default.

## Chain-Of-Title Publication Condition

Before the first remote publication, Matthew Penkala must personally review and
record approval of the following declaration in the private publication record:

> I confirm that I own, or am authorized to license, the Auralign material
> identified as first-party; that no employer, client, contractor, contributor,
> assignment, confidentiality duty, prior agreement, or other obligation prevents
> the offered license; and that all third-party material has been identified and
> preserved under its applicable terms.

An automated agent cannot execute this declaration, provide a signature, or
infer assent from a request to edit the repository. Matthew Penkala personally
confirmed the declaration and his controlling licensing authority in the
private publication record dated 2026-07-10. That user-authored record is
preserved outside the repository; this document records only that the gate was
satisfied for the authorized private source remote. It does not clear public
publication, customer delivery, hosted service use, containers, models,
datasets, or disputed later evidence.

## `Adapt whispermlx pipeline improvements`

The pre-publication change titled `Adapt whispermlx pipeline improvements`
changed Auralign's MLX, alignment, diarization, export, configuration, and test
implementation. The recorded review compared Auralign against
[`KalebJS/whispermlx`](https://github.com/KalebJS/whispermlx) at upstream commit
`37816743c29a569405f300bbb4b3ef8001152651` (tag `v3.12.2`) using a shallow,
read-only checkout. The review
inspected the upstream README, package metadata, ASR, transcription,
diarization, VAD, alignment, subtitle, schema, utility, and related test files,
then compared the relevant Auralign modules and tests.

The technical review disposition is **concepts and public interfaces only**.
Auralign
implemented local model-alias ergonomics, opt-in VAD-windowed ASR, progress and
alignment diagnostics, conservative speaker assignment, and subtitle behavior
within its existing architecture. The implementation record explicitly states
that WhisperMLX source was not copied and rejects upstream auto-download,
token-driven setup, and runtime-download behavior. No copied, translated,
patched, or AI-transformed WhisperMLX source was identified by the recorded
technical comparison.

WhisperX is a separately declared optional dependency, currently pinned by the
lock and component registry at version `3.8.6` under BSD-2-Clause. The relevant
pre-publication adaptation changed only Auralign diagnostic fields around its
pre-existing calls to documented WhisperX public APIs; it did not introduce a
WhisperX source copy. This is a repository provenance determination, not a
waiver of upstream terms. The private history bundle and development record
preserve the comparison evidence, subject to the owner's chain-of-title
confirmation above.

If later evidence contradicts this determination, publication fails closed:
the exact origin, copyright, license, notices, modifications, and file- or
snippet-level SPDX obligations must be recorded, or the affected material must
be removed and independently reimplemented.

## AI Assistance

Automated systems have assisted with candidate text, code, analysis, and
testing. They are tools, not recorded authors, licensors, owners, signatories,
or legal approvers. A human must review, select, integrate, modify as needed,
and accept responsibility for every contribution. AI output must not introduce
confidential material or unrecorded third-party expression. The repository does
not claim copyright in purely machine-generated material beyond rights
available under applicable law. The
[U.S. Copyright Office's AI initiative](https://www.copyright.gov/ai/) provides
official background for the distinction between human authorship and purely
machine-generated material; other jurisdictions may differ.

## Contributions

No unsolicited contributions are accepted. Invited contributors require a
separately executed confidentiality and contributor/IP agreement before work is
submitted. See [`CONTRIBUTING.md`](../../CONTRIBUTING.md). Git attribution does
not substitute for that agreement.
