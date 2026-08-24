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
    3: ("P", 1),
    4: ("LA", 2),
    6: ("RGBA", 4),
}
PALETTE_BITMAP_SIZE = 32
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
    palette: bytes | None
    transparency: bytes | None
    ihdr: bytes
    prefix: bytes
    suffix: bytes
    idat_lengths: tuple[int, ...]
    zlib_header: bytes
    raw_deflate: bytes
    adler32: bytes
    expected_filtered_size: int


class PaletteError(ValueError):
    """Palette metadata or carrier samples are inconsistent."""


class AmbiguousPaletteError(PaletteError):
    """Multiple used indices resolve to the same effective color."""


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


def _palette_colors(
    palette: bytes,
    transparency: bytes | None,
) -> tuple[tuple[int, int, int, int], ...]:
    if not 3 <= len(palette) <= 768 or len(palette) % 3:
        raise PaletteError("invalid PLTE length")
    entry_count = len(palette) // 3
    if transparency is not None and not 1 <= len(transparency) <= entry_count:
        raise PaletteError("invalid palette tRNS length")
    return tuple(
        (
            palette[offset],
            palette[offset + 1],
            palette[offset + 2],
            transparency[index]
            if transparency is not None and index < len(transparency)
            else 255,
        )
        for index, offset in enumerate(range(0, len(palette), 3))
    )


def palette_from_prefix(
    prefix: bytes,
    expected_ihdr: bytes,
    *,
    max_chunk_count: int | None = None,
) -> tuple[bytes, bytes | None]:
    """Read and validate palette metadata from an exact pre-IDAT PNG prefix."""
    if not prefix.startswith(PNG_SIGNATURE):
        raise PaletteError("stored PNG prefix has an invalid signature")
    if len(expected_ihdr) != IHDR_STRUCT.size:
        raise PaletteError("stored IHDR must contain exactly 13 bytes")

    offset = len(PNG_SIGNATURE)
    chunk_count = 0
    seen_ihdr = False
    palette: bytes | None = None
    transparency: bytes | None = None
    view = memoryview(prefix)
    while offset < len(prefix):
        if len(prefix) - offset < 12:
            raise PaletteError("stored PNG prefix contains a truncated chunk")
        length = int.from_bytes(prefix[offset : offset + 4], "big")
        if length > 0x7FFFFFFF:
            raise PaletteError("stored PNG prefix chunk length is invalid")
        chunk_type = prefix[offset + 4 : offset + 8]
        if not _is_chunk_type(chunk_type) or 97 <= chunk_type[2] <= 122:
            raise PaletteError("stored PNG prefix chunk type is invalid")
        payload_start = offset + 8
        payload_end = payload_start + length
        chunk_end = payload_end + 4
        if chunk_end > len(prefix):
            raise PaletteError("stored PNG prefix chunk exceeds its boundary")
        payload = view[payload_start:payload_end]
        stored_crc = int.from_bytes(prefix[payload_end:chunk_end], "big")
        calculated_crc = crc32(payload, crc32(chunk_type)) & 0xFFFFFFFF
        if stored_crc != calculated_crc:
            raise PaletteError("stored PNG prefix chunk has an invalid CRC")

        chunk_count += 1
        if max_chunk_count is not None and chunk_count > max_chunk_count:
            raise ResourceLimitError(
                "stored PNG prefix chunk count exceeds its configured limit"
            )
        if chunk_count == 1 and chunk_type != b"IHDR":
            raise PaletteError("stored IHDR is not the first PNG chunk")
        if chunk_type in APNG_CHUNKS:
            raise PaletteError("stored palette PNG contains an APNG marker")
        if chunk_type[0] <= 90 and chunk_type not in KNOWN_CRITICAL_CHUNKS:
            raise PaletteError("stored PNG prefix has an unknown critical chunk")

        if chunk_type == b"IHDR":
            if seen_ihdr or bytes(payload) != expected_ihdr:
                raise PaletteError("stored PNG prefix IHDR is inconsistent")
            seen_ihdr = True
        elif not seen_ihdr:
            raise PaletteError("stored PNG prefix data appears before IHDR")
        elif chunk_type == b"PLTE":
            if palette is not None or transparency is not None:
                raise PaletteError("stored PLTE ordering is invalid")
            palette = bytes(payload)
            _palette_colors(palette, None)
        elif chunk_type == b"tRNS":
            if palette is None or transparency is not None:
                raise PaletteError("stored palette tRNS ordering is invalid")
            transparency = bytes(payload)
            _palette_colors(palette, transparency)
        elif chunk_type in {b"IDAT", b"IEND"}:
            raise PaletteError("stored PNG prefix extends beyond pre-IDAT chunks")
        offset = chunk_end

    if not seen_ihdr or palette is None:
        raise PaletteError("stored palette PNG prefix is incomplete")
    return palette, transparency


