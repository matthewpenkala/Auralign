<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# macOS Neural Audio Intelligence Redesign — July 28, 2026

## Objective

Comprehensively redesign the entire native macOS application after the
preceding redesign remained compositionally similar to the baseline.

Success means a materially new product hierarchy and workflow—not a color,
spacing, card, or component restyle—while preserving the real local engine,
configuration, readiness, job lifecycle, artifact authority, privacy, and
release boundaries.

The owner additionally authorized representative and approximate visuals,
data, copy, parameter groupings, and controls where they communicate the
product more clearly. Exact engine evidence remains inspectable; it no longer
dictates every primary composition.

## Reconstructed requirements

### Explicit

- Redesign the entire macOS app.
- Inspect and improve the prior redesign rather than restarting blindly.
- Use the attached Apple UI design resource bundle.
- Do not use the macOS design-guidelines skill for visual direction.
- Treat a pure-SwiftUI limitation as a hypothesis, not a conclusion.
- Review correctness, edge cases, architecture, testing, privacy, performance,
  UX, accessibility, integration, and release readiness.
- Use concurrent specialist workstreams where separable.
- Implement and revalidate the result.

### Implicit completion criteria

- A current screenshot cannot reasonably be described as “almost identical.”
- Compose, Monitor, Deliver, Offline Core, exact configuration, expert
  parameters, Settings, setup, and failure states share one recognizable
  design system but have distinct visual postures.
- Primary actions are contextual.
- Abstract visuals are legible and do not impersonate unavailable metrics.
- The exact expert/evidence layer remains reachable.
- Keyboard and accessibility identities survive visual composition.
- No remote runtime, telemetry, dependency, or third-party asset is introduced.
- The final source is buildable and tested from a frozen candidate.

## Baseline inventory

The prior dirty candidate contained a substantial component rewrite:

- a four-stop color system;
- `AlignmentCanvas` and procedural source identity;
- section, banner, media-row, and modified-state components;
- responsive inspectors;
- revised New Job, Activity, Results, Runtime, configuration, parameters,
  Settings, setup, and fatal-state views.

It also retained the baseline's defining composition:

- stock grouped sidebar with ten persistent destinations;
- toolbar-only Start/Cancel;
- linear header/canvas/section stacking;
- repeated equal-weight evidence;
- the same signal strip on Compose, Monitor, and Deliver;
- exact forms and rows as the dominant product vocabulary.

The user's assessment was therefore correct: the code changed considerably,
but the product hierarchy did not.

## Resource-bundle evidence

Reviewed source:

Owner-supplied `apple-ui-design-resource-bundle-2026-07-28.zip`

SHA-256:

`4e2ae14db94c2f9c1905fff3f7ca89ee153ef758dfef55edb66d5894064a51d7`

The bundle is a dated collection of exact-revision reference material,
including visual-system studies, anti-generic design guidance, native Swift
projects, and Rive source/examples. It was used as research only.

No bundle code or assets were copied into Auralign. No Rive, WebKit,
JavaScript, third-party framework, font, or image was introduced.

The strongest cross-reference lessons applied were:

- make the working object the visual center;
- rank actions and information aggressively;
- use whitespace and editorial rhythm;
- use topology and depth to communicate mode;
- preserve detailed evidence behind progressive disclosure;
- avoid generic “AI gradient” and equal-card dashboard assembly.

## Native audio-interface rendering evidence

An owner-supplied forensic report on The God Particle 1.1.1 was also reviewed
as bounded technical evidence. The report's SHA-256 is
`619e84e35587409ad64c39edfbc28e836f2f8238e855c20452196305f308d3cf`.

The direct evidence establishes a native JUCE/Cradle interface assembled from
Lua-declared layout, Core Graphics/AppKit primitives, baked 2.5D raster
materials, layered control images, and a timer-stepped 30-frame JPEG sprite
cycle. It does not establish a plug-in-owned real-time 3D, particle-physics, or
custom shader renderer.

The Auralign inference is deliberately narrower: sophisticated hierarchy,
material depth, and state-reactive motion do not by themselves justify a
wholesale web, Metal, or real-time 3D frontend. Auralign therefore translates
only the construction lessons into original native layers:

- crisp vector structure around a distinct signal chamber;
- optical depth from original gradients, occlusion, highlights, and vignettes;
- quiet idle presentation that resolves when real source or active state
  exists;
- deterministic procedural detail rather than a copied raster animation; and
- foreground-, snapshot-, and accessibility-aware motion capped at 30 frames
  per second.

No report image, plug-in asset, sprite frame, font, code, control design, or
trade dress was incorporated into Auralign.

