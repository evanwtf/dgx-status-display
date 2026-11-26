"""scripts/service.py: render the unit safely, or refuse."""

from __future__ import annotations

from pathlib import Path

import pytest
import service
from service import ServiceError, check_installed, render, working_directory

TEMPLATE = (
    Path(__file__).resolve().parent.parent / "deploy/dgx-status.service"
).read_text()


def test_render_fills_the_user_and_the_checkout() -> None:
    text = render(TEMPLATE, "someone", "/srv/checkout")
    assert "User=someone" in text
    assert "WorkingDirectory=/srv/checkout" in text
    assert "@USER@" not in text and "@REPO@" not in text


@pytest.mark.parametrize("user", ["", "root"])
def test_render_refuses_root_or_no_user(user: str) -> None:
    with pytest.raises(ServiceError, match="not under sudo"):
        render(TEMPLATE, user, "/srv/checkout")


def test_render_refuses_an_odd_user_name() -> None:
    with pytest.raises(ServiceError, match="plain user name"):
        render(TEMPLATE, "a|b", "/srv/checkout")


@pytest.mark.parametrize("char", ["|", "&", "'", "\\", " ", "\n"])
def test_render_refuses_an_unsafe_checkout_path(char: str) -> None:
    with pytest.raises(ServiceError, match="the checkout path holds"):
        render(TEMPLATE, "someone", f"/srv/a{char}b")


def test_render_refuses_a_relative_checkout_path() -> None:
    with pytest.raises(ServiceError, match="not an absolute path"):
        render(TEMPLATE, "someone", "srv/checkout")


def test_render_refuses_a_leftover_placeholder() -> None:
    with pytest.raises(ServiceError, match="@NEW@"):
        render(TEMPLATE + "\nX=@NEW@\n", "someone", "/srv/checkout")


def test_working_directory_reads_the_unit() -> None:
    assert working_directory("[Service]\nWorkingDirectory=/a/b\n") == "/a/b"
    assert working_directory("[Service]\n") is None


def test_check_installed(tmp_path: Path) -> None:
    unit = tmp_path / "dgx-status.service"
    check_installed(unit, "/srv/new", force=False)  # nothing installed
    unit.write_text(render(TEMPLATE, "someone", "/srv/new"))
    check_installed(unit, "/srv/new", force=False)  # the same checkout
    with pytest.raises(ServiceError, match="FORCE=1"):
        check_installed(unit, "/srv/other", force=False)
    check_installed(unit, "/srv/other", force=True)


def test_main_writes_the_unit_or_nothing(tmp_path: Path) -> None:
    out = tmp_path / "unit"
    argv = ["render", "--user", "someone", "--repo", "/srv/checkout"]
    assert service.main([*argv, "--out", str(out)]) == 0
    assert "User=someone" in out.read_text()

    refused = tmp_path / "refused"
    root = ["render", "--user", "root", "--repo", "/srv/x", "--out", str(refused)]
    assert service.main(root) == 1
    assert not refused.exists()

    installed = tmp_path / "installed"
    installed.write_text("[Service]\nWorkingDirectory=/srv/other\n")
    other = [*argv, "--installed", str(installed), "--out", str(refused)]
    assert service.main(other) == 1
    assert not refused.exists()
    assert service.main([*other, "--force"]) == 0
