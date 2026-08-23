from uuid import NAMESPACE_DNS, UUID, uuid5

import pytest
from png2jxl import CorruptReconstructionError, NotReconstructableJxlError
from png2jxl.jumbf import PROJECT_UUID, build_jumbf, find_project_payload
from png2jxl.jxl_container import parse_boxes, parse_jxl_container

from .helpers import box, minimal_jxl


def test_project_uuid_is_derived_from_package_name() -> None:
    assert PROJECT_UUID == uuid5(NAMESPACE_DNS, "png2jxl")


def test_box_parser_supports_32_64_and_size_zero() -> None:
    data = box(b"one ", b"1") + box(b"two ", b"22", large=True)
    boxes = parse_boxes(data)
    assert [(item.box_type, item.data) for item in boxes] == [
        (b"one ", b"1"),
        (b"two ", b"22"),
    ]
    final = parse_boxes(box(b"last", b"payload", to_end=True))
    assert final[0].data == b"payload"


def test_box_parser_rejects_truncation() -> None:
    with pytest.raises(CorruptReconstructionError):
        parse_boxes(box(b"data", b"payload")[:-1])


def test_box_parser_rejects_invalid_large_size() -> None:
    malformed = b"\x00\x00\x00\x01data" + b"\x00" * 8
    with pytest.raises(CorruptReconstructionError, match="64-bit"):
        parse_boxes(malformed)


def test_minimal_jxl_container_is_validated() -> None:
    boxes = parse_jxl_container(minimal_jxl())
    assert boxes[0].box_type == b"JXL "
    assert boxes[-1].box_type == b"jxlc"


def test_non_container_is_not_reconstructable() -> None:
    with pytest.raises(NotReconstructableJxlError):
        parse_jxl_container(b"\xff\x0a")


def test_jumbf_project_detection_and_unrelated_coexistence() -> None:
    unrelated_uuid = UUID("00000000-0000-4000-8000-000000000001")
    unrelated_description = unrelated_uuid.bytes + b"\x02other\x00"
    unrelated = box(b"jumd", unrelated_description) + box(b"data", b"other")
    assert find_project_payload((unrelated, build_jumbf(b"payload"))) == b"payload"


def test_duplicate_project_jumbf_is_rejected() -> None:
    project = build_jumbf(b"payload")
    with pytest.raises(CorruptReconstructionError, match="duplicate"):
        find_project_payload((project, project))


def test_partial_project_identity_is_rejected() -> None:
    description = PROJECT_UUID.bytes + b"\x02wrong-label\x00"
    malformed = box(b"jumd", description) + box(b"pngr", b"payload")
    with pytest.raises(CorruptReconstructionError, match="partial"):
        find_project_payload((malformed,))
