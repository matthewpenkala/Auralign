<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Speaker Identification

Auralign treats diarization labels as anonymous clusters such as `SPEAKER_00`.
Current matching derives real names only from local enrolled profiles and a
conservative local embedding match. The separate identity resolver can consume
current-job, hash-bound match/rename evidence plus review/approval inputs while
leaving the compatibility matching artifacts unchanged.

## Model

| Key | Model ID | Required | Gated | Purpose |
| --- | --- | --- | --- | --- |
| `ecapa` | `speechbrain/spkrec-ecapa-voxceleb` | Required in strict/full mode | No | Local ECAPA speaker embeddings |

The SpeechBrain model card documents ECAPA-TDNN speaker embeddings, VoxCeleb
training, Apache-2.0 license, 16 kHz audio expectations, CPU use, and optional
GPU use with `run_opts={"device": "cuda"}`.

## Pull

```bash
export LTM_MODEL_CACHE="$PWD/models"
uv run ltm models pull --speaker-id ecapa --cache-dir models
uv run ltm models audit
```

Expected cache path:

```text
models/speechbrain--spkrec-ecapa-voxceleb/
```

## Offline Loading

Speaker-ID code should load from the local cache path and must not contact
remote services during matching. If the model is missing, enrollment and
matching should fail with an actionable remediation command rather than falling
back to any cloud service.

Enrollment resolves `ecapa` under `LTM_MODEL_CACHE` and calls SpeechBrain with
network fetches disabled. The only fallback embedding path is the explicit
`--allow-fallback-embedding` degraded mode used for local smoke tests and
bookkeeping. It must not support identity claims.

`speaker_id.mode` defaults to `auto`; `--speaker-id off` is an explicit degraded
opt-out. Auralign embeds only exclusive non-overlap turns of at least one second,
deterministically caps each anonymous cluster at 60 seconds, unit-normalizes
each segment vector, duration-weights the average, and normalizes the result.
It writes `speaker_cluster_embeddings.json` plus a private metadata-only
`speaker_embedding_report.json`. With no enrolled profiles, anonymous labels
remain unchanged and the map reports `no_enrolled_profiles`.

## Privacy Boundary

Speaker embeddings are biometric-adjacent sensitive artifacts. Do not commit
embedding files, enrollment samples, speaker profile metadata, raw media, or
transcripts. Keep profile storage local and explicit.

## Commands

```bash
uv run ltm speakers enroll --name "Alice" --audio alice.wav
uv run ltm speakers enroll --name "Alice" --audio-dir ./alice_samples
uv run ltm speakers list
uv run ltm speakers inspect "Alice"
uv run ltm speakers rename JOB_DIR SPEAKER_01 "Alice"
uv run ltm speakers match JOB_DIR
uv run ltm speakers export --out speaker_profiles.json
uv run ltm speakers import speaker_profiles.json
uv run ltm speakers delete "Alice" --yes
```

`rename` writes a record under `~/.local-transcript-max/renames/`. It does not
modify `JOB_DIR`, `speaker_map.json`, or transcript exports. Run `match` to
produce the currently supported job-local mapping artifacts from enrolled
profiles.

When the first `rename` argument names an existing job directory, the record is
bound to hashes of that job's canonical artifacts and can qualify as safe
resolver evidence. Legacy unbound records remain editable local metadata
but cannot drive identity application.

Matching writes:

```text
speaker_map.json
transcript.speaker_matched.json
```

## Offline Identity Resolver

Run review first:

```bash
uv run ltm speakers resolve-identities JOB_DIR --mode review
```

Review always writes `speaker_identity_report.json`, candidates, a deterministic
evidence JSONL ledger, and the authoritative JSON attribution audit. CSV is on
by default as a scan-friendly sidecar. Review writes derived named exports only
with `--write-identity-mapped-exports`.

`apply-safe` accepts only current-job hash-bound, healthy, non-ambiguous ECAPA
matches or collision-safe hash-bound explicit renames. `apply-approved` requires
`--approved-map` with the report run ID, job fingerprint, canonical hashes, and
explicit cluster mappings/turn overrides. Stale and cross-job maps fail closed.
If `--known-speakers <N>` is supplied to an apply mode, `--roster` must be
complete and have the same expected count; review instead records a structured
warning.

```bash
uv run ltm speakers resolve-identities JOB_DIR \
  --mode apply-safe --roster roster.json --known-speakers <N>
uv run ltm speakers resolve-identities JOB_DIR \
  --mode apply-approved --roster roster.json --known-speakers <N> \
  --approved-map speaker_identity_approved_map.json
```

SRT is always the primary named-speaker deliverable. Use
`--identity-deliverable-format all` for Markdown, VTT, TXT, and segments CSV
sidecars. `Unattributed` is a valid reviewed outcome, not a diarization cluster.
Role/direct-address/adjacency/sibling/stability evidence remains review-only;
waveform-stat or failed backends are non-evidence. Optional stability consumes
explicit bounded local sidecars and media review emits timestamp questions or
ingests conclusions—never media, snippets, or transcript excerpts.

The resolver never overwrites `transcript.json`, canonical SRT/VTT/Markdown,
RTTM, diarization, word metadata, media, external transcripts, approved maps,
or reviewed-media reports. Identical runs replace the derived family
deterministically under a private lock and publish the canonical report last.

## Deliverable Policy

Final speaker-matched, speaker-identified, or speaker-named deliverables should
be SRT. Markdown transcripts are useful for review, audit, and manual
line-oriented repair, but they should not be treated as the primary final
handoff format for named-speaker output.

The primary identity-resolver handoff artifact is:

```text
transcript.identity_mapped.srt
```

Optional Markdown, text, VTT, and CSV exports may be written as sidecars for QA
or accessibility, but SRT remains the final named-speaker deliverable.

Ambiguous or below-threshold matches stay `UNKNOWN`. The system makes no
biometric identity claim beyond local user-controlled speaker-profile matching.
