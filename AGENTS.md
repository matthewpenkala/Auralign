<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign Working Rules

## Instruction Authority And Historical Accuracy

- Apply the user's current, explicit task authorization to the release being
  worked on. When older Auralign policy, runbooks, examples, tests, or release
  documentation describe a superseded packaging architecture, reconcile and
  update the current-version material consistently rather than treating stale
  project wording as permanent technical authority.
- Do not rewrite historical facts. Version-specific release dossiers,
  manifests, tags, hashes, and acceptance records remain immutable evidence of
  the artifacts they describe. A new packaging architecture requires a new
  version, reviewed source commit, receipts, manifests, evidence, tag, and
  release.
- Explicit owner authorization can approve private acquisition, build,
  packaging, signing, notarization, installation, and private publication
  actions within this file's safeguards. It cannot fabricate third-party
  rights, waive upstream conditions, make an AI system a legal approver, expose
  private material publicly, or weaken Apple platform security.
- If current owner intent, project policy, and third-party evidence do not
  reconcile, fail closed at the narrow conflicting action. Continue all
  independent non-blocked work and report the exact human or external decision
  still required.

## Proportionality, Autonomy, And Controlled Safeguard Relaxation

- Favor completing the explicitly authorized product outcome over preserving
  obsolete internal process. Repository Markdown, policy flags, tests, and
  scripts are maintainable project source: update them when the authorized
  architecture changes, while preserving historical release evidence and
  external legal facts.
- Once the user has authorized a bounded release objective, do not repeatedly
  request permission for ordinary source edits, local builds, exact dependency
  acquisition, ignored-cache preparation, test execution, packaging
  experiments, documentation reconciliation, or other reversible work needed
  to achieve it.
- Reasonable, reviewed changes to source, tests, build logic, package topology,
  compliance metadata, and current-version documentation are permitted when
  they directly implement the authorized outcome. Do not preserve a stale gate
  merely because it exists; replace it with a gate that tests the new contract
  at equal or better fidelity.
- Internal policy is not a substitute for legal analysis and is not immutable.
  The owner may record a project redistribution decision based on authoritative
  upstream evidence. Require outside counsel or upstream written confirmation
  only when the evidence presents a genuine unresolved conflict, not as an
  automatic prerequisite for every third-party asset.
- Absence of a standalone upstream `LICENSE` file is not automatically a
  blocker when the exact pinned model card, authoritative repository metadata,
  and the official standard license text consistently establish the license.
  Record the absence and the evidence actually relied on.
- During development, rerun the smallest gate set that can disprove the change.
  A repair invalidates only evidence that depends on the changed source,
  runtime layout, signing topology, package payload, compliance record, or
  privacy boundary. Run the complete serialized release matrix once the final
  candidate is frozen and again only if that candidate changes materially.
- Diagnostic builds, fixtures, ad-hoc signatures, caches, and extracted
  packages may be used to find and repair defects. Label them accurately and
  never promote them as authoritative release evidence.
- Pause only for an action that truly needs new authority or secure human
  interaction, such as unresolved third-party redistribution rights, Apple
  account or Keychain UI, macOS installation authorization, an irreversible
  external mutation outside the authorized scope, or potential public
  exposure. State the single minimum action needed and resume immediately
  afterward.
- Privacy, secret handling, truthful reporting, non-public scope, non-force
  Git behavior, Developer ID signing, notarization, Gatekeeper, model-integrity
  verification, and source/artifact parity remain hard safeguards.

## Legal And Provenance Rules

- Read `docs/legal/README.md` and `docs/legal/AI_AGENT_POLICY.md` before
  editing.
- Auralign-owned material uses
  `LicenseRef-PolyForm-Internal-Use-1.0.0`; preserve all SPDX, copyright,
  license, attribution, and upstream notices.
- Never infer, remove, or change a license or owner. Never paste, transform,
  acquire, redistribute, or package third-party material without recording the
  exact source, immutable revision, license, compatibility, notices, required
  attribution, access conditions, redistribution disposition, and
  modification status.
- Update the component/external-asset registry for every dependency, model,
  dataset, base image, GitHub Action, or vendored-asset change. Unknown,
  incomplete, or conflicting provenance or licensing fails closed for that
  asset.
- Treat AI output as untrusted candidate material requiring human IP,
  provenance, privacy, security, and test review. AI systems are not authors,
  licensors, owners, signatories, attorneys, or legal approvers.
