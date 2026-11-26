"""Generate the firmware's JetBrains Mono bitmap fonts, byte for byte.

    uv run python scripts/gen_fonts.py            # write firmware/main/fonts/*.c
    uv run python scripts/gen_fonts.py --check    # compare; exit 1 on a diff

Inputs, each pinned by hash:
- JetBrains Mono v2.304 (OFL-1.1), the official release zip from
  github.com/JetBrains/JetBrainsMono. The SemiBold weight: thick enough to
  read across a room on black, still open at 36 px. Every value.
- Barlow Semi Condensed Medium 1.422 (OFL-1.1), from github.com/jpt/barlow at
  the tag's commit (the release has no assets). Labels, units, titles and
  the clock: condensed, so words fit in 220 px tiles.
- lv_font_conv 1.5.3 (MIT), LVGL's font converter, the npm tarball. Its
  dependencies are bundled in the tarball, so nothing else is fetched.
- node:22.22-alpine, pinned by digest, runs the converter. Nothing is
  installed on the host.

Downloads are cached in .cache/fonts/ (git-ignored).
"""

from __future__ import annotations

import argparse
import base64
import filecmp
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("gen_fonts")

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "firmware" / "main" / "fonts"
CACHE = ROOT / ".cache" / "fonts"

FONT_ZIP_URL = (
    "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/"
    "JetBrainsMono-2.304.zip"
)
FONT_ZIP_SHA256 = "6f6376c6ed2960ea8a963cd7387ec9d76e3f629125bc33d1fdcd7eb7012f7bbf"
FONT_MEMBER = "fonts/ttf/JetBrainsMono-SemiBold.ttf"
FONT_FILE = "JetBrainsMono-SemiBold.ttf"
FONT_SHA256 = "1b3bfa1ed5665a4ce3f9feb68d2d4e40e70bf8b4b7d9a3edd418f321b4e166a0"

# Tag 1.422 of jpt/barlow is commit 697600fc.
BARLOW_URL = (
    "https://raw.githubusercontent.com/jpt/barlow/"
    "697600fcece1685b0ba409eb82868546def9a84e/fonts/ttf/BarlowSemiCondensed-Medium.ttf"
)
BARLOW_FILE = "BarlowSemiCondensed-Medium.ttf"
BARLOW_SHA256 = "ef5c39de3ac1764a24c7495098672ccc034e39679ccd65d76eed752da59e11e1"

CONV_URL = "https://registry.npmjs.org/lv_font_conv/-/lv_font_conv-1.5.3.tgz"
CONV_SHA256 = "9f64fb8eb553dbab1990402eae74afbafd80b4f39a8314a01484083b6ed1000d"
# The npm registry's own integrity record for this version.
CONV_INTEGRITY = (
    "sha512-0xJQThBOw2iptFccSXrKDIUTQAwr/2zhKjCI1lATIRgZo8uvYRTmenKafW9yTw6G0y5Ay"
    "W00tqGpUtYuTuBIbQ=="
)

NODE_IMAGE = (
    "node:22.22-alpine@sha256:"
    "e58326d0d441090181ac150dc2078d3e2cf6a0d42e809aebba3ef5880935ffdd"
)

EM_DASH = "0x2014"
DEGREE = "0xB0"
DIGITS = "0123456789"
LOWER = "abcdefghijklmnopqrstuvwxyz"


@dataclass(frozen=True)
class FontSpec:
    """One generated font. Glyphs are only what the UI draws in it."""

    name: str
    size: int
    symbols: str = ""
    ranges: tuple[str, ...] = ()
    font: str = FONT_FILE

    @property
    def monospace(self) -> bool:
        return self.font == FONT_FILE

    def argv(self) -> list[str]:
        args = [
            "--font",
            self.font,
            "--size",
            str(self.size),
            "--bpp",
            "4",
            "--format",
            "lvgl",
            "--no-compress",
            "--lv-include",
            "lvgl.h",
            "--lv-font-name",
            self.name,
            "-o",
            f"out/{self.name}.c",
        ]
        if self.symbols:
            args[2:2] = ["--symbols", self.symbols]
        for r in self.ranges:
            args[2:2] = ["-r", r]
        return args


