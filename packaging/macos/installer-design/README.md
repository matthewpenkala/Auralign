# Auralign Installer design kit

This directory is a deliberately restrained visual scaffold for a macOS
Installer product archive. It is related to the app icon without repeating the
entire icon inside every Installer pane.

## Design

- Canvas: 620 × 418 points, rendered at 2× as 1240 × 836 RGB PNGs.
- Left brand rail: 210 points. It carries the mirrored cyan→violet waveform,
  three editing rails, and the selected amber playhead.
- Main field: intentionally quiet and opaque so Installer owns text, buttons,
  focus, localization, and accessibility.
- Light and Dark Aqua resources share identical geometry.
- No text, fake buttons, screenshots, platform masks, stochastic texture, or
  embedded raster appears in the source artwork.
- The waveform comes directly from `src/spec/icon-design.json`; the uploaded
  AI reference is not read by the builder.

The composition is asymmetrical on purpose. Installer normally needs the
reading surface more than it needs a poster. The small brand rail gives the
package a recognizable Auralign signature without competing with installation
status or security prompts.

## Files

- `Distribution.xml.template` — minimal product-distribution scaffold.
- `Resources/background.png` — Light appearance resource.
- `Resources/background-darkAqua.png` — Dark Aqua resource.
- `Resources/en.lproj/` — intentionally plain, localizable Installer copy.
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
and a temporary unsigned `pkgbuild`/`productbuild` round trip. The temporary
package has no payload and is discarded. It does not regenerate artwork from an
external checkout.

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

Do not add a license pane until approved license text exists. Do not use
Installer JavaScript for visual behavior.

## Internalization record

The kit was copied byte-for-byte from the owner-directed source subtree
`installer/pkg-design` at commit
`bd94520ecf106b7502ad04e19e4997dbb74d05dd`. The original manifest SHA-256 was
`94b89d5d1aae0469c28c22256d745d480ee59bc28863aa4cfbf1314f29acdc68`.
Auralign adapted only the verifier, Makefile, README, manifest, and production
Distribution integration. The production PNG and SVG artwork remains unchanged.
The Installer renders native, localized copy and controls over these intentionally
text-free background scaffolds; the backgrounds alone are not finished screens.
