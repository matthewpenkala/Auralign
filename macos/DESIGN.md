<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign Neural Audio Intelligence

## Status and authority

This is the current design record for the July 28, 2026 comprehensive macOS
redesign. It supersedes the earlier conservative workbench direction. That
earlier direction remains visible in Git history; it is not a constraint on
this product.

The visual reference authority for this run is the owner-supplied
`apple-ui-design-resource-bundle-2026-07-28.zip`, SHA-256
`4e2ae14db94c2f9c1905fff3f7ca89ee153ef758dfef55edb66d5894064a51d7`.
The macOS design-guidelines skill was explicitly excluded by the owner and was
not used to select the visual direction.

The resource bundle is research material, not an Auralign dependency. No image,
font, animation, Rive file, source file, trademark, or trade dress from it is
shipped. The implementation translates broad composition lessons into original
SwiftUI and Core Graphics code.

## Product thesis

Auralign is a neural audio intelligence workspace centered on private, offline
transcription: one source enters, a configurable processing topology shapes
the work, a live local process resolves it, and a transcript plus verified
artifacts leave.

The interface should feel spatial, editorial, and intentionally composed. Its
identity comes from:

- one evolving signal object rather than a dashboard of unrelated cards;
- large, legible workspace statements rather than toolbar-sized hierarchy;
- an atmospheric but restrained studio plane;
- contextual actions at the object they affect;
- progressive disclosure of exact evidence;
- distinctly different Compose, Monitor, Deliver, and Offline Core postures.

The design must not be mistaken for a stock settings utility with a colorful
chart added to it.

## Representative presentation is authorized

The owner explicitly authorized representative and approximate presentation
where it improves clarity, usability, hierarchy, or visual character. This
authorization applies to graphics, visible data, copy, parameter groupings,
summaries, and in some cases controls.

The primary interface therefore does not need to mirror the engine schema
field-for-field or event-for-event. It may:

- group many engine fields into a human phase or intent;
- replace raw diagnostic wording with concise product language;
- use an abstract source identity instead of literal samples;
- summarize exact evidence into a designed topology;
- show a representative phase control before the complete expert control set;
- omit low-value facts until the user asks for detail.

The boundary is behavioral integrity, not literalism:

- an enabled action must perform the action it names;
- a changed control must update a real supported value;
- a blocking, failed, complete, or offline state must remain accurate;
- approximate visuals must not claim nonexistent measurement precision;
- exact values, schema keys, provenance, and diagnostics remain available in
  the expert or evidence layer where they materially matter.

Decorative and representative visuals are hidden from accessibility. Their
meaningful state is also expressed through semantic SwiftUI text and controls.

## Research synthesis

The strongest supplied references converge on a few principles:

- Runway: make media or the working object the composition, not an accessory.
- Linear: rank information aggressively and keep chrome quiet.
- Granola: allow editorial warmth, rhythm, and reading space.
- Arc: use depth and spatial relationships to express mode and context.
- ClickHouse and Datadog: when evidence matters, make it directly inspectable
  rather than decorating an unrelated dashboard.
- The bundle's anti-generic guidance: avoid nested container soup, decorative
  cyan-purple “AI” gradients, equal-weight tile grids, and fake activity.

Auralign adopts the compositional lessons, not the references' individual
assets or product identities.

## Information architecture

### Persistent destinations

The navigation edge is a custom studio navigator, not a stock grouped List.
Its primary workflow is:

1. New Transcription — Compose.
2. Activity — Monitor.
3. Results — Deliver.

Signal Setup is one contextual configuration destination. Source, Recognition,
Speakers, Refinement, and Delivery are phase controls inside it rather than five
permanent sidebar destinations.

Runtime & Models is the inspectable Offline Core. Parameter Library remains the
exact expert surface. Settings and model setup are separate utilities.

### Action placement

Start appears where checked source, destination, configuration, and readiness
converge. The toolbar retains the same action as a shortcut and compact global
authority, but it is not the only visual place to start.

Cancel stays with the owned process and remains globally reachable. Transcript,
folder, and artifact actions live beside the result they affect.

## Screen compositions

### Compose — New Transcription

The order is deliberate:

1. A content-proportional workspace hero states the selected source context
   without reserving empty vertical space.
2. A generous local-source intake surface accepts drag/drop or file selection.
3. A representative phase map makes the run topology visible and directly
   opens the selected phase's configuration.
