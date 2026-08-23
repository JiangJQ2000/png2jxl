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
    HEADER,
    ReconstructionData,
    parse_reconstruction,
    serialize_reconstruction,
    source_digest,
)

from .helpers import make_png


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


def test_wire_roundtrip_is_deterministic() -> None:
    expected = reconstruction_data()
    first = serialize_reconstruction(expected)
    second = serialize_reconstruction(expected)
    assert first == second
    assert parse_reconstruction(first) == expected


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
