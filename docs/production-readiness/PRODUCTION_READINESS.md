<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign Production-Readiness Contract

Status: current implementation traceability, decision record, verification
contract, and release boundaries for resolver v1 and the repository-wide
technical completion pass.

## Publication Evidence Boundary

This file defines required gates; it does not preserve machine-local validation
results, commit identifiers, media characteristics, private corpus details, or
generated evidence. Those records belong in an access-controlled location
outside the repository. A release decision must be based on a fresh execution
of the commands below against the exact candidate tree.

## Evidence Precedence And Locked V1 Decisions

Current tested code governs behavior; canonical operational docs govern the
supported public contract; the resolver technical specification describes the
implemented identity layer. The execution specification resolved earlier design
choices where they conflicted.

| Decision | Disposition | Locked v1 contract |
| --- | --- | --- |
| Governing posture | Adopt | Autonomous investigation, conservative application |
| Default mode | Adopt | `review`; report-only and non-destructive |
| Safe apply | Refine | Current-job, hash-bound, healthy non-ambiguous ECAPA match or collision-safe hash-bound explicit rename only |
| Approved apply | Refine | Requires a current-run, hash-bound approved map; stale and cross-job inputs fail closed |
| Direct anchors | Refine | Deterministic roster-constrained candidate evidence; review-only |
| Roles and proximity | Adopt | Roles generate review candidates; forced proximity is report-only |
| Audit cardinality | Refine | One cluster row per stable cluster and exactly one child turn-or-fragment row per canonical turn; low confidence is a flag, not another row |
| IDs and reruns | Refine | Content-derived run, decision, and evidence IDs; deterministic ledger rewrite; identical runs are byte-idempotent |
| Publication | Refine | Per-job process lock, private unique staging, validate whole family, rehash canonical inputs, publish report/manifest last, rollback/cleanup on failure |
| Paths and privacy | Refine | Job-relative artifact IDs and hashes only; no absolute input/job/sibling/profile path or excerpt in normal reports/stdout |
| Canonical vs target | Adopt | Preserve canonical, target, and provenance labels; mutable external transcript is read-only |
| Structural repair | Reject for v1 | `report-only` and `label-only` only; emit `split_recommended`, never split turns |
| Unattributed | Adopt | Successful display outcome, configurable text, never an acoustic cluster |
| Named delivery | Adopt | SRT primary; JSON canonical derived data; Markdown/VTT/TXT/CSV optional; RTTM stays anonymous |
| Stability | Refine | Optional bounded sidecar comparison; every requested variant recorded; disagreement blocks safe apply only for affected scopes |
| Media review | Refine | Timestamp-bounded queue plus strict reviewed-report ingestion; no copied/generated media, frames, snippets, or excerpts |
| Siblings | Refine | Explicit job/root/manifest, allowlisted artifacts, bounded depth/count/bytes, no anonymous-label continuity |
| Pipeline/API | Refine | Resolver is opt-in; pipeline invokes only after canonical run metadata exists; API uses registered completed jobs and fixed artifact names |
| Local LLM | Excluded from supported v1 | Deterministic v1 does not depend on it; no LLM-only apply evidence |
| Existing `speaker_map.json` | Refine | Resolver reads it but does not overwrite the compatibility output of `speakers match` |
| Existing legacy rename | Refine | Command remains record-only; new hash-bound records may be safe; old slug-only records are non-evidence |

## Normative Coverage Matrix

Each row reconciles the adopted resolver requirements with current tested
behavior. Implementation and verification claims require current evidence.

### Invariants And Scope