4. Destination and run intent sit below as compact anchors.
5. Readiness and the contextual Start action close the composition.

The source identity is a deterministic abstract silhouette based on already
known media properties. It is not a waveform and makes no sample-level claim.

### Signal Setup

Five phase controls form the top-level configuration topology. Each phase
selects a real engine-backed section while allowing product language to group
the underlying schema more naturally.

The ordinary surface prioritizes human labels, effective intent, modified
state, dependencies, and recovery. Exact schema keys and exhaustive parameters
belong in the Parameter Library inspector.

### Monitor — Activity

The hero carries the actual lifecycle state and active engine phase. Its height
is intrinsic: compact evidence stays compact, while multiline status and
stacked actions expand without clipping. The signal field becomes active only
when the engine reports an active phase; ambient motion is non-metric,
phase-bounded, capped at 30 frames per second, disabled for deterministic
snapshots, and removed under Reduce Motion.

The field is a categorical phase map. Position and width do not represent
execution order, source time, progress percentage, or duration. The exact
engine stage ledger remains textual and ordered below it.

Warnings, failures, partial output, and technical diagnostics remain recovery
and evidence surfaces rather than competing focal objects.

### Deliver — Results

The transcript is the dominant reading plane. The completed signal field is not
repeated above it. Counts have one primary home in the transcript surface.

A narrow inspector contains concise automated-QA condition and verified
artifacts. Artifact actions remain individually keyboard and VoiceOver
accessible. Stale source/configuration state appears above the transcript
because it changes how the result should be interpreted.

### Offline Core — Runtime & Models

The top-level object is a representative four-node local topology:

- engine;
- required model system;
- effective configuration;
- local/offline posture.

The center seal reflects the accepted readiness decision. This map summarizes;
it does not replace evidence. The exact model and doctor ledgers, provenance,
failure causes, and recovery actions remain below and selectable.

### Parameter Library

This is intentionally expert and data-dense. It keeps exact search, filters,
categories, field controls, schema keys, errors, and an inspector. The
three-column layout activates only when its real minimum widths fit; narrower
windows use compact navigation instead of squeezing inaccessible columns.

### Settings, model setup, and fatal recovery

These utilities use the same atmosphere, typography, studio panels, and state
language without adding spectacle. Model setup is a guided offline completion
sequence. Fatal bootstrap presents only safe recovery and Quit; it never
renders a partial schema.

## Visual system

### Studio plane

`AuralignStudioBackdrop` draws original procedural convergence paths over a
dynamic opaque base. The paths are atmosphere, not audio or progress, are
non-interactive, and are accessibility-hidden. Reduce Transparency removes
them.

### Brand mark

The persistent sidebar identity uses a small-size adaptation of the bundled
Auralign application icon, not an unrelated system glyph or a downscaled
Finder asset. It removes the Dock icon's bevel, shadow, tiny nodes, and material
detail while retaining the recognizable mirrored source-to-delivery waveform
and cyan-to-violet spectrum. The adjacent live text
carries the accessible product name. Its standard-SF `AURALIGN` wordmark is only
one semantic step larger than ordinary interface text and is tracked to meet
the optical width of the two-line monospaced `NEURAL AUDIO` / `INTELLIGENCE`
descriptor. This keeps the three lines reading as one deliberate lockup rather
than a small label above a wider caption. The full icon remains authoritative
for Finder and Dock presentation.

### Dark appearance

Dark mode remains visibly violet. The window base, raised planes, sidebar, and
hero use slightly desaturated plum materials so purple reads as atmosphere
rather than a uniform wash. Full-strength violet remains concentrated in
selection, phase identity, registration marks, localized bloom, and spectral
edges. This is a calibration of the accepted purple direction, not a graphite
reset.

### Spectrum

Source, recognition, alignment, and delivery retain distinct spectrum stops.
The spectrum is used on edges, identity objects, topology, and active evidence.
It is not used as an undifferentiated full-window “AI gradient.”

Primary action text does not sit directly on the spectrum. It uses a dark
opaque high-contrast fill; the spectrum remains surrounding identity.

### Planes