- Record the actual human clearance identity, scope, date, and evidence
  boundary for every private release that redistributes third-party model
  files or another restricted external asset.
- Distinguish these questions for every external model:
  1. who may acquire the files;
  2. who may use the files;
  3. whether and under what conditions Auralign may redistribute the files to
     a recipient.
  Successful access or download does not answer the redistribution question.
- Do not delete or bypass compliance and release coverage merely to obtain a
  green result. It is permitted to revise overbroad, obsolete, duplicated, or
  architecture-specific gates so they enforce the authorized current contract.
  Container images remain internal-only and non-distributable until
  exact-image clearance is recorded.

## Private Repository And Release Publication

- Never send private source or sensitive runtime content to an unapproved
  service.
- A standard non-force push of reviewed source commits to the existing private
  GitHub repository is allowed only when explicitly requested and
  `scripts/check_private_push_authorization.py` dynamically confirms that the
  authenticated collaborator has current write access.
- A private tag, GitHub release, or release-asset upload requires separate,
  explicit user authorization. Before publication, confirm the repository is
  private, the tag is new and points to the exact accepted source SHA, the
  release is private, and every asset is explicitly allowlisted.
- Never change repository visibility, create public exposure, move or
  overwrite an existing tag, force-push, mirror-push, rewrite history, or
  publish raw validation logs, credentials, tokens, media, transcripts,
  private-path evidence, model caches, or unrelated build products.
- A final signed, notarized, stapled private Auralign product PKG may contain
  an independently verified model component when every rule in
  **Model-Inclusive Private Release Exception** is satisfied. This is a narrow
  exception for the exact accepted product PKG, not authorization to commit or
  upload raw model files, source caches, Hugging Face caches, or standalone
  model archives.
- After private publication, independently fetch or download the published
  asset and prove byte-for-byte SHA-256 parity with the exact locally
  install-tested artifact.

## Project Shape

- This repo contains Auralign's local transcription engine, currently packaged
  as `local-transcript-max`.
- Python source lives in `src/local_transcript_max`.
- Tests live in `tests`.
- Canonical documentation lives in `README.md` and `docs`; start with
  `docs/README.md` for the documentation index.
- The public CLI entry point is `ltm`.
- The native macOS application lives in `macos`.
- `scripts/build_macos_standalone.py` is a maintainer/release-machine
  orchestrator. Its compilers, SDKs, package tools, dependency resolvers, and
  controlled acquisition inputs are build-time requirements only.

## Continuation From Existing Checkpoints

- Reinspect the current filesystem, Git state, ignored and generated state,
  active processes, retained artifacts, current test evidence, and exact
  failure before changing anything. Continue from the actual checkpoint; do
  not restart completed redesign, research, accessibility, icon, Installer,
  bridge, runtime, signing, or cleanup work.
- Preserve an already accepted release as immutable historical evidence. A
  successor model-inclusive release must use a new reviewed source commit,
  semantic version/build number, tag, receipts, component identifiers,
  manifests, package, hashes, and release record.
- Keep one concise release ledger for the active candidate. For each required
  checkpoint record status (`PENDING`, `IN_PROGRESS`, `PASS`, or
  `BLOCKED_EXTERNAL`), exact source SHA, runtime/app/PKG identity, decisive
  command or native action, exit status, relevant hashes, and a privacy-safe
  evidence location.
- `PASS` requires direct evidence from the current candidate. Earlier releases,
  diagnostics, fixtures, cached output, and ad-hoc signatures may inform work
  but cannot establish final-candidate acceptance.
- `BLOCKED_EXTERNAL` is reserved for a genuinely unavailable Apple, macOS,
  GitHub, upstream-rights, or clean-recipient interaction. It must identify the
  single minimum human action or unavailable environment; it must not hide
  unfinished work that can continue locally.

## Development Workflow

- Use Python 3.11 and `uv` for dependency management.
- Prefer `uv run ...` for project commands so the locked environment is used.
- Keep package names, import paths, CLI names, and file formats stable unless a
  task explicitly asks to rename or migrate them.
- Use typed, testable Python that matches the existing strict `mypy` and
  `ruff` configuration.
- Use fresh, absent output directories for authoritative release builds.
  Diagnostic caches must be content-addressed, immutable, fully validated,
  fail closed on mismatch, and remain unable to influence an authoritative
  `--no-cache` build.
