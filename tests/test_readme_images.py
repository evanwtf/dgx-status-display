from __future__ import annotations

import logging
import struct
import zlib
from pathlib import Path

import pytest
import readme_images
from readme_images import (
    NAMES,
    SIGNATURE,
    ImageError,
    chunk,
    chunks,
    decode,
    encode,
    guard_hits,
    recompress,
    run,
)


def _stored(raw: bytes) -> bytes:
    """A zlib stream of stored blocks (BTYPE 00), as png.c writes it."""
    out = bytearray(b"\x78\x01")
    for i in range(0, len(raw), 65535):
        block = raw[i : i + 65535]
        n = len(block)
        out.append(1 if i + n == len(raw) else 0)
        out += struct.pack("<HH", n, n ^ 0xFFFF) + block
    out += struct.pack(">I", zlib.adler32(raw))
    return bytes(out)


def _rows(width: int, height: int) -> list[bytes]:
    """A pattern with flat runs and noise, so every byte value can appear."""
    return [
        bytes(((x // 7) * 37 + y * 11 + (x * y) % 5) & 0xFF for x in range(width * 3))
        for y in range(height)
    ]


def _png(
    rows: list[bytes],
    width: int,
    *,
    depth: int = 8,
    color: int = 2,
    filter_byte: int = 0,
) -> bytes:
    raw = b"".join(bytes([filter_byte]) + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, len(rows), depth, color, 0, 0, 0)
    return (
        SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", _stored(raw))
        + chunk(b"IEND", b"")
    )


def test_the_fixture_is_stored_like_png_c() -> None:
    # 120 x (200 x 3 + 1) > 65535 bytes: more than one stored block.
    src = _png(_rows(200, 120), 200)
    idat = dict(chunks(src))[b"IDAT"]
    assert idat[2] == 0  # first block: not final, stored
    assert len(idat) > 65535


def test_round_trip_keeps_every_pixel_and_shrinks() -> None:
    rows = _rows(200, 120)
    src = _png(rows, 200)
    out = recompress(src)
    assert len(out) < len(src)
    assert decode(out) == decode(src) == (200, 120, rows)
    assert [k for k, _ in chunks(out)] == [b"IHDR", b"IDAT", b"IEND"]


def test_guard_hits_sees_a_pattern_in_binary_bytes() -> None:
    assert guard_hits(b"\x89\xff" + b"~" + b"abc/\x00") != []
    assert guard_hits(b"\x89\xff~/\x00") == []


def test_encode_skips_output_the_public_guard_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _rows(32, 32)
    seen: list[int] = []

    def refuse_the_first(data: bytes) -> list[tuple[int, str]]:
        seen.append(len(data))
        return [(1, "user home")] if len(seen) == 1 else []

    monkeypatch.setattr(readme_images, "guard_hits", refuse_the_first)
    out = encode(32, 32, rows)
    assert len(seen) == 2
    assert seen[0] <= seen[1] == len(out)
    assert decode(out) == (32, 32, rows)


def test_encode_refuses_when_every_output_hits_the_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(readme_images, "guard_hits", lambda data: [(1, "x")])
    with pytest.raises(ImageError, match="public-repo guard"):
        encode(4, 4, _rows(4, 4))


def test_refuses_a_bad_signature() -> None:
    with pytest.raises(ImageError, match="bad signature"):
        decode(b"GIF89a" + bytes(40))


@pytest.mark.parametrize(
    ("depth", "color"),
    [(16, 2), (8, 6), (8, 0), (8, 3)],
    ids=["16-bit", "rgba", "gray", "palette"],
)
def test_refuses_another_bit_depth_or_color_type(depth: int, color: int) -> None:
    src = _png(_rows(4, 4), 4, depth=depth, color=color)
    with pytest.raises(ImageError, match="want 8-bit RGB"):
        decode(src)


def test_refuses_a_bad_crc() -> None:
    src = bytearray(_png(_rows(4, 4), 4))
    src[len(SIGNATURE) + 8] ^= 1  # first IHDR byte
    with pytest.raises(ImageError, match="bad CRC"):
        decode(bytes(src))


def test_refuses_a_row_filter_png_c_does_not_write() -> None:
    with pytest.raises(ImageError, match="filter 1"):
        decode(_png(_rows(4, 4), 4, filter_byte=1))


def test_refuses_a_short_idat() -> None:
    src = _png(_rows(4, 4), 5)  # IHDR says 5 wide; the rows hold 4
    with pytest.raises(ImageError, match="IDAT holds"):
        decode(src)


def test_run_writes_each_image(tmp_path: Path) -> None:
    src, dest = tmp_path / "preview", tmp_path / "img"
    src.mkdir()
    rows = _rows(16, 16)
    for name in ("a", "b"):
        (src / f"{name}.png").write_bytes(_png(rows, 16))
    assert run(["a", "b"], src, dest) == 0
    for name in ("a", "b"):
        assert decode((dest / f"{name}.png").read_bytes()) == (16, 16, rows)


def test_run_refuses_a_missing_preview_and_writes_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    src, dest = tmp_path / "preview", tmp_path / "img"
    src.mkdir()
    (src / "a.png").write_bytes(_png(_rows(4, 4), 4))
    with caplog.at_level(logging.ERROR):
        assert run(["a", "b"], src, dest) == 1
    assert "run make fw-preview" in caplog.text
    assert "b" in caplog.text
    assert not dest.exists()


def test_run_refuses_a_file_that_is_not_the_expected_png(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    src, dest = tmp_path / "preview", tmp_path / "img"
    src.mkdir()
    (src / "a.png").write_bytes(_png(_rows(4, 4), 4))
    (src / "b.png").write_bytes(b"not a png")
    with caplog.at_level(logging.ERROR):
        assert run(["a", "b"], src, dest) == 1
    assert "b.png: not a PNG" in caplog.text
    assert not dest.exists()


def test_names_are_the_composite_and_eight_far_pages_in_order() -> None:
    assert NAMES[0] == "pages"
    far = NAMES[1:]
    assert len(far) == 8
    pages = [n for n in far if n != "far-no-data"]
    assert [int(n.split("-")[2]) for n in pages] == list(range(7))
    assert far[-1] == "far-no-data"
