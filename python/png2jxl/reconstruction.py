"""Persistent pngr reconstruction payload format."""

from dataclasses import dataclass
from hashlib import sha256
from struct import Struct, pack
from zlib import crc32

from .exceptions import (
    CorruptReconstructionError,
    IncompatibleReconstructionError,
    ResourceLimitError,
)
from .limits import DEFAULT_LIMITS, ResourceLimits
from .png import (
    COLOR_INFO,
    IHDR_STRUCT,
    PALETTE_BITMAP_SIZE,
    PNG_SIGNATURE,
    PaletteError,
    palette_carrier_mode,
    parse_prefix,
)
from .png_filter import scanline_info

WIRE_MAJOR = 2
WIRE_MINOR = 0
PREFLATE_CODEC_ID = 1
PREFLATE_VERSION = (0, 7, 6)

HEADER = Struct(">HHHHHH32sQQI2s")


@dataclass(frozen=True, slots=True)
class ReconstructionData:
    source_sha256: bytes
    zlib_header: bytes
    prefix: bytes
    suffix: bytes
    idat_lengths: tuple[int, ...]
    row_filters: bytes
    ihdr: bytes
    corrections: bytes
    palette_used: bytes = b""

    @property
    def dimensions(self) -> tuple[int, int]:
        width, height, *_ = IHDR_STRUCT.unpack(self.ihdr)
        return width, height

    @property
    def color_type(self) -> int:
        return IHDR_STRUCT.unpack(self.ihdr)[3]

    @property
    def interlace_method(self) -> int:
        return IHDR_STRUCT.unpack(self.ihdr)[-1]

    @property
    def mode(self) -> str:
        if self.color_type == 3:
            palette, transparency = parse_prefix(self.prefix)[1:]
            return palette_carrier_mode(
                palette,
                transparency,
                self.palette_used,
            )
        return COLOR_INFO[self.color_type][0]

    @property
    def bytes_per_pixel(self) -> int:
        """Return the source PNG bytes per pixel used by its row filters."""
        return COLOR_INFO[self.color_type][1]

    @property
    def filtered_length(self) -> int:
        """Derive the expected filtered scanline plaintext length from IHDR."""
        length, _ = scanline_info(
            *self.dimensions,
            self.bytes_per_pixel,
            self.interlace_method,
        )
        return length

    @property
    def expected_source_length(self) -> int:
        return (
            len(self.prefix)
            + len(self.suffix)
            + sum(self.idat_lengths)
            + 12 * len(self.idat_lengths)
        )


def source_digest(source: bytes) -> bytes:
    return sha256(source).digest()


IEND_CHUNK = b"\x00\x00\x00\x00IEND" + crc32(b"IEND").to_bytes(4, "big")


def _strip_chunks(full_chunks: bytes, *, drop_iend: bool) -> bytes:
    """Store PNG chunks without their CRCs, optionally dropping IEND.

    `full_chunks` must begin at the first chunk (no PNG signature). Each chunk
    is kept as just its length, type, and payload.
    """
    out = bytearray()
    offset = 0
    total = len(full_chunks)
    while offset < total:
        if total - offset < 12:
            raise CorruptReconstructionError("stored chunk is truncated")
        length = int.from_bytes(full_chunks[offset : offset + 4], "big")
        chunk_type = full_chunks[offset + 4 : offset + 8]
        crc_end = offset + 8 + length + 4
        if crc_end > total:
            raise CorruptReconstructionError("stored chunk is truncated")
        if not (drop_iend and chunk_type == b"IEND"):
            out += full_chunks[offset : offset + 8 + length]
        offset = crc_end
    return bytes(out)


def _restore_chunks(stripped: bytes, *, append_iend: bool) -> bytes:
    """Recompute each chunk's CRC and optionally append a fresh IEND chunk."""
    out = bytearray()
    offset = 0
    total = len(stripped)
    while offset < total:
        if total - offset < 8:
            raise CorruptReconstructionError("stored chunk is truncated")
        length = int.from_bytes(stripped[offset : offset + 4], "big")
        chunk_type = stripped[offset + 4 : offset + 8]
        payload = stripped[offset + 8 : offset + 8 + length]
        crc = crc32(payload, crc32(chunk_type)) & 0xFFFFFFFF
        out += stripped[offset : offset + 8]
        out += payload
        out += crc.to_bytes(4, "big")
        offset += 8 + length
    if append_iend:
        out += IEND_CHUNK
    return bytes(out)


