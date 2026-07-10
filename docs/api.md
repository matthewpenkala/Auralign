<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Local API

The API uses the same local pipeline and therefore requires prepared model
caches and local input media for real transcription.

Run:

```bash
uv run ltm serve --host 127.0.0.1 --port 8765
```

Endpoints:

```text
GET    /health
GET    /models
POST   /models/pull
POST   /transcribe
GET    /jobs
GET    /jobs/{id}
GET    /jobs/{id}/status
GET    /jobs/{id}/qa
GET    /jobs/{id}/export/{format}
POST   /jobs/{id}/resolve-identities
GET    /jobs/{id}/identity/{artifact-id}
DELETE /jobs/{id}?confirm=true
```

The API uses local filesystem job state and local-process background tasks. It
does not use cloud inference. `POST /models/pull` defaults to dry-run; real
pulls must be explicitly requested, may download from Hugging Face, and still
obey model-token/gated checks.

Transcription admission is bounded to protect local unified memory from model
load storms. The default process limit allows one queued or running
transcription. Increase `LTM_API_MAX_CONCURRENT_JOBS` or
`LTM_API_MAX_QUEUED_JOBS` only after measuring memory headroom for the selected
ASR and diarization models. Real model pulls are rejected while a transcription
job is queued or running.

`GET /health` includes a `queue` object with `running`, `queued`, `active`,
`max_running`, `max_queued`, `effective_queued_limit`, and
`capacity_remaining`. A saturated queue returns HTTP 429 with the same effective
limit context. The default remains intentionally small: one queued or running
job per local API process.

Identity resolution is explicit and only accepts a registered completed job.
Its request selects `review`, `apply-safe`, or `apply-approved` and may opt into
fixed job-local inputs such as `speaker_identity_roster.json` and
`speaker_identity_approved_map.json`; arbitrary resolver paths are not accepted.
Retrieval uses a fixed artifact-ID allowlist and the same bounded, no-symlink
reader as canonical exports.

Security notes:

- The API is unauthenticated and defaults to `127.0.0.1`.
- Non-loopback binding fails unless `--allow-network-bind` is passed.
- A non-loopback bind automatically enables network-bound mode, which requires
  existing absolute `LTM_API_ALLOWED_INPUT_ROOTS` and
  `LTM_API_ALLOWED_OUTPUT_ROOTS`. Use it only behind external access controls.
- Export formats are allow-listed.
- Path traversal is rejected.
- Delete requires `confirm=true`.
- Error messages are redacted.