## Independent audit workstreams

Specialist passes were run and reconciled across visual/product hierarchy,
architecture/performance, accessibility/validation, typography, static
material depth, GPU architecture, external effect-code provenance, and baked
2D/2.5D adaptation.

### Visual and product

Finding: the prior candidate was a component refactor inside the old shell.
Must-fix recommendations were a Compose/Monitor/Deliver workflow, contextual
configuration and actions, distinct lifecycle postures, transcript-first
results, Offline Core topology, and a rewritten design record.

### Architecture and performance

Finding: no evidence justified a wholesale AppKit, web, Rive, or direct Metal
replacement. The binding constraints were information architecture, broad
observable invalidation, impossible breakpoints, redundant rendering, and an
ambiguous phase-field model. Later research, bounded-work analysis, and
earlier attributed profiling supported six original stitchable SwiftUI
shaders on isolated decorative hero, Alignment Field, and Core-lens layers.
The exact final six-shader candidate has not produced per-command GPU timing
or power evidence, so this record makes no quantified performance claim.
Semantic composition, the state-aware Canvas, and all content remain native
SwiftUI.

### Accessibility and validation

Finding: the prior candidate introduced semantic-container defects that
overwrote pipeline-stage and artifact-menu accessibility identifiers, erased
state detail in workspace headers, swallowed interactive trailing controls,
and truncated meaningful QA/phase text.

No P0 accessibility, privacy, or security issue was found. The P1 defects were
treated as implementation bugs, not test expectations to waive.

## Implemented direction

### Shell and navigation

- Replaced the stock grouped sidebar List with an original studio navigator.
- Reduced five permanent configuration destinations to one Signal Setup entry.
- Kept three primary workflow destinations: New Transcription, Activity,
  Results.
- Added the `AURALIGN / NEURAL AUDIO / INTELLIGENCE` brand lockup, indexed
  groups, selection edge, and persistent global status.
- Added the procedural studio backdrop to every production workspace and
  separate utility/failure scenes.

### Hierarchy and actions

- Restored an in-workspace hero; it is content-proportional rather than
  fixed-height, and the toolbar title is now a breadcrumb.
- Added large studio planes with spectral edges and designed rhythm.
- Kept the toolbar Start/Cancel shortcut and added Start beside the checked run
  readiness state.
- Kept artifact and result actions adjacent to the affected output.

### Compose

- Reordered the page around source intake first.
- Enlarged the local drop/selection object.
- Turned the abstract signal field into keyboard-accessible phase controls.
- Selecting a phase opens its real configuration.
- Kept destination, run intent, readiness, and expert access below.

### Configuration and parameters

- Added one five-phase topology inside Signal Setup.
- Kept real schema-backed controls beneath the representative grouping.
- Preserved the complete searchable Parameter Library and exact inspector.
- Corrected the expert browser's wide-layout breakpoint to fit its actual
  minimum column widths.

### Monitor

- Retained real active stage, elapsed time, ordered stage ledger, warnings,
  failure, partial-artifact, and diagnostic evidence.
- Reframed the signal field as categorical rather than chronological.
- Removed time-width and source-ruler implications.
- Isolated the elapsed clock in a small one-second TimelineView.
- Capped ambient active-field motion at 30 frames per second.

### Deliver

- Removed the repeated signal field from completed Results.
- Made the transcript the dominant reading plane.
- Removed duplicated Run Facts counts from the inspector.
- Kept concise QA and verified artifact surfaces beside the transcript.

### Offline Core

- Added a representative four-node topology for engine, models,
  configuration, and local/offline posture.
- Kept the exact doctor/model evidence, provenance, blockers, and recovery
  controls below.

### Utility and failure states

- Applied the studio plane and hierarchy to Settings and native model setup.
- Applied the same atmosphere to fail-closed bootstrap recovery without
  rendering partial configuration.

### Restrained depth and optical material

- Added one reusable static optical treatment to workspace heroes, coherent
  studio planes, and lower-intensity status/recovery banners.
- The treatment combines a clipped specular wash, sub-point spectral rim, and
  lower occlusion edge; its upper highlight wraps the leading corner and
  tapers as one continuous reflection rather than stopping as a disconnected
  line. It is not a stack of translucent cards.
- Added one bespoke lens to the center of the Offline Core topology, where the
  accepted readiness decision warrants a stronger focal object.