- Do not copy the development `.venv` into a release. Assemble a purpose-built,
  relocatable runtime from locked and verified inputs.

## Privacy And Data Safety

- This project is local/offline-first. Do not add cloud inference, telemetry,
  remote media uploads, or network-dependent normal processing.
- A connected build-time acquisition or explicit native first-launch model
  setup flow is permitted only when the user has authorized it and the exact
  model rules below are satisfied. After setup, production processing remains
  local and offline.
- Treat audio, video, transcripts, diarization output, speaker embeddings,
  model caches, Hugging Face tokens, OAuth tokens, `.env` files, generated
  outputs, acquisition metadata, and validation logs as sensitive.
- Never commit model weights, local Hugging Face caches, virtual environments,
  raw media, generated transcripts, or secrets.
- Keep sample secrets redacted and use `.env.example` for documented
  environment variables.
- Never put a token, Apple password, API key, or app-specific password in a
  command-line argument, report, committed file, package, documentation,
  release note, or chat response. Use Keychain and officially supported native
  authorization flows.
- Do not package Hugging Face token files, cache locks, partial blobs, download
  metadata, private acquisition markers, private paths, browser state, or
  unrelated cache content.

## Model Authority: Human And AI-Agent Entry Point

- Treat `src/local_transcript_max/config.py` as the technical manifest
  authority, `compliance/external_assets.toml` as the external-asset and
  redistribution authority, `docs/models.md` as the canonical human and
  AI-agent runbook, and `examples/config.strict-offline.yaml` as the exact
  strict production-lane example.
- Before any model-related change, acquisition, verification, assembly, or
  release action, read and reconcile all four authorities. Do not infer model
  IDs, revisions, file contracts, digests, licenses, gated status, access
  conditions, or readiness from prose or directory names.
- Source-repository access is not model acquisition, gated-model acceptance,
  permission to use, or permission to redistribute.
- A connected `ltm models pull` or equivalent exact acquisition is allowed on
  a controlled release machine only when:
  - the user has explicitly authorized acquisition on that machine;
  - the operator has satisfied applicable upstream access conditions;
  - secrets are supplied through an approved secure mechanism;
  - the destination is an ignored, private, task-scoped acquisition cache;
  - the acquired revision and files are verified against the manifest;
  - no acquired model bytes or private cache metadata enter Git.
- Runtime auto-download remains prohibited during normal transcription and
  readiness checks.
- Never hand-author or copy private `.ltm-model.json` acquisition markers into
  a distributable. A release model component must use a separate,
  deterministic, reviewed package-provenance schema that contains no token,
  personal information, private path, cache metadata, or acquisition
  timestamp.
- For the strict lane, preserve these exact development/source-cache checks:

  ```bash
  uv run ltm models verify \
    --asr large-v3 --diarization community-1 --alignment en --speaker-id ecapa
  LTM_OFFLINE=1 uv run ltm doctor --startup --config examples/config.strict-offline.yaml
  ```

  Missing files, absent or incomplete caches, wrong revisions, digest
  mismatches, unexpected entries, unsafe links, invalid provenance, or gated
  access failures are blockers. Report them honestly and do not claim real
  transcription readiness.
- Any model ID, revision, file contract, digest, URL, license, gated status,
  access method, acquisition method, redistribution disposition, or package
  destination change must update the manifest, external-asset registry,
  canonical model documentation, focused tests, notices, SBOM, and provenance
  evidence where applicable.
- For Community-1 clearance, preserve and reconcile these official sources in
  the evidence record:
  - <https://huggingface.co/pyannote/speaker-diarization-community-1>
  - <https://huggingface.co/docs/hub/models-gated>
  - <https://huggingface.co/docs/hub/oauth>
  - <https://creativecommons.org/licenses/by/4.0/>
- At the exact pinned Community-1 revision, record the model-card license
  declaration, repository license-file presence or absence, gate text and
  requested fields, any terms beyond the declared license, whether the gate is
  an acquisition mechanism or adds downstream restrictions, the registry
  disposition, and the human owner's final clearance decision.

## Model-Inclusive Private Release Exception

- Model weights and caches remain prohibited from Git, Git LFS, source
  archives, submodules, package registries, standalone model uploads, and raw
  GitHub release assets.