# Sizes follow from the tile: 198 px of text width, and a digit advance of
# 0.6 em. N characters fit at 198 / (0.6 * N) px.
FONTS: tuple[FontSpec, ...] = (
    # 3 characters: %, W, degrees, uptime ("100", "999").
    FontSpec("jbm_num_110", 110, symbols=DIGITS + ".->", ranges=(EM_DASH,)),
    # 4 characters: fans in kRPM ("13.5"). Also the NO DATA age ("2m 13s").
    FontSpec("jbm_num_82", 82, symbols=DIGITS + ".-> dhmsnevr", ranges=(EM_DASH,)),
    # 5 characters: memory ("121.9") and load ("12.34"); short names ("idle").
    FontSpec("jbm_mix_66", 66, symbols=DIGITS + ".->_+" + LOWER, ranges=(EM_DASH,)),
    # Longer GPU occupant names: 9 characters ("llama.cpp").
    FontSpec("jbm_text_36", 36, ranges=("0x20-0x7E", EM_DASH)),
    # Barlow: the words around the values.
    FontSpec("brl_24", 24, ranges=("0x20-0x7E", DEGREE, EM_DASH), font=BARLOW_FILE),
    FontSpec("brl_30", 30, ranges=("0x20-0x7E", DEGREE, EM_DASH), font=BARLOW_FILE),
    # Page titles only: GPU, Cooling, Host, All temperatures, Network,
    # Storage, LLM.
    FontSpec("brl_32", 32, symbols="GPUCoolingHstAl temprauNwkSLM", font=BARLOW_FILE),
    # The NO DATA headline only.
    FontSpec("brl_44", 44, symbols="No data from the DGX", font=BARLOW_FILE),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def npm_integrity(path: Path) -> str:
    digest = hashlib.sha512(path.read_bytes()).digest()
    return "sha512-" + base64.b64encode(digest).decode()


def fetch(url: str, dest: Path, want_sha256: str) -> None:
    """Download once; refuse a file whose hash differs from the pin."""
    if not dest.exists():
        logger.info("downloading %s", url)
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
        tmp.rename(dest)
    got = sha256(dest)
    if got != want_sha256:
        dest.unlink()
        raise SystemExit(f"{dest.name}: sha256 {got}, expected {want_sha256}")


def prepare(cache: Path) -> None:
    """Fetch and unpack the pinned inputs into cache/."""
    cache.mkdir(parents=True, exist_ok=True)
    zip_path = cache / "JetBrainsMono-2.304.zip"
    fetch(FONT_ZIP_URL, zip_path, FONT_ZIP_SHA256)
    with zipfile.ZipFile(zip_path) as z:
        (cache / FONT_FILE).write_bytes(z.read(FONT_MEMBER))
    if sha256(cache / FONT_FILE) != FONT_SHA256:
        raise SystemExit(f"{FONT_FILE}: unexpected sha256")
    fetch(BARLOW_URL, cache / BARLOW_FILE, BARLOW_SHA256)

    tgz = cache / "lv_font_conv-1.5.3.tgz"
    fetch(CONV_URL, tgz, CONV_SHA256)
    if npm_integrity(tgz) != CONV_INTEGRITY:
        raise SystemExit("lv_font_conv tarball: npm integrity mismatch")
    conv = cache / "conv"
    if not (conv / "package" / "lv_font_conv.js").exists():
        shutil.rmtree(conv, ignore_errors=True)
        with tarfile.open(tgz) as t:
            t.extractall(conv, filter="data")


def docker_argv(cache: Path, spec: FontSpec, uid: int, gid: int) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--init",
        "--network=none",
        "-u",
        f"{uid}:{gid}",
        "-v",
        f"{cache}:/w",
        "-w",
        "/w",
        NODE_IMAGE,
        "node",
        "conv/package/lv_font_conv.js",
        *spec.argv(),
    ]


def generate(cache: Path) -> Path:
    out = cache / "out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    for spec in FONTS:
        logger.info("generating %s (%d px)", spec.name, spec.size)
        subprocess.run(docker_argv(cache, spec, os.getuid(), os.getgid()), check=True)
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="compare with the committed files"
    )
    args = parser.parse_args(argv)

    prepare(CACHE)
    out = generate(CACHE)
    names = [f"{s.name}.c" for s in FONTS]
    if args.check:
        bad = [
            n
            for n in names
            if not (OUT_DIR / n).exists() or not filecmp.cmp(out / n, OUT_DIR / n)
        ]
        for n in bad:
            logger.error("differs from a fresh generation: firmware/main/fonts/%s", n)
        if bad:
            return 1
        logger.info("all %d fonts match", len(names))
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copyfile(out / n, OUT_DIR / n)
        logger.info(
            "wrote firmware/main/fonts/%s (%d bytes)", n, (OUT_DIR / n).stat().st_size
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
