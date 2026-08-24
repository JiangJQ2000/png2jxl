from dataclasses import replace

import pytest
from png2jxl import (
    DEFAULT_LIMITS,
    CorruptReconstructionError,
    IncompatibleReconstructionError,
    ResourceLimitError,
)
from png2jxl.png import parse_png
from png2jxl.reconstruction import (
    FLAG_PALETTE_USED_BITMAP,
    HEADER,
    ReconstructionData,
    parse_reconstruction,
    serialize_reconstruction,
    source_digest,
)

from .helpers import make_palette_png, make_png


def reconstruction_data() -> ReconstructionData:
    source = make_png(mode="LA", width=2, height=2, filters=b"\x00\x04")
    parsed = parse_png(source)
    return ReconstructionData(
        source_length=len(source),
        source_sha256=source_digest(source),
        ihdr=parsed.ihdr,
        filtered_length=parsed.expected_filtered_size,
        zlib_header=parsed.zlib_header,
        adler32=parsed.adler32,
        prefix=parsed.prefix,
        suffix=parsed.suffix,
        idat_lengths=parsed.idat_lengths,
        row_filters=b"\x00\x04",
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
        source_length=len(source),
        source_sha256=source_digest(source),
        ihdr=parsed.ihdr,
        filtered_length=parsed.expected_filtered_size,
        zlib_header=parsed.zlib_header,
        adler32=parsed.adler32,
        prefix=parsed.prefix,
        suffix=parsed.suffix,
        idat_lengths=parsed.idat_lengths,
        row_filters=b"\x00\x04",
        corrections=b"opaque corrections",
        palette_used=b"\x07" + b"\x00" * 31,
    )


def test_wire_roundtrip_is_deterministic() -> None:
    expected = reconstruction_data()
    first = serialize_reconstruction(expected)
    second = serialize_reconstruction(expected)
    assert first == second
    assert parse_reconstruction(first) == expected


def test_palette_wire_uses_flagged_v1_body() -> None:
    expected = palette_reconstruction_data()
    payload = serialize_reconstruction(expected)
    fields = HEADER.unpack_from(payload)
    assert fields[1:4] == (1, 0, FLAG_PALETTE_USED_BITMAP)
    assert payload.endswith(expected.palette_used)
    assert parse_reconstruction(payload) == expected


def test_unknown_wire_major_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    payload[8:10] = b"\x00\x02"
    with pytest.raises(IncompatibleReconstructionError):
        parse_reconstruction(bytes(payload))


def test_unknown_wire_minor_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    fields = list(HEADER.unpack_from(payload))
    fields[2] = 1
    payload[: HEADER.size] = HEADER.pack(*fields)
    with pytest.raises(IncompatibleReconstructionError):
        parse_reconstruction(bytes(payload))


def test_unknown_wire_flag_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    fields = list(HEADER.unpack_from(payload))
    fields[3] = 1 << 31
    payload[: HEADER.size] = HEADER.pack(*fields)
    with pytest.raises(IncompatibleReconstructionError, match="flags"):
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


def test_palette_flag_and_ihdr_must_agree() -> None:
    payload = bytearray(serialize_reconstruction(palette_reconstruction_data()))
    fields = list(HEADER.unpack_from(payload))
    fields[3] = 0
    payload[: HEADER.size] = HEADER.pack(*fields)
    with pytest.raises(IncompatibleReconstructionError, match="flag"):
        parse_reconstruction(bytes(payload))


def test_unknown_preflate_version_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    fields = list(HEADER.unpack_from(payload))
    fields[11] = 7
    payload[: HEADER.size] = HEADER.pack(*fields)
    with pytest.raises(IncompatibleReconstructionError):
        parse_reconstruction(bytes(payload))


def test_payload_length_corruption_is_rejected() -> None:
    payload = bytearray(serialize_reconstruction(reconstruction_data()))
    payload[23] ^= 1
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
