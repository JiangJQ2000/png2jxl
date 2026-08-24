"""Measure png2jxl archive size components and roundtrip time."""

import argparse
from pathlib import Path
from time import perf_counter

import pillow_jxl
from png2jxl import jxl_to_png, png_to_jxl
from png2jxl.api import _decode_png_samples
from png2jxl.jxl_container import jumb_payloads, parse_jxl_container
from png2jxl.png import palette_to_carrier, parse_png


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("png", type=Path)
    parser.add_argument("--effort", type=int, default=10)
    parser.add_argument("--num-threads", type=int, default=-1)
    arguments = parser.parse_args()

    source = arguments.png.read_bytes()
    parsed = parse_png(source)
    samples = _decode_png_samples(parsed)
    mode = parsed.mode
    if parsed.color_type == 3:
        assert parsed.palette is not None
        mode, samples, _used = palette_to_carrier(
            samples,
            parsed.palette,
            parsed.transparency,
        )
    baseline = pillow_jxl.Encoder(
        mode,
        lossless=True,
        effort=arguments.effort,
        use_container=True,
        num_threads=arguments.num_threads,
    )(samples, parsed.width, parsed.height, jpeg_encode=False)

    encode_start = perf_counter()
    archive = png_to_jxl(
        source,
        effort=arguments.effort,
        num_threads=arguments.num_threads,
    )
    encode_seconds = perf_counter() - encode_start
    assert archive is not None
    jumb_size = sum(
        len(payload) + 8 for payload in jumb_payloads(parse_jxl_container(archive))
    )

    decode_start = perf_counter()
    assert jxl_to_png(archive, num_threads=arguments.num_threads) == source
    decode_seconds = perf_counter() - decode_start

    print(f"source PNG:          {len(source):12,d} bytes")
    print(f"JXL without pngr:    {len(baseline):12,d} bytes")
    print(f"JUMBF box:           {jumb_size:12,d} bytes")
    print(f"final archive:       {len(archive):12,d} bytes")
    print(f"encode + verify:     {encode_seconds:12.3f} s")
    print(f"reconstruct:         {decode_seconds:12.3f} s")


if __name__ == "__main__":
    main()