| ID | Requirement | Disposition | Evidence |
| --- | --- | --- | --- |
| INV-01 | Local/offline-first; no cloud identity, telemetry, upload, or opportunistic download | Adopt | Offline/privacy tests and docs |
| INV-02 | Canonical media, raw ASR, alignment, diarization, timing, word metadata, and exports are immutable | Adopt | Before/after hashes and export tests |
| INV-03 | Identity is a derived display/evidence layer, not an acoustic, biometric, or legal truth claim | Adopt | Typed models and docs |
| INV-04 | Global cluster mapping and residual-fragment decisions remain separate | Adopt | Decision/audit tests |
| INV-05 | Weak/conflicting evidence remains review, unresolved, conflict, or unattributed | Adopt | Reconciliation tests |
| INV-06 | Every candidate/decision records scope, source, confidence, status, conflicts, reasons, and hashes | Adopt | Schema/cardinality validator |
| INV-07 | Excerpts, embeddings, rosters, private paths, and media are suppressed by default | Adopt | Privacy scans/tests |
| INV-08 | A supplied known-speaker count is paired with a complete matching roster for identity apply | Refine | Review warning/apply block matrix |
| SCOPE-01 | Load an existing job without rerunning ASR | Adopt | CLI integration test |
| SCOPE-02 | Hash/inventory canonical inputs and typed missing/malformed optional state | Adopt | Context-loader tests |
| SCOPE-03 | Read validated rename, approval, profile, anchor, roster, sibling, stability, and media evidence | Adopt | Source-specific tests |
| SCOPE-04 | Classify every UNKNOWN/null/no-coverage/uncertain residual independently | Adopt | Fragment fixtures |
| SCOPE-05 | Write reports, audit, queue, and derived exports only | Adopt | Artifact-matrix tests |
| SCOPE-06 | Never infer cross-recording identity from anonymous cluster numbers | Adopt | Sibling conflict tests |
| SCOPE-07 | Never auto-apply role-only, forced-proximity, waveform-stat, opaque-LLM, backend-failure, or stability-only evidence | Adopt | Negative apply tests |
| SCOPE-08 | Never broadly scan user/home folders or mutate an external handoff | Adopt | Path-boundary tests |

### Evidence Tiers And Statuses

| ID | Evidence/status | Disposition | V1 action |
| --- | --- | --- | --- |
| E-01 | Explicit approved map | Refine | Apply only in `apply-approved` after run/hash validation |
| E-02 | Legacy explicit rename | Refine | Safe only when enriched and hash/collision validated; otherwise review |
| E-03 | Local enrolled voice profile | Refine | Safe only for healthy finite ECAPA evidence bound to current job hashes |
| E-04 | Direct transcript anchor | Adopt | Review candidate only; roster name/alias constrained; no excerpt |
| E-05 | Targeted reviewed media report | Adopt | Support/reject/unattributed; never sole unapproved person mapping |
| E-06 | Diarization stability | Adopt | Supporting evidence; disagreement blocks affected safe apply |
| E-07 | Canonical timing/coverage | Adopt | Support/reject turn/fragment candidates |
| E-08 | Roster metadata | Adopt | Candidate and uniqueness constraints; role remains review-only |
| E-09 | Bounded sibling evidence | Adopt | Review only unless the same current-job hashes prove equivalence |
| E-10 | Adjacency/continuation | Refine | Turn/fragment review or label-only unattributed result; not safe person evidence |
| E-11 | Role/turn-taking | Adopt | Review-only candidate |
| E-12 | Forced proximity | Adopt | Report-only rejected competitor |
| ST-01 | `mapped` | Adopt | Concrete derived display label supported by the active mode |
| ST-02 | `review` | Adopt | Candidate requires human approval |
| ST-03 | `unresolved` | Adopt | No candidate is strong enough |
| ST-04 | `unattributed` | Adopt | Retained text intentionally has no person identity |
| ST-05 | `conflict` | Adopt | Incompatible plausible evidence |
| ST-06 | `rejected` | Adopt | Candidate considered but not selected |
| ST-07 | `backend_unavailable` | Refine | Backend state/non-evidence, never a display-decision status |

### Stable Reason Codes

All codes are adopted as a closed v1 vocabulary. Existing speaker-match reason
strings remain backward-compatible in their legacy artifact and are normalized
only inside resolver artifacts.

