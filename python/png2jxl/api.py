"""Public byte APIs for exact PNG reconstruction through JPEG XL."""

from hashlib import sha256
from struct import pack
from sys import maxsize
from zlib import adler32, crc32, decompressobj

import pillow_jxl

from . import _preflate
from .exceptions import (
    CorruptPngError,
    CorruptReconstructionError,
    ExactRoundtripError,
    IncompatibleReconstructionError,
    JxlCodecError,
    Png2JxlError,
    ResourceLimitError,
    UnsupportedPngError,
)
from .jumbf import build_jumbf, find_project_payload
from .jxl_container import jumb_payloads, parse_jxl_container
from .limits import DEFAULT_LIMITS, ResourceLimits
from .png import (
    AmbiguousPaletteError,
    PaletteError,
    ParsedPng,
    carrier_to_palette,
    palette_carrier_mode,
    palette_from_prefix,
    palette_to_carrier,
    parse_png,
)
from .png_filter import FilterError, refilter, unfilter
from .reconstruction import (
    PREFLATE_VERSION,
    ReconstructionData,
    parse_reconstruction,
    serialize_reconstruction,
    source_digest,
)

PREFLATE_LIMIT_ERROR = 27
PREFLATE_UNSUPPORTED_ERRORS = {22, 25, 26}


def _check_limits(limits: ResourceLimits) -> None:
    if not isinstance(limits, ResourceLimits):
        raise TypeError("limits must be a ResourceLimits instance")


def _check_num_threads(num_threads: int) -> None:
    if isinstance(num_threads, bool) or not isinstance(num_threads, int):
        raise TypeError("num_threads must be an integer")
    if not -1 <= num_threads <= maxsize:
        raise ValueError("num_threads must be -1 or a non-negative integer")


def _native_error_code(error: Exception) -> int | None:
    if error.args and isinstance(error.args[0], int):
        return error.args[0]
    if error.args and isinstance(error.args[0], tuple) and error.args[0]:
        code = error.args[0][0]
        return code if isinstance(code, int) else None
    return None


def _verify_native_version() -> None:
    expected = ".".join(str(part) for part in PREFLATE_VERSION)
    actual = _preflate.preflate_version()
    if actual != expected:
        raise IncompatibleReconstructionError(
            f"preflate version {actual} cannot read data pinned to {expected}"
        )


def _zlib_plaintext(stream: bytes, expected_length: int) -> bytes:
    decoder = decompressobj()
    try:
        plaintext = decoder.decompress(stream, expected_length + 1)
        if len(plaintext) > expected_length or decoder.unconsumed_tail:
            raise CorruptPngError("zlib plaintext exceeds the expected length")
        plaintext += decoder.flush()
    except CorruptPngError:
        raise
    except Exception as error:
        raise CorruptPngError("invalid zlib stream") from error
    if (
        not decoder.eof
        or decoder.unused_data
        or decoder.unconsumed_tail
        or len(plaintext) != expected_length
    ):
        raise CorruptPngError("zlib stream length is inconsistent with IHDR")
    return plaintext


def _encode_preflate(
    parsed: ParsedPng,
    limits: ResourceLimits,
) -> tuple[bytes, bytes]:
    try:
        plaintext, corrections = _preflate.preflate_encode(
            parsed.raw_deflate,
            verify=True,
            plain_text_limit=limits.max_filtered_size,
        )
    except _preflate.PreflateError as error:
        code = _native_error_code(error)
        if code == PREFLATE_LIMIT_ERROR:
            raise ResourceLimitError("preflate plaintext limit exceeded") from error
        if code in PREFLATE_UNSUPPORTED_ERRORS:
            raise UnsupportedPngError(
                f"preflate cannot reconstruct this PNG: {error}"
            ) from error
        raise CorruptPngError(f"invalid PNG DEFLATE stream: {error}") from error

    plaintext = bytes(plaintext)
    corrections = bytes(corrections)
    limits.ensure(
        len(corrections),
        limits.max_correction_size,
        "preflate corrections",
    )
    if len(plaintext) != parsed.expected_filtered_size:
        raise CorruptPngError("preflate plaintext length does not match IHDR")
    if (adler32(plaintext) & 0xFFFFFFFF).to_bytes(4, "big") != parsed.adler32:
        raise CorruptPngError("PNG Adler-32 does not match its filtered data")

    zlib_stream = parsed.zlib_header + parsed.raw_deflate + parsed.adler32
    if _zlib_plaintext(zlib_stream, parsed.expected_filtered_size) != plaintext:
        raise CorruptPngError("zlib and preflate produced different plaintext")
    return plaintext, corrections