- Despite that general prohibition, an explicitly owner-authorized private
  macOS release may include exact pinned model files inside the final Auralign
  product PKG when all of the following are true:
  - every included model is identified by the authoritative manifest;
  - its immutable revision and exact required files are recorded;
  - every required file has an allowlisted SHA-256 digest and size;
  - its declared license and complete applicable license text are preserved;
  - attribution, notices, model-card conditions, gated status, and requested
    access fields are recorded;
  - the external-asset registry records an affirmative, revision-specific
    redistribution disposition;
  - a human owner or qualified reviewer records the actual clearance decision,
    scope, date, and known limitations;
  - no model is included merely because it is locally present, small,
    downloadable, or usable;
  - the builder copies only exact allowlisted runtime files from an ignored,
    verified acquisition cache;
  - private acquisition markers and unrelated cache content are excluded;
  - the resulting model component, final PKG, notices, SBOM, provenance, and
    installed runtime pass all required gates.
- Gated acquisition is separate from redistribution. An Installer license or
  attribution screen must not claim to register a recipient's upstream gate
  acceptance. When redistribution is affirmatively cleared, the PKG may
  install the cleared copy without requiring each recipient to create an
  upstream account.
- If any required model lacks affirmative redistribution clearance, exclude
  only that model and use the native first-launch acquisition path described
  below. Do not silently omit it while claiming the product is complete or
  immediately offline-ready.
- An old policy statement such as “models are never packaged” does not block a
  later model-inclusive version after these requirements are satisfied.
  Preserve the old statement only inside immutable historical documentation
  for the releases where it was true.

## Preferred macOS Product Architecture

- Prefer one signed, scriptless product PKG with independently versioned
  application and compact bootstrap-model components:
  - Auralign Application at `/Applications/Auralign.app`;
  - Auralign Offline Bootstrap Models at a versioned directory below
    `/Library/Application Support/com.auralign.studio/Models/`.
- When an exact cleared model cannot fit the authorized private release host's
  per-asset limit, deliver it through the signed application's native model
  manager from its pinned authoritative source. Store each verified,
  independently versioned pack below
  `~/Library/Application Support/com.auralign.studio/Models/Packs/<pack-id>/<version>/`.
  This is a delivery-size accommodation, not runtime dependency acquisition:
  the executable engine and all native libraries remain in the signed app.
- Keep model weights outside `Auralign.app`. The application bundle contains
  executable code and immutable application resources; the bootstrap
  component contains immutable, machine-wide, read-only model resources; and
  verified native-download packs are private, immutable, read-only resources
  for the current user.
- Every bootstrap component and native-download model pack must contain:
  - only the exact pinned model directories and allowlisted files;
  - a deterministic canonical manifest;
  - a deterministic verification record;
  - an independently versioned model-bundle identifier;
  - `THIRD-PARTY-NOTICES.txt`;
  - complete applicable license texts and attribution;
  - a deterministic CycloneDX SBOM;
  - a deterministic completion marker bound to the canonical manifest.
- Use conservative root-owned, read-only permissions. Mutable configuration,
  logs, results, caches, temporary files, and user-specific state belong in
  appropriate per-user Library locations, never inside the app or immutable
  model bundle.
- The application and engine must resolve both the system bootstrap component
  and per-user packs through explicit, versioned trust contracts. They must
  not select an arbitrary directory because familiar filenames exist.
- Startup/readiness verification must fail closed on a wrong bundle ID or
  version, wrong revision, missing or extra file, digest or size mismatch,
  symlink, hardlink, unsafe ownership or permissions, missing completion
  marker, malformed manifest, or inconsistent model relationship.
- Package expansion must prove the application component contains no model
  weights and the bootstrap component contains no executable code, scripts,
  credentials, private paths, caches, partial downloads, or unexpected files.
- Native-download publication must separately prove the same exclusions and
  must atomically promote only a complete verified pack.
- Use versioned destinations or another deterministic scriptless upgrade
  design so an older install cannot leave stale files that satisfy a newer
  component accidentally.

## Native Model-Pack Delivery And First-Launch Fallback

- Native model-pack delivery is permitted for an affirmatively cleared,
  public model when its exact bytes cannot fit the authorized private release
  host's asset limit. A rights-uncleared required model may use the separate
  authorization fallback below. Do not add either path as speculative scope
  when the model can be included in the scriptless bootstrap component.
- Acquisition must occur in the installed application, never in `preinstall`
  or `postinstall`, and never through a privileged Installer network script.