- The Core lens receives one coordinate-aligned, appearance-aware decorative
  reconstruction of the local panel beneath it: panel tone, field contours,
  energy marks, and exactly one connector per facet. A bounded dome refraction
  magnifies and softens that source beneath the unchanged crisp status glyph.
  The original connectors stop at the lens boundary, so no straight or echo
  copy remains underneath. This provides real local background participation
  without a scrim, general workspace capture, semantic filtering, or a
  general-purpose glass pipeline.
- Applied a lower-intensity shared rim only to Signal Setup's selected phase,
  preserving its native Button semantics and keeping unselected phases quiet.
- Kept transcript, technical detail/log wells, native controls, and dense
  evidence rows opaque and calmer.
- Removed all optical/chromatic detail under Reduce Transparency, Increase
  Contrast, and Differentiate Without Color; inactive windows attenuate it.
- Added an original texture-free SwiftUI Metal diffusion layer behind workspace
  hero content, then applied bounded three-sample chromatic displacement to
  that decorative layer only. The active Alignment Field receives a separate
  shallow three-sample cylindrical bend inside its existing decorative Canvas.
  Both use one-point sample envelopes and leave their semantic overlays crisp.
- Added an original analytic Core lens material and bounded three-sample
  chromatic self-displacement. Its stronger background-lensing cue comes from
  a separately supplied 84-point local decorative backdrop. A Hermite-smoothed
  plano-convex inverse map produces approximately 1.43× center magnification;
  one shared center, eight compact soft-field reads, and two restrained RGB
  endpoints keep the center optical while preserving sharper edge geometry.
  The visible 72-point crop has a proven six-point sample envelope; the status
  glyph remains a crisp native layer above it.
- Both zero-sample analytic materials are direct shader-backed shape fills.
  Uniform highlight motion is calculated once per UI update rather than per
  fragment, and static accessibility tiers omit the optical subtree entirely.
- Kept workspace diffusion static. The Core highlight shifts at 15 FPS only
  while the engine is connecting or a readiness check is active. The existing
  Alignment Field remains the primary 30 FPS active-processing animation.
- Added no imported material asset, third-party visual dependency, full-panel
  filter, or custom renderer. The only broader optical scatter is the fixed,
  bounded frost kernel inside the 72-point Core lens.

### Baked 2D/2.5D adaptation

The owner-supplied God Particle evidence was evaluated again specifically as an
implementation alternative. Its fixed 30-frame JPEG atlas validates the
effectiveness of authored optical illusion, but not its suitability for every
Auralign surface.

- Large baked hero/backplate loops were rejected because responsive resizing,
  Light/Dark appearances, accessibility variants, and Retina scaling would
  multiply assets and decoded memory.
- The existing Alignment Field was retained because its procedural detail is
  source-seeded, real-state-coupled, resolution-independent, and already capped
  at 30 FPS.
- A baked sequence would be viable only for a small fixed-size object such as
  the Core lens, but the current analytic shader provides the required depth
  with no texture provenance or bundle-size cost.
- The Core lens is the second and final eligible animation, and only during a
  real short-lived readiness operation.

No report image, sprite, typeface, layout, control design, or trade dress was
incorporated.

### Effect-code provenance survey

Apple's SwiftUI shader sample and public shader contracts were used as the
primary implementation authority. GitHub repositories, gists, forums,
Stack Overflow, blogs, and other code hosts were searched as discovery and
practitioner evidence, then checked for source origin, exact revision, license,
sampling bounds, platform support, and energy behavior.

No external effect package or source file was adopted. General-purpose
broad/high-sample blur packages, private `CAFilter`/backdrop APIs, unlicensed
gists, Shadertoy-derived noncommercial work, zero-bound displaced sampling,
CPU-timer gradients, and always-running animation were rejected. The bounded
Core lens implementation was independently derived from registered
before/after behavior and public optical principles; the final Metal code is
original Auralign-owned source with constant bounded work.

## Correctness, privacy, and accessibility repairs

- Workspace headers no longer override state/detail child semantics.
- Pipeline-stage container identity moved to the passive section heading.
- Media rows separate passive identified content from interactive trailing
  controls.
- Artifact Action menus retain independent identifiers.
- Phase taps are real Buttons rather than tap gestures with synthetic button
  traits.
- Phase and QA text can wrap.
- Raw field, stage, warning, and alignment-evidence presentation passes through
  the privacy-safe message transformer.
- Representative Canvas layers remain hidden from accessibility.
- Status remains text-and-symbol backed rather than color-only.
- Primary action foreground/background contrast no longer depends on the
  variable identity spectrum.

## Architecture impact record

### Chosen

- SwiftUI for semantic UI, data flow, scenes, controls, text, navigation,
  focus, keyboard behavior, menus, responsive composition, and accessibility.
