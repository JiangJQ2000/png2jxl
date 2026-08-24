"""PNG scanline filtering and Adam7 layout handling."""

ADAM7_PASSES = (
    (0, 0, 8, 8),
    (4, 0, 8, 8),
    (0, 4, 4, 8),
    (2, 0, 4, 4),
    (0, 2, 2, 4),
    (1, 0, 2, 2),
    (0, 1, 1, 2),
)
_ADAM7_X_STARTS = tuple(item[0] for item in ADAM7_PASSES)
_ADAM7_Y_STARTS = tuple(item[1] for item in ADAM7_PASSES)
_ADAM7_X_STEPS = tuple(item[2] for item in ADAM7_PASSES)
_ADAM7_Y_STEPS = tuple(item[3] for item in ADAM7_PASSES)

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

    _REFILTER_SIGNATURE = None
else:
    _jitable = _numba.extending.register_jitable
    _INTEGER = _numba.types.intp
    _BYTES = _numba.typeof(b"")
    _BYTEARRAY = _numba.typeof(bytearray())
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


@_njit(_REFILTER_SIGNATURE)
def _refilter_adam7_core(
    samples: bytes,
    filter_types: bytes,
    filtered: bytearray,
    width: int,
    height: int,
    bytes_per_pixel: int,
) -> None:
    filter_offset = 0
    output_offset = 0
    for pass_index in range(7):
        x_start = _ADAM7_X_STARTS[pass_index]
        y_start = _ADAM7_Y_STARTS[pass_index]
        x_step = _ADAM7_X_STEPS[pass_index]
        y_step = _ADAM7_Y_STEPS[pass_index]
        if x_start >= width or y_start >= height:
            continue

        pass_row = 0
        for y in range(y_start, height, y_step):
            filter_type = filter_types[filter_offset]
            filter_offset += 1
            filtered[output_offset] = filter_type
            output_offset += 1

            pass_column = 0
            for x in range(x_start, width, x_step):
                pixel_offset = (y * width + x) * bytes_per_pixel
                for channel in range(bytes_per_pixel):
                    sample = samples[pixel_offset + channel]
                    left = (
                        samples[(y * width + x - x_step) * bytes_per_pixel + channel]
                        if pass_column
                        else 0
                    )
                    up = (
                        samples[((y - y_step) * width + x) * bytes_per_pixel + channel]
                        if pass_row
                        else 0
                    )
                    upper_left = (
                        samples[
                            ((y - y_step) * width + x - x_step) * bytes_per_pixel
                            + channel
                        ]
                        if pass_row and pass_column
                        else 0
                    )
                    prediction = _predictor(filter_type, left, up, upper_left)
                    filtered[output_offset] = (sample - prediction) & 0xFF
                    output_offset += 1
                pass_column += 1
            pass_row += 1


def _span(size: int, start: int, step: int) -> int:
    return 0 if start >= size else (size - start + step - 1) // step


def scanline_layout(
    width: int,
    height: int,
    bytes_per_pixel: int,
    interlace_method: int,
) -> tuple[tuple[int, int, int, int, int, int], ...]:
    """Return non-empty PNG passes including their pixel dimensions."""
    if width <= 0 or height <= 0 or bytes_per_pixel <= 0:
        raise FilterError("PNG scanline dimensions must be positive")
    if interlace_method not in {0, 1}:
        raise FilterError("invalid PNG interlace method")

    pass_specs = ((0, 0, 1, 1),) if interlace_method == 0 else ADAM7_PASSES
    layout = []
    for x_start, y_start, x_step, y_step in pass_specs:
        pass_width = _span(width, x_start, x_step)
        pass_height = _span(height, y_start, y_step)
        if pass_width and pass_height:
            layout.append(
                (
                    x_start,
                    y_start,
                    x_step,
                    y_step,
                    pass_width,
                    pass_height,
                )
            )
    return tuple(layout)


def scanline_info(
    width: int,
    height: int,
    bytes_per_pixel: int,
    interlace_method: int,
) -> tuple[int, int]:
    """Return filtered plaintext size and serialized scanline count."""
    layout = scanline_layout(width, height, bytes_per_pixel, interlace_method)
    filtered_size = sum(
        (pass_width * bytes_per_pixel + 1) * pass_height
        for *_, pass_width, pass_height in layout
    )
    filter_count = sum(pass_height for *_, pass_height in layout)
    return filtered_size, filter_count


def extract_filter_types(
    filtered: bytes,
    width: int,
    height: int,
    bytes_per_pixel: int,
    interlace_method: int,
) -> bytes:
    """Extract and validate filter bytes in serialized pass order."""
    layout = scanline_layout(width, height, bytes_per_pixel, interlace_method)
    expected_size, filter_count = scanline_info(
        width,
        height,
        bytes_per_pixel,
        interlace_method,
    )
    if len(filtered) != expected_size:
        raise FilterError(
            f"filtered data has length {len(filtered)}, expected {expected_size}"
        )

    filter_types = bytearray(filter_count)
    source_offset = 0
    filter_offset = 0
    for *_, pass_width, pass_height in layout:
        scanline_size = pass_width * bytes_per_pixel + 1
        for _row in range(pass_height):
            filter_type = filtered[source_offset]
            if filter_type > 4:
                raise FilterError(f"unsupported PNG filter type {filter_type}")
            filter_types[filter_offset] = filter_type
            filter_offset += 1
            source_offset += scanline_size
    return bytes(filter_types)


def refilter(
    samples: bytes,
    width: int,
    height: int,
    bytes_per_pixel: int,
    filter_types: bytes,
    interlace_method: int = 0,
) -> bytes:
    expected_samples = width * height * bytes_per_pixel
    if len(samples) != expected_samples:
        raise FilterError(
            f"sample data has length {len(samples)}, expected {expected_samples}"
        )
    filtered_size, filter_count = scanline_info(
        width,
        height,
        bytes_per_pixel,
        interlace_method,
    )
    if len(filter_types) != filter_count:
        raise FilterError(
            f"filter list has length {len(filter_types)}, expected {filter_count}"
        )
    invalid_filter = next((value for value in filter_types if value > 4), None)
    if invalid_filter is not None:
        raise FilterError(f"unsupported PNG filter type {invalid_filter}")

    filtered = bytearray(filtered_size)
    if interlace_method == 0:
        _refilter_core(
            samples,
            filter_types,
            filtered,
            width * bytes_per_pixel,
            height,
            bytes_per_pixel,
        )
    else:
        _refilter_adam7_core(
            samples,
            filter_types,
            filtered,
            width,
            height,
            bytes_per_pixel,
        )
    return bytes(filtered)
