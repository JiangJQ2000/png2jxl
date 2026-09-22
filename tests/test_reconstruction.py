from dataclasses import replace
from zlib import crc32 as _crc32

import pytest
from png2jxl import (
    DEFAULT_LIMITS,
    CorruptReconstructionError,
    IncompatibleReconstructionError,
    ResourceLimitError,
)
from png2jxl.png import parse_png
from png2jxl.reconstruction import (
    ReconstructionData,
    _pack_filters,
    _restore_prefix,
    _restore_suffix,
    _strip_prefix,
    _strip_suffix,
    _unpack_filters,
    parse_reconstruction,
    serialize_reconstruction,
    source_digest,
)

from .helpers import adam7_filter_count, make_palette_png, make_png


def reconstruction_data() -> ReconstructionData:
    source = make_png(mode="LA", width=2, height=2, filters=b"\x00\x04")
    parsed = parse_png(source)
    return ReconstructionData(
        source_sha256=source_digest(source),
        zlib_header=parsed.zlib_header,
        prefix=parsed.prefix,
        suffix=parsed.suffix,
        idat_lengths=parsed.idat_lengths,
        row_filters=b"\x00\x04",
        ihdr=parsed.ihdr,
        corrections=b"opaque corrections",
    )


def palette_reconstruction_data() -> ReconstructionData:
    source = make_palette_png(
        palette=b"\x00\x00\x00\xff\x00\x00\x00\x00\xff",
        width=2,
        height=2,
        indices=b"\x00\x01\x02\x01",
        filters=b"\x00\x04",
    )
    parsed = parse_png(source)
    return ReconstructionData(
        source_sha256=source_digest(source),
        zlib_header=parsed.zlib_header,
        prefix=parsed.prefix,
        suffix=parsed.suffix,
        idat_lengths=parsed.idat_lengths,
        row_filters=b"\x00\x04",
        ihdr=parsed.ihdr,
        corrections=b"opaque corrections",
        palette_used=b"\x07" + b"\x00" * 31,
    )


def adam7_reconstruction_data() -> ReconstructionData:
    width, height = 9, 7
    filters = bytes(row % 5 for row in range(adam7_filter_count(width, height)))
    source = make_png(
        mode="LA",
        width=width,
        height=height,
        filters=filters,
        interlace=1,
    )
    parsed = parse_png(source)
    return ReconstructionData(
        source_sha256=source_digest(source),
        zlib_header=parsed.zlib_header,
        prefix=parsed.prefix,
        suffix=parsed.suffix,
        idat_lengths=parsed.idat_lengths,
        row_filters=filters,
        ihdr=parsed.ihdr,
        corrections=b"opaque corrections",
    )


def test_wire_roundtrip_is_deterministic() -> None:
    expected = reconstruction_data()
    first = serialize_reconstruction(expected)
    second = serialize_reconstruction(expected)
    assert first == second
    assert parse_reconstruction(first) == expected


def test_adam7_wire_uses_pass_filter_count() -> None:
    expected = adam7_reconstruction_data()
    payload = serialize_reconstruction(expected)
    assert len(expected.row_filters) == 14
    assert parse_reconstruction(payload) == expected


@pytest.mark.parametrize(
    "data",
    [
        replace(adam7_reconstruction_data(), row_filters=b"\x00"),
        replace(adam7_reconstruction_data(), row_filters=b"\x00" * 15),
    ],
)
def test_adam7_layout_metadata_must_be_consistent(data: ReconstructionData) -> None:
    with pytest.raises(CorruptReconstructionError, match="inconsistent"):
        serialize_reconstruction(data)


def test_palette_used_bitmap_is_carried_in_body() -> None:
    expected = palette_reconstruction_data()
    payload = serialize_reconstruction(expected)
    assert payload.endswith(expected.palette_used)
    assert parse_reconstruction(payload) == expected


def test_unknown_wire_major_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    payload[0:2] = b"\x00\x03"
    with pytest.raises(IncompatibleReconstructionError):
        parse_reconstruction(bytes(payload))


def test_unknown_wire_minor_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    payload[2:4] = b"\x00\x01"
    with pytest.raises(IncompatibleReconstructionError):
        parse_reconstruction(bytes(payload))