def _strip_prefix(full_prefix: bytes) -> bytes:
    """Drop the PNG signature and every chunk CRC from a pre-IDAT prefix."""
    if not full_prefix.startswith(PNG_SIGNATURE):
        raise CorruptReconstructionError("stored prefix is missing the PNG signature")
    return _strip_chunks(full_prefix[len(PNG_SIGNATURE) :], drop_iend=False)


def _restore_prefix(stripped_prefix: bytes) -> tuple[bytes, bytes]:
    """Rebuild a full pre-IDAT prefix and return ``(full_prefix, ihdr)``.

    The stored prefix must begin with the IHDR chunk (length field 13, type
    ``IHDR``); its 13-byte payload is the reconstruction IHDR.
    """
    if len(stripped_prefix) < 12:
        raise CorruptReconstructionError("stored prefix is truncated")
    if int.from_bytes(stripped_prefix[0:4], "big") != IHDR_STRUCT.size:
        raise CorruptReconstructionError("stored prefix IHDR length is invalid")
    if stripped_prefix[4:8] != b"IHDR":
        raise CorruptReconstructionError("stored prefix first chunk is not IHDR")
    ihdr = stripped_prefix[8 : 8 + IHDR_STRUCT.size]
    return (
        PNG_SIGNATURE + _restore_chunks(stripped_prefix, append_iend=False),
        ihdr,
    )


def _strip_suffix(full_suffix: bytes) -> bytes:
    """Drop every chunk CRC and the IEND chunk from a post-IDAT suffix."""
    return _strip_chunks(full_suffix, drop_iend=True)


def _restore_suffix(stripped_suffix: bytes) -> bytes:
    """Rebuild a full post-IDAT suffix, appending a freshly computed IEND chunk."""
    return _restore_chunks(stripped_suffix, append_iend=True)


def _packed_filter_length(filter_count: int) -> int:
    if filter_count == 0:
        return 0
    return ((5**filter_count - 1).bit_length() + 7) // 8


def _pack_filters(filters: bytes) -> bytes:
    value = 0
    for byte in reversed(filters):
        value = value * 5 + byte
    length = _packed_filter_length(len(filters))
    return value.to_bytes(length, "big")


def _unpack_filters(packed: bytes, filter_count: int) -> bytes:
    expected = _packed_filter_length(filter_count)
    if len(packed) != expected:
        raise CorruptReconstructionError("row-filter packing length is inconsistent")
    if filter_count == 0:
        return b""
    value = int.from_bytes(packed, "big")
    out = bytearray()
    for _ in range(filter_count):
        out.append(value % 5)
        value //= 5
    if value != 0:
        raise CorruptReconstructionError("row-filter packing overflows its length")
    return bytes(out)


def _validate_ihdr(
    ihdr: bytes,
    limits: ResourceLimits,
) -> tuple[int, int, int]:
    if len(ihdr) != IHDR_STRUCT.size:
        raise CorruptReconstructionError("stored IHDR must contain 13 bytes")
    width, height, bit_depth, color_type, compression, filter_method, interlace = (
        IHDR_STRUCT.unpack(ihdr)
    )
    if width == 0 or height == 0:
        raise CorruptReconstructionError("stored dimensions must be non-zero")
    if width > limits.max_width or height > limits.max_height:
        raise ResourceLimitError("stored dimensions exceed configured limits")
    if width * height > limits.max_pixels:
        raise ResourceLimitError("stored pixel count exceeds its configured limit")
    if (
        bit_depth != 8
        or color_type not in COLOR_INFO
        or compression != 0
        or filter_method != 0
        or interlace not in {0, 1}
    ):
        raise IncompatibleReconstructionError("stored PNG profile is not supported")

    filtered_length, filter_count = scanline_info(
        width,
        height,
        COLOR_INFO[color_type][1],
        interlace,
    )
    limits.ensure(filtered_length, limits.max_filtered_size, "filtered plaintext")
    return color_type, filtered_length, filter_count


