<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign 0.2.1 macOS Release And Redesign Dossier

This dossier records the implemented macOS product design, its acceptance
boundaries, and the evidence that an Auralign 0.2.1 private release must
produce. It is not a release result, does not replace the controlling legal,
model, security, or build runbooks, and does not authorize public
distribution. A source commit, signature, notarization result, package hash,
installation result, or private-release identity is authoritative only when
the final release ledger binds it to the exact no-cache candidate.

Tracked source describes the contract. Candidate-specific hashes, logs,
receipts, screenshots, manifests, and Apple or GitHub results are generated
after the release-source commit and remain in the owner-local or private
release locations described below. This separation avoids circular source
documentation and prevents an earlier package, diagnostic runtime, or
synthetic fixture from being promoted as current release evidence.

The product, packaging, signing, installation, and private publication
operations are controlled by the
[Auralign 0.2.1 private macOS PKG runbook](macos-v0.2.1-private-pkg.md).
This dossier supplements that runbook without changing its 0.2.1 product,
build 1, engine-API, artifact-allowlist, or private-distribution contract.

## Implemented Workbench And Navigation

The SwiftUI application presents one native workbench around the authoritative
Python engine:

- **Workflow** contains New Transcription, Activity, and Results.
- **Configuration** contains Source & Audio, Recognition, Speakers,
  Refinement, and Delivery. These are navigation groupings over the canonical
  manifest fields, not a second configuration schema.
- **Expert** contains the searchable Parameter Library for the same canonical
  field catalog.
- **System** contains Runtime & Models, where configuration, engine, model,
  and startup evidence remain visible and fail closed.
- The navigation-edge status affordance exposes the current global state and
  routes to the relevant recovery surface.
- The window toolbar carries the single primary Start or Cancel action.
  Settings remains a separate native window for development-engine selection
  and the installed app's local model directory.

Activity owns event-driven execution state and process termination. Results
does not outrun process ownership and distinguishes current, stale, partial,
and failed output. Runtime & Models does not turn a configured path, fixture,
or cached summary into readiness: the exact effective configuration must have
current Python-authoritative validation and backend-startup evidence.

The implementation authority is the Swift source under `macos/Auralign`,
including `Views/MainView.swift`, `Views/NewJobView.swift`,
`Views/ProcessingView.swift`, `Views/ResultsView.swift`,
`Views/ReadinessView.swift`, `Views/AllParametersView.swift`, and
`Support/PipelineNavigationGroup.swift`. The bridge, model manifest,
`PipelineConfig`, and runtime doctor remain authoritative for engine behavior.

## Accessibility And Responsive Behavior

The implementation uses native controls and semantic accessibility labels,
values, traits, identifiers, focus recovery, and announcements. Status is
communicated with text and symbols rather than color alone. Release builds
follow the real macOS Reduce Transparency, Increase Contrast, Reduce Motion,
and Differentiate Without Color settings. Debug-only presentation overrides
are dual-gated and are not available in Release.

Automated Swift and signed UI tests cover important accessibility-adjacent
contracts, including:

- keyboard routes and the single primary Start or Cancel action;
- focus recovery when a validation problem is hidden in another section;
- Settings, readiness, Activity, Results, and stale-result states;
- the exact canonical parameter catalog and Parameter Library search;
- responsive layout at the supported window boundary;
- deterministic text expansion and accessibility-display projections; and
- fixture labeling that prevents synthetic state from appearing live.

Automation does not substitute for assistive-technology acceptance. Before
release closure, a human must exercise the exact final signed and installed app
with VoiceOver and Full Keyboard Access. The walkthrough must check reading
order, labels and values, primary-action state changes, global status,
navigation, Settings, hidden-error focus recovery, Activity, Results, minimum
and expanded window layouts, and each supported accessibility display option.
It must also confirm that controls remain reachable, content does not clip, and
announcements are neither missing nor duplicated.

**Current boundary:** the human VoiceOver and Full Keyboard Access walkthrough
is unresolved until its owner-local record is bound to the final installed
candidate. No automated test or screenshot may be cited as completion of that
walkthrough.

## Application Icon And Installer Presentation

The production icon is the first-party Audio Waveform Icon Composer project
selected and reviewed by the repository owner. The checked-in `.icon` document
is primary; the verified Xcode app-icon catalog is a fallback from the same
project. The exact source-project commits, imported hashes, modifications, and
review boundaries are recorded in `macos/ASSET_PROVENANCE.md`.

The Installer presentation is the internalized first-party Light and Dark Aqua
design kit under `packaging/macos/installer-design`. Its text-free background
resources leave step labels, copy, controls, focus, localization, security
prompts, and accessibility to native Installer. The repository-local verifier
checks the exact sources, exports, dimensions, color profiles, localizable
HTML, Distribution template, and a disposable no-payload product-build round
trip.

