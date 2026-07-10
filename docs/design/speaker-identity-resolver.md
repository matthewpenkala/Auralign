<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Speaker Identity Resolver Technical Specification

Status: implemented and supported in Auralign v1.

The resolver is the offline identity layer that follows transcription and
diarization. ASR owns words and timing, diarization owns anonymous acoustic
clusters, and the resolver owns evidence-audited display labels in derived
artifacts. It never changes the canonical transcript, diarization, alignment,
VAD, raw ASR, word timing, source media, or an external handoff transcript.

The executable contract is defined by the typed models in
`src/local_transcript_max/speaker_id/identity_models.py`, the resolver and
transaction logic in `identity_resolver.py` and `identity_io.py`, the export
logic in `identity_exports.py`, and the acceptance tests under `tests/`. The
field-level traceability matrix and release evidence are recorded in
`docs/production-readiness/PRODUCTION_READINESS.md`.

## Safety Invariants

- Runtime behavior is local/offline-first. The resolver has no upload,
  telemetry, cloud inference, or opportunistic-download path.
- Identity is a derived display/evidence claim, not an acoustic, biometric, or
  legal truth claim.
- Weak, conflicting, ambiguous, stale, degraded, or unbound evidence stays
  `review`, `unresolved`, `conflict`, `rejected`, or `unattributed`.
- Global cluster mapping and turn/fragment decisions remain separate. A global
  mapping cannot erase a local override or canonical `UNKNOWN` provenance.
- Anonymous cluster labels never carry identity across recordings.
- Normal reports, machine-readable stdout, and API responses contain artifact
  IDs and hashes rather than excerpts, embeddings, rosters, or absolute private
  paths.
- Apply modes validate the complete artifact family and rehash canonical inputs
  before publication.
- One job process owns a resolver transaction. Transcription and identity
  resolution use the same job lock and cannot publish concurrently.

## Modes

| Mode | Behavior |
| --- | --- |
| `review` | Writes deterministic candidates, evidence, decisions, audit, validation, and the configured review sidecars. It does not apply person labels. |
| `apply-safe` | Applies only current-job, hash-bound, healthy, non-ambiguous ECAPA matches or collision-safe hash-bound explicit rename evidence. |
| `apply-approved` | Applies a strict human-authored map bound to the current job fingerprint, canonical hashes, review run, and decision IDs. |

All modes preserve uncertainty. Direct/self anchors, roster roles, sibling
reports, forced proximity, waveform-stat embeddings, backend failures, and
stability-only observations cannot independently drive safe apply.

## Supported Evidence

Evidence is normalized into strict records with a content-derived ID, scope,
candidate, tier, confidence, action, source artifact ID/hash, reason codes,
conflicts, and rejection details.

| Evidence | Resolver use |
| --- | --- |
| Human-approved map | Apply only in `apply-approved` after current run/job/hash validation. |
| Explicit rename | Apply-safe only when the record is collision-safe and bound to current job artifacts; unbound legacy records are metadata only. |
| Enrolled voice profile | Apply-safe only for finite, dimension-valid, healthy `speechbrain-ecapa` evidence above threshold and ambiguity margin. |
| Direct or self-identification anchor | Roster-constrained review candidate; excerpt text is suppressed. |
| Role and turn-taking inference | Review candidate only. |
| Reviewed media metadata | Supports or rejects a candidate or selects `Unattributed`; it is never sole unapproved person evidence. |
| Diarization stability sidecar | Supporting evidence; disagreement blocks safe apply for affected turns. |
| Canonical timing and coverage | Supports residual-fragment classification and tail/no-coverage decisions. |
| Bounded sibling report | Review evidence only; anonymous labels do not imply continuity. |
| Adjacency/continuation | Turn/fragment review or `Unattributed`, never safe person evidence. |

## Resolution Flow

1. Acquire the job lock; all subsequent reads and publication remain protected.
2. Load the existing job through strict size, count, depth, type, symlink, JSON,
   JSONL, and CSV boundaries.
3. Inventory and hash canonical inputs; classify missing or malformed optional
   state without reading arbitrary paths.
4. Compare an explicitly supplied external transcript as read-only input and
   classify no drift, label-only drift, text drift, segmentation drift, or line
   count drift.
5. Build deterministic roster, profile, rename, anchor, sibling, stability, and
   reviewed-media evidence.
6. Reconcile global cluster candidates before child turn/fragment decisions,
   retaining competing candidates and structured rejections.
7. Generate exactly one cluster decision per stable cluster and exactly one
   child decision per canonical turn. Low confidence is a flag on a decision,
   not an extra audit row.
8. Validate decision IDs, parent/child graph, canonical hashes, labels, text,
   timing, counts, provenance, export joins, and privacy invariants.
9. Create a unique private same-directory transaction and publish the validated
   artifact family atomically, with the canonical report
   and its complete commit state written last. On failure, restore the prior complete
   family and remove private staging data.

Content-derived run, evidence, candidate, region, and decision IDs make an
identical repeat byte-idempotent. An approved apply records the originating
review run separately so the approved result can itself be repeated without
changing bytes.

## Artifacts

