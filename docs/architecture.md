# Architecture

## Ownership

`png2jxl` is Python-first. Modules have one responsibility:

- `png.py` strictly parses the supported PNG container profile.
- `png_filter.py` implements PNG filters 0–4 and Adam7 scanline layout.
- `reconstruction.py` owns the persistent `pngr` payload.
- `jumbf.py` owns the minimal project JUMBF envelope.
- `jxl_container.py` strictly reads top-level JXL box boundaries.
- `api.py` orchestrates exact encode and reconstruction.
- `plugin.py` wraps the upstream Pillow JXL save handler.
- `cli.py` implements the `python -m png2jxl` encode/decode/check entry points.
- `path.py`, `limits.py`, and `exceptions.py` expose small supporting APIs.

The only project native code is `src/lib.rs`. It releases the GIL around the
public `preflate-rs` whole-stream raw-DEFLATE APIs and converts structured Rust
errors to Python exceptions. It contains no PNG or JXL implementation.

If Numba is importable, `png_filter.py` eagerly JIT-compiles its non-interlaced
and Adam7 refilter loops from explicit signatures. Otherwise the same cores
execute as plain Python without changing the API.

## Encode pipeline

1. Validate the PNG signature, chunks, CRCs, IHDR profile, IDAT ordering, zlib
   wrapper, expected filtered length, and resource bounds.
2. Preserve exact prefix/suffix bytes, IDAT payload lengths, zlib header, and
   Adler-32. Analyze only the raw-DEFLATE body with verified preflate.
3. Cross-check preflate plaintext against a bounded standard zlib decode,
   expected length, and Adler-32; extract exact scanline filters in normal or
   Adam7 pass order. Build a minimal pixel-only PNG from the validated
   IHDR/PLTE/IDAT data and load it through Pillow to obtain the unfiltered,
   deinterlaced sample raster without reparsing unrelated ancillary metadata.
4. For indexed color, record a 256-bit used-index bitmap and expand indices to
   the smallest lossless L/LA/RGB/RGBA carrier. Reject two used indices with the
   same effective RGBA color because carrier pixels cannot disambiguate them.
5. Serialize the v2.0 reconstruction payload and wrap it in project JUMBF.
6. Encode samples through `pillow_jxl` with lossless mode, container mode, an
   uncompressed `jumb`, effort 1–10, and the requested JXL thread count.
7. Unless `only_if_smaller` rejects the result, reconstruct from the final JXL
   and require direct byte equality before returning.

## Decode pipeline

1. Enforce the JXL byte limit, strictly parse top-level boxes, and locate exactly
   one project JUMBF by both UUID and label.
2. Bounds-check and parse the persistent payload. Enforce stored IHDR, pixel,
   plaintext, prefix/suffix, correction, and final-size limits before JXL decode.
3. Decode only through `pillow_jxl`, then match mode, dimensions, and sample
   length to the stored IHDR.
4. For indexed color, derive the carrier mode from exact `PLTE`/`tRNS` bytes and
   the used-index bitmap, invert carrier samples to indices, and require that
   the observed index set exactly matches the bitmap.
5. Reorder source samples into normal or Adam7 pass scanlines, refilter with
   exact stored filters, verify Adler-32, recreate raw-DEFLATE,
   and verify the rebuilt zlib stream yields the same filtered plaintext.
6. Split the zlib bytes at the stored IDAT boundaries, regenerate IDAT CRCs,
   append exact prefix/suffix, validate the final PNG, and require stored length
   and SHA-256.

No path returns best-effort data.

## Resource limits

`ResourceLimits` is immutable and can be replaced per call. Defaults are 1 GiB
for input PNG/JXL, persistent payload, and final PNG; 512 MiB for filtered
plaintext, prefix, suffix, and preflate corrections; 100,000 per dimension;
128 million pixels; and 65,536 chunks/IDAT chunks. Peak process memory can be
several times a single limit because codec and verification buffers coexist.

## Trust and color boundaries

PNG, JUMBF, wire data, and JXL boxes are parsed defensively. The MVP decode API
nevertheless requires trusted JXL because `pillow_jxl` 1.3.8 decodes pixels
before returning codestream dimensions, so hostile codestream allocation cannot
be capped by stored metadata alone.

All original PNG metadata bytes remain exact. Indexed source samples are stored
indirectly as ordinary JXL pixels plus a used-index bitmap; `tRNS` entries not
present in the chunk have alpha 255, and no background compositing occurs.
Generic JXL viewer color, transparency-key, or metadata semantics may differ
when the public upstream encoder cannot express an original PNG profile or
ancillary chunk. Do not introduce another JXL binding to compensate.

## Pillow integration

Importing `png2jxl` idempotently captures and wraps the registered upstream JXL
save handler. It delegates unchanged unless `png_reconstruction=True`.

Exact mode rereads `im.filename` or requires explicit `source_png=bytes`, then
checks the current image mode, size, and samples against that source. It accepts
`effort`, `num_threads`, and `only_if_smaller`; the latter raises
`ArchiveNotSmallerError` in the save hook because Pillow requires bytes to be
written.
