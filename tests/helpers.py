from collections.abc import Iterable
from struct import pack
from zlib import compress, crc32

from png2jxl.png import PNG_SIGNATURE
from png2jxl.png_filter import refilter

MODE_INFO = {
    "L": (0, 1),
    "RGB": (2, 3),
    "LA": (4, 2),
    "RGBA": (6, 4),
}


def chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = crc32(chunk_type + payload) & 0xFFFFFFFF
    return pack(">I", len(payload)) + chunk_type + payload + pack(">I", checksum)


def make_png(
    mode: str = "RGBA",
    width: int = 3,
    height: int = 3,
    samples: bytes | None = None,
    filters: bytes | None = None,
    idat_splits: Iterable[int] | None = None,
    before_idat: Iterable[tuple[bytes, bytes]] = (),
    after_idat: Iterable[tuple[bytes, bytes]] = (),
    *,
    bit_depth: int = 8,
    color_type: int | None = None,
    interlace: int = 0,
) -> bytes:
    default_color_type, bytes_per_pixel = MODE_INFO[mode]
    if color_type is None:
        color_type = default_color_type
    if samples is None:
        samples = bytes(
            (index * 37 + 11) & 0xFF
            for index in range(width * height * bytes_per_pixel)
        )
    if filters is None:
        filters = bytes(row % 5 for row in range(height))

    filtered = refilter(samples, width, height, bytes_per_pixel, filters)
    zlib_stream = compress(filtered, level=6)
    if idat_splits is None:
        idat_payloads = [zlib_stream]
    else:
        idat_payloads = []
        offset = 0
        for length in idat_splits:
            idat_payloads.append(zlib_stream[offset : offset + length])
            offset += length
        if offset < len(zlib_stream):
            idat_payloads.append(zlib_stream[offset:])

    ihdr = pack(
        ">IIBBBBB",
        width,
        height,
        bit_depth,
        color_type,
        0,
        0,
        interlace,
    )
    pieces = [PNG_SIGNATURE, chunk(b"IHDR", ihdr)]
    pieces.extend(chunk(chunk_type, payload) for chunk_type, payload in before_idat)
    pieces.extend(chunk(b"IDAT", payload) for payload in idat_payloads)
    pieces.extend(chunk(chunk_type, payload) for chunk_type, payload in after_idat)
    pieces.append(chunk(b"IEND", b""))
    return b"".join(pieces)


def box(
    box_type: bytes,
    payload: bytes,
    *,
    large: bool = False,
    to_end: bool = False,
) -> bytes:
    if to_end:
        return b"\x00\x00\x00\x00" + box_type + payload
    if large:
        return pack(">I4sQ", 1, box_type, len(payload) + 16) + payload
    return pack(">I4s", len(payload) + 8, box_type) + payload


def minimal_jxl(*extra_boxes: bytes) -> bytes:
    signature = box(b"JXL ", b"\r\n\x87\n")
    file_type = box(b"ftyp", b"jxl \x00\x00\x00\x00jxl ")
    return signature + file_type + b"".join(extra_boxes) + box(b"jxlc", b"\xff\x0a")