@pytest.mark.parametrize(
    "data",
    [
        replace(palette_reconstruction_data(), palette_used=b"\x01"),
        replace(
            palette_reconstruction_data(),
            palette_used=b"\x00" * 31 + b"\x80",
        ),
    ],
)
def test_invalid_palette_bitmap_is_rejected(data: ReconstructionData) -> None:
    with pytest.raises(CorruptReconstructionError, match="bitmap"):
        serialize_reconstruction(data)


def test_palette_bitmap_presence_must_match_color_type() -> None:
    palette = palette_reconstruction_data()
    with pytest.raises(CorruptReconstructionError, match="bitmap"):
        serialize_reconstruction(replace(palette, palette_used=b""))
    non_palette = reconstruction_data()
    with pytest.raises(CorruptReconstructionError, match="bitmap"):
        serialize_reconstruction(replace(non_palette, palette_used=b"\x00" * 32))


def test_unknown_preflate_version_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    payload[10:12] = b"\x00\x07"
    with pytest.raises(IncompatibleReconstructionError):
        parse_reconstruction(bytes(payload))


def test_corrupt_zlib_header_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    payload[64] ^= 1
    with pytest.raises(CorruptReconstructionError):
        parse_reconstruction(bytes(payload))


def test_correction_limit_is_enforced() -> None:
    data = reconstruction_data()
    limits = replace(DEFAULT_LIMITS, max_correction_size=2)
    with pytest.raises(ResourceLimitError):
        serialize_reconstruction(data, limits)


def test_palette_prefix_chunk_limit_is_enforced() -> None:
    limits = replace(DEFAULT_LIMITS, max_chunk_count=1)
    with pytest.raises(ResourceLimitError, match="chunk count"):
        serialize_reconstruction(palette_reconstruction_data(), limits)


def test_base5_filter_packing_roundtrips() -> None:
    filters = bytes(range(5))
    packed = _pack_filters(filters)
    assert packed == (2930).to_bytes(2, "big")
    assert _unpack_filters(packed, 5) == filters
    for width in (1, 7, 1000):
        sample = bytes((i * 3) % 5 for i in range(width))
        assert _unpack_filters(_pack_filters(sample), width) == sample


def test_prefix_suffix_framing_is_stripped_and_restored() -> None:
    source = make_png(mode="RGB", width=3, height=3)
    parsed = parse_png(source)
    stripped = _strip_prefix(parsed.prefix)
    assert not stripped.startswith(b"\x89PNG")
    assert _restore_prefix(stripped)[0] == parsed.prefix
    stripped_suffix = _strip_suffix(parsed.suffix)
    assert b"IEND" not in stripped_suffix
    assert _restore_suffix(stripped_suffix) == parsed.suffix


def test_wire_stores_stripped_prefix_without_signature_or_crcs() -> None:
    data = reconstruction_data()
    payload = serialize_reconstruction(data)
    parsed = parse_reconstruction(payload)
    assert parsed.prefix == data.prefix
    assert parsed.suffix == data.suffix
    assert parsed.row_filters == data.row_filters


def test_suffix_strips_all_chunk_crcs_not_just_iend() -> None:
    chunk = b"\x00\x00\x00\x04tEXtdata" + _crc32(b"tEXtdata").to_bytes(4, "big")
    iend = b"\x00\x00\x00\x00IEND" + _crc32(b"IEND").to_bytes(4, "big")
    suffix = chunk + iend
    stripped = _strip_suffix(suffix)
    assert b"IEND" not in stripped
    assert _crc32(b"tEXtdata").to_bytes(4, "big") not in stripped
    assert _restore_suffix(stripped) == suffix


def test_stripped_prefix_requires_ihdr_first_chunk() -> None:
    data = reconstruction_data()
    payload = bytearray(serialize_reconstruction(data))
    from png2jxl.reconstruction import HEADER

    prefix_len = HEADER.unpack_from(payload)[7]
    prefix_start = HEADER.size
    bad = bytes(payload)
    bad = bytearray(bad)
    bad[prefix_start : prefix_start + 4] = (99).to_bytes(4, "big")
    bad[prefix_start + 4 : prefix_start + 8] = b"XUID"
    with pytest.raises(CorruptReconstructionError):
        parse_reconstruction(bytes(bad))
    _ = prefix_len
