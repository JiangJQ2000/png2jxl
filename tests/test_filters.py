from random import Random

import pytest
from png2jxl import png_filter
from png2jxl.png_filter import (
    FilterError,
    extract_filter_types,
    refilter,
    scanline_info,
    scanline_layout,
)

from .helpers import _adam7_refilter, adam7_filter_count


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    distances = (
        abs(estimate - left),
        abs(estimate - up),
        abs(estimate - upper_left),
    )
    return (left, up, upper_left)[distances.index(min(distances))]


def _reference_refilter(
    samples: bytes,
    width: int,
    height: int,
    bytes_per_pixel: int,
    filter_types: bytes,
) -> bytes:
    row_size = width * bytes_per_pixel
    output = bytearray((row_size + 1) * height)
    output_offset = 0
    for row, filter_type in enumerate(filter_types):
        output[output_offset] = filter_type
        output_offset += 1
        row_offset = row * row_size
        for column in range(row_size):
            left = (
                samples[row_offset + column - bytes_per_pixel]
                if column >= bytes_per_pixel
                else 0
            )
            up = samples[row_offset + column - row_size] if row else 0
            upper_left = (
                samples[row_offset + column - row_size - bytes_per_pixel]
                if row and column >= bytes_per_pixel
                else 0
            )
            prediction = (0, left, up, (left + up) // 2, _paeth(left, up, upper_left))[
                filter_type
            ]
            output[output_offset + column] = (
                samples[row_offset + column] - prediction
            ) & 0xFF
        output_offset += row_size
    return bytes(output)


@pytest.mark.parametrize("bytes_per_pixel", [1, 2, 3, 4])
def test_all_filters_roundtrip(bytes_per_pixel: int) -> None:
    width = 17
    height = 10
    random = Random(0x504E47 + bytes_per_pixel)
    samples = random.randbytes(width * height * bytes_per_pixel)
    filter_types = bytes(row % 5 for row in range(height))

    expected = _reference_refilter(
        samples,
        width,
        height,
        bytes_per_pixel,
        filter_types,
    )
    assert refilter(samples, width, height, bytes_per_pixel, filter_types) == expected
    assert (
        extract_filter_types(expected, width, height, bytes_per_pixel, 0)
        == filter_types
    )


@pytest.mark.parametrize("bytes_per_pixel", [1, 2, 3, 4])
@pytest.mark.parametrize("size", [(1, 1), (2, 3), (4, 5), (9, 7)])
def test_adam7_refilter_matches_pass_reference(
    bytes_per_pixel: int,
    size: tuple[int, int],
) -> None:
    width, height = size
    random = Random(0x4144414D + width + height + bytes_per_pixel)
    samples = random.randbytes(width * height * bytes_per_pixel)
    filter_types = bytes(row % 5 for row in range(adam7_filter_count(width, height)))
    expected = _adam7_refilter(
        samples,
        width,
        height,
        bytes_per_pixel,
        filter_types,
    )
    actual = refilter(
        samples,
        width,
        height,
        bytes_per_pixel,
        filter_types,
        1,
    )
    assert actual == expected
    assert (
        extract_filter_types(actual, width, height, bytes_per_pixel, 1) == filter_types
    )


def test_adam7_layout_skips_empty_passes() -> None:
    assert scanline_layout(1, 1, 4, 1) == ((0, 0, 8, 8, 1, 1),)
    filtered_size, filter_count = scanline_info(9, 7, 4, 1)
    assert filtered_size == 266
    assert filter_count == 14


def test_sub_filter_known_row() -> None:
    assert refilter(b"\x0a\x14\x1e", 3, 1, 1, b"\x01") == b"\x01\x0a\x0a\x0a"


def test_up_filter_known_rows() -> None:
    samples = b"\x0a\x14\x1e\x0f\x1e\x2d"
    assert refilter(samples, 3, 2, 1, b"\x00\x02") == (
        b"\x00\x0a\x14\x1e\x02\x05\x0a\x0f"
    )


def test_invalid_filter_is_rejected() -> None:
    with pytest.raises(FilterError):
        extract_filter_types(b"\x05\x00", 1, 1, 1, 0)
    with pytest.raises(FilterError):
        refilter(b"\x00", 1, 1, 1, b"\x05")


@pytest.mark.parametrize("interlace_method", [0, 1])
def test_plain_python_refilter_fallback(
    interlace_method: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    width, height, bytes_per_pixel = 9, 7, 4
    samples = Random(0x504E47).randbytes(width * height * bytes_per_pixel)
    filter_count = (
        height if interlace_method == 0 else adam7_filter_count(width, height)
    )
    filter_types = bytes(row % 5 for row in range(filter_count))
    core_name = "_refilter_core" if interlace_method == 0 else "_refilter_adam7_core"
    core = getattr(png_filter, core_name)
    monkeypatch.setattr(png_filter, core_name, getattr(core, "py_func", core))

    actual = refilter(
        samples,
        width,
        height,
        bytes_per_pixel,
        filter_types,
        interlace_method,
    )
    expected = (
        _reference_refilter(
            samples,
            width,
            height,
            bytes_per_pixel,
            filter_types,
        )
        if interlace_method == 0
        else _adam7_refilter(
            samples,
            width,
            height,
            bytes_per_pixel,
            filter_types,
        )
    )
    assert actual == expected


def test_numba_signatures_are_eagerly_compiled() -> None:
    pytest.importorskip("numba")
    assert png_filter._refilter_core.signatures
    assert png_filter._refilter_adam7_core.signatures