`AuralignStudioPanel` is the shared raised plane. It uses one hairline, one
spectral edge, restrained depth, and generous interior rhythm. Workspace heroes
and coherent studio planes share a clipped optical finish: a low-alpha
specular wash, a sub-point spectral rim whose upper highlight wraps the leading
corner before tapering out, and a lower occlusion edge. Recovery and causality
banners reuse the same grammar at lower intensity and with their real status
color. This creates continuity across Compose, Monitor, Deliver, Signal Setup,
Parameter Library, Offline Core, Settings, setup, and fail-closed recovery
without giving every element the same visual weight.

The transcript plane, technical log/detail wells, native form controls, and
dense evidence rows intentionally remain calmer and opaque. The Offline Core
center seal is the one small bespoke lens: concentric fallback material,
analytic depth, a bounded chromatic self-displacement, and a restrained
highlight make the accepted readiness decision feel like the topology's focal
object without obscuring the textual evidence. Its localized decorative
backdrop is explicitly supplied as one bounded, coordinate-aligned source
containing the panel tone, field contours, energy marks, and exactly one
connector per facet. A padded copy is magnified through a Hermite-smoothed
plano-convex inverse map, softened with one center and eight compact
neighboring reads, and given restrained RGB edge dispersion beneath the crisp
center glyph. The original connectors stop at the lens boundary, so topology
participates without a straight duplicate, scrim, general workspace capture,
or semantic filtering. The
highlight shifts slowly only while the engine is connecting or readiness is
actively being checked.
Signal Setup applies a lower-intensity version of the shared rim only to the
selected phase control, preserving the full native Button and making the
current configuration context spatially explicit.

Most material depth uses inexpensive static SwiftUI gradients, vector strokes,
masks, and shallow platform shadows. Six original stitchable SwiftUI Metal
functions are isolated to pure decorative layers: a texture-free analytic hero
diffusion fill, its bounded three-sample chromatic bend, a shallow
three-sample Alignment Field bend, a 72-point analytic Core lens material, its
three-sample self-displacement, and the Core's fixed eleven-read local-backdrop
refraction. The first three sampled effects declare one-point envelopes; the
Core backdrop pass declares a mathematically bounded six-point envelope and
uses an 84-point padded source for its visible 72-point crop. The two
zero-sample analytic materials fill their shapes directly; the lens highlight
center is computed once per UI update and passed as a uniform. This is not a
custom renderer, general backdrop-refraction system, or broad filter pipeline.
Text, controls, transcripts, logs, and whole panel hierarchies are never
filtered. Reduce Transparency, Increase Contrast, and Differentiate Without
Color omit the GPU optical subtrees; inactive windows attenuate them.

`AuralignSection` gives each coherent unit a title and generated visual index.
The index is decorative; title semantics remain the authority.

### Type

Auralign uses three system-provided designs across five semantic voices. This
creates product character and separates human language from instrument
evidence without shipping an unreviewed font dependency or making transcript
prose look unrelated to the rendered application.

| Voice | System design | Roles |
| --- | --- | --- |
| Display | Default | Large setup and utility titles; semantic scale and weight distinguish them without introducing a separate rounded or serif family |
| Product | Default | Brand, page and section titles, navigation, phase labels, short state, badges, primary actions; semantic weights remain intact without SF Rounded's bulbous terminals |
| Interface | Default | Native controls, body copy, row labels, explanations, QA, ordinary metadata |
| Instrument | Monospaced | Section indices, technical identifiers, schema keys, model revisions, hashes, exact paths, diagnostics, timestamps, durations, counts |
| Reading | Default | Transcript prose, differentiated by semantic body scale, reading measure, leading, and its dedicated plane |

Every role begins with a semantic SwiftUI text style rather than a fixed text
size. Enlarged-text settings, script fallback, and localized layout therefore
remain available. Exact point sizes are limited to non-text geometry such as
SF Symbols.

The owner-supplied plug-in forensic report reinforced the value of separating a
refined interface face from technical/numeric readouts. It was used as research
evidence only. Auralign does not copy or package Whyte, Source Code Pro, or any
other font or asset described by that report.

The product voice is intentionally concentrated through semantic scale,
weight, tracking, and placement rather than a separate rounded family.
Monospace is likewise restricted to values whose
exactness or column stability matters. Transcript prose stays in the default
system design so it belongs to the same application, while its body scale,
generous leading, constrained line length, and dedicated reading plane keep
verified human language distinct from surrounding machine evidence.

Eyebrows and studio labels use semantic caption styles with restrained tracking
rather than fixed microtype. Long evidence and QA text remains multiline.

