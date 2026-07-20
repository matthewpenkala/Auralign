<!--
SPDX-FileCopyrightText: 2026 Matthew Penkala

SPDX-License-Identifier: LicenseRef-PolyForm-Internal-Use-1.0.0
-->

# macOS Asset Provenance

## Application Icon

The production application icon is the first-party Audio Waveform icon project
selected by the repository owner for Auralign on 2026-07-20.

The current macOS 26 source is
`Auralign/audio-waveform-icon.icon`, a genuine Icon Composer 1.6 document with
eight SVG layers in four material groups. Xcode selects the document basename
`audio-waveform-icon` as the primary app icon and generates current and
back-deployed representations. The files in
`Auralign/Assets.xcassets/AppIcon.appiconset` are the same project's separately
verified conventional Xcode fallback; that catalog is retained as a fallback
source and is not selected as the primary icon.

The `.icon` package and fallback catalog were copied unchanged from the exact
source-project exports identified below. Auralign-specific integration changed
only repository-relative placement, Xcode selection, documentation, and
validation; it did not redraw, recolor, rename, optimize, or otherwise modify
the imported icon source files.

Authoritative local source project:

- source-project commit:
  `ae319eb89650b9c8b0ce4ce5bac5e8adf2a0bb48`;
- genuine source export:
  `exports/icon-composer/audio-waveform-icon.icon`;
- verified fallback source:
  `exports/xcode/AppIcon.xcassets/AppIcon.appiconset`;
- final release archive SHA-256:
  `1f8db9502713133f8d2963be8a6009e9ab6b593bb81ddc3e33b9e10210a0e5d6`;
- genuine `.icon` package snapshot SHA-256:
  `69b2088ccbe96a0c88fae867c775c6957f2ffe897b513667b44d216a0f292862`;
- genuine `.icon` package inventory:
  9 regular files, 29,869 bytes, and one `Assets` directory;
- `.icon` `icon.json` SHA-256:
  `e3e1589694b7f168fd04074b09b4d9a06620e4ccc34bd68e17ece5f64ddea4a3`;
- fallback `Contents.json` SHA-256:
  `13b1c7461192863c9f3ab3ceab313acd6f6531898360983495bf13d48dfc967f`;
- immutable AI reference-evidence SHA-256:
  `24a627e0bec4201d19f24c37422fddafda48d7cd450807ff523ffe3f9dded3af`.

The source project records the supplied AI-generated raster as immutable
identity, measurement, and comparison evidence only—not a production
dependency. The shipping artwork is deterministic authored vector/procedural
material plus output from Apple's official tools. No third-party icon pixels,
vectors, palettes, logos, or trade dress are included. The original package
records human review, source-layer binding, Default/Dark/Mono inspection,
`ictool`, `actool`, Xcode integration, small-size, reproducibility, archive,
and independent-release checks. Auralign independently revalidates the exact
vendored files and its own built bundle.

The earlier generated Auralign icon is superseded, is not referenced by a
production target, and is not tracked or shipped. Machine-local redesign
evidence was retained during integration, but the current icon's provenance
does not depend on ignored workstation state: it is bound above to the
repository-local bytes, immutable source-project commit, and exact release
archive hash. All icon material remains subject to the repository owner's human
intellectual-property, provenance, and release review. This first-party asset
does not change Auralign's external-component registry or distribution posture.

## Installer Design Kit

The owner directed Auralign to internalize the first-party Installer presentation
kit from source subtree `installer/pkg-design` at immutable source-project commit
`bd94520ecf106b7502ad04e19e4997dbb74d05dd`. The original manifest SHA-256 is
`94b89d5d1aae0469c28c22256d745d480ee59bc28863aa4cfbf1314f29acdc68`.
All 12 source-kit files were initially copied byte-for-byte before repository-
specific integration.

The initially internalized production artwork was byte-identical to the source
kit:

- original Light background PNG:
  `1df7830cbfab4da98f80e5c89a8ed31fc7321a5d95a0e16a305a05f307e6c2ee`;
- original Dark Aqua background PNG:
  `513c9cf18b978409ec2c5ed85e08a856abfd38389a503c232dc78c1a412fd366`;
- original Light SVG master:
  `b4dbf9587022f998fb16d57e73dfe141d8fede631b385f0941c438ff8bbf17ae`;
- original Dark SVG master:
  `fe24491644f2c6193b48bc65108c92bf7de942ba93a1f3f19b6f8f79729aa416`.

After native Installer inspection on 2026-07-20, Auralign intentionally adapted
the first-party vector masters to keep the upper 170-point brand region quiet
for Installer's step labels, remove the superseded lower inset card, enlarge the
exact styled symbol into a borderless lower-rail composition with intentional
left/right/bottom crop, soften the rail-to-content transition, and balance Dark
Aqua toward Installer's native chrome and scroll material. The localizable HTML
uses the macOS system font and includes a concise informational legal/privacy
summary derived from Auralign's controlling repository documents. It does not
replace or expand those terms.

The styled symbol is mechanically derived from the owner's preferred
deterministic icon construction at immutable source-project commit
`ae319eb89650b9c8b0ce4ce5bac5e8adf2a0bb48`:

- canonical styled SVG SHA-256:
  `38a9c73d747e58f5946c8a47505795becda67c7f9ebe8252220a834e85ff0397`;
- preferred 1024-pixel reference SHA-256:
  `2c1b5ed43a0ae83cb43582bbe34cef1071e44cf257ef5b41edb0480a21dc1e4a`;
- curated transparent symbol SVG SHA-256:
  `8ed79fd4430b534862a90d224fd03c6aac8e1fae32c580b6716146c27b8167b7`;
- curated 1822×1499 RGBA+sRGB raster SHA-256:
  `6f556cd43c2c30f3658490da933e1e345a52320f223af47b3de192cae1f63963`.

The repository-local exporter reproduces that raster from the curated SVG and
fails closed on source hash, alpha bounds, crop, dimensions, byte count, or
output-hash drift. Redundant full-canvas and low-resolution placement variants
are recorded in the brand manifest but are not retained.

Current adapted production artwork:

- Light background PNG:
  `b9e1e34a58ce6a6f250cea4fe79db7cdf165f359e9b0fe1efe398496e4bf1014`;
- Dark Aqua background PNG:
  `c7c9ba8bfed462b8cc5afe84250fc7177f0ee8033de96a3ceeded8206757deef`;
- Light SVG master:
  `78701511adef20903dd4c82672b511df1344e6fedf1429d5fcba01d20545bd25`;
- Dark SVG master:
  `baae4aa1190d9d95035a827ff9e686964e0ba027191e2e1b9846280c5cb73be2`.

The build has no external absolute-path dependency. The two 1240×836 RGB+sRGB
backgrounds remain intentionally text-free scaffolds for a 620×418 Installer
canvas; Installer retains native text, controls, focus, localization, security,
and accessibility. The completed PKG—not a blank background—is the visual
acceptance artifact. Human IP, provenance, and release review remains
authoritative.
