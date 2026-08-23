"""Strict parsing for the supported byte-exact PNG profile."""

from dataclasses import dataclass
from struct import Struct
from zlib import crc32

from .exceptions import (
    CorruptPngError,
    NotPngError,
    ResourceLimitError,
    UnsupportedPngError,
)
from .limits import DEFAULT_LIMITS, ResourceLimits

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IHDR_STRUCT = Struct(">IIBBBBB")
APNG_CHUNKS = {b"acTL", b"fcTL", b"fdAT"}
KNOWN_CRITICAL_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND"}
COLOR_INFO = {
    0: ("L", 1),
    2: ("RGB", 3),
    4: ("LA", 2),
    6: ("RGBA", 4),
}
LEGAL_DEPTHS = {
    0: {1, 2, 4, 8, 16},
    2: {8, 16},
    3: {1, 2, 4, 8},
    4: {8, 16},
    6: {8, 16},
}


@dataclass(frozen=True, slots=True)
class ParsedPng:
    width: int
    height: int
    bit_depth: int
    color_type: int
    compression_method: int
    filter_method: int
    interlace_method: int
    mode: str
    bytes_per_pixel: int
    ihdr: bytes
    prefix: bytes
    suffix: bytes
    idat_lengths: tuple[int, ...]
    zlib_header: bytes
    raw_deflate: bytes
    adler32: bytes
    expected_filtered_size: int


def _is_chunk_type(chunk_type: bytes) -> bool:
    return len(chunk_type) == 4 and all(
        65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type
    )


def _parse_ihdr(payload: bytes, limits: ResourceLimits) -> tuple[int, ...]:
    if len(payload) != IHDR_STRUCT.size:
        raise CorruptPngError("IHDR must contain exactly 13 bytes")
    values = IHDR_STRUCT.unpack(payload)
    width, height, bit_depth, color_type, compression, filter_method, interlace = values
    if width == 0 or height == 0:
        raise CorruptPngError("PNG dimensions must be non-zero")
    if width > limits.max_width or height > limits.max_height:
        raise ResourceLimitError("PNG dimensions exceed configured limits")
    if width * height > limits.max_pixels:
        raise ResourceLimitError("PNG pixel count exceeds its configured limit")
    if color_type not in LEGAL_DEPTHS:
        raise CorruptPngError(f"invalid PNG color type {color_type}")
    if bit_depth not in LEGAL_DEPTHS[color_type]:
        raise CorruptPngError(
            f"bit depth {bit_depth} is invalid for color type {color_type}"
        )
    if color_type == 3:
        raise UnsupportedPngError("indexed-color PNG is not supported")
    if bit_depth != 8:
        raise UnsupportedPngError("only 8-bit PNG samples are supported")
    if compression != 0:
        raise UnsupportedPngError("unsupported PNG compression method")
    if filter_method != 0:
        raise UnsupportedPngError("unsupported PNG filter method")
    if interlace not in {0, 1}:
        raise CorruptPngError("invalid PNG interlace method")
    if interlace != 0:
        raise UnsupportedPngError("Adam7 interlacing is not supported")
    return values


