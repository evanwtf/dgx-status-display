"""Refuse private infrastructure in a public repository.

    python scripts/check_public.py            # scan the repo; exit 1 on a hit

The rule is baseline's public-repos convention
(https://github.com/evanwtf/baseline/blob/main/conventions/public-repos.md):
no LAN addresses (10.x is allowed: the DGX fabric), private DNS names,
home-directory paths, device identifiers (MACs, board serials), and no name
from the local deny list.

It scans every tracked file and every new file git does not ignore, so a leak
fails before the commit. A hit is reported as `file:line (kind)`, never with
the matched text: the CI log is public too.

Deny list: `.public-denylist` at the repo root (git-ignored, never
committed), one host or name per line; `#` starts a comment. The
PUBLIC_DENYLIST environment variable names another file. Use it for private
names that look ordinary.

Stdlib only, so the pre-commit hook runs from a bare `git commit`. This file
holds the patterns and is the one file the scan skips.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger("check_public")

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()
DENYLIST_NAME = ".public-denylist"

_HEX2 = "[0-9A-Fa-f]{2}"
PATTERNS: dict[str, re.Pattern[str]] = {
    "LAN address": re.compile(
        r"\b(?:192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
    ),
    "private DNS name": re.compile(
        r"\b[A-Za-z0-9-]+\.(?:internal|lan|local|home\.arpa|localdomain)\b"
    ),
    "home directory": re.compile(r"(?:/home|/Users)/[A-Za-z0-9_][A-Za-z0-9_.-]*"),
    "user home": re.compile(r"~[A-Za-z][A-Za-z0-9_-]*/"),
    # The /dev/serial/by-id/ name of an ESP32 USB-Serial-JTAG port carries the
    # chip's MAC.
    "ESP32 serial": re.compile(
        rf"usb-Espressif_USB_JTAG_serial_debug_unit_(?:{_HEX2}[:-]?){{5}}{_HEX2}"
    ),
    "MAC address": re.compile(
        rf"\b(?:{_HEX2}:){{5}}{_HEX2}\b|\b(?:{_HEX2}-){{5}}{_HEX2}\b"
    ),
    # Twelve hex digits alone are too common (sizes, short SHAs). Flag them
    # only next to a word that says MAC or serial.
    "bare MAC": re.compile(
        r"(?i:mac|serial|ether|unit)[^\n]{0,16}?\b[0-9A-Fa-f]{12}\b"
    ),
}


def load_denylist(path: Path) -> list[re.Pattern[str]]:
    """One name per line, matched whole-word and case-insensitive."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    names = [ln.split("#", 1)[0].strip() for ln in lines]
    return [
        re.compile(rf"(?<![\w.-]){re.escape(n)}(?![\w-])", re.IGNORECASE)
        for n in names
        if n
    ]


def scan_text(text: str, deny: Iterable[re.Pattern[str]] = ()) -> list[tuple[int, str]]:
    """(line number, kind) for each line that matches."""
    deny = list(deny)
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in PATTERNS.items():
            if pattern.search(line):
                hits.append((n, kind))
        if any(d.search(line) for d in deny):
            hits.append((n, "denylisted name"))
    return hits


def scan_files(
    root: Path, paths: Iterable[str], deny: Iterable[re.Pattern[str]] = ()
) -> list[str]:
    """`path:line (kind)` for each hit. Never the matched text."""
    deny = list(deny)
    out = []
    for rel in paths:
        if rel == SELF:
            continue
        path = root / rel
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        out += [f"{rel}:{n} ({kind})" for n, kind in scan_text(text, deny)]
    return out


def repo_files(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [p for p in result.stdout.decode().split("\0") if p]


def denylist_path(root: Path) -> Path:
    env = os.environ.get("PUBLIC_DENYLIST")
    return Path(env) if env else root / DENYLIST_NAME


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    del argv
    hits = scan_files(ROOT, repo_files(ROOT), load_denylist(denylist_path(ROOT)))
    for hit in hits:
        logger.error("private identifier: %s", hit)
    if hits:
        logger.error("%d hit(s). Describe machines by role; see AGENTS.md.", len(hits))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
