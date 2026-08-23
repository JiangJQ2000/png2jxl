from dataclasses import replace
from io import BytesIO
from random import Random

import pillow_jxl
import pytest
from png2jxl import (
    DEFAULT_LIMITS,
    ExactRoundtripError,
    NotReconstructableJxlError,
    Png2JxlError,
    ResourceLimitError,
    is_png_reconstructable_jxl,
    jxl_to_png,
    png_to_jxl,
)
from png2jxl.jumbf import build_jumbf, find_project_payload
from png2jxl.jxl_container import jumb_payloads, parse_jxl_container
from png2jxl.reconstruction import parse_reconstruction, serialize_reconstruction

from .helpers import make_png


@pytest.mark.parametrize("mode", ["L", "LA", "RGB", "RGBA"])
def test_exact_roundtrip_for_supported_modes(mode: str) -> None:
    source = make_png(
        mode=mode,
        width=4,
        height=5,
        filters=bytes(range(5)),
        idat_splits=[0, 1, 0, 2, 3],
        before_idat=[(b"tEXt", b"before\x00metadata")],
        after_idat=[(b"tEXt", b"after\x00metadata")],
    )
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    assert is_png_reconstructable_jxl(archive)
    assert jxl_to_png(archive) == source

    jpeg, info, pixels, _icc, boxes = pillow_jxl.Decoder()(archive)
    assert not jpeg
    assert info.mode == mode
    assert bytes(pixels)
    assert any(bytes(item.box_type) == b"jumb" for item in boxes)


@pytest.mark.parametrize("kind", ["compressible", "noisy"])
def test_exact_roundtrip_for_data_characteristics(kind: str) -> None:
    if kind == "compressible":
        samples = b"\x00" * (16 * 16 * 4)
    else:
        samples = Random(0x4A584C).randbytes(16 * 16 * 4)
    source = make_png(
        mode="RGBA",
        width=16,
        height=16,
        samples=samples,
        filters=bytes(row % 5 for row in range(16)),
    )
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    assert jxl_to_png(archive) == source


def test_only_if_smaller_returns_none() -> None:
    source = make_png(width=1, height=1, filters=b"\x00")
    assert png_to_jxl(source, effort=1, only_if_smaller=True) is None


def test_ordinary_jxl_is_not_identified() -> None:
    pixels = b"\x01\x02\x03"
    archive = bytes(
        pillow_jxl.Encoder("RGB", lossless=True, effort=1, use_container=True)(
            pixels,
            1,
            1,
            jpeg_encode=False,
        )
    )
    assert not is_png_reconstructable_jxl(archive)
    with pytest.raises(NotReconstructableJxlError):
        jxl_to_png(archive)


def test_invalid_public_arguments_are_rejected() -> None:
    source = make_png()
    with pytest.raises(TypeError):
        png_to_jxl(bytearray(source))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="effort"):
        png_to_jxl(source, effort=0)
    with pytest.raises(TypeError, match="num_threads"):
        png_to_jxl(source, num_threads=True)
    with pytest.raises(ValueError, match="num_threads"):
        png_to_jxl(source, num_threads=-2)
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    with pytest.raises(TypeError, match="num_threads"):
        jxl_to_png(archive, num_threads=False)
    with pytest.raises(ValueError, match="num_threads"):
        jxl_to_png(archive, num_threads=-2)
    assert not is_png_reconstructable_jxl(b"not a JXL")


def test_num_threads_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_png()
    real_encoder = pillow_jxl.Encoder
    real_decoder = pillow_jxl.Decoder
    encoder_threads: list[int] = []
    decoder_threads: list[int] = []

    def encoder(*args: object, **kwargs: object) -> object:
        num_threads = kwargs["num_threads"]
        assert isinstance(num_threads, int)
        encoder_threads.append(num_threads)
        return real_encoder(*args, **kwargs)

    def decoder(*args: object, **kwargs: object) -> object:
        num_threads = kwargs["num_threads"]
        assert isinstance(num_threads, int)
        decoder_threads.append(num_threads)
        return real_decoder(*args, **kwargs)

    monkeypatch.setattr(pillow_jxl, "Encoder", encoder)
    monkeypatch.setattr(pillow_jxl, "Decoder", decoder)

    archive = png_to_jxl(source, effort=1, num_threads=2)
    assert archive is not None
    assert encoder_threads == [2]
    assert decoder_threads == [2]
    assert jxl_to_png(archive, num_threads=0) == source
    assert decoder_threads == [2, 0]


