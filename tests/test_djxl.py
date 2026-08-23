import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from png2jxl import png_to_jxl

from .helpers import make_png


@pytest.mark.skipif(shutil.which("djxl") is None, reason="djxl is not installed")
def test_archive_decodes_with_djxl(tmp_path: Path) -> None:
    source = make_png(mode="RGBA", width=4, height=4)
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    archive_path = tmp_path / "image.png.jxl"
    decoded_path = tmp_path / "decoded.png"
    archive_path.write_bytes(archive)

    subprocess.run(
        ["djxl", str(archive_path), str(decoded_path)],
        check=True,
        capture_output=True,
    )
    with Image.open(decoded_path) as decoded:
        decoded.load()
        assert decoded.mode == "RGBA"
        assert decoded.size == (4, 4)
