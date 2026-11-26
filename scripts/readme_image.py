"""Copy the preview's composite into docs/img/, recompressed.

    uv run python scripts/firmware.py preview
    uv run python scripts/readme_image.py

The preview's PNG writer stores pixels without compression, to stay a few
lines of C. This re-encodes the image data with zlib (stdlib only).
"""

from __future__ import annotations

import logging
import struct
import sys
import zlib
from pathlib import Path

logger = logging.getLogger("readme_image")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "firmware" / "preview" / "pages.png"
DEST = ROOT / "docs" / "img" / "pages.png"
SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(SIGNATURE):
        raise ValueError("not a PNG file")
    out = []
    pos = len(SIGNATURE)
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        out.append((kind, data[pos + 8 : pos + 8 + n]))
        pos += 12 + n
    return out


def chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def recompress(data: bytes) -> bytes:
    """The same image, with the IDAT stream at zlib level 9."""
    parts = chunks(data)
    raw = zlib.decompress(b"".join(body for kind, body in parts if kind == b"IDAT"))
    out = [SIGNATURE]
    for kind, body in parts:
        if kind == b"IDAT":
            continue
        if kind == b"IEND":
            out.append(chunk(b"IDAT", zlib.compress(raw, 9)))
        out.append(chunk(kind, body))
    return b"".join(out)


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(recompress(SRC.read_bytes()))
    logger.info("wrote %s (%d bytes)", DEST.relative_to(ROOT), DEST.stat().st_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