- A cleared public pack may download directly from the exact revision-bound
  URLs sealed in the signed catalog and needs no credential. If a model is not
  cleared for redistribution and requires individual upstream access, use the
  official browser authorization flow. For Hugging Face, prefer a public
  native OAuth client using PKCE or device authorization and request only the
  minimum gated-repository access required.
- Do not scrape a gate form, click or accept terms for the user, substitute an
  Auralign checkbox for upstream consent, embed a shared token, or claim that
  Auralign registered upstream acceptance.
- Download only pinned allowlisted files into a private temporary directory,
  verify every size, hash, structure, license, notice, provenance, SBOM, and
  completion record, reject unexpected content, atomically promote the
  verified pack into user Application Support, remove temporary files and
  unnecessary authentication material, and execute the strict offline
  readiness check.
- The assistant must be fully native, keyboard accessible, VoiceOver
  compatible, truthful about network use and external terms, and usable
  without Terminal or token copying.
- A user-facing `Resolve All` or `Complete Offline Setup` action may coordinate
  all remaining verified setup work. It must dynamically distinguish what is
  locally repairable, what can be acquired through an official authorized
  flow, and what requires a human upstream action. It must never conceal a
  blocker, weaken exact verification, or imply consent that did not occur.

## Standalone Runtime And FFmpeg Rules

- The installed product must not require Xcode, Xcode Command Line Tools,
  Python, Homebrew, pyenv, Git, `uv`, `pip`, a source checkout, `.venv`, shell
  profile configuration, developer environment variables, or Terminal
  knowledge.
- Maintainers may use Xcode, Command Line Tools, compilers, `uv`, Apple package
  tools, and other controlled build inputs on the release machine. Those are
  build-time requirements and must not leak into the installed-product
  contract.
- The installed product must not invoke compilers, create virtual
  environments, run `pip`, clone repositories, or acquire executable runtime
  dependencies.
- Build or assemble a purpose-built standalone Python engine. The app locates
  it relative to its signed installed bundle, never through current working
  directory, repository root, `.venv/bin/python`, `$PATH`, Homebrew, pyenv, or
  developer shell variables.
- Mutable runtime state must never be written into signed application or model
  bundles.
- Build FFmpeg with the reviewed LGPL-compatible configuration and preserve its
  corresponding source, license, notices, configuration, and signature
  evidence.
- Treat a freshly linked FFmpeg executable that aborts on first execution as a
  post-link runtime-closure or dynamic-loader failure until evidence proves
  otherwise. Capture its exact termination status and dyld diagnostic.
- Verify architecture, deployment target, install names, dependencies,
  `LC_RPATH`, `@rpath`, `@loader_path`, `@executable_path`, absolute paths,
  bundled libraries, relocation, signing, and execution before embedding and
  again from the signed packaged payload.
- Do not weaken Gatekeeper, SIP, Hardened Runtime, library validation, signing,
  or notarization to make a dependency run. Repair the linkage or rebuild the
  dependency correctly.

## Standalone Builder Contract

- `scripts/build_macos_standalone.py` is a fail-closed release orchestrator,
  not a development launcher. It should deterministically perform or
  coordinate:
  1. clean/reviewed source and locked-input validation;
  2. host architecture, SDK, deployment-target, and toolchain validation;
  3. standalone Python/runtime assembly;
  4. LGPL-compatible FFmpeg build and first-execution closure testing;
  5. native extension and library relocation;
  6. staged engine execution and bridge handshake;
  7. native Swift Release build;
  8. app-runtime embedding and forbidden-path scans;
  9. exact model-cache verification and deterministic model-component
     assembly when authorized;
  10. nested code signing from the inside out;
  11. outer Developer ID Application signing;
  12. scriptless app and model component packaging;
  13. Developer ID Installer signing;
  14. notarization, stapling, and validation;
  15. exact final-PKG expansion and payload/BOM/resource audit;
  16. hashes, manifests, notices, SBOM, provenance, and release metadata.
- The authoritative production build must use `--no-cache`, begin with an
  absent uniquely named output directory, and neither read nor publish
  diagnostic-cache entries.
- Diagnostic resumability may reuse only immutable, content-addressed,
  complete, fully validated stage outputs. Missing/corrupt metadata, incomplete
  markers, source/runtime/toolchain mismatch, or hash mismatch forces safe
  recomputation.
- Every embedded-Python probe must set `PYTHONDONTWRITEBYTECODE=1` and use
  `python -B`; probes must not leave `.pyc` or `__pycache__` residue in staged
  or signed payloads.
