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
