"""Persistent pngr reconstruction payload format."""

from dataclasses import dataclass
from hashlib import sha256
from struct import Struct, pack

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
    PaletteError,
    palette_carrier_mode,
    palette_from_prefix,
)
from .png_filter import scanline_info

MAGIC = b"\x89PNGR\r\n\x1a"
WIRE_MAJOR = 1
WIRE_MINOR = 0
FLAGS = 0
FLAG_PALETTE_USED_BITMAP = 1 << 0
KNOWN_FLAGS = FLAG_PALETTE_USED_BITMAP
PREFLATE_CODEC_ID = 1
PREFLATE_VERSION = (0, 7, 6)

HEADER = Struct(">8sHHIQQ32s32sHHHH13sQ2s4sQQQII")


@dataclass(frozen=True, slots=True)
class ReconstructionData:
    source_length: int
    source_sha256: bytes
    ihdr: bytes
    filtered_length: int
    zlib_header: bytes
    adler32: bytes
    prefix: bytes
    suffix: bytes
    idat_lengths: tuple[int, ...]
    row_filters: bytes
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
            palette, transparency = palette_from_prefix(self.prefix, self.ihdr)
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


def source_digest(source: bytes) -> bytes:
    return sha256(source).digest()


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
    if len(data.zlib_header) != 2 or len(data.adler32) != 4:
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
    limits.ensure(data.source_length, limits.max_reconstructed_size, "source PNG")
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
            palette, transparency = palette_from_prefix(
                data.prefix,
                data.ihdr,
                max_chunk_count=limits.max_chunk_count,
            )
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
    expected_source_length = (
        len(data.prefix) + len(data.suffix) + zlib_length + 12 * len(data.idat_lengths)
    )
    if data.source_length != expected_source_length:
        raise CorruptReconstructionError("stored source length is inconsistent")


def serialize_reconstruction(
    data: ReconstructionData,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> bytes:
    _validate_common(data, limits)
    idat_table = b"".join(pack(">I", length) for length in data.idat_lengths)
    body = b"".join(
        (
            data.prefix,
            data.suffix,
            idat_table,
            data.row_filters,
            data.corrections,
            data.palette_used,
        )
    )
    payload_size = HEADER.size + len(body)
    limits.ensure(payload_size, limits.max_payload_size, "reconstruction payload")

    header = HEADER.pack(
        MAGIC,
        WIRE_MAJOR,
        WIRE_MINOR,
        FLAG_PALETTE_USED_BITMAP if data.color_type == 3 else FLAGS,
        payload_size,
        data.source_length,
        data.source_sha256,
        sha256(body).digest(),
        PREFLATE_CODEC_ID,
        *PREFLATE_VERSION,
        data.ihdr,
        data.filtered_length,
        data.zlib_header,
        data.adler32,
        len(data.prefix),
        len(data.suffix),
        len(data.corrections),
        len(data.idat_lengths),
        len(data.row_filters),
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
        magic,
        major,
        minor,
        flags,
        payload_size,
        source_length,
        source_sha256,
        body_sha256,
        codec_id,
        codec_major,
        codec_minor,
        codec_patch,
        ihdr,
        filtered_length,
        zlib_header,
        adler32,
        prefix_length,
        suffix_length,
        correction_length,
        idat_count,
        row_filter_count,
    ) = HEADER.unpack_from(payload)

    if magic != MAGIC:
        raise CorruptReconstructionError("invalid reconstruction magic")
    if major != WIRE_MAJOR or minor > WIRE_MINOR:
        raise IncompatibleReconstructionError(
            f"unsupported reconstruction wire version {major}.{minor}"
        )
    if flags & ~KNOWN_FLAGS:
        raise IncompatibleReconstructionError("unsupported reconstruction flags")
    if payload_size != len(payload):
        raise CorruptReconstructionError(
            "reconstruction payload length is inconsistent"
        )
    if sha256(payload[HEADER.size :]).digest() != body_sha256:
        raise CorruptReconstructionError("reconstruction body SHA-256 mismatch")
    if (
        codec_id != PREFLATE_CODEC_ID
        or (
            codec_major,
            codec_minor,
            codec_patch,
        )
        != PREFLATE_VERSION
    ):
        raise IncompatibleReconstructionError("incompatible preflate codec version")
    if idat_count == 0:
        raise CorruptReconstructionError("reconstruction contains no IDAT lengths")
    if idat_count > limits.max_idat_count:
        raise ResourceLimitError("stored IDAT count exceeds its configured limit")

    limits.ensure(source_length, limits.max_reconstructed_size, "source PNG")
    color_type, expected_filtered, expected_filter_count = _validate_ihdr(ihdr, limits)
    if filtered_length != expected_filtered:
        raise CorruptReconstructionError("stored filtered length is inconsistent")
    if row_filter_count != expected_filter_count:
        raise CorruptReconstructionError("stored row-filter count is inconsistent")
    limits.ensure(prefix_length, limits.max_prefix_size, "PNG prefix")
    limits.ensure(suffix_length, limits.max_suffix_size, "PNG suffix")
    limits.ensure(
        correction_length,
        limits.max_correction_size,
        "preflate corrections",
    )
    has_palette_bitmap = bool(flags & FLAG_PALETTE_USED_BITMAP)
    if has_palette_bitmap != (color_type == 3):
        raise IncompatibleReconstructionError(
            "palette reconstruction flag and IHDR are inconsistent"
        )
    palette_used_length = PALETTE_BITMAP_SIZE if has_palette_bitmap else 0
    idat_table_length = idat_count * 4
    variable_length = (
        prefix_length
        + suffix_length
        + idat_table_length
        + row_filter_count
        + correction_length
        + palette_used_length
    )
    if variable_length != len(payload) - HEADER.size:
        raise CorruptReconstructionError(
            "reconstruction section lengths are inconsistent"
        )

    view = memoryview(payload)
    offset = HEADER.size
    prefix = bytes(view[offset : offset + prefix_length])
    offset += prefix_length
    suffix = bytes(view[offset : offset + suffix_length])
    offset += suffix_length
    idat_lengths = tuple(
        int.from_bytes(view[position : position + 4], "big")
        for position in range(offset, offset + idat_table_length, 4)
    )
    offset += idat_table_length
    row_filters = bytes(view[offset : offset + row_filter_count])
    offset += row_filter_count
    corrections = bytes(view[offset : offset + correction_length])
    offset += correction_length
    palette_used = bytes(view[offset : offset + palette_used_length])

    data = ReconstructionData(
        source_length=source_length,
        source_sha256=source_sha256,
        ihdr=ihdr,
        filtered_length=filtered_length,
        zlib_header=zlib_header,
        adler32=adler32,
        prefix=prefix,
        suffix=suffix,
        idat_lengths=idat_lengths,
        row_filters=row_filters,
        corrections=corrections,
        palette_used=palette_used,
    )
    _validate_common(data, limits)
    return data
