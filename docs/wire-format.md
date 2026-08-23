# `pngr` wire format v1.0

The reconstruction record is persistent, deterministic, big-endian, and
bounded. It never uses pickle or marshal.

## JUMBF envelope

`pillow_jxl` writes one top-level, uncompressed `jumb` box whose payload is:

```text
jumd
  type UUID: 6f1387ee-629c-5300-bb69-c533644ba5fa
  toggles: label-present only
  label: png2jxl-png\0
pngr
  v1.0 reconstruction payload
```

The UUID is UUIDv5 derived from the literal name `png2jxl` in the DNS
namespace. Identification requires both UUID and label. Unrelated JUMBF boxes
may coexist; partial identity matches, duplicates, malformed project
descriptions, or unexpected project child boxes are corrupt.

## Fixed header

The header uses `>8sHHIQQ32s32sHHHH13sQ2s4sQQQII`:

| Field | Meaning |
| --- | --- |
| magic | `89 50 4e 47 52 0d 0a 1a` |
| major/minor | `1`, `0` |
| flags | `0`; unknown bits are incompatible |
| payload size | complete header and body length |
| source length | exact original PNG byte length |
| source SHA-256 | exact original PNG digest |
| body SHA-256 | digest of every byte after the fixed header |
| codec ID/version | preflate ID `1`, version `0.7.6` |
| IHDR | exact 13-byte IHDR payload |
| filtered length | expected filtered scanline plaintext length |
| zlib header/Adler | exact 2-byte header and 4-byte trailer |
| section lengths | prefix, suffix, opaque correction lengths |
| counts | IDAT payload count and row-filter count |

## Variable body

Sections appear without alignment or padding in this exact order:

1. exact PNG prefix through the byte before the first IDAT length field;
2. exact suffix immediately after the last IDAT CRC through IEND/EOF;
3. `idat_count` unsigned 32-bit original payload lengths;
4. one original filter byte per row;
5. opaque preflate correction bytes.

The reader checks the body digest and every count/length before slicing. IDAT
lengths must sum to the recreated zlib stream length, and prefix + IDAT chunks +
suffix must equal the stored source length. Only wire 1.0 and preflate 0.7.6 are
accepted. Any future incompatible serialization requires a new major version;
new optional compatible behavior requires a new minor version and explicit old
archive tests.

The hashes detect accidental or unauthenticated byte modification. They are not
digital signatures and do not establish who created an archive.
