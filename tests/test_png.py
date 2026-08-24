from dataclasses import replace

import pytest
from png2jxl import (
    DEFAULT_LIMITS,
    CorruptPngError,
    ResourceLimitError,
    UnsupportedPngError,
)
from png2jxl.png import parse_png

from .helpers import chunk, make_palette_png, make_png


def test_parse_preserves_png_structure() -> None:
    source = make_png(
        mode="RGB",
        idat_splits=[0, 1, 0, 2],
        before_idat=[(b"tEXt", b"key\x00value"), (b"PLTE", b"\x00\x00\x00")],
        after_idat=[(b"tEXt", b"after\x00idat")],
    )
    parsed = parse_png(source)
    assert parsed.mode == "RGB"
    assert parsed.idat_lengths[:4] == (0, 1, 0, 2)
    assert parsed.prefix.endswith(chunk(b"PLTE", b"\x00\x00\x00"))
    assert parsed.suffix.startswith(chunk(b"tEXt", b"after\x00idat"))
    assert sum(parsed.idat_lengths) == len(parsed.raw_deflate) + 6


def test_crc_corruption_is_rejected() -> None:
    source = bytearray(make_png())
    source[-1] ^= 1
    with pytest.raises(CorruptPngError, match="CRC"):
        parse_png(bytes(source))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mode": "L", "color_type": 3, "bit_depth": 4}, "8-bit"),
        ({"bit_depth": 16}, "8-bit"),
    ],
)
def test_unsupported_profiles_are_explicit(kwargs: dict, message: str) -> None:
    with pytest.raises(UnsupportedPngError, match=message):
        parse_png(make_png(**kwargs))


def test_invalid_interlace_method_is_corrupt() -> None:
    with pytest.raises(CorruptPngError, match="interlace"):
        parse_png(make_png(interlace=2))


def test_parse_adam7_profile_uses_pass_scanline_size() -> None:
    parsed = parse_png(
        make_png(
            mode="RGB",
            width=9,
            height=7,
            interlace=1,
            idat_splits=[0, 1, 0, 2],
        )
    )
    assert parsed.interlace_method == 1
    assert parsed.expected_filtered_size == 9 * 7 * 3 + 14


def test_apng_marker_is_rejected() -> None:
    source = make_png(before_idat=[(b"acTL", b"\x00" * 8)])
    with pytest.raises(UnsupportedPngError, match="APNG"):
        parse_png(source)


def test_nonconsecutive_idat_is_rejected() -> None:
    source = make_png(after_idat=[(b"tEXt", b"gap\x00chunk"), (b"IDAT", b"")])
    with pytest.raises(CorruptPngError, match="consecutive"):
        parse_png(source)


def test_dimension_limit_is_enforced() -> None:
    limits = replace(DEFAULT_LIMITS, max_width=2)
    with pytest.raises(ResourceLimitError):
        parse_png(make_png(width=3), limits)


def test_trailing_bytes_are_rejected() -> None:
    with pytest.raises(CorruptPngError, match="trailing"):
        parse_png(make_png() + b"extra")


def test_parse_indexed_color_preserves_palette_metadata() -> None:
    palette = b"\x00\x00\x00\x80\x80\x80\xff\xff\xff"
    transparency = b"\xff\x80"
    source = make_palette_png(
        palette=palette,
        transparency=transparency,
        filters=b"\x00\x04",
        idat_splits=[0, 1, 0, 2],
    )
    parsed = parse_png(source)
    assert parsed.mode == "P"
    assert parsed.bytes_per_pixel == 1
    assert parsed.palette == palette
    assert parsed.transparency == transparency
    assert parsed.expected_filtered_size == (parsed.width + 1) * parsed.height


def test_indexed_color_requires_plte() -> None:
    source = make_png(mode="L", color_type=3)
    with pytest.raises(CorruptPngError, match="requires PLTE"):
        parse_png(source)


@pytest.mark.parametrize(
    "chunks",
    [
        [(b"PLTE", b"\x00\x00")],
        [(b"PLTE", b"\x00\x00\x00"), (b"PLTE", b"\xff\xff\xff")],
        [(b"tRNS", b"\xff"), (b"PLTE", b"\x00\x00\x00")],
        [(b"PLTE", b"\x00\x00\x00"), (b"tRNS", b"")],
        [(b"PLTE", b"\x00\x00\x00"), (b"tRNS", b"\xff\xff")],
        [
            (b"PLTE", b"\x00\x00\x00"),
            (b"tRNS", b"\xff"),
            (b"tRNS", b"\xff"),
        ],
    ],
)
def test_invalid_palette_chunks_are_rejected(
    chunks: list[tuple[bytes, bytes]],
) -> None:
    source = make_png(mode="L", color_type=3, before_idat=chunks)
    with pytest.raises(CorruptPngError):
        parse_png(source)


def test_palette_trns_after_idat_is_rejected() -> None:
    source = make_palette_png(
        palette=b"\x00\x00\x00",
        after_idat=[(b"tRNS", b"\xff")],
    )
    with pytest.raises(CorruptPngError, match="tRNS"):
        parse_png(source)
