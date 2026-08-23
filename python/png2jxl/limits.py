"""Resource limits used while parsing and reconstructing images."""

from dataclasses import dataclass

from .exceptions import ResourceLimitError

MIB = 1024 * 1024
GIB = 1024 * MIB


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    max_source_png_size: int = GIB
    max_jxl_size: int = GIB
    max_width: int = 100_000
    max_height: int = 100_000
    max_pixels: int = 128_000_000
    max_chunk_count: int = 65_536
    max_idat_count: int = 65_536
    max_filtered_size: int = 512 * MIB
    max_prefix_size: int = 512 * MIB
    max_suffix_size: int = 512 * MIB
    max_payload_size: int = GIB
    max_correction_size: int = 512 * MIB
    max_reconstructed_size: int = GIB

    def ensure(self, value: int, maximum: int, name: str) -> None:
        if value < 0 or value > maximum:
            raise ResourceLimitError(f"{name} exceeds its limit of {maximum} bytes")


DEFAULT_LIMITS = ResourceLimits()