| Group | Adopted codes |
| --- | --- |
| Approval/profile | `explicit_approved_map`, `legacy_explicit_rename`, `local_profile_match`, `local_profile_ambiguous`, `local_profile_backend_unavailable` |
| Anchors/inference | `direct_address_anchor`, `self_identification_anchor`, `role_inference_only`, `adjacent_turn_continuation`, `same_speaker_bounded_fragment`, `forced_proximity_only` |
| Coverage/tail | `no_diarization_coverage`, `post_vad_tail`, `asr_tail_artifact_risk`, `insufficient_evidence`, `unattributed_more_honest` |
| Provenance/override | `derived_label_drift`, `canonical_unknown_folded_into_cluster`, `requires_turn_level_override`, `turn_level_override`, `phrase_continuation_override`, `question_answer_boundary_override` |
| Boundaries/validation | `mixed_turn_boundary`, `partial_line_speaker_change`, `label_only_fallback`, `split_recommended`, `non_label_text_preserved`, `label_join_regression_check` |
| Media | `source_media_review_required`, `source_media_speech_confirmed`, `source_media_tail_artifact`, `video_speaker_indicator_support`, `channel_separation_support` |
| Stability | `diarization_stable`, `diarization_unstable_boundary`, `ensemble_disagreement` |
| Competition/context | `competing_candidate_rejected`, `missing_canonical_artifact`, `sibling_evidence_stale`, `sibling_evidence_conflict`, `roster_count_conflict`, `roster_missing_expected_names`, `complete_roster_provided` |

### Typed Schema Lock

Every named field below is required or explicitly optional in a strict,
versioned Pydantic model. Inputs and reports reject unknown fields and unknown
versions.

| Schema | Locked fields |
| --- | --- |
| `speaker_identity_context.v1` | `schema_version`, `run_id`, `job_fingerprint`, `job_ref`, `input_hash`, `artifacts`, `external_transcript`, `known_speakers`, `artifacts_read`, `missing_artifacts`, `malformed_artifacts`, `generated_at` |
| Artifact reference | `artifact_id`, `relative_path`, `sha256`, `size_bytes`, `state`, `error_code` |
| External transcript context | `artifact`, `drift_classification`, `line_count_matches`, `turn_count_matches`, `non_label_text_matches`, `target_labels` |
| Policy | `mode`, `min_apply_confidence`, `profile_threshold`, `profile_ambiguity_margin`, `max_fragment_duration`, `max_fragment_words`, `max_sibling_jobs`, `unattributed_label`, `audit_csv`, `deliverable_format`, `turn_boundary_repair`, `source_media_review` |
| Candidate | `candidate_id`, `assignment_scope`, `scope_key`, `candidate_name`, `status`, `confidence`, `strongest_tier`, `evidence_ids`, `conflicts`, `reason_codes`, `requires_approval` |
| Evidence | `evidence_id`, `assignment_scope`, `scope_key`, `speaker`, `turn_index`, `word_index_start`, `word_index_end`, `candidate_name`, `tier`, `source_artifact_id`, `source_hash`, `artifact_metadata`, `span`, `confidence`, `action`, `competing_candidate`, `competing_candidate_rejection_reason`, `reason_codes`, `safe_excerpt` |
| Conflict/rejection | `candidate_name`, `status`, `confidence`, `evidence_ids`, `reason_codes` |
| Backend state | `backend`, `status`, `reason_code`, `safe_detail` |
| Cluster decision | `decision_id`, `parent_decision_id`, `child_decision_ids`, `assignment_scope`, `scope_key`, `speaker`, `turn_index`, `member_turn_indexes`, `original_diarization_label`, `canonical_label`, `target_label`, `source_label_provenance`, `status`, `display_label`, `mapped_global_candidate`, `local_override_candidate`, `confidence`, `evidence_ids`, `conflicts`, `rejections`, `reason_codes`, `structural_repair_mode`, `requires_media_review`, `requires_approval` |
| Turn/fragment decision | `decision_id`, `parent_decision_id`, `child_decision_ids`, `assignment_scope`, `scope_key`, `speaker`, `turn_index`, `member_turn_indexes`, `original_diarization_label`, `canonical_label`, `target_label`, `source_label_provenance`, `status`, `display_label`, `mapped_global_candidate`, `local_override_candidate`, `confidence`, `evidence_ids`, `conflicts`, `rejections`, `reason_codes`, `structural_repair_mode`, `requires_media_review`, `requires_approval` |
| Canonical report | `schema_version`, `run_id`, `approval_source_run_id`, `generated_at`, `mode`, `resolver_context`, `policy`, `cluster_decisions`, `turn_decisions`, `backend_statuses`, `stability_summary`, `media_review_summary`, `artifacts_written`, `validation_summary`, `qa_flags`, `warnings`, `commit_state` |
| Audit row | `decision_id`, `parent_decision_id`, `child_decision_ids`, `assignment_scope`, `source_line`, `turn_index`, `word_index_start`, `word_index_end`, `start`, `end`, `duration_seconds`, `word_count`, `original_diarization_label`, `canonical_label`, `target_label`, `source_label_provenance`, `canonical_label_was_unknown`, `drift_classification`, `assigned_display_label`, `mapped_global_candidate`, `local_override_candidate`, `resolution_status`, `confidence`, `evidence_type`, `evidence_reason`, `evidence_ids`, `artifact_metadata_used`, `competing_candidate`, `competing_candidate_rejection_reason`, `unattributed_is_more_honest`, `structural_repair_mode`, `action`, `requires_media_review`, `requires_human_approval`, `non_label_text_preserved`, `label_join_regression_checked`, `reason_codes` |
| Roster | `schema_version`, `expected_speaker_count`, `participants`; participant fields `name`, `aliases`, `role`, `profile_id`, `expected` |
| Approved map | `schema_version`, `job_fingerprint`, `resolver_run_id`, `canonical_artifact_hashes`, `approved_at`, `approved_by`, `cluster_mappings`, `turn_overrides`; override fields `turn_index`, `decision_id`, `display_label`, `reason` |
| Stability config/result | Config: `schema_version`, `variants`; request fields `variant_id`, `artifact`, `backend`, `device`. Result: `schema_version`, `run_id`, `variants`, `turn_stability`, `warnings`; every result variant records `variant_id`, `backend`, `device`, `status`, `artifact_id`, optional `artifact_hash`, and optional safe reason |
| Media queue/report | Queue: `schema_version`, `run_id`, `regions`; region fields `region_id`, `decision_id`, `turn_index`, `start`, `end`, `original_speaker`, `reason_codes`, `questions`. Report: `schema_version`, `run_id`, `job_fingerprint`, `regions`; reviewed region fields `region_id`, `conclusions`, `measurements`, `reviewed_at` |
| Identity export | `schema_version`, `resolver_run_id`, `canonical_transcript_hash`, `primary_deliverable`, `display_labels`, `turn_decision_ids`, `validation_summary`, `transcript` |
| Validation summary | `valid`, `audit_complete`, `expected_decision_ids`, `actual_decision_ids`, `duplicate_decision_ids`, `missing_decision_ids`, `external_transcript_drift_classification`, `line_count_preserved`, `turn_count_preserved`, `cue_count_preserved`, `non_label_text_preserved`, `label_join_regression_checked`, `canonical_unknown_folded_into_cluster_count`, `canonical_hashes_unchanged`, `warnings`, `errors` |
| Machine error | `schema_version`, `code`, `message`, `remediation`, `safe_context` |

