"""Byte-exact PNG reconstruction on top of JPEG XL."""

from importlib.metadata import PackageNotFoundError, version

from .api import is_png_reconstructable_jxl, jxl_to_png, png_to_jxl
from .exceptions import (
    ArchiveNotSmallerError,
    CorruptPngError,
    CorruptReconstructionError,
    ExactRoundtripError,
    IncompatibleReconstructionError,
    JxlCodecError,
    NotPngError,
    NotReconstructableJxlError,
    Png2JxlError,
    ResourceLimitError,
    SourcePngMismatchError,
    UnsupportedPngError,
)
from .limits import DEFAULT_LIMITS, ResourceLimits
from .plugin import register

try:
    __version__ = version("png2jxl")
except PackageNotFoundError:
    __version__ = "0.1.0"

register()

__all__ = [
    "ArchiveNotSmallerError",
    "CorruptPngError",
    "CorruptReconstructionError",
    "DEFAULT_LIMITS",
    "ExactRoundtripError",
    "IncompatibleReconstructionError",
    "JxlCodecError",
    "NotPngError",
    "NotReconstructableJxlError",
    "Png2JxlError",
    "ResourceLimitError",
    "ResourceLimits",
    "SourcePngMismatchError",
    "UnsupportedPngError",
    "is_png_reconstructable_jxl",
    "jxl_to_png",
    "png_to_jxl",
    "register",
]
