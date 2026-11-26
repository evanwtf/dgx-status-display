"""The systemd unit is a template: no user or home path is written into it."""

from __future__ import annotations

import re
from pathlib import Path

UNIT = Path(__file__).resolve().parent.parent / "deploy" / "dgx-status.service"


def render(user: str, repo: str) -> str:
    """The same substitution the README install step does with sed."""
    return UNIT.read_text().replace("@USER@", user).replace("@REPO@", repo)


def test_template_has_placeholders_not_values() -> None:
    text = UNIT.read_text()
    assert "User=@USER@" in text
    assert "WorkingDirectory=@REPO@" in text
    assert "ExecStart=@REPO@/.venv/bin/dgx-status" in text
    assert "DGX_STATUS_DEVICE=" not in text  # the daemon finds the port itself


def test_render_fills_every_placeholder() -> None:
    text = render("someone", "/srv/checkout")
    assert not re.search(r"@[A-Z]+@", text)
    assert "User=someone" in text
    assert "ExecStart=/srv/checkout/.venv/bin/dgx-status" in text
    assert "SupplementaryGroups=dialout" in text


def _settings() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in UNIT.read_text().splitlines():
        if line and not line.startswith(("#", "[")) and "=" in line:
            key, _, value = line.partition("=")
            out.setdefault(key, []).append(value)
    return out


def test_network_reaches_the_host_and_loopback_only() -> None:
    """The LAN, Cluster IP and LLM data need the host network namespace and
    AF_INET; IPAddressDeny/Allow keep the sockets on loopback."""
    s = _settings()
    assert "PrivateNetwork" not in s
    families = s["RestrictAddressFamilies"][0].split()
    assert {"AF_INET", "AF_INET6", "AF_UNIX", "AF_NETLINK"} <= set(families)
    assert s["IPAddressDeny"] == ["any"]
    assert s["IPAddressAllow"] == ["localhost"]
