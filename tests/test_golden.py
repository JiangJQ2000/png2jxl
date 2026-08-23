from pathlib import Path

import pillow_jxl
from png2jxl import is_png_reconstructable_jxl, jxl_to_png

FIXTURES = Path(__file__).parent / "fixtures"


def test_v1_golden_archive_reconstructs_exact_source() -> None:
    source = (FIXTURES / "v1-rgba.png").read_bytes()
    archive = (FIXTURES / "v1-rgba.png.jxl").read_bytes()
    assert is_png_reconstructable_jxl(archive)
    assert jxl_to_png(archive) == source


def test_v1_golden_archive_is_an_ordinary_jxl() -> None:
    archive = (FIXTURES / "v1-rgba.png.jxl").read_bytes()
    jpeg, info, pixels, _icc, _boxes = pillow_jxl.Decoder()(archive)
    assert not jpeg
    assert info.mode == "RGBA"
    assert (info.width, info.height) == (4, 5)
    assert len(pixels) == 4 * 5 * 4