def palette_carrier_mode(
    palette: bytes,
    transparency: bytes | None,
    used_bitmap: bytes,
) -> str:
    """Return the minimal lossless JXL carrier mode for used palette entries."""
    colors = _palette_colors(palette, transparency)
    if len(used_bitmap) != PALETTE_BITMAP_SIZE:
        raise PaletteError("palette used-index bitmap must contain 32 bytes")
    used = [
        index
        for index in range(PALETTE_BITMAP_SIZE * 8)
        if used_bitmap[index // 8] & (1 << (index % 8))
    ]
    if not used:
        raise PaletteError("palette used-index bitmap must not be empty")
    if used[-1] >= len(colors):
        raise PaletteError("palette used-index bitmap exceeds PLTE entries")

    seen: set[tuple[int, int, int, int]] = set()
    grayscale = True
    has_alpha = False
    for index in used:
        color = colors[index]
        if color in seen:
            raise AmbiguousPaletteError(
                "used palette indices have duplicate effective colors"
            )
        seen.add(color)
        red, green, blue, alpha = color
        grayscale = grayscale and red == green == blue
        has_alpha = has_alpha or alpha != 255
    if grayscale:
        return "LA" if has_alpha else "L"
    return "RGBA" if has_alpha else "RGB"


def palette_to_carrier(
    indices: bytes,
    palette: bytes,
    transparency: bytes | None,
) -> tuple[str, bytes, bytes]:
    """Expand palette indices and return mode, carrier samples, and used bitmap."""
    colors = _palette_colors(palette, transparency)
    bitmap = bytearray(PALETTE_BITMAP_SIZE)
    for index in indices:
        if index >= len(colors):
            raise PaletteError("palette index exceeds the PLTE entry count")
        bitmap[index // 8] |= 1 << (index % 8)
    used_bitmap = bytes(bitmap)
    mode = palette_carrier_mode(palette, transparency, used_bitmap)

    encoded_entries: list[bytes] = []
    for red, green, blue, alpha in colors:
        if mode == "L":
            encoded_entries.append(bytes((red,)))
        elif mode == "LA":
            encoded_entries.append(bytes((red, alpha)))
        elif mode == "RGB":
            encoded_entries.append(bytes((red, green, blue)))
        else:
            encoded_entries.append(bytes((red, green, blue, alpha)))
    channels = len(encoded_entries[0])
    samples = bytearray(len(indices) * channels)
    offset = 0
    for index in indices:
        samples[offset : offset + channels] = encoded_entries[index]
        offset += channels
    return mode, bytes(samples), used_bitmap


def carrier_to_palette(
    samples: bytes,
    mode: str,
    palette: bytes,
    transparency: bytes | None,
    used_bitmap: bytes,
) -> bytes:
    """Invert verified JXL carrier samples to their exact palette indices."""
    expected_mode = palette_carrier_mode(palette, transparency, used_bitmap)
    if mode != expected_mode:
        raise PaletteError("palette carrier mode is inconsistent")
    colors = _palette_colors(palette, transparency)
    channels = {"L": 1, "LA": 2, "RGB": 3, "RGBA": 4}[mode]
    if len(samples) % channels:
        raise PaletteError("palette carrier sample length is inconsistent")

    reverse: dict[int, int] = {}
    for index, (red, green, blue, alpha) in enumerate(colors):
        if not used_bitmap[index // 8] & (1 << (index % 8)):
            continue
        if mode == "L":
            key = red
        elif mode == "LA":
            key = (red << 8) | alpha
        elif mode == "RGB":
            key = (red << 16) | (green << 8) | blue
        else:
            key = (red << 24) | (green << 16) | (blue << 8) | alpha
        reverse[key] = index

    indices = bytearray(len(samples) // channels)
    observed = bytearray(PALETTE_BITMAP_SIZE)
    sample_offset = 0
    for output_offset in range(len(indices)):
        if mode == "L":
            key = samples[sample_offset]
        elif mode == "LA":
            key = (samples[sample_offset] << 8) | samples[sample_offset + 1]
        elif mode == "RGB":
            key = (
                (samples[sample_offset] << 16)
                | (samples[sample_offset + 1] << 8)
                | samples[sample_offset + 2]
            )
        else:
            key = (
                (samples[sample_offset] << 24)
                | (samples[sample_offset + 1] << 16)
                | (samples[sample_offset + 2] << 8)
                | samples[sample_offset + 3]
            )
        try:
            index = reverse[key]
        except KeyError as error:
            raise PaletteError(
                "palette carrier contains an undeclared color"
            ) from error
        indices[output_offset] = index
        observed[index // 8] |= 1 << (index % 8)
        sample_offset += channels
    if bytes(observed) != used_bitmap:
        raise PaletteError("palette carrier does not use the declared index set")
    return bytes(indices)


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
    seen_trns = False
    seen_idat = False
    idat_ended = False
    seen_iend = False
    first_idat_offset = 0
    final_idat_end = 0
    ihdr = b""
    ihdr_values: tuple[int, ...] | None = None
    palette: bytes | None = None
    transparency: bytes | None = None
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
            if seen_plte or seen_trns or seen_idat:
                raise CorruptPngError("PLTE must occur once and before IDAT")
            if not 3 <= length <= 768 or length % 3:
                raise CorruptPngError("invalid PLTE length")
            assert ihdr_values is not None
            if ihdr_values[3] not in {2, 3, 6}:
                raise CorruptPngError("PLTE is prohibited for this color type")
            seen_plte = True
            if ihdr_values[3] == 3:
                palette = bytes(payload)
        elif chunk_type == b"tRNS" and ihdr_values is not None and ihdr_values[3] == 3:
            if not seen_plte or seen_trns or seen_idat:
                raise CorruptPngError(
                    "palette tRNS must occur once after PLTE and before IDAT"
                )
            assert palette is not None
            if not 1 <= length <= len(palette) // 3:
                raise CorruptPngError("invalid palette tRNS length")
            seen_trns = True
            transparency = bytes(payload)
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
    if color_type == 3 and palette is None:
        raise CorruptPngError("indexed-color PNG requires PLTE before IDAT")
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
        palette=palette,
        transparency=transparency,
        ihdr=ihdr,
        prefix=prefix,
        suffix=suffix,
        idat_lengths=tuple(idat_lengths),
        zlib_header=zlib_header,
        raw_deflate=zlib_stream[2:-4],
        adler32=zlib_stream[-4:],
        expected_filtered_size=expected_filtered_size,
    )
