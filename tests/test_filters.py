from random import Random

import pytest
from png2jxl import png_filter
from png2jxl.png_filter import FilterError, refilter, unfilter


@pytest.mark.parametrize("bytes_per_pixel", [1, 2, 3, 4])
def test_all_filters_roundtrip(bytes_per_pixel: int) -> None:
    width = 17
    height = 10
    random = Random(0x504E47 + bytes_per_pixel)
    samples = random.randbytes(width * height * bytes_per_pixel)
    filter_types = bytes(row % 5 for row in range(height))

    filtered = refilter(samples, width, height, bytes_per_pixel, filter_types)
    recreated, found_filters = unfilter(
        filtered,
        width,
        height,
        bytes_per_pixel,
    )
    assert recreated == samples
    assert found_filters == filter_types
    assert (
        refilter(
            recreated,
            width,
            height,
            bytes_per_pixel,
            found_filters,
        )
        == filtered
    )


def test_sub_filter_known_row() -> None:
    assert refilter(b"\x0a\x14\x1e", 3, 1, 1, b"\x01") == b"\x01\x0a\x0a\x0a"


def test_up_filter_known_rows() -> None:
    samples = b"\x0a\x14\x1e\x0f\x1e\x2d"
    assert refilter(samples, 3, 2, 1, b"\x00\x02") == (
        b"\x00\x0a\x14\x1e\x02\x05\x0a\x0f"
    )


def test_invalid_filter_is_rejected() -> None:
    with pytest.raises(FilterError):
        unfilter(b"\x05\x00", 1, 1, 1)


def test_numba_signatures_are_eagerly_compiled() -> None:
    pytest.importorskip("numba")
    assert png_filter._unfilter_core.signatures
    assert png_filter._refilter_core.signatures
