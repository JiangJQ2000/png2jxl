# `pngr` wire format v2.0

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
  v2.0 reconstruction payload
```

The UUID is UUIDv5 derived from the literal name `png2jxl` in the DNS
namespace. Identification requires both UUID and label. Unrelated JUMBF boxes
may coexist; partial identity matches, duplicates, malformed project
descriptions, or unexpected project child boxes are corrupt.

## Fixed header

The header uses `>HHHHHH32sQQI2s` (66 bytes):

| Field | Meaning |
| --- | --- |
| major/minor | `2`, `0` |
| codec ID/version | preflate ID `1`, version `0.7.6` |
| source SHA-256 | exact original PNG digest |
| prefix length | exact prefix byte length |
| suffix length | exact suffix byte length |
| IDAT count | original IDAT chunk count |
| zlib header | exact 2-byte zlib wrapper |

The codec is fixed to preflate 0.7.6; the runtime `_verify_native_version`
check pins the installed binding to the same version, so the wire records it
for documentation and rejects any other codec.

## Variable body

Sections appear without alignment or padding in this exact order:

1. the PNG prefix (signature and pre-IDAT chunk CRCs removed; see below);
2. the PNG suffix (all chunk CRCs removed and the trailing IEND chunk omitted;
   see below);
3. `idat_count` unsigned 32-bit original payload lengths;
4. the original row filters, base-5 packed into the minimum number of bytes;
5. opaque preflate correction bytes;
6. when the IHDR color type is 3, a fixed 32-byte used-index bitmap. Its
   presence is derived from the color type; its 32 bytes are stored here.

### Stripped framing

The stored prefix drops the fixed 8-byte PNG signature and the 4-byte CRC of
every pre-IDAT chunk (IHDR, and PLTE/tRNS for indexed color). Each chunk is
stored as just its 4-byte length, 4-byte type, and payload. The prefix always
begins with the IHDR chunk: the reader requires its length field to be `13` and
its type to be `IHDR`, then takes the 13-byte payload.

The stored suffix drops the 4-byte CRC of every post-IDAT
chunk and omits the trailing IEND chunk entirely. The remaining chunks are
stored as length, type, and payload only. On reconstruction the reader appends
`crc32(type + payload)` to each and finishes with a freshly computed IEND chunk.

### Row-filter packing

Each scanline filter is a value in `[0, 5)`. The `n` filter values are packed as
a single base-5 integer `sum(filter[i] * 5**i)` and stored big-endian in
`ceil(log2(5**n) / 8)` bytes. The reader knows `n` from the IHDR, splits off
exactly that many bytes, and unpacks the digits.

## Reconstruction notes

For palette archives, exact `PLTE` and optional `tRNS` chunks remain in the PNG
prefix. Missing `tRNS` entries have alpha 255. The used entries determine the
ordinary JXL carrier: grayscale opaque uses L, grayscale with transparency uses
LA, colored opaque uses RGB, and colored with transparency uses RGBA. Multiple
used indices may not resolve to the same effective RGBA color; unused duplicate
entries are allowed.

The reader validates the codec version, every length, and the section layout
before slicing, and confirms the sections exactly consume the payload. The
final `source SHA-256` covers the fully rebuilt PNG; it is the authoritative
byte-exact check. There is intentionally no body-level digest: the source
digest already catches any corruption, at the cost of detecting metadata-only
tampering only after full reconstruction.

Only wire 2.0 and preflate 0.7.6 are accepted. Any future incompatible
serialization requires a new major version; new optional compatible behavior
requires a new minor version and explicit old archive tests.

The hashes detect accidental or unauthenticated byte modification. They are not
digital signatures and do not establish who created an archive.
