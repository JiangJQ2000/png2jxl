"""Exact-roundtrip checks against Pillow's pinned upstream PNG corpus."""

import os
from hashlib import sha256
from pathlib import Path

import pytest
from png2jxl import CorruptPngError, UnsupportedPngError, jxl_to_png, png_to_jxl

EXPECTED_PNG_COUNT = 420
EXPECTED_APNG_COUNT = 54

UNSUPPORTED_PATHS = {
    "1_trns.png",
    "a_fli.png",
    "blend_transparency.png",
    "bmp/html/pal1.png",
    "bmp/html/pal1bg.png",
    "bmp/html/pal4.png",
    "bmp/html/pal8nonsquare-e.png",
    "eps/non_zero_bb.png",
    "eps/non_zero_bb_scale2.png",
    "eps/zero_bb.png",
    "eps/zero_bb_scale2.png",
    "g4-fillorder-test.png",
    "hopper_bw_500.png",
    "i_trns.png",
    "morph_a.png",
    "sunraster.im1.png",
    "tRNS_null_1x1.png",
    "tiny.png",
}

CORRUPT_PATHS = {
    "broken.png",
    "broken_data_stream.png",
    "hopper_idat_after_image_end.png",
    "padded_idat.png",
    "truncated_end_chunk.png",
    "truncated_image.png",
    "unknown_compression_method.png",
}

_root_value = os.environ.get("PILLOW_TEST_IMAGES")
PILLOW_ROOT = Path(_root_value) if _root_value else None
if PILLOW_ROOT is not None and not PILLOW_ROOT.is_dir():
    raise RuntimeError(f"PILLOW_TEST_IMAGES is not a directory: {PILLOW_ROOT}")

PILLOW_PNGS = (
    tuple(sorted(PILLOW_ROOT.rglob("*.png"))) if PILLOW_ROOT is not None else ()
)
pytestmark = pytest.mark.skipif(
    PILLOW_ROOT is None,
    reason="PILLOW_TEST_IMAGES is not set",
)


def _relative(path: Path) -> str:
    assert PILLOW_ROOT is not None
    return path.relative_to(PILLOW_ROOT).as_posix()


def test_pillow_corpus_shape() -> None:
    paths = {_relative(path) for path in PILLOW_PNGS}
    apng_paths = {path for path in paths if path.startswith("apng/")}
    unsupported_paths = apng_paths | UNSUPPORTED_PATHS
    exact_paths = paths - unsupported_paths - CORRUPT_PATHS

    assert len(paths) == EXPECTED_PNG_COUNT
    assert len(apng_paths) == EXPECTED_APNG_COUNT
    assert len(unsupported_paths) == 72
    assert len(exact_paths) == 341
    assert UNSUPPORTED_PATHS <= paths
    assert CORRUPT_PATHS <= paths
    assert not (UNSUPPORTED_PATHS & CORRUPT_PATHS)


@pytest.mark.parametrize("path", PILLOW_PNGS, ids=_relative)
def test_pillow_png(path: Path) -> None:
    relative = _relative(path)
    source = path.read_bytes()

    if relative.startswith("apng/") or relative in UNSUPPORTED_PATHS:
        with pytest.raises(UnsupportedPngError):
            png_to_jxl(source, effort=1)
        return

    if relative in CORRUPT_PATHS:
        with pytest.raises(CorruptPngError):
            png_to_jxl(source, effort=1)
        return

    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    reconstructed = jxl_to_png(archive)
    assert reconstructed == source
    assert sha256(reconstructed).digest() == sha256(source).digest()
