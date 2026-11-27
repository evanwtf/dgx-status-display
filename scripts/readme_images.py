"""Copy the README images from the preview into docs/img/, recompressed.

    make readme-images

or, by hand:

    uv run python scripts/firmware.py preview
    uv run python scripts/readme_images.py

The preview's PNG writer (firmware/test/preview/png.c) stores pixels with no
compression, to stay a few lines of C: 8-bit RGB, no interlace, filter None
on each row. This script decodes each image and compresses the same rows with
zlib at level 9 (stdlib only). It checks that the output decodes to the same
pixels before it writes the file. Per-row filters (Sub, Up, Paeth) made these
flat-color images about 40 % larger, so every row keeps filter None.
"""

from __future__ import annotations

import logging
import struct
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path

from check_public import denylist_path, load_denylist, scan_text

logger = logging.getLogger("readme_images")

ROOT = Path(__file__).resolve().parent.parent
PREVIEW = ROOT / "firmware" / "preview"
DEST = ROOT / "docs" / "img"
SIGNATURE = b"\x89PNG\r\n\x1a\n"
BPP = 3  # bytes per pixel: 8-bit RGB

# The images the README shows. The far pages are in carousel order.
NAMES = (
    "pages",
    "far-busy-0-gpu",
    "far-busy-1-gpu-pwr",
    "far-hot-2-gpu-temp",
    "far-busy-3-llm",
    "far-busy-4-model",
    "far-busy-5-memory",
    "far-busy-6-cpu",
    "far-no-data",
)


class ImageError(ValueError):
    """The file is not the PNG format that png.c writes."""


def chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """The (type, body) of each chunk. Refuses a bad signature, length or CRC."""
    if not data.startswith(SIGNATURE):
        raise ImageError("not a PNG file (bad signature)")
    out = []
    pos = len(SIGNATURE)
    while pos < len(data):
        if pos + 12 > len(data):
            raise ImageError("truncated chunk header")
        (n,) = struct.unpack(">I", data[pos : pos + 4])
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + n]
        if len(body) != n or pos + 12 + n > len(data):
            raise ImageError(f"truncated {kind!r} chunk")
        (crc,) = struct.unpack(">I", data[pos + 8 + n : pos + 12 + n])
        if crc != zlib.crc32(kind + body) & 0xFFFFFFFF:
            raise ImageError(f"bad CRC in {kind!r} chunk")
        out.append((kind, body))
        pos += 12 + n
    return out


def chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def decode(data: bytes) -> tuple[int, int, list[bytes]]:
    """(width, height, rows) of an 8-bit RGB, non-interlaced PNG."""
    parts = chunks(data)
    kinds = [k for k, _ in parts]
    if not kinds or kinds[0] != b"IHDR" or kinds[-1] != b"IEND":
        raise ImageError("chunks must start with IHDR and end with IEND")
    if any(k not in (b"IHDR", b"IDAT", b"IEND") for k in kinds):
        raise ImageError(f"unexpected chunks: {sorted(set(kinds))!r}")
    ihdr = parts[0][1]
    if len(ihdr) != 13:
        raise ImageError("bad IHDR length")
    width, height, depth, color, comp, filt, lace = struct.unpack(">IIBBBBB", ihdr)
    if (depth, color, comp, filt, lace) != (8, 2, 0, 0, 0):
        raise ImageError(
            f"want 8-bit RGB, no interlace; got bit depth {depth}, color type "
            f"{color}, compression {comp}, filter {filt}, interlace {lace}"
        )
    try:
        raw = zlib.decompress(b"".join(body for kind, body in parts if kind == b"IDAT"))
    except zlib.error as e:
        raise ImageError(f"bad IDAT stream: {e}") from e
    stride = width * BPP + 1
    if len(raw) != stride * height:
        raise ImageError(f"IDAT holds {len(raw)} bytes, want {stride * height}")
    rows = []
    for y in range(height):
        line = raw[y * stride : (y + 1) * stride]
        if line[0] != 0:
            raise ImageError(f"row {y} has filter {line[0]}; png.c writes None (0)")
        rows.append(line[1:])
    return width, height, rows


def _deflate(raw: bytes, mem_level: int, strategy: int) -> bytes:
    z = zlib.compressobj(9, zlib.DEFLATED, 15, mem_level, strategy)
    return z.compress(raw) + z.flush()


def guard_hits(data: bytes) -> list[tuple[int, str]]:
    """What the public-repo guard (check_public.py) finds in these bytes."""
    text = data.decode("utf-8", errors="replace")
    return scan_text(text, load_denylist(denylist_path(ROOT)))


def encode(width: int, height: int, rows: Sequence[bytes]) -> bytes:
    """An 8-bit RGB PNG: filter None on each row, zlib level 9.

    The public-repo guard scans every file as text, so compressed bytes can
    match a pattern by chance (for example a tilde, a name and a slash).
    This tries a few zlib settings and keeps the smallest output with no
    guard hit.
    """
    raw = b"".join(b"\x00" + row for row in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pngs = sorted(
        (
            SIGNATURE
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", _deflate(raw, mem_level, strategy))
            + chunk(b"IEND", b"")
            for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED)
            for mem_level in (8, 9, 7)
        ),
        key=len,
    )
    for png in pngs:
        if not guard_hits(png):
            return png
    raise ImageError("every zlib setting gives bytes the public-repo guard refuses")


def recompress(data: bytes) -> bytes:
    """The same pixels, smaller. Refuses an output that decodes differently."""
    width, height, rows = decode(data)
    out = encode(width, height, rows)
    if decode(out) != (width, height, rows):
        raise ImageError("the re-encoded image has different pixels")
    return out


def run(names: Sequence[str], src: Path, dest: Path) -> int:
    """Write each image. Refuses (and writes nothing) if one is missing."""
    missing = [n for n in names if not (src / f"{n}.png").is_file()]
    if missing:
        logger.error(
            "no preview image for %s in %s: run make fw-preview",
            ", ".join(missing),
            src,
        )
        return 1
    outputs = {}
    for name in names:
        try:
            outputs[name] = recompress((src / f"{name}.png").read_bytes())
        except ImageError as e:
            logger.error("%s.png: %s", name, e)
            return 1
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (dest / f"{name}.png").write_bytes(data)
        logger.info("wrote %s.png (%d bytes)", name, len(data))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    return run(NAMES, PREVIEW, DEST)


if __name__ == "__main__":
    raise SystemExit(main())