### Artifact Matrix

| Artifact | Review | Apply-safe | Apply-approved | Ownership |
| --- | --- | --- | --- | --- |
| `speaker_identity_report.json` | Always; commit marker written last | Always | Always | Resolver |
| `speaker_identity_candidates.json` | Always | Always | Always | Resolver |
| `speaker_identity_evidence.jsonl` | Always; deterministic replacement | Always | Always | Resolver |
| `speaker_identity_attribution_audit.json` | Always; authoritative | Always | Always | Resolver |
| `speaker_identity_attribution_audit.csv` | Default on; explicit opt-out | Default on | Default on | Resolver sidecar |
| `speaker_identity_diarization_stability.json` | When requested, including failed variants | Same | Same | Resolver |
| `speaker_identity_media_review_queue.json` | Default queue policy or explicit write flag; `none` omits it unless explicitly requested; empty is valid | Same | Same | Resolver |
| `speaker_identity_media_review_report.json` | Validated input only; never overwritten | Same | Same | Human/reviewer |
| `speaker_identity_approved_map.json` | Optional human input; never synthesized as approval | Not consumed | Required input | Human |
| `transcript.identity_mapped.json` | Only with explicit export request | Always after successful apply | Always | Resolver |
| `transcript.identity_mapped.srt` | Only with explicit export request | Always; primary | Always; primary | Resolver |
| `transcript.identity_mapped.md` | `all` sidecar only | Optional | Optional | Resolver |
| `transcript.identity_mapped.vtt` | `all` sidecar only | Optional | Optional | Resolver |
| `transcript.identity_mapped.txt` | `all` sidecar only | Optional | Optional | Resolver |
| `transcript.identity_mapped.segments.csv` | `all` sidecar; original/canonical/target/display labels | Optional | Optional | Resolver |

