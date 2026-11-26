"""The firmware Docker wrapper: pinned image, ownership restored, exit code kept."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from firmware import COMMANDS, IDF_IMAGE, archive_elf, coredump_argv, docker_argv


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


def test_coredump_from_the_board_reads_and_never_writes() -> None:
    argv = coredump_argv(
        Path("/repo"),
        1000,
        1000,
        "firmware/build/x.elf",
        device="/dev/ttyACM0",
        device_gid=20,
        save="firmware/coredump/core-1.elf",
    )
    assert "--device=/dev/ttyACM0" in argv
    assert argv[argv.index("--group-add") + 1] == "20"
    assert argv[argv.index("-u") + 1] == "1000:1000"
    cmd = argv[-1]
    assert cmd.startswith(
        "esp-coredump --chip esp32c6 --port /dev/ttyACM0 info_corefile"
    )
    assert "--save-core firmware/coredump/core-1.elf" in cmd
    assert cmd.endswith("firmware/build/x.elf")
    for word in ("write_flash", "erase", "flash ", "monitor"):
        assert word not in cmd


def test_coredump_from_a_file_opens_no_device() -> None:
    argv = coredump_argv(Path("/repo"), 1, 1, "e.elf", core="firmware/coredump/c.elf")
    assert not any(a.startswith("--device") for a in argv)
    assert "--core firmware/coredump/c.elf --core-format elf e.elf" in argv[-1]
    with pytest.raises(ValueError):
        coredump_argv(Path("/repo"), 1, 1, "e.elf")


def test_archive_elf_names_the_copy_by_its_sha(tmp_path: Path) -> None:
    assert archive_elf(tmp_path) is None
    elf = tmp_path / "firmware/build/dgx_status_display.elf"
    elf.parent.mkdir(parents=True)
    elf.write_bytes(b"\x7fELF test")
    kept = archive_elf(tmp_path)
    digest = hashlib.sha256(b"\x7fELF test").hexdigest()[:16]
    assert kept == tmp_path / f"firmware/elf/dgx_status_display-{digest}.elf"
    assert kept.read_bytes() == elf.read_bytes()


def test_kept_elfs_and_cores_are_git_ignored() -> None:
    gitignore = (Path(__file__).parent.parent / ".gitignore").read_text().splitlines()
    assert "firmware/elf/" in gitignore
    assert "firmware/coredump/" in gitignore
