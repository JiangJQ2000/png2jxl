"""Public exception types for png2jxl."""


class Png2JxlError(Exception):
    """Base class for all png2jxl failures."""


class NotPngError(Png2JxlError):
    """The input is not a PNG file."""


class UnsupportedPngError(Png2JxlError):
    """The PNG is legal but outside the supported profile."""


class CorruptPngError(Png2JxlError):
    """The PNG container or compressed image data is corrupt."""


class NotReconstructableJxlError(Png2JxlError):
    """The input is not a png2jxl reconstruction archive."""


class CorruptReconstructionError(Png2JxlError):
    """The reconstruction envelope or payload is corrupt."""


class IncompatibleReconstructionError(Png2JxlError):
    """The reconstruction format or codec version is unsupported."""


class JxlCodecError(Png2JxlError):
    """pillow_jxl could not encode or decode the image."""


class ResourceLimitError(Png2JxlError):
    """A configured resource limit was exceeded."""


class SourcePngMismatchError(Png2JxlError):
    """The source PNG does not describe the current Pillow image."""


class ArchiveNotSmallerError(Png2JxlError):
    """The exact archive is not smaller than the source PNG."""


class ExactRoundtripError(Png2JxlError):
    """An exact reconstruction check failed."""
