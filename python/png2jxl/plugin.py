"""Pillow save-handler integration."""

from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import pillow_jxl  # noqa: F401 - importing registers the upstream Pillow plugin
from PIL import Image

from .api import png_to_jxl
from .exceptions import ArchiveNotSmallerError, SourcePngMismatchError

SaveHandler = Callable[[Image.Image, BinaryIO, str | bytes], None]
_delegate: SaveHandler | None = None


def _source_png(image: Image.Image) -> bytes:
    explicit = image.encoderinfo.get("source_png")
    if explicit is not None:
        if not isinstance(explicit, bytes):
            raise TypeError("source_png must be bytes")
        return explicit

    filename = getattr(image, "filename", None)
    if not filename:
        raise SourcePngMismatchError(
            "exact PNG saving requires a path-backed source or source_png bytes"
        )
    try:
        return Path(filename).read_bytes()
    except OSError as error:
        raise SourcePngMismatchError("could not reread the source PNG") from error


def _validate_source(image: Image.Image, source: bytes) -> None:
    try:
        with Image.open(BytesIO(source)) as source_image:
            source_image.load()
            matches = (
                source_image.format == "PNG"
                and source_image.mode == image.mode
                and source_image.size == image.size
                and source_image.tobytes() == image.tobytes()
            )
    except Exception as error:
        raise SourcePngMismatchError("source_png is not a readable PNG") from error
    if not matches:
        raise SourcePngMismatchError(
            "source PNG dimensions, mode, or samples differ from the Pillow image"
        )


def _save(image: Image.Image, fp: BinaryIO, filename: str | bytes) -> None:
    if not image.encoderinfo.get("png_reconstruction", False):
        if _delegate is None:
            raise RuntimeError("upstream JXL save handler is unavailable")
        _delegate(image, fp, filename)
        return

    source = _source_png(image)
    _validate_source(image, source)
    archive = png_to_jxl(
        source,
        effort=image.encoderinfo.get("effort", 10),
        num_threads=image.encoderinfo.get("num_threads", -1),
        only_if_smaller=image.encoderinfo.get("only_if_smaller", False),
    )
    if archive is None:
        raise ArchiveNotSmallerError("exact JXL archive is not smaller than the PNG")
    fp.write(archive)


def register() -> None:
    """Install the png2jxl JXL save wrapper once."""
    global _delegate

    current = Image.SAVE.get("JXL")
    if current is _save:
        return
    if current is None:
        Image.init()
        current = Image.SAVE.get("JXL")
    if current is None:
        raise RuntimeError("pillow_jxl did not register a JXL save handler")
    _delegate = current
    Image.register_save("JXL", _save)
