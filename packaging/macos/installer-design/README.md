# Auralign Installer design kit

This directory is a deliberately restrained visual scaffold for a macOS
Installer product archive. It is related to the app icon without repeating the
entire icon inside every Installer pane.

## Design

- Canvas: 620 × 418 points, rendered at 2× as 1240 × 836 RGB PNGs.
- Left brand rail: 210 points. Its upper region remains quiet for Installer's
  native step labels; the lower region carries an enlarged, borderless
  cyan→violet waveform, three editing rails, and the selected amber playhead.
  The outer and lower artwork is intentionally cropped by the rail rather than
  enclosed in another card.
- Main field: intentionally quiet and opaque so Installer owns text, buttons,
  focus, localization, and accessibility. Dark Aqua is balanced toward
  Installer's native chrome and scroll material so the system-owned text region
  reads as part of one composition.
- Light and Dark Aqua resources share identical geometry.
- No text, fake buttons, screenshots, platform masks, stochastic texture, or
  embedded data URL appears in the vector masters.
- The waveform uses the exact repository-local transparent source and
  high-resolution raster mechanically extracted from the owner's preferred
  deterministic styled icon at immutable source-project commit
  `ae319eb89650b9c8b0ce4ce5bac5e8adf2a0bb48`.

The composition is asymmetrical on purpose. Installer normally needs the
reading surface more than it needs a poster. The small brand rail gives the
package a recognizable Auralign signature without competing with installation
status or security prompts.

## Files

- `Distribution.xml.template` — minimal product-distribution scaffold.
- `Resources/background.png` — Light appearance resource.
- `Resources/background-darkAqua.png` — Dark Aqua resource.
- `Resources/en.lproj/` — localizable system-font Installer copy, including an
  informational legal/privacy notice derived from the repository's controlling
  license, third-party, model-access, and local-processing rules.
- `BrandAssets/` — curated transparent styled source, exact high-resolution
  raster, deterministic exporter, and provenance record.
- `Sources/` — deterministic vector masters.
- `preview.png` — non-production side-by-side review sheet.
- `manifest.json` — dimensions, hashes, quiet-zone policy, contrast, and
  validation evidence.

## Verify

```sh
make -C packaging/macos/installer-design check
```

The repository-local verifier validates both committed PNGs, XML and HTML
resources, color profiles, text-free SVG artwork, the immutable imported hashes,
exact styled-symbol reproduction, and a temporary unsigned
`pkgbuild`/`productbuild` round trip. The temporary package has no payload and
is discarded. It does not regenerate artwork from an external checkout.

## Integration

1. Replace the four `@@…@@` tokens in `Distribution.xml.template`:

   - `@@PRODUCT_NAME@@`
   - `@@PACKAGE_ID@@`
   - `@@COMPONENT_PACKAGE@@`

2. Build the real component package with its stable identifier, version, and
   `/Applications` install location.
3. Build the product archive:

   ```sh
   productbuild \
     --distribution path/to/Distribution.xml \
     --package-path path/to/components \
     --resources packaging/macos/installer-design/Resources \
     path/to/Auralign.pkg
   ```

4. Apply the actual Developer ID Installer signature, trusted timestamp, and
   notarization in the release pipeline. This design kit does not invent or
   substitute signing credentials.
5. Validate the finished artifact with `pkgutil`, `spctl -a -t install`, and a
   clean-machine installation test.

The public Distribution XML reference formally documents `background`.
`background-darkAqua` is retained because the installed macOS 26
`productbuild`/Installer path accepts it and the generated round-trip gate
tests it. If a future toolchain rejects that extension, remove only that
element; keep `background.png` as the compatible baseline.

Do not add a separate click-through license-agreement pane until approved
agreement text exists. The welcome pane's current legal/privacy section is
informational and points to the complete license and third-party notices shipped
inside the app. Do not use Installer JavaScript for visual behavior.

## Internalization record

The kit was initially copied byte-for-byte from the owner-directed source subtree
`installer/pkg-design` at commit
`bd94520ecf106b7502ad04e19e4997dbb74d05dd`. The original manifest SHA-256 was
`94b89d5d1aae0469c28c22256d745d480ee59bc28863aa4cfbf1314f29acdc68`.
Auralign first adapted the verifier, Makefile, README, manifest, and production
Distribution integration. After native Installer inspection on 2026-07-20, it
protected the upper 170-point native step-label region, removed the lower inset
card, and enlarged the owner's preferred styled waveform into a borderless
ambient composition with intentional left, right, and bottom crop. The curated
symbol was mechanically derived from the exact styled icon source at commit
`ae319eb89650b9c8b0ce4ce5bac5e8adf2a0bb48`; Auralign internalizes its source,
high-resolution raster, deterministic exporter, and hashes. It also changed the
localizable HTML copy to the macOS system font. The welcome copy gained an
informational legal/privacy summary derived from Auralign's controlling
repository documents; it does not replace or expand those terms.
Installer continues to own controls, focus, localization, and security
behavior over these background scaffolds.