These source and design-kit checks establish provenance and integration. They
do not prove that the final PKG contains the expected resources or that native
Installer rendered them correctly. Final acceptance therefore requires
expansion of the exact signed package plus native Light and Dark appearance
inspection. That inspection must remain bound to the final package hash.

## Nested Runtime And Application Layout

The standalone architecture embeds the purpose-built Python engine in the
sealed nested bundle:

```text
Auralign.app/
  Contents/
    MacOS/Auralign
    Frameworks/
      AuralignEngineRuntime.bundle/
        Contents/
          Info.plist
          Resources/Runtime/
```

The app resolves the engine relative to its installed bundle and launches the
embedded interpreter with bytecode-disabled, isolated, sanitized, strictly
offline process settings. The runtime contains the reviewed CPython, Auralign
engine, CPU dependency closure, PyAV, Torch-family closure, FFmpeg, FFprobe,
licenses, notices, and runtime metadata needed by the installed application.
Models are never part of this bundle. The builder removes reviewed,
production-unreachable upstream test, compatibility-fixture, and sample-data
trees from the copied runtime, including `transformers.testing_utils`,
`joblib/test`, the NetworkX atlas sample corpus, and pyannote's example
audio/annotation fixture. It also removes exact-hash-bound upstream
documentation, SpeechBrain recipes/templates/tools, Lightning demos, NumPy
examples, and SymPy benchmark/test-example roots. The exact 209 generic
test-root inventory and 18 additional fixture/development roots are
content-bound; the runtime retains four reviewed NumPy, SymPy, and Torch public
or lazy-compatibility `testing` API directories needed by traced or
conservative compatibility paths, not broad fixture trees. It also removes
nine upstream package model-weight files that are outside Auralign's external
model manifest: faster-whisper's optional Silero VAD ONNX, WhisperX's unused
VAD checkpoint, ONNX Runtime example models, and TorchMetrics image-metric
weights.
Candidate-bound evidence inventories every removed file with its original
hash, records content-addressed absent result states, and confirms that
repository source, cached wheels, and source archives remain untouched.
Runtime smoke proves the supported production imports and joblib serialization
path still work after pruning.

The standalone release does not support faster-whisper's optional
`asr.vad_filter=true` path because that path requires the excluded Silero model.
It fails clearly before inference if selected. The default remains `false`, and
Auralign's model-free local EnergyVAD remains active. This is a deliberate
standalone limitation, not permission to relabel or silently retain a model
weight.

Code objects are signed intentionally inside-out: nested Mach-O objects first,
then the runtime bundle, then the outer application. The package is a
scriptless app-only Installer payload targeting `/Applications/Auralign.app`.
Mutable data belongs outside the sealed application under the signed-in user's
Application Support, Caches, Logs, selected output, and temporary locations.
The installed app must not depend on a checkout, `.venv`, Homebrew, `uv`, Git,
shell profiles, the current working directory, or installer-time acquisition.

These are implementation and build-contract facts. Relocation, `sys.prefix`,
native dependency closure, bridge handshake, process ownership, shutdown,
Finder launch, and post-sign immutability still require direct evidence from
the final packaged payload.

## Privacy, Local-First Operation, And Models

Auralign remains local/offline-first. It does not upload media, transcripts,
speaker data, diagnostic content, or telemetry, and it does not automatically
download models. Runtime and diagnostic copy surfaces must redact or bound
paths, stderr, model locations, transcript previews, and other sensitive
content.

The application and PKG must exclude:

- model weights, model or dataset caches, and generated linkage audits;
- media, transcripts, diarization output, speaker embeddings, and profiles;
- tokens, credentials, Keychain material, `.env` files, and private paths;
- fixture selectors, Debug automation, synthetic content, and test hooks; and
- task caches, build products, logs, result bundles, and unrelated evidence.

Installation alone never establishes model or real-media readiness. Models are
separately acquired and verified according to `docs/models.md`, including
upstream terms and gated access. The final release-host evidence must verify
the exact configured ASR, diarization, alignment, and speaker-identification
entries and pass strict offline startup. Missing, incomplete, mismatched, or
unaccepted model state must remain visibly blocked without network fallback.

## Compliance, SBOM, Notices, And Provenance

The release-source gates must preserve and validate:

- REUSE/SPDX coverage and the Auralign internal-use license;
- `NOTICE`, `THIRD_PARTY_NOTICES.md`, and the generated component inventory;
- the component and external-asset registries;
- immutable source and dependency hashes;
- source/wheel/sdist content and history/privacy scans;
- vulnerability policy and reviewed exceptions; and
- the exact icon, Installer, native-library, and model-pointer provenance.

