# AGENTS.md

## Mission

`png2jxl` adds byte-exact PNG reconstruction to ordinary, pixel-lossless JPEG
XL images. A successful encode must satisfy:

```python
archive = png_to_jxl(source_png)
assert archive is not None
assert jxl_to_png(archive) == source_png
```

File-byte equality, source length, and SHA-256 are mandatory. Never downgrade
an exact request to ordinary pixel-lossless JXL.

## Required reading

Before changing implementation or persistent data, read the relevant document:

- `docs/architecture.md` — module ownership, pipelines, Pillow integration,
  resource and trust boundaries.
- `docs/wire-format.md` — persistent `pngr` payload and JUMBF envelope.
- `docs/testing.md` — supported PNG profile, fixtures, compatibility, and
  required checks.

## Fixed architecture

- Python owns PNG parsing, PNG filters, orchestration, wire data, JUMBF,
  read-only JXL box validation, paths, and Pillow integration.
- All JPEG XL encoding and decoding goes through `pillow_jxl` from
  `pillow-jxl-plugin`.
- Rust contains only the thin PyO3 `_preflate` adapter over public
  `preflate-rs` raw-DEFLATE APIs.
- Do not add a second JXL binding, copy JXL codec code, use private preflate
  container modules, or move PNG business logic into Rust without profiling.
- Do not write or splice JXL boxes in production code; `pillow_jxl` writes the
  uncompressed `jumb` box.

## Non-negotiable behavior

- Raw PNG bytes are canonical; `Image.tobytes()` is never file-level evidence.
- Successful public encode/decode always performs complete verification. There
  is no public `verify=False` path and no best-effort return.
- The `pngr` v1.0 format is persistent. Never change its serialization under
  the same version or casually update pinned `preflate-rs`.
- Treat PNG, JUMBF, reconstruction data, and JXL box boundaries as untrusted and
  check lengths before slicing, allocation, or expensive work.
- MVP `jxl_to_png` accepts trusted JXL only: current `pillow_jxl` cannot expose
  codestream dimensions before decoding. Validate archive size and stored IHDR
  before decode, then validate actual decoded mode, size, and sample length.
- Preserve normal `pillow_jxl` behavior unless `png_reconstruction=True`.
- JPEG byte reconstruction remains upstream `pillow_jxl` functionality.

## MVP PNG profile

Support only static, non-interlaced, compression/filter method 0, 8-bit L, LA,
RGB, and RGBA PNG with filters 0–4, strict CRCs, and consecutive IDAT chunks.
Reject indexed color, bit depths 1/2/4/16, Adam7, APNG, invalid chunks, and
incompatible zlib features. Do not expand claims before exact-roundtrip tests.

## Dependencies

- `pillow-jxl-plugin==1.3.8`
- `preflate-rs==0.7.6`
- Python `>=3.11` with no upper metadata cap; only tested versions are claimed.

Inspect current upstream source before dependency-sensitive changes and run old
archive tests after any update.

## Required checks

```bash
maturin develop
pytest -q
ruff check .
ruff format --check .
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
```

Use byte-exact/file-level reversible and pixel-lossless precisely; never
conflate them. This repository has not selected a project license and must not
be published until that decision and dependency compatibility are reviewed.