- Sign nested Mach-O objects first, then the nested runtime
  helper/container, then the outer app. Use `codesign --deep` for recursive
  verification, not as the primary signing strategy. Do not mutate a payload
  after its applicable signing boundary.

## PKG Presentation

- Preserve Installer-native text, controls, keyboard focus, localization, and
  accessibility.
- Preserve the authoritative imported Light and Dark Aqua presentation
  resources and their provenance.
- Text-free backgrounds are visual scaffolding. Do not rasterize welcome,
  license, destination, progress, security, or conclusion UI into them and do
  not imitate Installer controls.
- Adapt the actual Auralign identifiers, versions, component names, welcome and
  conclusion copy, license/read-me content, model disclosure, attribution, and
  third-party notices for the exact new release.
- Keep the PKG scriptless wherever practical. Never acquire models,
  executable dependencies, or credentials in Installer scripts.

## Documentation And Governance Reconciliation

- Reconcile the authorized current-version architecture everywhere it is
  represented. Search at least `README.md`, `AGENTS.md`, `docs`, legal policy,
  model and package runbooks, the external-asset registry, compliance policy,
  examples, release scripts, tests, Installer resources, notices, SBOM logic,
  and generated release metadata.
- Replace current-version assumptions such as:
  - “models are never packaged”;
  - “standalone distribution is not authorized”;
  - “the PKG is app-only”;
  - “the installed app requires the repository or `.venv`”;
  - “recipients require developer tools”;
  - “runtime or model acquisition is always prohibited”;
  - obsolete signing, notarization, receipt, package, or publication limits.
- Distinguish repository development, controlled release-machine acquisition,
  deterministic runtime/model assembly, cleared redistribution, recipient
  installation, optional user-authorized fallback acquisition, and strict
  offline processing.
- Keep historical release documents intact when their statements remain true
  for the old artifact. Add new current-version documentation instead of
  retroactively altering an accepted release's architecture or evidence.
- Complete tracked documentation and runbooks before the release-source commit.
  Do not create circular tracked records that require a final package hash
  before the package exists; store generated evidence with the accepted
  artifact in the approved local/private evidence location.

## Credentials And Secure Native Interaction

- Do not reuse a Hugging Face token or other credential previously exposed in
  chat or logs. Treat it as unsuitable for release automation.
- Never request or accept the user's primary Apple password in chat, commands,
  files, logs, or environment variables.
- Use real Developer ID Application and Developer ID Installer certificates
  through the native Keychain. Use an Apple-supported `notarytool` Keychain
  profile backed by an approved App Store Connect API key or app-specific
  password.
- Inspect available identities and profiles read-only before requesting help.
  Pause only when a native Apple, Keychain, Installer, or macOS authorization
  interaction genuinely requires the user. Describe the exact secure UI action
  and continue immediately after it.
- Touch ID, Apple Watch, or the native macOS authorization sheet may satisfy a
  local installation or Keychain authorization requirement. Record that the
  native authorization occurred without claiming that the biometric itself
  proves package identity; verify package identity separately.
- Never invent a signing identity, Team ID, certificate authority,
  notarization submission, Apple acceptance result, stapled ticket, or
  Gatekeeper outcome.

## Required Verification

- Run release-candidate gates serially from controlled state so failures and
  logs remain attributable. During development, focused and parallel read-only
  diagnostics are allowed when their outputs cannot race on shared generated
  state.
- Any source, runtime layout, signing topology, package, compliance, privacy,
  model, or notice repair invalidates only the dependent evidence. Record the
  dependency and rerun that set before relying on it.
- Before committing, run at minimum:

  ```bash
  uv run pytest -q
  uv run ruff check .
  uv run mypy src/local_transcript_max
  ```

- Also run the applicable macOS, release-contract, compliance, REUSE, notices,
  SBOM, provenance, privacy, history, secret, model, strict-offline,
  standalone-runtime, Installer-design, package, and accessibility gates.
- Build-machine acceptance includes:
  - complete Python tests;
  - Ruff and strict mypy;
  - Swift unit and signed UI tests;
  - Xcode Analyze and Release build;
  - exact schema/manifest coverage;
  - release-fixture, prototype-selector, automation-hook, credential, model,
    cache, and private-path exclusion scans;
  - license, REUSE, notices, provenance, SBOM, privacy, history, and secret
    checks;
  - runtime closure, deployment-target, forbidden-path, and code-signing
    topology checks;
  - deterministic input, source, toolchain, inventory, and output records.