def test_jxl_size_limit_is_enforced() -> None:
    source = make_png()
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    limits = replace(DEFAULT_LIMITS, max_jxl_size=len(archive) - 1)
    with pytest.raises(ResourceLimitError):
        jxl_to_png(archive, limits=limits)


def _reencode_with_jumb(
    archive: bytes,
    jumb: bytes,
    *,
    change_pixel: bool = False,
) -> bytes:
    jpeg, info, pixels, _icc, _boxes = pillow_jxl.Decoder()(archive)
    assert not jpeg
    pixels = bytearray(pixels)
    if change_pixel:
        pixels[0] ^= 1
    encoder = pillow_jxl.Encoder(
        info.mode,
        lossless=True,
        effort=1,
        use_container=True,
    )
    return bytes(
        encoder(
            bytes(pixels),
            info.width,
            info.height,
            jpeg_encode=False,
            jumb=jumb,
            compress=False,
        )
    )


def test_tampered_pixels_are_rejected() -> None:
    source = make_png(mode="RGB", width=3, height=3)
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    jumb = jumb_payloads(parse_jxl_container(archive))[0]
    tampered = _reencode_with_jumb(archive, jumb, change_pixel=True)
    with pytest.raises(Png2JxlError):
        jxl_to_png(tampered)


def test_tampered_corrections_are_rejected() -> None:
    source = make_png(mode="RGBA", width=3, height=3)
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    project_payload = find_project_payload(jumb_payloads(parse_jxl_container(archive)))
    payload = bytearray(project_payload)
    payload[-1] ^= 1
    tampered = _reencode_with_jumb(
        archive,
        build_jumbf(bytes(payload)),
    )
    with pytest.raises(Png2JxlError):
        jxl_to_png(tampered)


def test_tampered_source_hash_is_rejected() -> None:
    source = make_png(mode="L", width=2, height=2)
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    project_payload = find_project_payload(jumb_payloads(parse_jxl_container(archive)))
    reconstruction = parse_reconstruction(project_payload)
    changed = replace(reconstruction, source_sha256=b"\x00" * 32)
    tampered = _reencode_with_jumb(
        archive,
        build_jumbf(serialize_reconstruction(changed)),
    )
    with pytest.raises(ExactRoundtripError):
        jxl_to_png(tampered)


def test_tampered_adler_is_rejected() -> None:
    source = make_png(mode="RGB", width=2, height=2)
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    project_payload = find_project_payload(jumb_payloads(parse_jxl_container(archive)))
    reconstruction = parse_reconstruction(project_payload)
    changed = replace(reconstruction, adler32=b"\x00" * 4)
    tampered = _reencode_with_jumb(
        archive,
        build_jumbf(serialize_reconstruction(changed)),
    )
    with pytest.raises(Png2JxlError):
        jxl_to_png(tampered)


def test_predicate_never_decodes_pixels(monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_png()
    archive = png_to_jxl(source, effort=1)
    assert archive is not None

    class FailingDecoder:
        def __call__(self, _archive: bytes) -> None:
            raise AssertionError("predicate must not decode JXL pixels")

    monkeypatch.setattr(pillow_jxl, "Decoder", FailingDecoder)
    assert is_png_reconstructable_jxl(archive)


def test_archive_can_be_opened_by_pillow() -> None:
    source = make_png(mode="LA")
    archive = png_to_jxl(source, effort=1)
    assert archive is not None
    with BytesIO(archive) as stream:
        from PIL import Image

        with Image.open(stream) as image:
            image.load()
            assert image.mode == "LA"
            assert image.size == (3, 3)
