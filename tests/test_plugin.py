from io import BytesIO
from pathlib import Path

import png2jxl.plugin as plugin
import pytest
from PIL import Image
from png2jxl import (
    ArchiveNotSmallerError,
    SourcePngMismatchError,
    jxl_to_png,
    register,
)

from .helpers import make_palette_png, make_png


def palette_source() -> bytes:
    return make_palette_png(
        palette=b"\xff\x00\x00\x00\xff\x00\x00\x00\xff",
        transparency=b"\xff\x80\x00",
        indices=b"\x00\x01\x02\x01\x00\x02",
        filters=b"\x00\x04",
    )


def test_explicit_source_png_save() -> None:
    source = make_png(mode="RGBA")
    with Image.open(BytesIO(source)) as image:
        output = BytesIO()
        image.save(
            output,
            format="JXL",
            png_reconstruction=True,
            source_png=source,
            effort=1,
            num_threads=1,
        )
    assert jxl_to_png(output.getvalue()) == source


def test_path_backed_source_save(tmp_path: Path) -> None:
    source = make_png(mode="RGB")
    source_path = tmp_path / "source.png"
    archive_path = tmp_path / "source.png.jxl"
    source_path.write_bytes(source)
    with Image.open(source_path) as image:
        image.save(
            archive_path,
            format="JXL",
            png_reconstruction=True,
            effort=1,
        )
    assert jxl_to_png(archive_path.read_bytes()) == source


def test_explicit_palette_source_png_save() -> None:
    source = palette_source()
    with Image.open(BytesIO(source)) as image:
        assert image.mode == "P"
        output = BytesIO()
        image.save(
            output,
            format="JXL",
            png_reconstruction=True,
            source_png=source,
            effort=1,
        )
    assert jxl_to_png(output.getvalue()) == source


def test_path_backed_palette_source_save(tmp_path: Path) -> None:
    source = palette_source()
    source_path = tmp_path / "palette.png"
    archive_path = tmp_path / "palette.png.jxl"
    source_path.write_bytes(source)
    with Image.open(source_path) as image:
        assert image.mode == "P"
        image.save(
            archive_path,
            format="JXL",
            png_reconstruction=True,
            effort=1,
        )
    assert jxl_to_png(archive_path.read_bytes()) == source


def test_num_threads_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_png()
    seen: list[object] = []

    def encode(_source: bytes, **kwargs: object) -> bytes:
        seen.append(kwargs["num_threads"])
        return b"archive"

    monkeypatch.setattr(plugin, "png_to_jxl", encode)
    with Image.open(BytesIO(source)) as image:
        output = BytesIO()
        image.save(
            output,
            format="JXL",
            png_reconstruction=True,
            source_png=source,
            num_threads=3,
        )

    assert seen == [3]
    assert output.getvalue() == b"archive"


def test_stream_source_requires_explicit_bytes() -> None:
    source = make_png()
    with Image.open(BytesIO(source)) as image, pytest.raises(SourcePngMismatchError):
        image.save(
            BytesIO(),
            format="JXL",
            png_reconstruction=True,
            effort=1,
        )


def test_modified_image_is_rejected() -> None:
    source = make_png(mode="RGB")
    with Image.open(BytesIO(source)) as image:
        image.putpixel((0, 0), (0, 0, 0))
        with pytest.raises(SourcePngMismatchError):
            image.save(
                BytesIO(),
                format="JXL",
                png_reconstruction=True,
                source_png=source,
                effort=1,
            )


def test_registration_is_idempotent() -> None:
    first = Image.SAVE["JXL"]
    register()
    register()
    assert Image.SAVE["JXL"] is first


def test_ordinary_jxl_save_still_delegates() -> None:
    image = Image.new("RGB", (2, 2), (1, 2, 3))
    output = BytesIO()
    image.save(output, format="JXL", lossless=True, effort=1)
    with Image.open(BytesIO(output.getvalue())) as decoded:
        decoded.load()
        assert decoded.mode == "RGB"
        assert decoded.tobytes() == image.tobytes()


def test_pillow_only_if_smaller_raises() -> None:
    source = make_png(width=1, height=1, filters=b"\x00")
    with Image.open(BytesIO(source)) as image, pytest.raises(ArchiveNotSmallerError):
        image.save(
            BytesIO(),
            format="JXL",
            png_reconstruction=True,
            source_png=source,
            effort=1,
            only_if_smaller=True,
        )