`speaker_map.json`, canonical transcript/export files, RTTM, diarization, VAD,
alignment, QA, raw ASR, media, and external transcripts are never resolver
outputs.

### CLI Contract And Provisional-Option Disposition

The command is `ltm speakers resolve-identities JOB_DIR`.

| Option | Disposition | Contract |
| --- | --- | --- |
| `--mode review|apply-safe|apply-approved` | Adopt | Default `review` |
| `--roster PATH` | Adopt | Strict versioned sensitive input |
| `--known-speakers N` | Add/refine | Explicit count shared by post-process and opt-in pipeline |
| `--sibling-job PATH` | Adopt | Repeatable explicit job |
| `--sibling-root PATH` | Adopt | Depth-one bounded root; home/user root rejected |
| `--sibling-manifest PATH` | Adopt | Strict bounded manifest |
| `--external-transcript PATH` | Adopt | Read-only drift/provenance input |
| `--approved-map PATH` | Adopt | Required only for `apply-approved` |
| `--write-identity-mapped-exports` | Adopt | Review opt-in; apply modes imply it |
| `--identity-deliverable-format srt|all` | Adopt | Default `srt` |
| `--audit-csv/--no-audit-csv` | Refine | Default on; JSON remains authoritative |
| `--stability-config PATH` | Refine | Bounded reviewed sidecar inputs; no implicit model run |
| `--write-media-review-queue` | Adopt | Timestamp-bounded disputed regions only |
| `--media-review-report PATH` | Adopt | Strict run/hash/region validation |
| `--unattributed-label TEXT` | Adopt | Default `Unattributed`; validated display label |
| `--min-apply-confidence FLOAT` | Adopt | Finite range `[0,1]` |
| `--profile-threshold FLOAT` | Adopt | Finite range `[0,1]` |
| `--profile-ambiguity-margin FLOAT` | Adopt | Finite range `[0,1]` |
| `--max-fragment-duration FLOAT` | Refine | Replaces provisional minimum; positive bounded value |
| `--max-fragment-words INTEGER` | Refine | Replaces provisional minimum; positive bounded value |
| `--max-sibling-jobs INTEGER` | Adopt | Positive hard upper bound |
| `--source-media-review none|queue|reviewed-report` | Refine | No automatic media inspection |
| `--turn-boundary-repair report-only|label-only` | Refine | Structural value rejected in v1 |
| `--machine-readable` | Add | Typed safe error/result JSON; stdout contains no transcript/path |
| `--write-evidence-jsonl`, `--write-candidates`, `--write-attribution-audit`, `--write-report` | Reject redundancy | Mandatory artifacts are not optional switches |
| `--run-diarization-stability` | Refine | Replaced by explicit stability config; no hidden rerun/download |
| `--allow-direct-anchor-apply`, `--allow-role-inference-apply`, `--allow-forced-proximity` | Reject | Violate conservative v1 apply policy |
| `--allow-role-inference` | Reject redundancy | Roles are always review candidates when a roster provides them |
| `--safe-excerpts` | Reject | V1 always suppresses excerpts |

### Validation And Identity QA

The validator must independently prove all of the following: canonical hashes
recorded and unchanged; external drift classified; canonical/target/provenance
labels retained; folded UNKNOWN/no-coverage turns detected; non-label text,
line/turn/cue counts, word text/timing/original speakers, and turn timing
preserved; label/text joining rejected; allowed labels recorded; Unattributed
count/reasons recorded; exact audit ID set and parent/child graph complete with
no duplicates; every required turn/fragment/cluster covered; competing
candidates/rejections present; all requested stability variants and escalated
regions present; media queue bounded and media report excerpt-free; backend
failure is non-evidence; stdout/report has no excerpt or absolute private path.

All 30 v1 QA flags are adopted inside resolver report validation (canonical
`qa_report.json` is not mutated):

