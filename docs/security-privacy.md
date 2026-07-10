<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Security And Privacy

Auralign is local-first. ASR, alignment, diarization, and speaker matching use
local assets and can run fully offline after cache preparation. Reviewed model
acquisition and optional approved dataset-mirror sync are network operations;
unverified sample acquisition is blocked.
Transcript cleanup is localhost-only by default, but an operator can explicitly
permit a non-local endpoint outside offline mode.

## Rights And Responsibility Boundary

Auralign's software license governs Auralign-owned source; it does not determine
ownership of or grant rights in recordings, video, transcripts, speaker names,
voiceprints/embeddings, profiles, exports, or model/dataset content. Operators
are responsible for obtaining and documenting every permission and lawful basis
required to record, identify, analyze, retain, disclose, and delete that
material.

Before processing real people, review applicable recording-consent,
confidentiality, privacy, data-protection, publicity/voice, biometric, labor,
contractual, and sector-specific requirements. A dataset/model license or a
speaker's presence in public media does not by itself resolve those rights.
Use the minimum necessary data and avoid identity resolution where anonymous
speaker labels meet the purpose.

## Never Commit

- Hugging Face tokens
- `.env` files
- raw audio or video
- transcripts and diarization outputs
- speaker embeddings and enrollment samples
- speaker rosters, approved maps, identity evidence, and identity-mapped exports
- model caches and dataset caches
- debug reports containing local media paths
- `out/`, `outputs/`, and batch/API job stores
- local cleanup outputs unless reviewed

## Token Handling

Use environment variables or the standard Hugging Face token store. Do not write
tokens into committed files.

```bash
export LTM_HF_TOKEN="REDACTED_USER_SUPPLIED_TOKEN"
# or
export HF_TOKEN="REDACTED_USER_SUPPLIED_TOKEN"
```

`LTM_HF_TOKEN` takes precedence over `HF_TOKEN`. Logs and audit reports may
record token presence, but must never record token values.

## Access, Retention, And Deletion

- Restrict raw media, transcripts, embeddings, profiles, rosters, approved
  maps, identity evidence, QA/debug output, and job stores to authorized people
  and processes.
- Define a purpose and retention period before processing. Delete source,
  derived artifacts, staging files, backups, caches, and exported copies when
  they are no longer authorized or needed.
- Deleting a canonical job does not automatically delete exports, benchmark
  copies, API job stores, backups, model/dataset caches, or content already sent
  to a cleanup endpoint. Track each copy at its trust boundary.
- Do not distribute user data or generated outputs with source, packages,
  containers, issues, pull requests, CI artifacts, support bundles, or
  publication evidence.
- Hashes and artifact IDs can still be sensitive identifiers. Share only the
  minimum redacted evidence necessary for an approved purpose.

## Bootstrap Logs

`bootstrap.sh` writes `auralign_bootstrap.log`. It logs repo IDs and statuses
only, plus budget and local path summaries:

```text
OK asr large-v3 Systran/faster-whisper-large-v3
SKIP diarization pyannote/speaker-diarization-community-1 requires HF_TOKEN or LTM_HF_TOKEN
```

Do not paste logs into public systems without reviewing local paths and model
access details.

## Dataset Mirrors

Full research corpora such as LibriSpeech, AMI, and VoxCeleb are cloud-only by
default. Auralign may generate `rclone` commands for an optional public dataset
mirror, but runtime transcription must not depend on cloud storage.

Do not put cloud credentials in `.env`, docs, CLI arguments, or committed files.
Use a named `rclone` remote such as `gdrive:auralign-datasets`. The CLI refuses
remote values that look like inline credentials.

Bulk dataset mirror sync requires the explicit
`--yes-i-understand-bulk-transfer` flag and should only be run after human
approval.

The VoxCeleb mirror is reference-only and must remain disabled absent explicit
human legal approval for a defined purpose, jurisdiction, access group, and
retention period. Research or attribution metadata does not resolve underlying
audiovisual, voice, publicity, privacy, or biometric rights.

## Offline Mode

After model cache preparation:

```bash
LTM_OFFLINE=1 uv run ltm doctor
```

Offline mode should inspect local files only. Missing cache entries must produce
clear remediation commands rather than opportunistic downloads.

Remote cleanup endpoints are also rejected in offline mode. Localhost cleanup
remains optional local IPC and is disabled by default.

## Raw Preservation

The pipeline must not overwrite:

- `transcript.raw.json`
- `diarization.json`
- `exclusive_diarization.json`
- original word timelines
- original speaker turns

Agentic diarization, targeted re-decode, speaker matching, and cleanup all write
separate artifacts.

The identity resolver uses a private per-job lock and unique private staging
directory. It rehashes canonical inputs before publishing, writes its canonical
report last as the commit marker, rolls back a failed multi-artifact publish,
and never owns the human-approved map or reviewed-media report. Reports use
artifact IDs/hashes and suppress transcript excerpts and absolute paths.

## Local Cleanup

`--cleanup local-llm` is disabled by default and localhost-only unless
`--allow-remote-cleanup` is explicitly passed. `LTM_OFFLINE=1` still forbids
non-local cleanup endpoints. Cleanup is not ground truth and must not summarize,
translate, invent names, or change meaning.

A permitted remote cleanup endpoint receives the transcript text being cleaned
and is therefore a separate trust boundary. Do not enable it for sensitive
content without an approved endpoint and data-handling policy. Generated
`cleanup_diff.json` data can include up to 500 characters each of original and
cleaned preview text; treat cleanup reports and outputs as sensitive.

Before permitting remote cleanup, document the endpoint operator, location,
purpose, legal basis, authentication, encryption, access controls, retention,
deletion, subprocessors, training/secondary-use policy, incident process, and
contractual terms. Local deletion cannot recall text already transmitted; verify
remote deletion separately. Never treat `--allow-remote-cleanup` as legal or
security approval by itself.

## Security Reporting

Follow [`SECURITY.md`](../SECURITY.md). Use GitHub private vulnerability
reporting once the private remote exists and an established private owner
channel before then. Initial reports must exclude recordings, transcripts,
identities, embeddings, profiles, credentials, private source, and unredacted
local paths.
