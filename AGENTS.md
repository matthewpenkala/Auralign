<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign Working Rules

## Legal And Provenance Rules

- Read `docs/legal/README.md` and `docs/legal/AI_AGENT_POLICY.md` before editing.
- Auralign-owned material uses
  `LicenseRef-PolyForm-Internal-Use-1.0.0`; preserve all SPDX, copyright,
  license, attribution, and upstream notices.
- Never infer, remove, or change a license or owner. Never paste or transform
  third-party material without recording exact source, immutable revision,
  license, compatibility, notices, and modifications.
- Update the component/external-asset registry for every dependency, model,
  dataset, base image, GitHub Action, or vendored-asset change. Unknown or
  conflicting provenance/licensing fails closed.
- Treat AI output as untrusted candidate material requiring human IP,
  provenance, privacy, security, and test review. AI systems are not authors,
  licensors, owners, signatories, or legal approvers.
- Never send private source or sensitive runtime content to an unapproved
  service. Never publish, push, release, upload, or distribute source, packages,
  containers, models, datasets, or validation evidence.
- Do not weaken compliance or release gates. Container images remain
  internal-only and non-distributable until exact-image clearance is recorded.

## Project Shape

- This repo contains Auralign's local transcription engine, currently packaged as `local-transcript-max`.
- Python source lives in `src/local_transcript_max`.
- Tests live in `tests`.
- Canonical documentation lives in `README.md` and `docs`; start with
  `docs/README.md` for the documentation index.
- The public CLI entry point is `ltm`.

## Development Workflow

- Use Python 3.11 and `uv` for dependency management.
- Prefer `uv run ...` for project commands so the locked environment is used.
- Keep package names, import paths, CLI names, and file formats stable unless a task explicitly asks to rename or migrate them.
- Use typed, testable Python that matches the existing strict `mypy` and `ruff` configuration.

## Privacy And Data Safety

- This project is local/offline-first. Do not add cloud inference, telemetry, remote uploads, or network-dependent runtime behavior without explicit approval.
- Treat audio, video, transcripts, diarization output, speaker embeddings, model caches, Hugging Face tokens, `.env` files, and generated outputs as sensitive.
- Do not commit model weights, local Hugging Face caches, virtual environments, raw media, generated transcripts, or secrets.
- Keep sample secrets redacted and use `.env.example` for documented environment variables.

## Standard Checks

Run these before committing code changes when relevant:

```bash
uv run pytest -q
uv run ruff check .
uv run mypy src/local_transcript_max
```

## Git Expectations

- Keep `main` as the default branch.
- Use small, intentional commits with clear messages.
- Before staging broad changes, check ignored and untracked files so local caches or generated artifacts do not slip into version control.

## Optional Codex Source Hydration

The following rules apply only when a Codex task explicitly uses the local
`merge` CLI for development-time source hydration. They are not runtime or
build requirements. Use `merge` only when a task names
or clearly requires external specs, architecture docs, data dictionaries, QA
checklists, model cards, dataset manifests, or small representative samples.
Do not add Merge calls to Auralign runtime code.

### Commands

- `merge search-tools "<intent>"` - Search for tools (returns compact schemas)
- `merge execute-tool <tool_name> '<json_params>'` - Execute a tool

### Workflow

1. Search for ALL tools you need in parallel: run multiple `merge search-tools` calls simultaneously.
2. Execute lookups in parallel, then execute the main tool.

Always search before executing. Never guess tool names. Pass null for optional params.

### Auralign Drive Guardrails

- Prefer named Google Drive source files over broad Drive searches.
- Use Drive context only to inform implementation, tests, docs, or local setup.
- Never fetch or commit raw audio/video, transcripts, diarization outputs, speaker embeddings, enrollment samples, model caches, dataset caches, `.env` files, tokens, or generated outputs.
- For VoxCeleb, LibriSpeech, AMI, and similar corpora, default to metadata,
  licensing docs, manifests, and dictionaries. Tiny samples require verified
  exact provenance/license and explicit approval.
- Bulk dataset download is out of scope for Merge/Codex hydration and requires explicit human approval plus ignored local cache paths such as `datasets/`.