`identity_roster_count_conflict`, `identity_low_confidence`, `identity_conflict`,
`identity_role_inference_only`, `identity_forced_proximity_only`,
`identity_no_diarization_coverage`, `identity_post_vad_tail`,
`identity_backend_unavailable`, `identity_missing_canonical_artifact`,
`identity_derived_label_drift`,
`identity_canonical_unknown_folded_into_cluster`,
`identity_turn_level_override_required`,
`identity_phrase_continuation_override`,
`identity_question_answer_boundary_override`,
`identity_mixed_turn_boundary`, `identity_partial_line_speaker_change`,
`identity_label_only_fallback`, `identity_split_recommended`,
`identity_non_label_text_drift`, `identity_line_count_drift`,
`identity_label_join_regression`, `identity_sibling_evidence_stale`,
`identity_external_transcript_mismatch`, `identity_audit_missing_decision`,
`identity_diarization_unstable`, `identity_stability_variant_failed`,
`identity_media_review_required`,
`identity_media_review_confirmed_tail_artifact`,
`identity_unattributed_more_honest`,
`identity_private_excerpt_suppressed`.

### Required Tests And Fixtures

All design unit, integration, and regression rows are adopted. Evidence must
cover: strict schema/version round trips; malformed/duplicate-key/non-finite
JSON, CSV, and JSONL; every mode and invalid transition; all roster/count
combinations; stale/cross-job approval and reviewed reports; every drift class;
folded canonical UNKNOWN; global/local precedence; conflict/collision and
rejected competitors; mixed turns and split recommendations; complete audit
cardinality; backend absent/failed/ambiguous/degraded; no fallback identity;
SRT-first exact content/timing/cue invariants; canonical hash non-mutation;
stability agreement/disagreement/failure; bounded sibling and media behavior;
path traversal, symlink, out-of-root, special-file, size/count/depth attacks;
deterministic IDs/bytes; interrupted transaction cleanup/rollback; concurrent
resolver and resolver-versus-transcription locking; CLI help/error/stdout
privacy; API registered-job/path/queue safety; opt-in pipeline parity; existing
speaker match/rename/export/import and anonymous pipeline regressions; a large
synthetic bounded-resource job.

Required synthetic fixtures are: complete two-speaker; partial four-speaker;
unstable overlap; reviewed tail artifact; rejected competitor; exact external
Markdown; label-only drift; folded UNKNOWN; local override; mixed line;
label/text join regression; text drift; malicious paths; stale approval;
concurrent run; malformed artifact; and large job. Concrete speaker counts and
names are labeled synthetic; generic operational examples use
`--known-speakers <N>`.

### Implementation Coverage And Acceptance Disposition

Report-only resolution, fragment/audit handling, stability sidecars, existing
speaker-ID integration, roster/anchor evidence, media metadata, sibling
evidence, safe/approved apply, and opt-in pipeline/API integration are
implemented. Structural splitting, implicit model-running stability, and
local-LLM planning are outside the deterministic v1 contract.

Every acceptance criterion in the design is adopted with these refinements:
review warns on incomplete roster while apply modes block; audit cardinality is
the exact ID/graph contract above; evidence JSONL is replaced, never appended;
external inputs and canonical artifacts are never overwritten; SRT is primary;
real model accuracy is not inferred from synthetic tests.

## Reconciled Audit Ledger

### Closed Must-Fix Findings

- The complete typed resolver, command, artifacts, exports, bounded evidence,
  pipeline integration, registered-job API execution/retrieval, and adversarial
  tests are implemented.
- Degraded, non-finite, zero, wrong-dimension, ambiguous, fallback, and unbound
  profile evidence fails closed; healthy ECAPA evidence is bound to current job
  hashes.
- Canonical WAV publication uses independent atomic copies rather than source
  hard links.
- Structured redaction handles container-valued secret fields.
- Core structured/text writers use private unique same-directory atomic files;
  the resolver adds shared job locking, family validation, rollback, and
  report-last publication.
- Display labels reject control, bidi, newline, reserved, normalization, and
  collision hazards; CSV cells neutralize spreadsheet formulas.
- Strict bounded parsing and path, symlink, special-file, size, count, and depth
  containment are enforced on resolver inputs.
- Cleanup accepts only validated loopback HTTP(S), rejects credentials and
  redirects, and bounds response bytes and JSON shape.
- Resolver/API results use job-relative allowlisted artifact IDs, and resolver
  API execution is limited to registered completed jobs.
- Mixed/partial turns cannot inherit a whole-line person label without an
  explicit turn approval. Stability aligns anonymous partitions before
  comparison, and sibling reports cannot transfer identity through matching
  anonymous labels.
- Optional resolver controls are acquired, hashed, parsed, and reused exactly
  once under the job lock. Reviewed media is bound to the current job, valid
  queue-producing run lineage, and an exact queued region/decision.
