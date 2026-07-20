# Auralign Installer brand artwork

These repository-local assets give macOS Installer the dimensional visual
family selected by the owner without depending on Icon Composer or an external
checkout at package-build time.

## Production asset

`auralign-styled-symbol-tight.png` is the high-resolution transparent source
used by both Installer background masters. It contains only the waveform,
center axis, editing rails, and controls; it does not contain the app-icon frame
or panel. The production composition enlarges it beyond the 210-point brand
rail and intentionally clips its outer and lower energy at the rail boundary.
There is no decorative card around the symbol.

`auralign-styled-symbol.svg` is the deterministic transparent construction
source. The checked-in PNG keeps rasterization identical across the release
build and is not copied into the final package separately; it is composited
into the text-free Light and Dark Aqua Installer backgrounds.

## Visual provenance

The artwork is derived mechanically from:

- `exports/svg/audio_waveform_icon_final_styled_2048.svg`
- `exports/raster/srgb/audio_waveform_icon_final_1024.png`

That raster is the user's preferred visual reference. The extracted symbol
retains its baked gradients, directional edge lighting, restrained local bloom,
contact shadows, and dimensional controls.

The immutable source-project commit is
`ae319eb89650b9c8b0ce4ce5bac5e8adf2a0bb48`. The canonical styled SVG SHA-256
is `38a9c73d747e58f5946c8a47505795becda67c7f9ebe8252220a834e85ff0397`;
the preferred 1024-pixel reference SHA-256 is
`2c1b5ed43a0ae83cb43582bbe34cef1071e44cf257ef5b41edb0480a21dc1e4a`.

Apple's Icon Composer Liquid Glass treatment is applied by Apple's renderer and
cannot be exported as reusable SVG effects. For an Installer background, use a
baked transparent PNG when exact appearance matters. The SVG is the
deterministic construction source, not a promise of identical rendering in
every consumer.

`manifest.json` records the canonical source hashes, retained group IDs, crop,
render dimensions, color-profile presence, output hashes, and Auralign
placement decision.

Verify exact deterministic reproduction with:

```sh
uv run --frozen python \
  packaging/macos/installer-design/BrandAssets/export_brand_artwork.py
```

Pass `--write` only when intentionally replacing the checked-in raster after
the exact source, alpha bounds, crop, output size, byte count, and SHA-256 all
match. The exporter never reads or writes the external source project.
