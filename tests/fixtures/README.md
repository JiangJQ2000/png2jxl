# Golden fixtures

`v2-rgba.png.jxl` was created by png2jxl 0.1.0 with wire version 2.0,
`preflate-rs` 0.7.6, and `pillow-jxl-plugin` 1.3.8. Tests require it to
reconstruct `v2-rgba.png` exactly.

`v2-palette.json` stores a palette PNG and its v2.0 JXL archive as
base64. It exercises adaptive RGBA expansion, a dead duplicate palette entry,
and filters 0 through 4.
