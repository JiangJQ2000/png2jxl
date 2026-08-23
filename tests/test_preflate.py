from zlib import compressobj

import pytest
from png2jxl import _preflate


def raw_deflate(data: bytes) -> bytes:
    compressor = compressobj(level=6, wbits=-15)
    return compressor.compress(data) + compressor.flush()


@pytest.mark.parametrize(
    "plaintext",
    [
        b"hello hello hello",
        bytes(range(256)) * 4,
        b"png2jxl" * 10_000,
    ],
    ids=["short-text", "all-byte-values", "large-repeated"],
)
def test_preflate_exact_roundtrip(plaintext: bytes) -> None:
    raw = raw_deflate(plaintext)
    decoded, corrections = _preflate.preflate_encode(raw, verify=True)
    assert bytes(decoded) == plaintext
    assert bytes(_preflate.preflate_decode(decoded, corrections)) == raw


def test_preflate_plaintext_limit() -> None:
    raw = raw_deflate(b"too large")
    with pytest.raises(_preflate.PreflateError) as caught:
        _preflate.preflate_encode(raw, plain_text_limit=2)
    assert caught.value.args[0] == 27


def test_preflate_rejects_trailing_bytes() -> None:
    with pytest.raises(_preflate.PreflateError):
        _preflate.preflate_encode(raw_deflate(b"data") + b"trailing")


def test_preflate_version_is_pinned() -> None:
    assert _preflate.preflate_version() == "0.7.6"
