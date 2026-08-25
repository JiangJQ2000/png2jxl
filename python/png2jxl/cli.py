"""Command-line interface for byte-exact PNG reconstruction."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .api import is_png_reconstructable_jxl, jxl_to_png, png_to_jxl
from .exceptions import Png2JxlError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_SMALLER = 3


def _effort(value: str) -> int:
    try:
        effort = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from None
    if not 1 <= effort <= 10:
        raise argparse.ArgumentTypeError("effort must be between 1 and 10")
    return effort


def _checked_output(source: Path, output: Path | None, extension: str) -> Path:
    target = output if output is not None else source.with_suffix(extension)
    if target.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {target}")
    return target


def _run_encode(arguments: argparse.Namespace) -> int:
    source = arguments.source
    output = _checked_output(source, arguments.output, ".jxl")
    data = source.read_bytes()
    archive = png_to_jxl(
        data,
        effort=arguments.effort,
        num_threads=arguments.num_threads,
        only_if_smaller=arguments.only_if_smaller,
    )
    if archive is None:
        print(
            f"{source}: archive is not smaller than {len(data):,d} bytes; "
            "nothing written",
            file=sys.stderr,
        )
        return EXIT_NOT_SMALLER
    output.write_bytes(archive)
    ratio = len(archive) / len(data) * 100 if data else float("inf")
    print(f"wrote {output}: {len(archive):,d} bytes ({ratio:.1f}% of {len(data):,d})")
    return EXIT_OK


def _run_decode(arguments: argparse.Namespace) -> int:
    source = arguments.source
    output = _checked_output(source, arguments.output, ".png")
    rebuilt = jxl_to_png(source.read_bytes(), num_threads=arguments.num_threads)
    output.write_bytes(rebuilt)
    print(f"wrote {output}: {len(rebuilt):,d} bytes")
    return EXIT_OK


def _run_check(arguments: argparse.Namespace) -> int:
    reconstructable = is_png_reconstructable_jxl(arguments.source.read_bytes())
    if reconstructable:
        print(f"{arguments.source}: reconstructable", file=sys.stdout)
        return EXIT_OK
    print(f"{arguments.source}: not reconstructable", file=sys.stderr)
    return EXIT_ERROR


HANDLERS = {
    "encode": _run_encode,
    "decode": _run_decode,
    "check": _run_check,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m png2jxl",
        description="Byte-exact PNG reconstruction on top of JPEG XL.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    encode = subparsers.add_parser(
        "encode",
        help="encode a PNG into a verified JXL archive",
    )
    encode.add_argument("source", type=Path, help="source PNG file")
    encode.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output JXL path (default: replace the extension with .jxl)",
    )
    encode.add_argument(
        "--effort",
        type=_effort,
        default=7,
        help="JXL encoder effort, 1-10 (default: 7)",
    )
    encode.add_argument(
        "--threads",
        dest="num_threads",
        type=int,
        default=-1,
        help="encoder threads, -1 for automatic (default: -1)",
    )
    encode.add_argument(
        "--only-if-smaller",
        action="store_true",
        help="exit with code 3 instead of writing a larger archive",
    )

    decode = subparsers.add_parser(
        "decode",
        help="reconstruct the exact source PNG from an archive",
    )
    decode.add_argument("source", type=Path, help="png2jxl JXL archive")
    decode.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output PNG path (default: replace the extension with .png)",
    )
    decode.add_argument(
        "--threads",
        dest="num_threads",
        type=int,
        default=-1,
        help="decoder threads, -1 for automatic (default: -1)",
    )

    check = subparsers.add_parser(
        "check",
        help="report whether a JXL carries a png2jxl envelope",
    )
    check.add_argument("source", type=Path, help="candidate JXL archive")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        return HANDLERS[arguments.command](arguments)
    except (Png2JxlError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
