# png2jxl

`png2jxl` converts supported PNG images to pixel-lossless JPEG XL while preserving enough information to reconstruct the original PNG **byte-for-byte**.

## Installation

```bash
pip install -U png2jxl
```

## Python API

```python
from png2jxl import jxl_to_png, png_to_jxl

source = open("image.png", "rb").read()
archive = png_to_jxl(source)

assert archive is not None
assert jxl_to_png(archive) == source
```

Encoding and decoding are verified automatically against the original source.

To reject results that are larger than the source PNG:

```python
archive = png_to_jxl(source, effort=10, only_if_smaller=True)
```

## Command line

```bash
python -m png2jxl encode image.png   # writes image.jxl
python -m png2jxl decode image.jxl   # writes image.png
python -m png2jxl check image.jxl    # checks for the pngr envelope
```

* Use `-o/--output` to choose an output path; otherwise the extension is swapped automatically.
* Existing output files are never overwritten.
* `encode` supports `--effort 1-10`, `--threads`, and `--only-if-smaller`.
* Exit codes: `0` success, `1` failure, `2` usage error, `3` rejected by `--only-if-smaller`.

## Pillow integration

Importing `png2jxl` extends the registered JXL save handler:

```python
import png2jxl
from PIL import Image

with Image.open("image.png") as image:
    image.save(
        "image.png.jxl",
        format="JXL",
        png_reconstruction=True,
        effort=10,
    )
```

For path-backed PNG images, the original bytes are read from `image.filename`. For images opened from streams or modified in memory, pass the original bytes explicitly with `source_png=source`.

Ordinary JXL/JPEG behavior continues to delegate to `pillow_jxl`.

## Supported PNGs

Supported:

* Static 8-bit `L`, `LA`, `RGB`, and `RGBA` images
* 8-bit indexed-color PNGs with a valid `PLTE` and optional `tRNS`
* PNG filters 0–4
* Consecutive `IDAT` chunks

Not supported:

* 1/2/4-bit indexed samples
* 16-bit samples
* APNG
* Indexed images where two used palette entries resolve to the same effective RGBA color

Unused duplicate palette entries are allowed.

## Limits and notes

Public functions accept an immutable `ResourceLimits` value. Defaults allow up to 1 GiB input/output and 512 MiB of filtered plaintext or preflate corrections.

Some generic-viewer color, transparency, or ancillary-metadata behavior may be limited by the upstream `pillow_jxl` encoder even though the original PNG bytes remain reconstructable. `jxl_to_png` currently assumes trusted JXL input because the upstream decoder does not expose a dimension-only preflight before pixel allocation.

## Dependencies

* [`pillow-jxl-plugin`](https://github.com/Isotr0py/pillow-jpegxl-plugin) — JPEG XL encoding and decoding
* [`preflate-rs`](https://github.com/microsoft/preflate-rs) — exact reconstruction of the original DEFLATE stream

## Development

* [Format specification](docs/wire-format.md)
* [Architecture notes](docs/architecture.md)