- Application acceptance includes Finder launch, embedded-engine handshake,
  exact model readiness, local-only processing, no checkout or developer-tool
  dependency, correct process ownership/reaping, clean cancellation and
  termination, truthful partial-artifact behavior, and no mutable writes into
  signed or model bundles.
- Package acceptance includes:
  - `codesign --verify --strict --deep`;
  - Developer ID authority, team, Hardened Runtime, and entitlement inspection;
  - `pkgutil --check-signature`;
  - `spctl --assess --type execute` for the app;
  - `spctl --assess --type install` for the PKG;
  - accepted notarization;
  - `stapler validate`;
  - package expansion, BOM, receipt, component, payload, and script inspection;
  - Light and Dark Installer-resource validation;
  - safe reinstall and upgrade;
  - final runtime, model, bridge, FFmpeg, PyAV, Torch, TorchCodec, and lifecycle
    smoke tests from the exact packaged payload.
- Clean-recipient acceptance must distinguish observed evidence from
  unavailable external evidence. Do not fabricate a clean-Mac result. A
  complete offline claim requires installation and Finder launch on a
  supported clean recipient configuration without Python, Homebrew, Git,
  Xcode, Command Line Tools, repository, `.venv`, tokens, or shell setup.
- Do not call a package complete, immediately ready, or offline-ready unless
  the exact installed payload actually passes all applicable runtime and model
  gates.
- Clean-recipient and user-facing acceptance should cover:
  - a supported macOS version and a new local user;
  - a quarantine-bearing downloaded PKG when available;
  - no Python, Homebrew, Git, Xcode, Command Line Tools, repository, `.venv`,
    token, or shell configuration;
  - installation in `/Applications`, correct receipts, safe same-version
    reinstall, safe upgrade, and user-data preservation;
  - Finder launch, final icon and identity, single-instance behavior, embedded
    engine handshake, owned-process shutdown/reaping, and no network during
    normal processing;
  - immediate strict-offline readiness when every required model is bundled,
    or the exact native guided setup when one is not;
  - no credentials or private build paths in installed logs;
  - actual Light and Dark Installer screenshots or a precise statement that a
    required visual environment was unavailable.
- Model acceptance should cover exact model count and revisions, file sizes and
  hashes, notices and attribution, correct component/bundle versions,
  wrong-revision rejection, incomplete-install rejection, unexpected-file and
  link rejection, no token/cache leakage, strict-offline `models verify`,
  strict-offline startup doctor, and real readiness reported separately from
  fixtures.

## Git Expectations

- Keep `main` as the default branch.
- Use small, intentional commits with clear messages.
- Before staging broad changes, inventory tracked, staged, unstaged, ignored,
  and untracked files so caches, model weights, build products, credentials,
  media, transcripts, or private evidence cannot enter source control.
- Complete all tracked documentation and governance changes before the
  release-source commit. Store generated package hashes and artifact evidence
  only in an approved non-circular release/evidence location.
- Before an explicitly requested private `main` push:
  1. review the complete intended diff;
  2. create the intentional commit;
  3. fetch the private origin;
  4. run `uv run python scripts/check_private_push_authorization.py` on the
     clean committed tree;
  5. use a normal `git push origin main`;
  6. prove local `main`, `origin/main`, and the live remote ref are identical.
- Never use `--force`, `--all`, `--mirror`, tag replacement, or history
  rewriting.
- Build an authoritative release only from the exact clean source SHA already
  pushed to private `origin/main`.

## Final Release And Cleanup

- Reconcile semantic version, app build number, package version, component
  versions, receipt identifiers, manifests, tag, release notes, and artifact
  metadata before publication.
- Sign, notarize, staple, Gatekeeper-assess, install-test, and hash the exact
  final product PKG before uploading it.
- Attach only the explicitly authorized signed product PKG, checksum material,
  and approved privacy-safe compliance artifacts to the private release.
- Confirm commit, tag, local source, remote source, release metadata, and
  published asset hashes agree.
- Preserve the accepted artifact and required privacy-safe evidence. Remove
  only inventoried task-owned diagnostics, temporary downloads, redundant
  build directories, generated caches, stale processes, and safe transient
  material.
- Do not delete user data, model acquisition sources needed for reproducibility,
  credentials, unrelated caches, active Codex/ChatGPT state, or previous
  immutable release evidence.