def parse_png(
    data: bytes,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> ParsedPng:
    if not isinstance(data, bytes):
        raise TypeError("PNG input must be bytes")
    limits.ensure(len(data), limits.max_source_png_size, "source PNG")
    if not data.startswith(PNG_SIGNATURE):
        raise NotPngError("input does not start with the PNG signature")

    offset = len(PNG_SIGNATURE)
    chunk_count = 0
    seen_ihdr = False
    seen_plte = False
    seen_idat = False
    idat_ended = False
    seen_iend = False
    first_idat_offset = 0
    final_idat_end = 0
    ihdr = b""
    ihdr_values: tuple[int, ...] | None = None
    view = memoryview(data)
    idat_payloads: list[memoryview] = []
    idat_lengths: list[int] = []

    while offset < len(data):
        if len(data) - offset < 12:
            raise CorruptPngError("truncated PNG chunk")
        chunk_offset = offset
        length = int.from_bytes(data[offset : offset + 4], "big")
        if length > 0x7FFFFFFF:
            raise CorruptPngError("PNG chunk length exceeds the format limit")
        chunk_type = data[offset + 4 : offset + 8]
        if not _is_chunk_type(chunk_type) or 97 <= chunk_type[2] <= 122:
            raise CorruptPngError("invalid PNG chunk type")

        payload_start = offset + 8
        payload_end = payload_start + length
        chunk_end = payload_end + 4
        if chunk_end > len(data):
            raise CorruptPngError("PNG chunk extends beyond the input")
        payload = view[payload_start:payload_end]
        stored_crc = int.from_bytes(data[payload_end:chunk_end], "big")
        calculated_crc = crc32(payload, crc32(chunk_type)) & 0xFFFFFFFF
        if stored_crc != calculated_crc:
            raise CorruptPngError(
                f"CRC mismatch in {chunk_type.decode('ascii', 'replace')} chunk"
            )

        chunk_count += 1
        if chunk_count > limits.max_chunk_count:
            raise ResourceLimitError("PNG chunk count exceeds its configured limit")
        if chunk_count == 1 and chunk_type != b"IHDR":
            raise CorruptPngError("IHDR must be the first PNG chunk")
        if chunk_type in APNG_CHUNKS:
            raise UnsupportedPngError("APNG is not supported")
        if chunk_type[0] <= 90 and chunk_type not in KNOWN_CRITICAL_CHUNKS:
            raise UnsupportedPngError(
                f"unknown critical PNG chunk {chunk_type.decode('ascii')}"
            )

        if chunk_type == b"IHDR":
            if seen_ihdr:
                raise CorruptPngError("PNG contains multiple IHDR chunks")
            ihdr = bytes(payload)
            ihdr_values = _parse_ihdr(ihdr, limits)
            seen_ihdr = True
        elif not seen_ihdr:
            raise CorruptPngError("PNG data appears before IHDR")
        elif chunk_type == b"PLTE":
            if seen_plte or seen_idat:
                raise CorruptPngError("PLTE must occur once and before IDAT")
            if not 3 <= length <= 768 or length % 3:
                raise CorruptPngError("invalid PLTE length")
            assert ihdr_values is not None
            if ihdr_values[3] not in {2, 6}:
                raise CorruptPngError("PLTE is prohibited for this color type")
            seen_plte = True
        elif chunk_type == b"IDAT":
            if idat_ended:
                raise CorruptPngError("IDAT chunks must be consecutive")
            if not seen_idat:
                first_idat_offset = chunk_offset
            seen_idat = True
            if len(idat_lengths) >= limits.max_idat_count:
                raise ResourceLimitError("IDAT count exceeds its configured limit")
            idat_lengths.append(length)
            idat_payloads.append(payload)
            final_idat_end = chunk_end
        elif chunk_type == b"IEND":
            if length != 0:
                raise CorruptPngError("IEND must be empty")
            if not seen_idat:
                raise CorruptPngError("PNG contains no IDAT chunks")
            if chunk_end != len(data):
                raise CorruptPngError("trailing bytes after IEND are not supported")
            seen_iend = True
        elif seen_idat:
            idat_ended = True

        offset = chunk_end
        if seen_iend:
            break

    if not seen_iend or ihdr_values is None:
        raise CorruptPngError("PNG is missing IEND")

    width, height, bit_depth, color_type, compression, filter_method, interlace = (
        ihdr_values
    )
    mode, bytes_per_pixel = COLOR_INFO[color_type]
    expected_filtered_size = (width * bytes_per_pixel + 1) * height
    limits.ensure(
        expected_filtered_size,
        limits.max_filtered_size,
        "filtered PNG plaintext",
    )

    zlib_stream = b"".join(idat_payloads)
    if len(zlib_stream) < 6:
        raise CorruptPngError("IDAT data is too short for a zlib stream")
    zlib_header = zlib_stream[:2]
    cmf, flags = zlib_header
    if cmf & 0x0F != 8 or cmf >> 4 > 7:
        raise CorruptPngError("IDAT uses an invalid zlib compression method")
    if ((cmf << 8) | flags) % 31:
        raise CorruptPngError("IDAT has an invalid zlib header checksum")
    if flags & 0x20:
        raise UnsupportedPngError("zlib preset dictionaries are not supported")

    prefix = data[:first_idat_offset]
    suffix = data[final_idat_end:]
    limits.ensure(len(prefix), limits.max_prefix_size, "PNG prefix")
    limits.ensure(len(suffix), limits.max_suffix_size, "PNG suffix")

    return ParsedPng(
        width=width,
        height=height,
        bit_depth=bit_depth,
        color_type=color_type,
        compression_method=compression,
        filter_method=filter_method,
        interlace_method=interlace,
        mode=mode,
        bytes_per_pixel=bytes_per_pixel,
        ihdr=ihdr,
        prefix=prefix,
        suffix=suffix,
        idat_lengths=tuple(idat_lengths),
        zlib_header=zlib_header,
        raw_deflate=zlib_stream[2:-4],
        adler32=zlib_stream[-4:],
        expected_filtered_size=expected_filtered_size,
    )
