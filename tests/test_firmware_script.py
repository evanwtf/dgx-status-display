"""The firmware Docker wrapper: pinned image, ownership restored, exit code kept."""

from __future__ import annotations

from pathlib import Path

from firmware import COMMANDS, IDF_IMAGE, docker_argv


def test_image_is_pinned_by_tag_and_digest() -> None:
    assert IDF_IMAGE.startswith("espressif/idf:v5.5.6@sha256:")
    assert len(IDF_IMAGE.split("@sha256:")[1]) == 64


def test_argv_runs_as_the_calling_user() -> None:
    argv = docker_argv(COMMANDS["build"], Path("/repo"), 1000, 1001)
    assert argv[:4] == ["docker", "run", "--rm", "--init"]
    assert argv[argv.index("-u") + 1] == "1000:1001"
    assert "HOME=/tmp" in argv
    tmpfs = argv[argv.index("--tmpfs") + 1]
    assert tmpfs.startswith("/opt/esp/root_managed_components:")
    assert "uid=1000,gid=1001" in tmpfs
    assert "/repo:/project" in argv
    assert IDF_IMAGE in argv
    assert argv[-1] == "cd firmware && idf.py build"
    assert not any("chown" in a for a in argv)


def test_no_step_touches_a_serial_port() -> None:
    """Flashing stays a manual step. No --device, no flash, no monitor."""
    for cmd in COMMANDS.values():
        argv = docker_argv(cmd, Path("/repo"), 1, 1)
        joined = " ".join(argv)
        assert "--device" not in joined
        assert "flash" not in joined
        assert "monitor" not in joined
        assert "/dev/tty" not in joined


def test_readme_uses_the_same_image_pin() -> None:
    """The README flash commands and the build must use one image."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert f"export IDF_IMAGE={IDF_IMAGE}" in readme


def test_preview_step_renders_into_the_ignored_preview_dir() -> None:
    assert "make -C test/preview" in COMMANDS["preview"]
    gitignore = (Path(__file__).parent.parent / ".gitignore").read_text().splitlines()
    assert "firmware/preview/" in gitignore