## Technology decision

The hypothesis that conventional SwiftUI caused the earlier redesign failure
was investigated rather than assumed.

### Selected architecture

- SwiftUI: scene structure, semantic controls, text, focus, keyboard behavior,
  menus, navigation, responsive composition, and accessibility.
- SwiftUI Canvas/Core Graphics: the phase field, source identity, Offline Core,
  and atmospheric studio plane.
- SwiftUI Metal shaders: only the isolated hero diffusion, decorative
  Alignment Field, and Offline Core layers, with semantic content rendered
  separately.
- AppKit: the existing surgical integrations for file panels, workspace
  actions, process/window lifecycle, pasteboard, and accessibility
  announcements.

### Why no wholesale replacement

The failed candidate retained the old hierarchy and information architecture;
it did not reach a demonstrated SwiftUI rendering limit. The redesigned
composition builds successfully with original native code and no third-party
runtime.

- A web frontend would duplicate the state, focus, menu, keyboard,
  accessibility, privacy, and release bridges without solving the identified
  hierarchy problem.
- Rive could improve authored motion iteration, but the supplied Rive material
  is not an approved dependency. Adoption would add license/provenance,
  external-asset, framework, bundle, signing, and release-closure work.
- A direct Metal renderer is not justified: the state-aware Canvas is already
  inexpensive, responsive, and accessible through semantic overlays. A narrow
  SwiftUI shader layer is justified for constant-work optical character because
  it leaves composition, interaction, and text in ordinary SwiftUI.

This is not a permanent ban on a different bounded renderer. A future surface
may earn Rive, direct Metal, or an AppKit text component with measured evidence
and a complete dependency, accessibility, privacy, and release impact record.

## Accessibility and privacy contract

- All actions are real Button, Menu, Picker, Toggle, DisclosureGroup, or
  standard text interaction.
- Interactive trailing controls remain separate accessibility children.
- Parent identifiers never overwrite child identifiers.
- Status uses symbol and text in addition to color.
- Reduce Motion, Reduce Transparency, Increase Contrast, and Differentiate
  Without Color have explicit presentation paths.
- Representative Canvas marks are hidden from accessibility and paired with
  semantic overlays.
- Privacy-safe presentation replaces sensitive paths in errors, warnings,
  stage detail, and evidence intended for screenshots or shared UI.
- Transcript and long QA text remain selectable, multiline, and readable.

## Performance contract

- No filesystem probes or engine work occur from visual body evaluation.
- Active field animation is snapshot-aware, foreground-aware, Reduce
  Motion-aware, and capped at 30 frames per second.
- Structural optical depth remains static. No per-frame diffusion, custom blur,
  shader, or broad glow is added to ordinary panels, banners, transcript, or
  evidence rows. Workspace-hero displacement remains static unless the
  underlying hero already has legitimate state motion.
- The only additional optical animation is a 72-point Core lens highlight
  during a real readiness operation. It is capped at 15 frames per second,
  absolute-time phased over eight seconds, and suspended while idle, inactive,
  under Reduce Motion, or in deterministic capture.
- Hero, Alignment Field, and lens-material displacement each use exactly three
  coherent reads with a declared one-point sample envelope. The static Core
  backdrop pass uses one center read, eight compact soft-field reads, and two
  restrained RGB endpoints over one padded decorative source, with a proven
  six-point envelope. Analytic color functions use no texture, loop, or
  data-dependent sample count.
- Seeded source components are computed once per draw.
- The elapsed clock is isolated in a small one-second TimelineView rather than
  invalidating the complete Activity workspace every second.
- Responsive breakpoints must be derived from the minimum widths they enable.
- Quantified GPU-performance claims require exact-candidate Metal and hitch
  evidence; a trace of a different installed build is rejected. Without
  per-command timing, retain only directly verified bounds and state the
  measurement limitation.

## Completion evidence

A redesign is not accepted from code volume or a successful compile. The
candidate requires:

- Swift unit and signed fixture UI tests;
- macOS GUI contract tests;
- Debug and Release builds plus Xcode Analyze;
- appearance, window-size, accessibility-appearance, lifecycle, Settings,
  model-setup, and fatal-state captures;
- direct inspection of representative final frames;
- privacy, external-dependency, and Release-bundle closure checks;
- an explicit finding/closure ledger that distinguishes automated proof from
  manual or unavailable evidence.