- Profile maps are untrusted compatibility inputs: current alternatives are
  recomputed from current contained ECAPA profiles and cluster embeddings, and
  the active resolver ambiguity policy is reapplied.
- Explicit competing candidates populate candidate conflicts, structured
  decision rejections, and audit rejection fields.
- DER is computed from RTTM with disclosed scoring semantics; Energy VAD is
  implemented under its truthful name; the nonfunctional NeMo surface is
  removed; re-decode is candidate-only; Python 3.11/3.12 and package version
  claims are tested.

### Closed Defense-In-Depth Findings

- Profile import/export is portable, root-contained, finite, bounded, private,
  and collision-safe.
- The API job store has queue limits, configured roots, corruption quarantine,
  restart recovery, no-follow bounded reads, and safe deletion ordering.
- API deletion nonblockingly honors the shared identity lock, so a completed
  job cannot disappear during synchronous resolver publication.
- Sibling manifest and root enumeration deduplicate and enforce the cap before
  resolving or materializing an unbounded candidate set.
- Public CLI/progress/batch exception text redacts credentials and absolute
  local paths.
- Speaker/resolver artifacts and transaction paths use private permissions.
- Docker uses a pinned uv image, multi-stage build, non-root runtime, explicit
  `linux/amd64` platform, and least-privilege Compose mounts.
- CI validates the locked package on Python 3.11 and 3.12 and includes a package
  build/install smoke job.

### Intentional V1 Boundaries

- Local-LLM identity planning, generated media helpers, structural turn
  splitting, identity RTTM, implicit stability-model execution, NeMo, and MLX
  diarization are outside the deterministic supported contract.
- Stability analysis consumes explicitly supplied bounded local sidecars.
- Public PyPI publication is a separate human-authorized release operation.

### Human Blocked

- Owner execution of the chain-of-title attestation; acceptance or remediation
  of the exact expiring vulnerability register; public-network
  authentication/TLS; gated or unverified external-asset terms; and approval of
  a non-sensitive real-model canary corpus. The WhisperMLX/WhisperX provenance
  review is resolved in `docs/legal/PROVENANCE.md`. The repository has
  controlling PolyForm Internal Use terms, private security reporting,
  invite-only contribution policy, and fail-closed asset governance.

### Rejected With Rationale

- Canonical or external-transcript overwrite, broad home scans, real-name RTTM,
  cross-recording anonymous-label continuity, waveform/role/proximity/LLM-only
  auto-apply, hidden downloads, invented legal/security decisions, unverified
  history rewriting, remote publishing without explicit authorization, and
  automatic structural splitting all violate the locked safety and authority
  boundaries.

## Candidate Verification Contract

Run these gates against the exact candidate tree and retain only non-sensitive
results outside the repository:

```bash
uv lock --check
uv run reuse lint
uv run python scripts/check_compliance.py
uv run pytest -q
uv run ruff check .
uv run mypy src/local_transcript_max
uv build
uv run python scripts/verify_distribution.py
docker compose config
git diff --check
```

The source, wheel, sdist, CPU dependency, and CUDA 12.6 dependency SBOM scopes
must validate independently. The CPU resolution must contain no Triton, NVIDIA,
CUDA, cuDNN, or NCCL package. Container images remain internal-only even after
a successful build and smoke check.

## Release Decision

| Release boundary | Decision | Basis |
| --- | --- | --- |
| Technical local production readiness | **REQUIRES FRESH CANDIDATE GATES** | Repository-local unit, type, lint, packaging, compliance, and profile gates must all pass against the exact candidate tree; private media evidence remains external. |
| Trusted private-remote readiness | **NOT YET AUTHORIZED** | Controlling terms, safeguards, resolved repository provenance, and sanitized-history controls are present, but the owner chain-of-title record, security disposition, remote configuration, and explicit push authorization remain required. |
| Public publication readiness | **BLOCKED; OUTSIDE CURRENT LICENSE POSTURE** | Auralign is intentionally source-available under PolyForm Internal Use, not open source. Public branding, external assets, public-network operation, and separate commercial/public terms require owner/counsel decisions. |

The resolver is current supported behavior. Local LLM identity planning,
generated media helpers, structural turn splitting,
identity-labeled RTTM, and implicit stability reruns are intentional v1 product
boundaries rather than incomplete resolver work.
