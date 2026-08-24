# Testing and compatibility

## Supported exact profile

The test matrix covers static, 8-bit L,
LA, RGB, RGBA, and indexed-color PNG; filters 0–4 including mixed rows and
passes;
single/multiple/zero-length IDAT chunks; unusual split boundaries; legal
RGB/RGBA PLTE; ancillary chunks before and after IDAT; and compressible/noisy
samples.

Indexed-color coverage includes different palette sizes, absent/full/partial
`tRNS`, L/LA/RGB/RGBA carrier selection, dead duplicate colors, explicit
rejection of duplicate effective colors through multiple used indices, all
five filters, multiple IDAT chunks, out-of-range indices, and malformed or
misordered `PLTE`/`tRNS` chunks.

Unsupported legal profiles and corrupt inputs must raise typed exceptions.
Every correctness fix requires a regression test.

## Required coverage

- Native raw-DEFLATE: plaintext/correction roundtrip, trailing input,
  correction corruption, and plaintext limits.
- PNG: signature, IHDR profile, chunk ordering/bounds/CRCs, APNG markers, zlib
  wrapper, expected plaintext, Adam7 pass geometry, filters, optional Numba JIT
  and pure-Python fallback, and resource limits.
- Wire/JUMBF/JXL: deterministic serialization, body digest, version/flags,
  palette bitmap bounds and set consistency, malicious lengths,
  unrelated/duplicate/malformed JUMBF, 32/64/size-0 boxes, and truncation.
- End to end: generated archive normally decodes through `pillow_jxl` and
  optional `djxl`, then reconstructs byte-for-byte with matching SHA-256.
- Tampering: decoded pixels, palette metadata/bitmap, corrections, source hash,
  IHDR, IDAT lengths, Adler-32, duplicate project boxes, and unsupported
  wire/preflate versions.
- Pillow: normal delegation, path source, explicit source bytes, stream failure,
  changed image mismatch, size rejection, and idempotent registration.
- Pillow corpus: all 420 PNG files from Pillow 12.3.0 commit
  `bb1d8e8ab8d29048624d96e3ee53cecf7c13d13d`; supported files must roundtrip
  byte-for-byte, while known unsupported or corrupt files must raise their
  corresponding typed exception. The pinned corpus currently contains 341
  exact-roundtrip cases, 72 unsupported profiles, and 7 corrupt inputs.

## Compatibility policy

Keep a source PNG and v1 archive golden pair. Future dependency changes must
decode that archive before compatibility is claimed. Encoder byte output itself
need not be stable because JXL encoder output may change; the `pngr` serialization
and reconstruction behavior must remain stable.

Python package metadata has only a lower bound of 3.11. CI tests the full
Linux/macOS/Windows by Python 3.11–3.14 matrix. Every matrix job also builds a
release wheel and uploads the wheel directly as an unarchived Actions artifact.
It benchmarks Pillow's `hopper.png` at effort 10. Benchmark timing is observational and
has no unstable pass/fail threshold. Newer Python versions may install but are
not claimed until their exact tests pass.

## Commands

```bash
maturin develop
pytest -q
ruff check .
ruff format --check .
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
```

The Pillow corpus test is skipped unless `PILLOW_TEST_IMAGES` names a checkout's
`Tests/images` directory. CI always sets it to the pinned Pillow checkout.

For size and timing measurements, run `python tools/benchmark.py image.png`.
Use `--num-threads` to compare JXL thread counts.