| Artifact | Contract |
| --- | --- |
| `speaker_identity_report.json` | Canonical commit marker, policy, context, decisions, QA flags, validation, warnings, and artifact inventory. |
| `speaker_identity_candidates.json` | Deterministically ordered candidate set. |
| `speaker_identity_evidence.jsonl` | Deterministic replacement ledger; never append-only. |
| `speaker_identity_attribution_audit.json` | Authoritative exact-cardinality decision audit. |
| `speaker_identity_attribution_audit.csv` | Scan-friendly sidecar, enabled by default with formula-safe cells. |
| `speaker_identity_diarization_stability.json` | Present when an explicit bounded stability configuration is supplied, including failed/unavailable variants. |
| `speaker_identity_media_review_queue.json` | Timestamp-only disputed regions when queue mode or the explicit write flag is active. |
| `speaker_identity_media_review_report.json` | Strict reviewer-owned input; never overwritten by the resolver. |
| `speaker_identity_approved_map.json` | Strict human-owned apply input; never synthesized as approval. |
| `transcript.identity_mapped.srt` | Primary derived named-speaker deliverable in apply modes or explicit review export. |
| `transcript.identity_mapped.json` | Derived typed transcript plus validation metadata. |
| `transcript.identity_mapped.md`, `.vtt`, `.txt`, `.segments.csv` | Optional `all` sidecars. |

`speaker_map.json` remains the compatibility output of `ltm speakers match`.
The identity resolver can read it as bounded context but does not overwrite it.
RTTM remains anonymous.

## Command Contract

```bash
uv run ltm speakers resolve-identities JOB_DIR [OPTIONS]
```

The default is `--mode review`. Supported options are:

- `--mode review|apply-safe|apply-approved`
- `--roster PATH` and `--known-speakers <N>`
- repeatable `--sibling-job PATH`, `--sibling-root PATH`, and
  `--sibling-manifest PATH`
- `--external-transcript PATH` and `--approved-map PATH`
- `--write-identity-mapped-exports` and
  `--identity-deliverable-format srt|all`
- `--audit-csv/--no-audit-csv`
- `--stability-config PATH`
- `--write-media-review-queue` and `--media-review-report PATH`
- `--unattributed-label TEXT`
- finite `--min-apply-confidence`, `--profile-threshold`, and
  `--profile-ambiguity-margin` values in `[0,1]`
- bounded `--max-fragment-duration`, `--max-fragment-words`, and
  `--max-sibling-jobs`
- `--source-media-review none|queue|reviewed-report`
- `--turn-boundary-repair report-only|label-only`
- `--machine-readable`

Review mode warns when `--known-speakers <N>` is not paired with a complete
matching roster. Apply modes fail closed for that mismatch. `apply-approved`
requires `--approved-map`.

The resolver does not perform structural turn splitting. `report-only` records
the boundary issue and `label-only` permits derived label repair after all
text/timing/count invariants pass.

## Approval Contract

An approved map uses schema `speaker_identity_approved_map.v1` and contains:

- `job_fingerprint`
- `resolver_run_id`
- `canonical_artifact_hashes`
- `approved_at` and `approved_by`
- `cluster_mappings`
- optional turn overrides containing `turn_index`, the review `decision_id`,
  `display_label`, and `reason`

Unknown fields, unknown versions, duplicate overrides, stale hashes, cross-job
maps, missing decisions, unsafe labels, and malformed values fail closed.

## Roster, Profiles, Siblings, Stability, And Media

- Rosters use `speaker_identity_roster.v1`. Names and aliases are Unicode
  normalization-unique, profile IDs are unique, and expected participants must
  match the declared count.
- Profile files stay inside the configured profile root with private
  permissions. Portable export/import never trusts an exported absolute path.
- Explicit sibling jobs, a depth-one sibling root, or a strict manifest are
  bounded by job count and artifact bytes. Only allowlisted reports are read.
- Stability comparison consumes explicit local sidecars. It does not rerun
  models, download assets, or replace canonical diarization.
- Media queues contain only timestamps, safe reason codes, and review
  questions. Reviewed reports contain conclusions and numeric/boolean
  measurements, not copied frames, audio, or transcript excerpts.

## Pipeline And API

`ltm transcribe` exposes the same resolver modes through
`--speaker-identity-resolver`; the default is `off`. Opt-in execution happens
after canonical `run_metadata.json` exists and while the pipeline still owns the
job lock.

The API accepts resolver execution only for registered completed jobs and fixed
job-local input names. Identity retrieval uses a closed artifact-ID allowlist.
Job roots, queue limits, no-follow reads, corruption quarantine, deletion order,
and network-bound mode are enforced by the job store and server configuration.

## Validation Contract

The validator independently checks:

- exact cluster/turn decision coverage, unique IDs, and complete parent/child
  links;
- canonical hashes before and after resolution;
- original, canonical, target, and display label provenance;
- canonical `UNKNOWN` folded into a target cluster;
- word text, word timing, turn timing, turn count, line count, SRT cue count,
  and non-label text preservation;
- label-line join regressions, allowed labels, competing candidates, and
  rejection reasons;
- all requested stability variants and all escalated media regions;
- backend failure as non-evidence; and
- suppression of private excerpts and absolute paths.

The repository includes unit, integration, API, pipeline, export, concurrency,
rollback, path-attack, malformed-input, deterministic-rerun, and large-synthetic
acceptance tests. Synthetic names and counts are explicitly fixtures;
operational instructions use `--known-speakers <N>`.

## Intentional V1 Boundaries

The deterministic v1 contract excludes opaque LLM identity evidence,
automatic media-helper generation, structural turn splitting, implicit model
reruns, identity-labeled RTTM, broad filesystem discovery, canonical or
external-transcript mutation, and remote identity inference. These exclusions
are safety and product boundaries, not missing parts of supported resolver v1.
