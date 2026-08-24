# Golden fixtures

`v1-rgba.png.jxl` was created by png2jxl 0.1.0 with wire version 1.0,
`preflate-rs` 0.7.6, and `pillow-jxl-plugin` 1.3.8. Tests require it to
reconstruct `v1-rgba.png` exactly.

`v1-palette.json` stores a palette PNG and its flagged v1.0 JXL archive as
base64. It exercises adaptive RGBA expansion, a dead duplicate palette entry,
and filters 0 through 4.