- SwiftUI Canvas/Core Graphics for original atmosphere and bounded signature
  visuals.
- Original SwiftUI stitchable Metal shaders for isolated decorative hero
  diffusion, the Alignment Field, and the Core lens only.
- Existing surgical AppKit for file panels, Finder/workspace actions,
  pasteboard, window/process lifecycle, and announcements.

### Rejected for this candidate

- Full web frontend: unnecessary duplicate state/accessibility/privacy/release
  bridge.
- Rive runtime: no demonstrated need sufficient to justify new dependency,
  provenance, framework, signing, and release closure.
- Direct Metal renderer: the bounded Canvas does not miss its frame budget, and
  a renderer rewrite would add lifecycle and accessibility risk. Narrow
  SwiftUI Metal decoration was adopted instead.
- Wholesale AppKit: would not solve the identified hierarchy or composition
  failure.

### Future escalation triggers

- Measured Canvas frame misses after static/dynamic separation.
- Transcript scale/interaction measurements that justify an AppKit text view.
- A bounded authored-motion prototype that materially outperforms Canvas and
  closes accessibility, provenance, size, and release impacts.

## Finding and closure ledger

| Finding | Priority | Closure |
| --- | --- | --- |
| Old ten-destination stock shell dominated product | Must fix | Custom navigator; one contextual Signal Setup |
| Start detached in toolbar | Must fix | Contextual checked Start plus toolbar shortcut |
| Same signature strip repeated across lifecycle | Must fix | Compose/Monitor keep distinct map; Deliver removes it; Offline Core has separate topology |
| Phase visual implied false sequence/time | Must fix | Categorical equal-width map, explicit boundary, ruler removed |
| Results duplicated counts/evidence | Must fix | Transcript-first; duplicate Run Facts removed |
| Readiness was a repeated evidence list | Must fix | Offline Core summary plus retained exact ledger |
| Header accessibility labels erased detail | P1 | Generic container label removed; real heading and child detail retained |
| Section identifier overwrote stage children | P1 | Identifier moved to passive section title |
| Media row swallowed artifact menu | P1 | Passive content and interactive trailing control separated |
| Phase interaction lacked keyboard action | P1 | Real Buttons |
| Important text truncated | P1 | Multiline phase, QA, validation, and optional media-row presentation |
| Raw engine text could disclose paths | P2 | Privacy-safe presentation applied consistently |
| Expert breakpoint could not fit columns | High | Breakpoint raised to actual geometry |
| Root Activity timer invalidated full workspace | High | Small one-second TimelineView |
| Source identity recomputed seeded components per sample | High | Components/normalizer cached once per draw |
| Depth language was isolated to the signal chamber | P2 | Shared static optical grammar on heroes, studio planes, and status/recovery surfaces; bespoke Offline Core lens; reading/evidence surfaces intentionally suppressed |
| Panel highlight stopped before the leading corner | P2 | Continuous wrapped and naturally tapered upper-left reflection |
| Offline Core connector rays ignored the center lens | P2 | Clipped, bent chromatic connector echoes beneath the crisp center glyph |
| Third-party visual runtime risk | High | No new dependency or bundle asset |

## Validation record

This section is updated only from the final frozen candidate.

- Swift Debug build: **PASS**
- Swift Release build: **PASS**
- Swift unit tests: **PASS — 197 tests**
- Signed fixture UI tests: **PASS — 11 tests on the final functional
  candidate before the typography-only SF display-role correction**
- Python GUI contract tests: **PASS — 18 tests**
- Xcode Analyze: **PASS**
- `git diff --check`: **PASS**
- Complete screenshot matrix: **PASS — 44 deterministic frames before the
  final typography-only SF display-role correction**
- Direct visual inspection and repair: **PASS**
- Release dependency/bundle closure: **PASS — including `default.metallib`**
- Manual VoiceOver/Full Keyboard Access: **NOT AUTOMATED**

## Evidence limitations

Synthetic fixtures are clearly labeled and are not live engine evidence.
Automated screenshot inspection does not establish a complete manual VoiceOver
or Full Keyboard Access traversal. Debug and Release source/build proof does
not establish notarization, installation, or clean-recipient acceptance for a
future release package. A later focused XCUITest rerun lost its macOS
accessibility-helper connection while repeatedly scrolling; it did not expose
an application assertion, crash, or product-behavior regression. The final
non-rounded SF display role passed source contracts, Debug compilation,
Analyze, and Release compilation, but the complete 44-frame matrix was not
regenerated after that typography-only correction.
