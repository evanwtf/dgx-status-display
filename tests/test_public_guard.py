"""This repository is public: no file may name private infrastructure.

The patterns live in scripts/check_public.py (also a pre-commit hook). This
test runs the same scan in the default suite and proves each pattern works.
Examples are built by concatenation, so this file holds no real match.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from check_public import (
    DENYLIST_NAME,
    ROOT,
    SELF,
    denylist_path,
    load_denylist,
    main,
    repo_files,
    scan_files,
    scan_text,
)


def test_no_file_names_private_infrastructure() -> None:
    files = repo_files(ROOT)
    assert len(files) > 50  # the scan really sees the repo
    hits = scan_files(ROOT, files, load_denylist(denylist_path(ROOT)))
    assert not hits, "private identifiers (file:line): " + ", ".join(hits)


def test_denylist_is_git_ignored() -> None:
    gitignore = (ROOT / ".gitignore").read_text().splitlines()
    assert f"/{DENYLIST_NAME}" in gitignore


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("ssh to 192." + "168.1.20 first", "LAN address"),
        ("docker net 172." + "17.0.2", "LAN address"),
        ("vpn 172." + "31.255.1", "LAN address"),
        ("host gpu-box" + "." + "internal:9400", "private DNS name"),
        ("printer." + "lan", "private DNS name"),
        ("nas.home" + ".arpa", "private DNS name"),
        ("box." + "localdomain", "private DNS name"),
        ("mdns name spark." + "local", "private DNS name"),
        ("ExecStart=/ho" + "me/someone/repo/.venv/bin/x", "home directory"),
        ("open /Us" + "ers/someone/Desktop", "home directory"),
        ("cd /ho" + "me/someone", "home directory"),
        ("see ~some" + "one/notes", "user home"),
        (
            "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_"
            + "AA:BB:CC:"
            + "dd:ee:ff-if00",
            "ESP32 serial",
        ),
        ("mac " + "00:1a:2b:" + "3c:4d:5e", "MAC address"),
        ("mac " + "00-1a-2b-" + "3c-4d-5e", "MAC address"),
        ("MAC=" + "001a2b" + "3c4d5e", "bare MAC"),
        ("serial number " + "A1B2C3" + "D4E5F6", "bare MAC"),
    ],
)
def test_each_pattern_catches_its_example(text: str, kind: str) -> None:
    assert (1, kind) in scan_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "10.0.0.2 is the DGX fabric",  # allowed by the convention
        "172.15.0.1 and 172.32.0.1 are public",
        "see docs/plan.md and the landing page",
        "the internal state",
        "pip installs into ~/.local/bin",
        "~/git/dgx-status-display",
        "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00",
        "logged at 22:44:57",
        "sha256:bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84",
        "VmallocTotal:   135288315904 kB",
        "a uuid 123e4567-e89b-12d3-a456-426614174000",
        "/home/ alone",
    ],
)
def test_ordinary_text_is_not_flagged(text: str) -> None:
    assert scan_text(text) == []


def test_report_names_file_and_line_never_the_text(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("10.0.0.1 ok\nnode9" + "." + "internal here\n")
    hits = scan_files(tmp_path, ["a.md"])
    assert hits == ["a.md:2 (private DNS name)"]
    assert "node9" not in " ".join(hits)


def test_the_guard_skips_only_its_pattern_file(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("node." + "lan\n")
    assert SELF == "scripts/check_public.py"
    assert scan_files(tmp_path, ["x.txt", SELF]) == ["x.txt:1 (private DNS name)"]


def test_denylist_names_are_whole_words(tmp_path: Path) -> None:
    deny_file = tmp_path / "deny"
    deny_file.write_text("# private hosts\nbigbox\n\nRack-7  # the closet\n")
    deny = load_denylist(deny_file)
    assert len(deny) == 2
    assert scan_text("ssh bigbox now", deny) == [(1, "denylisted name")]
    assert scan_text("on rack-7", deny) == [(1, "denylisted name")]
    assert scan_text("bigboxes and rack-70", deny) == []
    assert load_denylist(tmp_path / "missing") == []


def test_denylist_env_overrides_the_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PUBLIC_DENYLIST", str(tmp_path / "d"))
    assert denylist_path(ROOT) == tmp_path / "d"


def test_main_passes_on_this_repo() -> None:
    assert main([]) == 0
