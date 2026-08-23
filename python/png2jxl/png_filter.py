"""PNG scanline filtering and unfiltering."""

try:
    import numba as _numba
except Exception:
    _numba = None


def _njit(signature: object):
    if _numba is None:
        return lambda function: function
    return _numba.njit(signature, cache=True, nogil=True)


if _numba is None:

    def _jitable(function):
        return function

    _UNFILTER_SIGNATURE = None
    _REFILTER_SIGNATURE = None
else:
    _jitable = _numba.extending.register_jitable
    _INTEGER = _numba.types.intp
    _BYTES = _numba.typeof(b"")
    _BYTEARRAY = _numba.typeof(bytearray())
    _UNFILTER_SIGNATURE = _numba.types.void(
        _BYTES,
        _BYTEARRAY,
        _INTEGER,
        _INTEGER,
        _INTEGER,
    )
    _REFILTER_SIGNATURE = _numba.types.void(
        _BYTES,
        _BYTES,
        _BYTEARRAY,
        _INTEGER,
        _INTEGER,
        _INTEGER,
    )


class FilterError(ValueError):
    """Filtered scanlines are malformed."""


@_jitable
def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= up_distance and left_distance <= upper_left_distance:
        return left
    if up_distance <= upper_left_distance:
        return up
    return upper_left


@_jitable
def _predictor(
    filter_type: int,
    left: int,
    up: int,
    upper_left: int,
) -> int:
    if filter_type == 0:
        return 0
    if filter_type == 1:
        return left
    if filter_type == 2:
        return up
    if filter_type == 3:
        return (left + up) // 2
    return _paeth(left, up, upper_left)


@_njit(_UNFILTER_SIGNATURE)
def _unfilter_core(
    filtered: bytes,
    samples: bytearray,
    row_size: int,
    height: int,
    bytes_per_pixel: int,
) -> None:
    source_offset = 0
    output_offset = 0
    for row in range(height):
        filter_type = filtered[source_offset]
        source_offset += 1
        for column in range(row_size):
            left = (
                samples[output_offset + column - bytes_per_pixel]
                if column >= bytes_per_pixel
                else 0
            )
            up = samples[output_offset + column - row_size] if row else 0
            upper_left = (
                samples[output_offset + column - row_size - bytes_per_pixel]
                if row and column >= bytes_per_pixel
                else 0
            )
            prediction = _predictor(filter_type, left, up, upper_left)
            samples[output_offset + column] = (
                filtered[source_offset + column] + prediction
            ) & 0xFF
        source_offset += row_size
        output_offset += row_size


@_njit(_REFILTER_SIGNATURE)
def _refilter_core(
    samples: bytes,
    filter_types: bytes,
    filtered: bytearray,
    row_size: int,
    height: int,
    bytes_per_pixel: int,
) -> None:
    output_offset = 0
    for row in range(height):
        filter_type = filter_types[row]
        filtered[output_offset] = filter_type
        output_offset += 1
        row_offset = row * row_size
        for column in range(row_size):
            sample = samples[row_offset + column]
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
            prediction = _predictor(filter_type, left, up, upper_left)
            filtered[output_offset + column] = (sample - prediction) & 0xFF
        output_offset += row_size


def unfilter(
    filtered: bytes,
    width: int,
    height: int,
    bytes_per_pixel: int,
) -> tuple[bytes, bytes]:
    row_size = width * bytes_per_pixel
    expected_size = (row_size + 1) * height
    if len(filtered) != expected_size:
        raise FilterError(
            f"filtered data has length {len(filtered)}, expected {expected_size}"
        )

    filter_types = filtered[:: row_size + 1]
    invalid_filter = next((value for value in filter_types if value > 4), None)
    if invalid_filter is not None:
        raise FilterError(f"unsupported PNG filter type {invalid_filter}")

    samples = bytearray(row_size * height)
    _unfilter_core(filtered, samples, row_size, height, bytes_per_pixel)
    return bytes(samples), filter_types


def refilter(
    samples: bytes,
    width: int,
    height: int,
    bytes_per_pixel: int,
    filter_types: bytes,
) -> bytes:
    row_size = width * bytes_per_pixel
    expected_size = row_size * height
    if len(samples) != expected_size:
        raise FilterError(
            f"sample data has length {len(samples)}, expected {expected_size}"
        )
    if len(filter_types) != height:
        raise FilterError(
            f"filter list has length {len(filter_types)}, expected {height}"
        )
    invalid_filter = next((value for value in filter_types if value > 4), None)
    if invalid_filter is not None:
        raise FilterError(f"unsupported PNG filter type {invalid_filter}")

    filtered = bytearray((row_size + 1) * height)
    _refilter_core(
        samples,
        filter_types,
        filtered,
        row_size,
        height,
        bytes_per_pixel,
    )
    return bytes(filtered)
