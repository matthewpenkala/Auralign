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

## Model Linkage: Human And AI-Agent Entry Point

- Treat `docs/models.md` as the canonical, human-readable and AI-agent-readable
  model-linkage runbook. It defines the reviewed sequence from source clone,
  through explicit connected acquisition, to exact local verification and
  strict offline operation.
- Before any model-related change or setup action, read the manifest in
  `src/local_transcript_max/config.py`, the compliance authority in
  `compliance/external_assets.toml`, and the exact production-lane example in
  `examples/config.strict-offline.yaml`. Do not infer model IDs, revisions,
  licenses, gated status, file contracts, or readiness from prose alone.
- Source-repository access is not model acquisition, gated-model acceptance,
  or permission to redistribute an external asset. Each operator must review
  upstream terms and separately satisfy gated access requirements.
- Do not download models merely to develop, test, document, or inspect this
  repository. A connected `ltm models pull` is allowed only when the user has
  explicitly authorized acquisition for that machine and the operator has
  satisfied applicable upstream terms. Runtime auto-download remains
  prohibited.
- Never put a token in a CLI argument, report, committed file, or documentation
  except as a clearly redacted example. Never hand-author `.ltm-model.json`.
- Never commit, upload, mirror, or package model weights or caches. Do not use
  Git LFS, GitHub Releases or Packages, submodules, or machine-local symlinks
  for model assets.
- For the strict lane, use these exact checks:

  ```bash
  uv run ltm models verify \
    --asr large-v3 --diarization community-1 --alignment en --speaker-id ecapa
  LTM_OFFLINE=1 uv run ltm doctor --startup --config examples/config.strict-offline.yaml
  ```

  Missing packages, absent or incomplete caches, wrong revisions, and gated-
  access failures are blockers; report them honestly and do not claim real
  transcription readiness.
- Any model ID, revision, file contract, URL, license, access, acquisition, or
  redistribution change must update the manifest, external-asset registry,
  canonical model documentation, focused tests, and generated notices where
  applicable. Unknown or conflicting provenance fails closed.

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
