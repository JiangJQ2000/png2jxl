"""Strict, read-only JPEG XL container box parsing."""

from dataclasses import dataclass

from .exceptions import CorruptReconstructionError, NotReconstructableJxlError
from .limits import DEFAULT_LIMITS, ResourceLimits

JXL_SIGNATURE_BOX = b"\x00\x00\x00\x0cJXL \r\n\x87\n"


@dataclass(frozen=True, slots=True)
class Box:
    box_type: bytes
    data: memoryview
    offset: int
    size: int
    header_size: int


def parse_boxes(data: bytes | memoryview) -> tuple[Box, ...]:
    view = memoryview(data)
    boxes: list[Box] = []
    offset = 0
    while offset < len(view):
        if len(view) - offset < 8:
            raise CorruptReconstructionError("truncated box header")
        size32 = int.from_bytes(view[offset : offset + 4], "big")
        box_type = bytes(view[offset + 4 : offset + 8])
        header_size = 8
        if size32 == 1:
            if len(view) - offset < 16:
                raise CorruptReconstructionError("truncated 64-bit box header")
            size = int.from_bytes(view[offset + 8 : offset + 16], "big")
            header_size = 16
            if size < header_size:
                raise CorruptReconstructionError("invalid 64-bit box size")
        elif size32 == 0:
            size = len(view) - offset
        else:
            size = size32
            if size < header_size:
                raise CorruptReconstructionError("invalid box size")

        end = offset + size
        if end > len(view):
            raise CorruptReconstructionError("box extends beyond its container")
        boxes.append(
            Box(
                box_type=box_type,
                data=view[offset + header_size : end],
                offset=offset,
                size=size,
                header_size=header_size,
            )
        )
        offset = end
        if size32 == 0 and offset != len(view):
            raise CorruptReconstructionError("size-0 box must be final")
    return tuple(boxes)


def parse_jxl_container(
    data: bytes,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> tuple[Box, ...]:
    if not isinstance(data, bytes):
        raise TypeError("JXL input must be bytes")
    limits.ensure(len(data), limits.max_jxl_size, "JXL input")
    if not data.startswith(JXL_SIGNATURE_BOX):
        raise NotReconstructableJxlError("input is not a JPEG XL container")

    boxes = parse_boxes(data)
    if len(boxes) < 3:
        raise CorruptReconstructionError("JPEG XL container is incomplete")
    signature = boxes[0]
    if (
        signature.box_type != b"JXL "
        or signature.size != 12
        or bytes(signature.data) != b"\r\n\x87\n"
    ):
        raise CorruptReconstructionError("invalid JPEG XL signature box")

    file_type = boxes[1]
    if file_type.box_type != b"ftyp" or len(file_type.data) < 12:
        raise CorruptReconstructionError("missing JPEG XL file type box")
    if (len(file_type.data) - 8) % 4:
        raise CorruptReconstructionError("invalid JPEG XL compatible-brand list")
    file_type_data = bytes(file_type.data)
    compatible_brands = {
        file_type_data[position : position + 4]
        for position in range(8, len(file_type_data), 4)
    }
    if file_type_data[:4] != b"jxl " or b"jxl " not in compatible_brands:
        raise CorruptReconstructionError("invalid JPEG XL file type brand")
    if not any(box.box_type in {b"jxlc", b"jxlp"} for box in boxes[2:]):
        raise CorruptReconstructionError("JPEG XL container has no codestream box")
    return boxes


def jumb_payloads(boxes: tuple[Box, ...]) -> tuple[bytes, ...]:
    return tuple(bytes(box.data) for box in boxes if box.box_type == b"jumb")
