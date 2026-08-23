"""Minimal JUMBF envelope for png2jxl reconstruction data."""

from uuid import UUID

from .exceptions import CorruptReconstructionError, NotReconstructableJxlError
from .jxl_container import parse_boxes

PROJECT_UUID = UUID("6f1387ee-629c-5300-bb69-c533644ba5fa")
PROJECT_LABEL = "png2jxl-png"
LABEL_PRESENT = 0x02
CONTENT_BOX_TYPE = b"pngr"
_DISALLOWED_LABEL = set("/;?:#")


def _box(box_type: bytes, data: bytes) -> bytes:
    if len(box_type) != 4:
        raise ValueError("box types must contain four bytes")
    size = len(data) + 8
    if size > 0xFFFFFFFF:
        raise ValueError("JUMBF child box is too large")
    return size.to_bytes(4, "big") + box_type + data


def build_jumbf(payload: bytes) -> bytes:
    label = PROJECT_LABEL.encode("utf-8") + b"\x00"
    description = PROJECT_UUID.bytes + bytes((LABEL_PRESENT,)) + label
    return _box(b"jumd", description) + _box(CONTENT_BOX_TYPE, payload)


def _parse_description(data: bytes) -> tuple[UUID, str]:
    if len(data) < 17:
        raise CorruptReconstructionError("truncated JUMBF description")
    content_type = UUID(bytes=data[:16])
    toggles = data[16]
    if toggles & 0xF0:
        raise CorruptReconstructionError("JUMBF description uses reserved toggles")
    offset = 17
    label = ""
    if toggles & LABEL_PRESENT:
        terminator = data.find(b"\x00", offset)
        if terminator < 0:
            raise CorruptReconstructionError("unterminated JUMBF label")
        try:
            label = data[offset:terminator].decode("utf-8")
        except UnicodeDecodeError as error:
            raise CorruptReconstructionError("invalid UTF-8 JUMBF label") from error
        if any(
            ord(character) < 0x20
            or 0x7F <= ord(character) <= 0x9F
            or character in _DISALLOWED_LABEL
            for character in label
        ):
            raise CorruptReconstructionError("invalid character in JUMBF label")
        offset = terminator + 1
    if toggles & 0x04:
        offset += 4
    if toggles & 0x08:
        offset += 32
    if offset != len(data):
        raise CorruptReconstructionError("invalid JUMBF description length")
    return content_type, label


def find_project_payload(jumb_boxes: tuple[bytes, ...]) -> bytes:
    project_payload: bytes | None = None
    marker = PROJECT_LABEL.encode("utf-8")

    for jumb_data in jumb_boxes:
        try:
            children = parse_boxes(jumb_data)
            if not children or children[0].box_type != b"jumd":
                raise CorruptReconstructionError(
                    "JUMBF description must be the first child box"
                )
            content_type, label = _parse_description(bytes(children[0].data))
        except CorruptReconstructionError:
            if PROJECT_UUID.bytes in jumb_data or marker in jumb_data:
                raise
            continue

        uuid_matches = content_type == PROJECT_UUID
        label_matches = label == PROJECT_LABEL
        if uuid_matches != label_matches:
            raise CorruptReconstructionError("partial png2jxl JUMBF identity match")
        if not uuid_matches:
            continue
        if project_payload is not None:
            raise CorruptReconstructionError("duplicate png2jxl JUMBF boxes")
        if len(children) != 2 or children[1].box_type != CONTENT_BOX_TYPE:
            raise CorruptReconstructionError("invalid png2jxl JUMBF content boxes")
        project_payload = bytes(children[1].data)

    if project_payload is None:
        raise NotReconstructableJxlError(
            "JPEG XL container has no png2jxl reconstruction box"
        )
    return project_payload
