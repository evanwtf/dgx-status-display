"""This repository is public: no file may name private infrastructure.

The rule is baseline's public-repos convention
(https://github.com/evanwtf/baseline/blob/main/conventions/public-repos.md):
no LAN addresses, private DNS names, home-directory paths or device
identifiers. `10.x.x.x` addresses are allowed (the DGX fabric).

The scan covers every tracked file, and every new file git does not ignore,
so a leak fails before it is committed. A failure names the file, line and
kind of match, never the matched text: the CI log is public too.

This file holds the patterns, so it is the one file the scan skips. Its
examples are built by string concatenation.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve().relative_to(ROOT).as_posix()

_HEX2 = "[0-9A-Fa-f]{2}"
PATTERNS: dict[str, re.Pattern[str]] = {
    "LAN address": re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"),
    "private DNS name": re.compile(
        r"\b[A-Za-z0-9-]+\.(?:internal|lan|home\.arpa|localdomain)\b"
    ),
    "home directory": re.compile(r"(?:/home|/Users)/[A-Za-z0-9_.-]+/"),
    # The /dev/serial/by-id/ name of an ESP32 USB-Serial-JTAG port carries the
    # chip's MAC. A bare MAC in any other form is caught too.
    "ESP32 serial": re.compile(
        rf"usb-Espressif_USB_JTAG_serial_debug_unit_(?:{_HEX2}:){{5}}{_HEX2}"
    ),
    "MAC address": re.compile(rf"\b(?:{_HEX2}:){{5}}{_HEX2}\b"),
}


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, kind) for each line that matches a pattern."""
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in PATTERNS.items():
            if pattern.search(line):
                hits.append((n, kind))
    return hits


def scan_files(root: Path, paths: list[str]) -> list[str]:
    """`path:line (kind)` for each hit. Never the matched text."""
    out = []
    for rel in paths:
        if rel == SELF:
            continue
        path = root / rel
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        out += [f"{rel}:{n} ({kind})" for n, kind in scan_text(text)]
    return out


def repo_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return [p for p in result.stdout.decode().split("\0") if p]


def test_no_file_names_private_infrastructure() -> None:
    files = repo_files()
    assert len(files) > 50  # the scan really sees the repo
    hits = scan_files(ROOT, files)
    assert not hits, "private identifiers (file:line): " + ", ".join(hits)


# Examples, built by concatenation so this file does not hold a real match.
@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("ssh to 192." + "168.1.20 first", "LAN address"),
        ("host gpu-box" + "." + "internal:9400", "private DNS name"),
        ("printer." + "lan", "private DNS name"),
        ("nas.home" + ".arpa", "private DNS name"),
        ("box." + "localdomain", "private DNS name"),
        ("ExecStart=/ho" + "me/someone/repo/.venv/bin/x", "home directory"),
        ("open /Us" + "ers/someone/Desktop", "home directory"),
        (
            "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_"
            + "AA:BB:CC:"
            + "dd:ee:ff-if00",
            "ESP32 serial",
        ),
        ("mac " + "00:1a:2b:" + "3c:4d:5e", "MAC address"),
    ],
)
def test_each_pattern_catches_its_example(text: str, kind: str) -> None:
    assert (1, kind) in scan_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "10.0.0.2 is the DGX fabric",  # allowed by the convention
        "see docs/plan.md and the landing page",
        "the internal state",
        "~/git/dgx-status-display",
        "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00",
        "logged at 22:44:57",
        "sha256:bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84",
        "/home/ alone",
    ],
)
def test_ordinary_text_is_not_flagged(text: str) -> None:
    assert scan_text(text) == []


def test_report_names_file_and_line_never_the_text(tmp_path: Path) -> None:
    secret = "10.0.0.1 ok\nnode9" + "." + "internal here\n"
    (tmp_path / "a.md").write_text(secret)
    hits = scan_files(tmp_path, ["a.md"])
    assert hits == ["a.md:2 (private DNS name)"]
    assert "node9" not in " ".join(hits)


def test_the_guard_skips_only_itself(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("node." + "lan\n")
    assert scan_files(tmp_path, ["x.txt", SELF]) == ["x.txt:1 (private DNS name)"]
