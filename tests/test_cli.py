import subprocess
import sys
from pathlib import Path

import pytest
from png2jxl import jxl_to_png, png_to_jxl
from png2jxl.cli import EXIT_ERROR, EXIT_NOT_SMALLER, EXIT_OK, main

from .helpers import make_png, minimal_jxl


def _write_png(tmp_path: Path, name: str = "image.png") -> tuple[Path, bytes]:
    source = make_png()
    path = tmp_path / name
    path.write_bytes(source)
    return path, source


def test_encode_writes_default_archive_and_roundtrips(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, source = _write_png(tmp_path)
    assert main(["encode", str(path)]) == EXIT_OK
    archive_path = tmp_path / "image.jxl"
    assert archive_path.is_file()
    captured = capsys.readouterr()
    assert "image.jxl" in captured.out
    assert jxl_to_png(archive_path.read_bytes()) == source


def test_encode_honors_output_effort_and_threads(
    tmp_path: Path,
) -> None:
    path, source = _write_png(tmp_path)
    output = tmp_path / "custom-archive.jxl"
    code = main(
        [
            "encode",
            str(path),
            "-o",
            str(output),
            "--effort",
            "3",
            "--threads",
            "1",
        ]
    )
    assert code == EXIT_OK
    archive = output.read_bytes()
    assert len(archive) > 0
    assert jxl_to_png(archive) == source


def test_encode_refuses_existing_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, _source = _write_png(tmp_path)
    existing = tmp_path / "image.jxl"
    existing.write_bytes(b"sentinel")
    assert main(["encode", str(path)]) == EXIT_ERROR
    assert existing.read_bytes() == b"sentinel"
    assert "refusing to overwrite" in capsys.readouterr().err


def test_decode_reconstructs_exact_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, source = _write_png(tmp_path)
    archive = png_to_jxl(source)
    assert archive is not None
    archive_path = tmp_path / "archive.jxl"
    archive_path.write_bytes(archive)
    assert main(["decode", str(archive_path)]) == EXIT_OK
    restored = tmp_path / "archive.png"
    assert restored.read_bytes() == source
    assert "wrote" in capsys.readouterr().out


def test_decode_refuses_existing_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, source = _write_png(tmp_path, name="source.png")
    archive = png_to_jxl(source)
    assert archive is not None
    (tmp_path / "image.jxl").write_bytes(archive)
    existing = tmp_path / "image.png"
    existing.write_bytes(b"sentinel")
    assert main(["decode", str(tmp_path / "image.jxl")]) == EXIT_ERROR
    assert existing.read_bytes() == b"sentinel"
    assert "refusing to overwrite" in capsys.readouterr().err


def test_decode_to_explicit_output(
    tmp_path: Path,
) -> None:
    _, source = _write_png(tmp_path)
    archive = png_to_jxl(source)
    assert archive is not None
    archive_path = tmp_path / "archive.jxl"
    archive_path.write_bytes(archive)
    restored = tmp_path / "restored.bin"
    assert (
        main(["decode", str(archive_path), "-o", str(restored), "--threads", "1"])
        == EXIT_OK
    )
    assert restored.read_bytes() == source


def test_check_accepts_real_archive(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, source = _write_png(tmp_path)
    archive = png_to_jxl(source)
    assert archive is not None
    archive_path = tmp_path / "image.jxl"
    archive_path.write_bytes(archive)
    assert main(["check", str(archive_path)]) == EXIT_OK
    captured = capsys.readouterr()
    assert "reconstructable" in captured.out


@pytest.mark.parametrize("payload", [b"not a jxl at all", minimal_jxl()])
def test_check_rejects_other_files(
    tmp_path: Path,
    payload: bytes,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = tmp_path / "candidate.jxl"
    candidate.write_bytes(payload)
    assert main(["check", str(candidate)]) == EXIT_ERROR
    assert "not reconstructable" in capsys.readouterr().err


def test_encode_reports_not_smaller_without_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path, source = _write_png(tmp_path)
    baseline = png_to_jxl(source)
    if baseline is not None and len(baseline) < len(source):
        pytest.skip("fixture already compresses smaller than the source")
    assert main(["encode", str(path), "--only-if-smaller"]) == EXIT_NOT_SMALLER
    assert not (tmp_path / "image.jxl").exists()
    assert "nothing written" in capsys.readouterr().err


def test_encode_reports_bad_input_on_stderr(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bogus = tmp_path / "bogus.png"
    bogus.write_bytes(b"definitely not a PNG")
    assert main(["encode", str(bogus)]) == EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err


def test_missing_source_is_an_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.png"
    assert main(["encode", str(missing)]) == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_usage_errors_exit_with_code_two() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["encode"])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        main(["frobnicate"])
    assert excinfo.value.code == 2
    with pytest.raises(SystemExit) as excinfo:
        main(["encode", "x.png", "--effort", "11"])
    assert excinfo.value.code == 2


def test_module_help_runs_in_a_subprocess() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "png2jxl", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "encode" in completed.stdout
    assert "decode" in completed.stdout
    assert "check" in completed.stdout
