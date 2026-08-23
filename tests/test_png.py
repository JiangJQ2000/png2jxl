from dataclasses import replace

import pytest
from png2jxl import (
    DEFAULT_LIMITS,
    CorruptPngError,
    ResourceLimitError,
    UnsupportedPngError,
)
from png2jxl.png import parse_png

from .helpers import chunk, make_png


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
        ({"color_type": 3}, "indexed"),
        ({"bit_depth": 16}, "8-bit"),
        ({"interlace": 1}, "Adam7"),
    ],
)
def test_unsupported_profiles_are_explicit(kwargs: dict, message: str) -> None:
    with pytest.raises(UnsupportedPngError, match=message):
        parse_png(make_png(**kwargs))


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
