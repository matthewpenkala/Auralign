<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# Auralign Liquid Glass Clean-Room Engineering Record

This record preserves the behavioral research, provenance boundary, independent
implementation requirements, and acceptance criteria for Auralign's bounded
optical materials.

The correct rewrite is an Auralign-owned macOS SwiftUI/Metal optical subsystem—not a port of LiquidGlassKit’s code, naming, assets, or UIKit architecture.

## Clean-room and legal conclusion

The initial LiquidGlassKit research reference was:

- Default branch `master`
- Commit [`2eb41c5d0115d23179d7a3eb133cb07bac7bb7d8`](https://github.com/DnV1eX/LiquidGlassKit/commit/2eb41c5d0115d23179d7a3eb133cb07bac7bb7d8)
- Tag/release [`1.0.1`](https://github.com/DnV1eX/LiquidGlassKit/releases/tag/1.0.1)
- Documentation-derived behavior from the [revision-pinned README](https://github.com/DnV1eX/LiquidGlassKit/blob/2eb41c5d0115d23179d7a3eb133cb07bac7bb7d8/README.md)

GitHub reports no license, and no root license file was present at that revision. Therefore:

- Do not copy or translate upstream Swift, Metal, tests, constants, type layouts, prose, screenshots, GIFs, assets, or API naming.
- Do not add LiquidGlassKit as a dependency or attribute it as a shipped component when none of its material is incorporated.
- Retain an internal clean-room dossier naming the reference revision, specification author, implementation author, permitted inputs, implementation hashes, and later human IP/provenance review.
- The implementation author should remain upstream-source-blind and work only from this behavioral specification.

The upstream package is also documented as iOS/UIKit-oriented, with no claimed macOS or AppKit support. Literal API parity would be both unnecessary and technically inappropriate for Auralign.

## Licensed implementation reference

The later, materially more useful reference is:

- Project: [`ybouane/liquidglass`](https://github.com/ybouane/liquidglass)
- Live renderer: <https://liquid-glass.ybouane.com/>
- Reviewed commit:
  [`5ebda520bebdef7786566bc8cb151cac0e593314`](https://github.com/ybouane/liquidglass/commit/5ebda520bebdef7786566bc8cb151cac0e593314)
- Reviewed package version: `1.0.3`
- License evidence: `package.json` declares `MIT`, the revision-pinned README
  ends with `MIT`, and GitHub identifies the repository as MIT-licensed. The
  reviewed revision contains no standalone root `LICENSE` file.

No JavaScript package, WebGL renderer, DOM capture dependency, source file,
asset, API, or runtime behavior is added to Auralign. Auralign independently
implements only the transferable architecture and optical principles:

- one padded, coordinate-aligned decorative source per eligible focal lens;
- a sharp refracted sample plus a bounded softened sample;
- a plano-convex/dome profile for center magnification;
- restrained wavelength separation;
- interior-weighted softness with sharper edge geometry;
- analytic Fresnel, rim, and specular cues; and
- dirty/static scheduling rather than continuous idle work.

The production expression remains Auralign-owned SwiftUI and Metal source.
The web implementation is a documented engineering reference, not a shipped
component or dependency. Its MIT license is compatible with this research and
independent adaptation; no upstream notice is removed or recharacterized.

## Rejected Auralign experiment evidence

The rejected intermediate experiment:

- Adds a sixth stitchable shader.
- Replaces hand-shaped connector echoes with seven sampled reads.
- Uses spherical magnification, anisotropic bend, radial/tangential scatter, and strong RGB separation.
- Declares a conservative ten-point sample envelope.
- Compiles successfully against macOS 14 with the macOS 26.5 SDK.

It proved the approach was coherent and buildable. It did **not** meet the
visual target and was never accepted as release evidence.

## Why the current result misses

The failure is principally architectural, not a lack of magnification.

That shader already had substantial central magnification:

```text
source = mix(position, center, 0.30 × sphericalDepth × intensity)
```

But it samples only four thin connector rays. A lens cannot look like a magnifying, frosted bubble if its raster source contains almost no visual information.

The other decisive differences are:

| Property | Current experiment | Required target |
|---|---:|---:|
| Source | Four thin connector rays | Padded, coordinate-aligned decorative backdrop |
| Scatter radius | Approximately 0.24–0.58 pt | Effective σ approximately 3.1 pt at a 72-pt lens |
| Scatter reads | Four surrounding reads plus center | Thirteen-tap frost field plus two chroma reads |
| Red/blue mixing | 72% | Approximately 10–16%, edge-weighted |
| Visual emphasis | Crisp spectral separation | Magnification and broad frost first; chroma last |
| Edge source coverage | Exactly lens-sized | At least the sample envelope beyond the crop |
| Semantic handling | Correctly isolated | Preserve unchanged |

The registered reference images support the same diagnosis. After applying the supplied crop/scale registration and excluding the play-overlay region:

- Outside-lens grayscale correlation was approximately `0.84`, supporting the registration.
- A simple spherical inverse-map fit favored `κ≈0.10–0.15`.
- Frost favored approximately `σ≈12–14 px` for a `138 px` radius, or `0.087–0.101R`.
- The top caustic region was somewhat sharper, around `0.058–0.072R`.

Because these are compressed JPEG/video stills with a large play overlay and lighting effects, they do not uniquely determine the original renderer. They are strong enough to establish that broad frost is essential and chromatic separation is secondary.

## Required architecture

The lens should have six independently controlled layers:

1. Static outer atmosphere.
2. Explicit decorative underlay, padded beyond the lens bounds.
3. Bounded Metal refraction and frost.
4. Low-opacity glass veil and tonal shaping.
5. Analytic rim, caustic, and specular highlights.
6. Crisp native status symbol and all semantic content.

The crucial API invariant is:

```text
opaque/static carrier
    decorative underlay
        bounded optical filter
    rim and highlights
    crisp semantic SwiftUI content
```

Never offer an unrestricted “glass any arbitrary `View`” API. That would eventually permit filtering text, buttons, focus rings, transcripts, logs, configuration values, or user media.

Use a domain-specific source such as:

```swift
enum AuralignOpticalDecoration {
    case connectorTopology(colors: [Color])
    case analyticField(accent: Color)
}
```

Every optical subtree remains:

```swift
.allowsHitTesting(false)
.accessibilityHidden(true)
```

Apple documents that `layerEffect` samples only the rasterized contents of the modified view—not arbitrary sibling content behind it—and requires a truthful maximum sampling distance. AppKit-backed content may also fail to render into that layer. This confirms that an explicit underlay is the correct public-API architecture. [Apple `layerEffect` documentation](https://developer.apple.com/documentation/swiftui/view/layereffect%28_%3Amaxsampleoffset%3Aisenabled%3A%29)

### Padded source construction

For the accepted fixed 72-point Core lens:

- Lens radius: `R = 36 pt`
- Declared sample padding: `P = 6 pt`
- Source raster size: `84 × 84 pt`
- Visible crop: centered `72 × 72 pt`

Draw the lens-local connector copies through the entire padded raster. Apply the shader to the padded source and only then crop/clip to the 72-point circle.

This prevents the broad kernel from sampling transparent pixels at the lens boundary.

## Independent lens formula

For destination point \(p\), center \(c\), radius \(R\), the accepted
implementation uses a Hermite-smoothed plano-convex inverse map:

```text
u = (p - c) / R
r = clamp(length(u), 0, 1)
d = 1 - r
depth = d² × (3 - 2d)
κ = clamp(0.30 × intensity, 0, 0.30)
p_source = c + (p - c) × (1 - κ × depth)
```

At the optical center, the local scale is:

```text
M_center = 1 / (1 - 0.30) ≈ 1.43×
```

The map returns continuously to the undistorted source at the lens boundary,
so the outside connector and its single refracted continuation meet without
an echo stroke.

## Frost/scatter kernel

The accepted bounded pass uses eleven fixed, explicitly unrolled reads:

- One center read.
- Four inner-ring reads.
- Four diagonal reads.
- Two radial chromatic reads.

The soft field uses the shared center plus four axial and four diagonal reads:

```text
soft = center × 0.20
     + Σ(axial four) × 0.12
     + Σ(diagonal four) × 0.08
```

The radius falls from `2.75 pt` in the optical center to `0.45 pt` at
the rim. The final soft contribution similarly falls from `0.76` to `0.10`.
This preserves the reference architecture's important asymmetry: the interior
is optically softened while the refracted boundary stays comparatively crisp.

The kernel should have fixed work:

- No data-dependent loops.
- No time-dependent sample count.
- No root-window capture.
- No `texture2d` supplied from CPU snapshots.
- No private backdrop APIs.

## Chromatic separation

Chromatic aberration is downstream of the shared refraction and deliberately
subordinate:

```text
edge = smoothstep(0.62, 0.98, r)
δ = mix(0.08 pt, 0.70 pt, edge) × intensity

redSample  = sample(p_source + n × δ)
blueSample = sample(p_source - n × δ)

sharp = (
    redSample.r,
    centerSample.g,
    blueSample.b
)

output = mix(sharp, soft, mix(0.76, 0.10, edge) × intensity)
```

Every return remains premultiplied and clamps `RGB ≤ alpha`.

## Rim and caustic

Keep surface lighting analytic and separate from the sampled underlay:

- Main perimeter: `0.8–1.0 pt`, white/cyan, approximately `0.45–0.62` peak opacity.
- Inner halo: `1.2–1.6 pt`, blurred or feathered, approximately `0.14–0.22`.
- Lower occlusion: dark semantic shadow, `0.08–0.16`.
- Key light direction: upper-left.
- One broad upper caustic and one smaller counter-highlight.
- No continuously moving glare while idle.

The crisp symbol remains above every material layer in
[`ReadinessView.swift`](../../macos/Auralign/Views/ReadinessView.swift).

## Truthful sample-envelope proof

Numerical maximization of the accepted inverse map plus its widest soft or
dispersion sample gives a worst per-axis reach of `5.557718 pt`. Therefore
declare:

```swift
maxSampleOffset: CGSize(width: 6, height: 6)
```

The bound is conservative for this exact fixed 72-point lens and must be
re-derived if radius, refraction, scatter, or dispersion changes.

## Renderer selection

Auralign targets macOS 14 but builds with the macOS 26 SDK. Use three explicit tiers:

```text
staticOnly
metal
native
```

Selection rules:

- Reduce Transparency, Increase Contrast, or Differentiate Without Color → `staticOnly`.
- macOS 26 native glass may be used for ordinary system-like controls where appropriate.
- The bespoke Offline Core lens may remain Metal-backed because it requires an explicit decorative source and calibrated frost.
- macOS 14–25 → bounded Metal implementation.
- Reduce Motion alone may retain the static material but must stop highlight motion.
- Inactive window → retain the existing `0.42` attenuation.
- Snapshot mode → deterministic, motion-free rendering.

Do not expose macOS 26’s `Glass` type in a back-deployed stored property or public signature. Isolate it inside an availability-gated view.

Apple explicitly says Reduce Transparency should replace translucent backgrounds with opaque ones. [Apple accessibility guidance](https://developer.apple.com/documentation/SwiftUI/EnvironmentValues/accessibilityReduceTransparency)

## Full clean-room behavior roadmap

For completeness beyond the immediate Core lens:

### P0: Rendering core

- Circle, capsule, and rounded-rectangle masks.
- Explicit decorative underlays.
- Light/Dark adaptation.
- Focal and utility density presets.
- Static, Metal, and native tiers.
- Exact sample manifest and mathematical bounds.

### P1: Interactive selection lens

- Native `Button`/`Picker` semantics remain authoritative.
- Visual scale: `1.00` idle, up to `1.06–1.10` active.
- Velocity stretch capped around `6%`.
- Visual offset capped around `4 pt`.
- Semantic hit region must not change.
- Reduce Motion uses a direct state change or short opacity transition.

### P1: Slider/toggle styling

- Style native controls rather than rebuilding their state machines.
- Logical values clamp exactly.
- Any elastic edge resistance is visual only:

```text
visualOvershoot =
    sign(x) × L × (1 - exp(-abs(x) / L))
```

- State must never be conveyed by transparency or hue alone.
- Keyboard stepping, focus rings, VoiceOver values, and native actions remain intact.

### P2: Group merging

If merged glass shapes become necessary, use an independently authored signed-distance-field composition:

```text
shape = smoothUnion(shapeA, shapeB, mergeRadius)
```

Keep the initial production implementation to a bounded number of shapes. The upstream project itself does not document completed grouped morphing, so this is not required for initial acceptance.

## Integration seams

- The bounded local source and foreground separation live in
  [`ReadinessView.swift`](../../macos/Auralign/Views/ReadinessView.swift).
- The fixed refraction/frost shader lives in
  [`AuralignOptics.metal`](../../macos/Auralign/Shaders/AuralignOptics.metal).
- The accessibility and inactive-window policy remains centralized in
  [`AuralignMetalOptics.swift`](../../macos/Auralign/Views/AuralignMetalOptics.swift).
- Current architecture summaries are reconciled in
  [`macos/DESIGN.md`](../../macos/DESIGN.md) and the
  [macOS redesign record](macos-ui-redesign-2026-07-28.md).

## Automated acceptance

Add per-function contracts instead of relying on global string counts:

```text
function name
source kind
sample count
maximum sample offset
parameter clamps
animated/static
semantic content allowed = false
premultiplied output = true
```

Required mathematical tests:

- Negative, zero, nominal, over-one, `NaN`, and infinite inputs.
- All combinations of Reduce Transparency, Increase Contrast, Reduce Motion, and Differentiate Without Color.
- Active/inactive operation and window.
- Snapshot/live state.
- Transparent and opaque inputs.
- Exact sample count.
- Maximum per-axis displacement.
- `0 ≤ RGB ≤ alpha ≤ 1`.
- No loop, random, private symbol, root capture, or unbounded time dependency.

Required pixel fixtures:

- Flat field.
- Fine stripes.
- Checkerboard.
- Radial grid.
- Light and dark high-contrast fields.
- Sparse connector topology.

Acceptance measurements:

- Magnification is visibly radial and continuous at the boundary.
- Interior high-frequency energy is reduced roughly `35–55%`.
- Chroma edge energy remains subordinate to luminance/rim energy.
- No transparent holes or clipped sampling at the circle.
- The crisp status glyph is pixel-identical with optics enabled and disabled.
- Repeated Reduce Motion captures are identical.

## Capture expansion

The current 44-frame matrix lacks Readiness accessibility variants. Add twelve logical frames:

1. Readiness ready, Light
2. Readiness minimum size, Dark
3. Readiness wide, Dark
4. Increase Contrast, Dark
5. Reduce Transparency, Dark
6. Differentiate Without Color, Dark
7. Reduce Transparency plus Increase Contrast
8. Increase Contrast, Light
9. Reduce Transparency, Light
10. Blocked plus Differentiate Without Color
11. Checking plus Reduce Motion
12. Ready plus inactive window

That raises the logical matrix to 56 frames.

Run the new Readiness frames at both 1× and 2× backing scale. If a real 1× environment is unavailable, record that as unavailable external evidence rather than claiming a pass.

## Manual accessibility acceptance

- VoiceOver: the optical decoration is silent; Readiness remains one concise summary with separately reachable evidence rows.
- Full Keyboard Access: surrounding native actions retain predictable Tab order, Return/Space activation, and an unfiltered focus ring.
- Voice Control and Switch Control: no decorative target is exposed.
- Reduce Transparency: refractive and chromatic layers disappear immediately; an opaque static surface remains.
- Increase Contrast: perimeter and symbol contrast increase without stronger distortion.
- Differentiate Without Color: wording, symbol, and structure communicate state.
- Reduce Motion: highlight motion stops immediately.
- Inactive window: optics attenuate and stop animating; semantic content remains unchanged.
- Resizing: no edge leakage, sampling seams, or focus-ring clipping.
- Transcript/log stress: selectable text remains entirely outside the filtered subtree.

## Performance budget

At `72 pt` and Retina `2×`, one invalidated refractive frame has the
conservative upper bound:

```text
144 × 144 = 20,736 pixels
20,736 × 11 reads ≈ 228,096 reads/frame
```

If the static refraction were conservatively invalidated at the Core
material's full 15 FPS ceiling:

```text
≈ 3.42 million layer reads/second
```

The refraction has no time input; only the separate surface highlight is
state-animated. This figure is therefore an upper-bound estimate, not measured
performance evidence.

Acceptance requires an Instruments trace showing:

- No periodic idle work.
- No updates while the window is inactive.
- Immediate suspension under Reduce Motion.
- Active animation capped at 15 FPS.
- No new SwiftUI hitches.
- Recorded exact-candidate GPU time, CPU time, power/thermal behavior, and downgrade threshold.

## Acceptance boundary

The architecture is accepted only after the exact integrated source builds,
the focused contracts pass, light and dark Core-lens captures show one
unduplicated refracted backdrop beneath an unchanged crisp status glyph, the
accessibility fallback removes optical sampling, and attributed profiling
confirms the bounded pass adds no material regression. Research prototypes,
earlier captures, and source-level inspection are not acceptance evidence.