def _encode_jxl(
    samples: bytes,
    mode: str,
    width: int,
    height: int,
    effort: int,
    num_threads: int,
    jumb: bytes,
) -> bytes:
    try:
        encoder = pillow_jxl.Encoder(
            mode,
            lossless=True,
            effort=effort,
            use_container=True,
            num_threads=num_threads,
        )
        return bytes(
            encoder(
                samples,
                width,
                height,
                jpeg_encode=False,
                jumb=jumb,
                compress=False,
            )
        )
    except Exception as error:
        raise JxlCodecError(f"pillow_jxl encode failed: {error}") from error


def png_to_jxl(
    png: bytes,
    *,
    effort: int = 10,
    num_threads: int = -1,
    only_if_smaller: bool = False,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> bytes | None:
    """Encode a PNG into an ordinary JXL that can reconstruct the exact file."""
    _check_limits(limits)
    if not isinstance(png, bytes):
        raise TypeError("PNG input must be bytes")
    if isinstance(effort, bool) or not isinstance(effort, int) or not 1 <= effort <= 10:
        raise ValueError("effort must be an integer between 1 and 10")
    _check_num_threads(num_threads)
    if not isinstance(only_if_smaller, bool):
        raise TypeError("only_if_smaller must be bool")
    _verify_native_version()

    parsed = parse_png(png, limits)
    plaintext, corrections = _encode_preflate(parsed, limits)
    try:
        source_samples, row_filters = unfilter(
            plaintext,
            parsed.width,
            parsed.height,
            parsed.bytes_per_pixel,
        )
    except FilterError as error:
        raise CorruptPngError(str(error)) from error

    carrier_mode = parsed.mode
    carrier_samples = source_samples
    palette_used = b""
    if parsed.color_type == 3:
        assert parsed.palette is not None
        try:
            carrier_mode, carrier_samples, palette_used = palette_to_carrier(
                source_samples,
                parsed.palette,
                parsed.transparency,
            )
        except AmbiguousPaletteError as error:
            raise UnsupportedPngError(str(error)) from error
        except PaletteError as error:
            raise CorruptPngError(str(error)) from error

    reconstruction = ReconstructionData(
        source_length=len(png),
        source_sha256=source_digest(png),
        ihdr=parsed.ihdr,
        filtered_length=len(plaintext),
        zlib_header=parsed.zlib_header,
        adler32=parsed.adler32,
        prefix=parsed.prefix,
        suffix=parsed.suffix,
        idat_lengths=parsed.idat_lengths,
        row_filters=row_filters,
        corrections=corrections,
        palette_used=palette_used,
    )
    payload = serialize_reconstruction(reconstruction, limits)
    archive = _encode_jxl(
        carrier_samples,
        carrier_mode,
        parsed.width,
        parsed.height,
        effort,
        num_threads,
        build_jumbf(payload),
    )
    limits.ensure(len(archive), limits.max_jxl_size, "encoded JXL")
    if only_if_smaller and len(archive) >= len(png):
        return None

    recreated = jxl_to_png(archive, num_threads=num_threads, limits=limits)
    if recreated != png:
        raise ExactRoundtripError("encoded JXL did not reconstruct the source bytes")
    return archive


def _decode_jxl(archive: bytes, num_threads: int) -> tuple[object, bytes]:
    try:
        jpeg, info, data, _icc, _boxes = pillow_jxl.Decoder(num_threads=num_threads)(
            archive
        )
    except Exception as error:
        raise JxlCodecError(f"pillow_jxl decode failed: {error}") from error
    if jpeg:
        raise CorruptReconstructionError("PNG reconstruction JXL contains JPEG data")
    return info, bytes(data)


def _decode_preflate(filtered: bytes, corrections: bytes) -> bytes:
    try:
        return bytes(_preflate.preflate_decode(filtered, corrections))
    except _preflate.PreflateError as error:
        raise CorruptReconstructionError(
            f"preflate reconstruction failed: {error}"
        ) from error


def _idat_run(zlib_stream: bytes, lengths: tuple[int, ...]) -> bytes:
    chunks: list[bytes] = []
    offset = 0
    for length in lengths:
        payload = zlib_stream[offset : offset + length]
        if len(payload) != length:
            raise CorruptReconstructionError("IDAT lengths exceed the zlib stream")
        checksum = crc32(b"IDAT" + payload) & 0xFFFFFFFF
        chunks.append(pack(">I", length) + b"IDAT" + payload + pack(">I", checksum))
        offset += length
    if offset != len(zlib_stream):
        raise CorruptReconstructionError("IDAT lengths do not consume the zlib stream")
    return b"".join(chunks)


def jxl_to_png(
    jxl: bytes,
    *,
    num_threads: int = -1,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> bytes:
    """Reconstruct and fully verify the exact source PNG from a trusted JXL."""
    _check_limits(limits)
    if not isinstance(jxl, bytes):
        raise TypeError("JXL input must be bytes")
    _check_num_threads(num_threads)
    _verify_native_version()

    boxes = parse_jxl_container(jxl, limits)
    payload = find_project_payload(jumb_payloads(boxes))
    reconstruction = parse_reconstruction(payload, limits)
    width, height = reconstruction.dimensions
    bytes_per_pixel = reconstruction.bytes_per_pixel
    palette_metadata: tuple[bytes, bytes | None] | None = None
    if reconstruction.color_type == 3:
        try:
            palette_metadata = palette_from_prefix(
                reconstruction.prefix,
                reconstruction.ihdr,
            )
            carrier_mode = palette_carrier_mode(
                *palette_metadata,
                reconstruction.palette_used,
            )
        except PaletteError as error:
            raise CorruptReconstructionError(str(error)) from error
    else:
        carrier_mode = reconstruction.mode
    carrier_bytes_per_pixel = {"L": 1, "LA": 2, "RGB": 3, "RGBA": 4}[carrier_mode]

    info, samples = _decode_jxl(jxl, num_threads)
    if info.mode != carrier_mode or info.width != width or info.height != height:
        raise CorruptReconstructionError(
            "decoded JXL mode or dimensions do not match reconstruction metadata"
        )
    expected_samples = width * height * carrier_bytes_per_pixel
    if len(samples) != expected_samples:
        raise CorruptReconstructionError("decoded JXL sample length is inconsistent")

    source_samples = samples
    if palette_metadata is not None:
        try:
            source_samples = carrier_to_palette(
                samples,
                info.mode,
                *palette_metadata,
                reconstruction.palette_used,
            )
        except PaletteError as error:
            raise CorruptReconstructionError(str(error)) from error

    try:
        filtered = refilter(
            source_samples,
            width,
            height,
            bytes_per_pixel,
            reconstruction.row_filters,
        )
    except FilterError as error:
        raise CorruptReconstructionError(str(error)) from error
    if len(filtered) != reconstruction.filtered_length:
        raise CorruptReconstructionError("recreated filtered length is inconsistent")
    if (adler32(filtered) & 0xFFFFFFFF).to_bytes(4, "big") != reconstruction.adler32:
        raise CorruptReconstructionError("recreated filtered data fails Adler-32")

    raw_deflate = _decode_preflate(filtered, reconstruction.corrections)
    expected_raw_length = sum(reconstruction.idat_lengths) - 6
    if len(raw_deflate) != expected_raw_length:
        raise CorruptReconstructionError("recreated DEFLATE length is inconsistent")
    zlib_stream = reconstruction.zlib_header + raw_deflate + reconstruction.adler32
    try:
        standard_plaintext = _zlib_plaintext(
            zlib_stream,
            reconstruction.filtered_length,
        )
    except CorruptPngError as error:
        raise CorruptReconstructionError(str(error)) from error
    if standard_plaintext != filtered:
        raise CorruptReconstructionError("recreated zlib plaintext is inconsistent")

    rebuilt = b"".join(
        (
            reconstruction.prefix,
            _idat_run(zlib_stream, reconstruction.idat_lengths),
            reconstruction.suffix,
        )
    )
    limits.ensure(len(rebuilt), limits.max_reconstructed_size, "reconstructed PNG")
    if len(rebuilt) != reconstruction.source_length:
        raise ExactRoundtripError("reconstructed PNG length does not match the source")
    if sha256(rebuilt).digest() != reconstruction.source_sha256:
        raise ExactRoundtripError("reconstructed PNG SHA-256 does not match the source")
    try:
        parse_png(rebuilt, limits)
    except Png2JxlError as error:
        raise CorruptReconstructionError(
            "reconstructed bytes are not a valid supported PNG"
        ) from error
    return rebuilt


def is_png_reconstructable_jxl(
    jxl: bytes,
    *,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> bool:
    """Return whether a JXL has a valid, compatible png2jxl envelope."""
    try:
        _check_limits(limits)
        if not isinstance(jxl, bytes):
            return False
        _verify_native_version()
        boxes = parse_jxl_container(jxl, limits)
        payload = find_project_payload(jumb_payloads(boxes))
        parse_reconstruction(payload, limits)
    except (Png2JxlError, TypeError, ValueError):
        return False
    return True
