from __future__ import annotations

import struct
import zlib

import pytest
from readme_image import SIGNATURE, chunk, chunks, recompress


def _stored_png(width: int, height: int) -> bytes:
    raw = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        SIGNATURE
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 0))
        + chunk(b"IEND", b"")
    )


def test_recompress_keeps_the_pixels_and_shrinks() -> None:
    src = _stored_png(64, 64)
    out = recompress(src)
    assert len(out) < len(src)
    src_raw = zlib.decompress(dict(chunks(src))[b"IDAT"])
    out_raw = zlib.decompress(dict(chunks(out))[b"IDAT"])
    assert out_raw == src_raw
    assert [k for k, _ in chunks(out)] == [b"IHDR", b"IDAT", b"IEND"]


def test_rejects_a_non_png() -> None:
    with pytest.raises(ValueError, match="not a PNG"):
        chunks(b"GIF89a....")