def _validate_common(data: ReconstructionData, limits: ResourceLimits) -> None:
    if len(data.source_sha256) != 32:
        raise CorruptReconstructionError("source SHA-256 must contain 32 bytes")
    if len(data.zlib_header) != 2:
        raise CorruptReconstructionError("invalid stored zlib wrapper")
    cmf, zlib_flags = data.zlib_header
    if cmf & 0x0F != 8 or cmf >> 4 > 7:
        raise CorruptReconstructionError("stored zlib method is invalid")
    if ((cmf << 8) | zlib_flags) % 31:
        raise CorruptReconstructionError("stored zlib header checksum is invalid")
    if zlib_flags & 0x20:
        raise IncompatibleReconstructionError(
            "stored zlib preset dictionary is unsupported"
        )
    if not data.idat_lengths:
        raise CorruptReconstructionError("reconstruction contains no IDAT lengths")
    if len(data.idat_lengths) > limits.max_idat_count:
        raise ResourceLimitError("stored IDAT count exceeds its configured limit")
    if any(length < 0 or length > 0x7FFFFFFF for length in data.idat_lengths):
        raise CorruptReconstructionError("stored IDAT length is invalid")
    if any(filter_type > 4 for filter_type in data.row_filters):
        raise CorruptReconstructionError("stored PNG filter type is invalid")

    color_type, expected_filtered, expected_filter_count = _validate_ihdr(
        data.ihdr,
        limits,
    )
    if len(data.row_filters) != expected_filter_count:
        raise CorruptReconstructionError("stored row-filter count is inconsistent")
    if data.filtered_length != expected_filtered:
        raise CorruptReconstructionError("stored filtered length is inconsistent")
    limits.ensure(
        data.expected_source_length,
        limits.max_reconstructed_size,
        "source PNG",
    )
    limits.ensure(len(data.prefix), limits.max_prefix_size, "PNG prefix")
    limits.ensure(len(data.suffix), limits.max_suffix_size, "PNG suffix")
    limits.ensure(
        len(data.corrections),
        limits.max_correction_size,
        "preflate corrections",
    )
    if color_type == 3:
        if len(data.palette_used) != PALETTE_BITMAP_SIZE:
            raise CorruptReconstructionError(
                "palette reconstruction requires a 32-byte used-index bitmap"
            )
        try:
            palette, transparency = parse_prefix(data.prefix, limits)[1:]
            palette_carrier_mode(palette, transparency, data.palette_used)
        except PaletteError as error:
            raise CorruptReconstructionError(str(error)) from error
    elif data.palette_used:
        raise CorruptReconstructionError(
            "non-palette reconstruction contains a used-index bitmap"
        )

    zlib_length = sum(data.idat_lengths)
    if zlib_length < 6:
        raise CorruptReconstructionError("stored IDAT run is too short")


