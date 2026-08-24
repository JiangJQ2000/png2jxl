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
ADAM7_PASSES = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)


def chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = crc32(chunk_type + payload) & 0xFFFFFFFF
    return pack(">I", len(payload)) + chunk_type + payload + pack(">I", checksum)


def adam7_filter_count(width: int, height: int) -> int:
    return sum(
        len(range(y_start, height, y_step))
        for x_start, y_start, x_step, y_step in ADAM7_PASSES
        if range(x_start, width, x_step) and range(y_start, height, y_step)
    )


def _adam7_refilter(
    samples: bytes,
    width: int,
    height: int,
    bytes_per_pixel: int,
    filters: bytes,
) -> bytes:
    filtered_parts: list[bytes] = []
    filter_offset = 0
    for x_start, y_start, x_step, y_step in ADAM7_PASSES:
        xs = range(x_start, width, x_step)
        ys = range(y_start, height, y_step)
        pass_width = len(xs)
        pass_height = len(ys)
        if not pass_width or not pass_height:
            continue

        pass_samples = bytearray(pass_width * pass_height * bytes_per_pixel)
        pass_offset = 0
        for y in ys:
            for x in xs:
                source_offset = (y * width + x) * bytes_per_pixel
                pass_samples[pass_offset : pass_offset + bytes_per_pixel] = samples[
                    source_offset : source_offset + bytes_per_pixel
                ]
                pass_offset += bytes_per_pixel
        pass_filters = filters[filter_offset : filter_offset + pass_height]
        filtered_parts.append(
            refilter(
                bytes(pass_samples),
                pass_width,
                pass_height,
                bytes_per_pixel,
                pass_filters,
            )
        )
        filter_offset += pass_height
    if filter_offset != len(filters):
        raise ValueError("Adam7 filter count is inconsistent")
    return b"".join(filtered_parts)


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
        filter_count = adam7_filter_count(width, height) if interlace == 1 else height
        filters = bytes(row % 5 for row in range(filter_count))

    if interlace == 1:
        filtered = _adam7_refilter(
            samples,
            width,
            height,
            bytes_per_pixel,
            filters,
        )
    else:
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


def make_palette_png(
    *,
    palette: bytes,
    transparency: bytes | None = None,
    width: int = 3,
    height: int = 2,
    indices: bytes | None = None,
    filters: bytes | None = None,
    idat_splits: Iterable[int] | None = None,
    before_plte: Iterable[tuple[bytes, bytes]] = (),
    after_palette: Iterable[tuple[bytes, bytes]] = (),
    after_idat: Iterable[tuple[bytes, bytes]] = (),
    interlace: int = 0,
) -> bytes:
    if indices is None:
        entry_count = len(palette) // 3
        indices = bytes(index % entry_count for index in range(width * height))
    palette_chunks = [*before_plte, (b"PLTE", palette)]
    if transparency is not None:
        palette_chunks.append((b"tRNS", transparency))
    palette_chunks.extend(after_palette)
    return make_png(
        mode="L",
        width=width,
        height=height,
        samples=indices,
        filters=filters,
        idat_splits=idat_splits,
        before_idat=palette_chunks,
        after_idat=after_idat,
        color_type=3,
        interlace=interlace,
    )


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