The no-cache standalone build must generate a candidate-bound runtime
inventory, signed-app inventory, CycloneDX SBOM, FFmpeg build/configuration
record, corresponding FFmpeg source archive, and package release manifest. The
SBOM must describe the exact installed runtime rather than a development
environment or lock file alone. Required notices and license material must be
present in the sealed payload and remain consistent with the tracked
registries. Apple notarization acceptance is necessary but not sufficient: the
candidate is rejected if the notarization log contains any warning or error
issue, including an archive Apple could not inspect.

Passing tracked compliance checks does not by itself prove the final package.
Conversely, a generated package manifest cannot amend missing or conflicting
tracked provenance. Unknown or conflicting ownership, license, notice, source,
or modification evidence fails closed.

## Evidence Classes And Promotion Boundary

| Evidence class | What it may establish | What it cannot establish |
| --- | --- | --- |
| Tracked source and tests | Implemented contracts, reviewed provenance, deterministic validation rules | Final signatures, notarization, installation, model availability, or package bytes |
| Synthetic fixtures and Debug captures | Layout, state projection, keyboard/focus automation, appearance and text-expansion review | Live engine behavior, model readiness, Release-hook exclusion, or packaged identity |
| Disposable diagnostic runtime or ad-hoc signature | Bounded runtime, dyld, pruning, bridge, app, and packaging defect discovery | Authoritative release acceptance or publication identity |
| Live source/runtime checks | Behavior of the exact examined checkout, runtime, configuration, and local models | Behavior of a later source commit or differently packaged app |
| Signed packaged-payload checks | Signature topology, sealed runtime behavior, package contents, Gatekeeper result | Native installation behavior unless the same package is installed and tested |
| Installed-app checks | Finder launch, receipt, bridge, ownership, data locations, reinstall behavior | A clean-machine claim or private-release parity without separate proof |
| Private-release download | Remote tag and asset identity, privacy-safe asset allowlist, downloaded-byte parity | Any fact not bound to the downloaded asset and immutable source tag |

An unreceipted screenshot is presentation evidence only. A synthetic capture is
usable only with its adjacent fail-closed capture receipt, but even a valid
receipt does not become live-engine or package evidence. A screenshot of a
live app must still be bound to the exact executable and candidate through
privacy-safe owner-local evidence.

## Non-Circular Evidence Locations

Do not commit generated release evidence or write final hashes into this
tracked dossier.

- The authoritative builder starts from an absent, uniquely named ignored work
  directory under `build/`. Its `evidence/` directory holds candidate-bound
  build logs, manifests, signature results, notarization results, expanded
  package inspection, and runtime smoke evidence.
- After acceptance, only the reviewed release-asset allowlist is retained under
  ignored `dist/macos-v0.2.1/` and uploaded to the matching private GitHub
  release. The final package release manifest and checksum material carry the
  exact source and artifact hashes.
- Screenshots, capture receipts, Xcode result bundles, native-install records,
  local-model evidence, clean-machine evidence, Apple logs, certificate
  details, and other sensitive diagnostics remain owner-local. They are not
  private-release assets unless a controlling runbook explicitly adds a
  privacy-safe derived artifact to the allowlist.
- The installed application and package receipt are inspected in place, but
  they are not treated as source-controlled evidence.

Diagnostic caches and prior release directories may be preserved for
comparison, but they remain separate identities and cannot be merged into the
authoritative no-cache evidence set.

## Outstanding Final-Candidate Acceptance

This dossier intentionally does not claim completion of the following:

- a final reviewed source SHA or source-tree digest, candidate-bound
  confirmation of the 0.2.1/build 1 identity, or synchronized immutable
  private tag;
- fresh serialized Python, Swift/Xcode, compliance, privacy, model, SBOM,
  Installer, and no-cache standalone gates;
- final Developer ID Application or Installer signing identities;
- Apple notarization acceptance, stapling, or Gatekeeper acceptance;
- final PKG name, size, SHA-256, receipt, installation, or reinstall result;
- human VoiceOver and Full Keyboard Access acceptance;
- final live-engine, model, real-media, or packaged-payload behavior;
- native Installer Light and Dark appearance acceptance;
- private GitHub release creation or independently downloaded asset parity; or
- installation on a separate clean macOS 14 Apple Silicon host or VM.

The clean macOS 14 environment is a distinct external limitation: success on a
newer development machine cannot prove absence of undeclared Python, Homebrew,
Git, cache, or workstation dependencies on the oldest supported system. If
that environment is unavailable, the release ledger must state the exact
limitation rather than infer a pass.

The final release ledger closes each item only with direct evidence from the
same source commit and artifact identity. Any source, runtime-layout, signing,
package, compliance, privacy, or provenance repair invalidates its dependent
evidence and requires a fresh candidate.