def serialize_reconstruction(
    data: ReconstructionData,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> bytes:
    _validate_common(data, limits)
    stripped_prefix = _strip_prefix(data.prefix)
    stripped_suffix = _strip_suffix(data.suffix)
    packed_filters = _pack_filters(data.row_filters)
    idat_table = b"".join(pack(">I", length) for length in data.idat_lengths)
    body = b"".join(
        (
            stripped_prefix,
            stripped_suffix,
            idat_table,
            packed_filters,
            data.corrections,
            data.palette_used,
        )
    )
    payload_size = HEADER.size + len(body)
    limits.ensure(payload_size, limits.max_payload_size, "reconstruction payload")

    header = HEADER.pack(
        WIRE_MAJOR,
        WIRE_MINOR,
        PREFLATE_CODEC_ID,
        *PREFLATE_VERSION,
        data.source_sha256,
        len(stripped_prefix),
        len(stripped_suffix),
        len(data.idat_lengths),
        data.zlib_header,
    )
    return header + body


def parse_reconstruction(
    payload: bytes,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> ReconstructionData:
    limits.ensure(len(payload), limits.max_payload_size, "reconstruction payload")
    if len(payload) < HEADER.size:
        raise CorruptReconstructionError("truncated reconstruction header")

    (
        major,
        minor,
        codec_id,
        codec_major,
        codec_minor,
        codec_patch,
        source_sha256,
        prefix_length,
        suffix_length,
        idat_count,
        zlib_header,
    ) = HEADER.unpack_from(payload)

    if major != WIRE_MAJOR:
        raise IncompatibleReconstructionError(
            f"unsupported reconstruction wire version {major}.{minor}"
        )
    if minor > WIRE_MINOR:
        raise IncompatibleReconstructionError(
            f"unsupported reconstruction wire version {major}.{minor}"
        )
    if len(source_sha256) != 32:
        raise CorruptReconstructionError("source SHA-256 must contain 32 bytes")
    if (
        codec_id != PREFLATE_CODEC_ID
        or (codec_major, codec_minor, codec_patch) != PREFLATE_VERSION
    ):
        raise IncompatibleReconstructionError("incompatible preflate codec version")
    if idat_count == 0:
        raise CorruptReconstructionError("reconstruction contains no IDAT lengths")
    if idat_count > limits.max_idat_count:
        raise ResourceLimitError("stored IDAT count exceeds its configured limit")
    if len(zlib_header) != 2:
        raise CorruptReconstructionError("invalid stored zlib wrapper")
    cmf, zlib_flags = zlib_header
    if cmf & 0x0F != 8 or cmf >> 4 > 7:
        raise CorruptReconstructionError("stored zlib method is invalid")
    if ((cmf << 8) | zlib_flags) % 31:
        raise CorruptReconstructionError("stored zlib header checksum is invalid")
    if zlib_flags & 0x20:
        raise IncompatibleReconstructionError(
            "stored zlib preset dictionary is unsupported"
        )
    limits.ensure(prefix_length, limits.max_prefix_size, "PNG prefix")
    limits.ensure(suffix_length, limits.max_suffix_size, "PNG suffix")

    view = memoryview(payload)
    offset = HEADER.size
    stripped_prefix = bytes(view[offset : offset + prefix_length])
    offset += prefix_length
    stripped_suffix = bytes(view[offset : offset + suffix_length])
    offset += suffix_length
    idat_table_length = idat_count * 4
    idat_lengths = tuple(
        int.from_bytes(view[position : position + 4], "big")
        for position in range(offset, offset + idat_table_length, 4)
    )
    offset += idat_table_length

    prefix, ihdr = _restore_prefix(stripped_prefix)
    suffix = _restore_suffix(stripped_suffix)
    color_type, _expected_filtered, expected_filter_count = _validate_ihdr(ihdr, limits)
    if any(length < 0 or length > 0x7FFFFFFF for length in idat_lengths):
        raise CorruptReconstructionError("stored IDAT length is invalid")
    palette_used_length = PALETTE_BITMAP_SIZE if color_type == 3 else 0

    packed_filter_length = _packed_filter_length(expected_filter_count)
    packed_filters = bytes(view[offset : offset + packed_filter_length])
    offset += packed_filter_length
    row_filters = _unpack_filters(packed_filters, expected_filter_count)

    remaining = len(payload) - offset
    if remaining < palette_used_length:
        raise CorruptReconstructionError("reconstruction sections overflow payload")
    corrections = bytes(view[offset : offset + remaining - palette_used_length])
    offset += remaining - palette_used_length
    palette_used = bytes(view[offset : offset + palette_used_length])
    offset += palette_used_length
    if offset != len(payload):
        raise CorruptReconstructionError(
            "reconstruction section lengths are inconsistent"
        )

    data = ReconstructionData(
        source_sha256=source_sha256,
        zlib_header=zlib_header,
        prefix=prefix,
        suffix=suffix,
        idat_lengths=idat_lengths,
        row_filters=row_filters,
        ihdr=ihdr,
        corrections=corrections,
        palette_used=palette_used,
    )
    _validate_common(data, limits)
    return data
