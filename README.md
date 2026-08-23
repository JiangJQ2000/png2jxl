# png2jxl

```bash
pip install -U png2jxl
```

[!WARNING]
png2jxl is currently in an early stage of development. APIs, file formats,
and behavior may change without notice, and backward compatibility is not
guaranteed at this time.

`png2jxl` converts supported PNG files to pixel-lossless JPEG XL while retaining
enough information to reconstruct the original PNG **byte-for-byte**.

The output remains a regular JPEG XL image: [`pillow-jxl-plugin`](https://github.com/Isotr0py/pillow-jpegxl-plugin) handles JXL
coding, while [`preflate-rs`](https://github.com/microsoft/preflate-rs) enables exact reconstruction of the original
DEFLATE stream.

```python
from png2jxl import jxl_to_png, png_to_jxl

source = open("image.png", "rb").read()
archive = png_to_jxl(source)
assert archive is not None
assert jxl_to_png(archive) == source
```

Successful calls always verify exact source length and SHA-256. Encoding also
reconstructs the completed JXL and compares it directly with the source. There
is no unverified or best-effort public mode.

Use `only_if_smaller=True` when a larger result should be rejected with `None`:

```python
archive = png_to_jxl(source, effort=10, only_if_smaller=True)
```

## Pillow integration

Importing `png2jxl` idempotently augments the registered upstream JXL save
handler:

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

Path-backed PNG images are reread from `image.filename`. Images opened from a
stream, copied, cropped, converted, generated, or otherwise detached from exact
source bytes require `source_png=source`. The source file must describe the
current mode, dimensions, and samples. Ordinary JXL/JPEG behavior continues to
delegate to `pillow_jxl`.

## Support and limits

Supported PNG images are static, non-interlaced, 8-bit L, LA, RGB, or RGBA
files using PNG filters 0–4 and consecutive IDAT chunks. Indexed color,
low-bit-depth or 16-bit samples, Adam7, and APNG are rejected explicitly.

Public functions accept an immutable `ResourceLimits` value. Defaults allow up
to 1 GiB input/output and 512 MiB filtered plaintext or preflate corrections;
see [the architecture notes](docs/architecture.md) before increasing them.

Generic-viewer color, transparency-key, or ancillary-metadata semantics can be
limited by the public `pillow_jxl` encoder even though the original PNG bytes
remain reconstructable. `jxl_to_png` currently assumes trusted JXL input because
the upstream decoder does not expose a dimension-only preflight before pixel
allocation.

## Development

```bash
maturin develop
pytest -q
ruff check .
ruff format --check .
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
```

The persistent format is documented in [docs/wire-format.md](docs/wire-format.md).
