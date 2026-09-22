import base64
import json
from pathlib import Path

import pillow_jxl
from png2jxl import is_png_reconstructable_jxl, jxl_to_png

FIXTURES = Path(__file__).parent / "fixtures"


def test_v2_golden_archive_reconstructs_exact_source() -> None:
    source = (FIXTURES / "v2-rgba.png").read_bytes()
    archive = (FIXTURES / "v2-rgba.png.jxl").read_bytes()
    assert is_png_reconstructable_jxl(archive)
    assert jxl_to_png(archive) == source


def test_v2_golden_archive_is_an_ordinary_jxl() -> None:
    archive = (FIXTURES / "v2-rgba.png.jxl").read_bytes()
    jpeg, info, pixels, _icc, _boxes = pillow_jxl.Decoder()(archive)
    assert not jpeg
    assert info.mode == "RGBA"
    assert (info.width, info.height) == (4, 5)
    assert len(pixels) == 4 * 5 * 4


def _palette_golden() -> tuple[bytes, bytes]:
    fixture = json.loads((FIXTURES / "v2-palette.json").read_text())
    return (
        base64.b64decode(fixture["png_base64"], validate=True),
        base64.b64decode(fixture["jxl_base64"], validate=True),
    )


def test_v2_palette_golden_archive_reconstructs_exact_source() -> None:
    source, archive = _palette_golden()
    assert is_png_reconstructable_jxl(archive)
    assert jxl_to_png(archive) == source


def test_v2_palette_golden_archive_is_an_ordinary_jxl() -> None:
    _source, archive = _palette_golden()
    jpeg, info, pixels, _icc, _boxes = pillow_jxl.Decoder()(archive)
    assert not jpeg
    assert info.mode == "RGBA"
    assert (info.width, info.height) == (4, 5)
    assert len(pixels) == 4 * 5 * 4