- Finish with a clean worktree, local/remote/tag parity, published/local PKG
  hash parity, secret/process checks, and no stale Auralign, test, build,
  package, signing, or notarization processes.

## Final Report And Terminal Condition

- Continue autonomously through implementation, source reconciliation, gates,
  commit, normal private push, authoritative no-cache build, Developer ID
  signing, Installer signing, notarization, stapling, installation,
  user-facing acceptance, private tag/release publication, independent asset
  hash verification, and cleanup when all of those actions are explicitly
  authorized.
- Do not stop merely because the app builds, the runtime exists, a component
  package was created, notarization succeeded, or a generalized audit is
  complete.
- Completion means:
  - the intended current-version source is reviewed, committed, and clean;
  - local `main`, `origin/main`, the immutable release tag, and the recorded
    source SHA agree;
  - the exact final product PKG is signed, notarized, stapled, Gatekeeper
    accepted, expanded/audited, and install-tested;
  - the installed app uses its embedded engine and the exact accepted model
    bundle without developer-tool or checkout dependencies;
  - the private release points to the same source and contains the exact
    locally accepted PKG bytes;
  - published/local SHA-256 parity is independently proven;
  - only approved evidence remains and the repository/process state is clean.
- One genuinely external limitation may remain only when it cannot be resolved
  without secure user interaction or an unavailable environment. Mark it
  `BLOCKED_EXTERNAL`, name the exact missing interaction/environment, and
  identify the single minimum next action. Do not use an external limitation
  to leave independent local work unfinished.
- The final report must begin with the ordered release checkpoint ledger and
  then include:
  - baseline and final Git state;
  - source commit, remote parity, tag, private release, and visibility
    confirmation;
  - runtime and PKG component architecture;
  - what the standalone builder builds and which dependencies are maintainer
    only;
  - confirmation that recipients need no Xcode, Command Line Tools, Python,
    Homebrew, Git, repository, `.venv`, token copying, or Terminal;
  - exact FFmpeg failure classification, repair, closure, and LGPL evidence;
  - model-delivery decision, bundled versus first-launch-acquired inventory,
    exact revisions/hashes, bundle version, clearance boundary, licenses,
    notices, and attribution;
  - source, Swift, runtime, privacy, compliance, SBOM, model, package,
    signature, notarization, stapling, Gatekeeper, reinstall/upgrade, and
    lifecycle results;
  - safely redacted signing identities and notarization identifier/result;
  - package identifier, version, receipts, size, SHA-256, installation result,
    and published-asset parity;
  - icon and Installer-resource provenance plus actual Light and Dark visual
    inspection disposition;
  - clean-recipient evidence and precise unavailable-environment limitations;
  - residual risks and known limitations;
  - confirmation that no credentials, raw model caches, media, transcripts,
    private evidence, or unrelated files entered Git or unauthorized uploads;
  - confirmation that nothing was publicly exposed.

## Optional Codex Source Hydration

The following rules apply only when a Codex task explicitly uses the local
`merge` CLI for development-time source hydration. They are not runtime or
build requirements. Use `merge` only when a task names or clearly requires
external specs, architecture docs, data dictionaries, QA checklists, model
cards, dataset manifests, or small representative samples. Do not add Merge
calls to Auralign runtime code.

### Commands

- `merge search-tools "<intent>"` - Search for tools (returns compact schemas)
- `merge execute-tool <tool_name> '<json_params>'` - Execute a tool

### Workflow

1. Search for all tools needed in parallel with multiple
   `merge search-tools` calls.
2. Execute lookups in parallel, then execute the main tool.

Always search before executing. Never guess tool names. Pass `null` for
optional parameters.

### Auralign Drive Guardrails

- Prefer named Google Drive source files over broad Drive searches.
- Use Drive context only to inform implementation, tests, documentation, legal
  review, provenance, or local setup.
- Never fetch or commit raw audio/video, transcripts, diarization outputs,
  speaker embeddings, enrollment samples, model caches, dataset caches,
  `.env` files, tokens, or generated outputs through source hydration.
- For VoxCeleb, LibriSpeech, AMI, and similar corpora, default to metadata,
  licensing documents, manifests, and dictionaries. Tiny samples require
  verified exact provenance/license and explicit approval.
- Bulk dataset download is out of scope for Merge/Codex hydration and requires
  explicit human approval plus ignored local cache paths such as `datasets/`.
