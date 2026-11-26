"""The firmware Docker wrapper: pinned image, ownership restored, exit code kept."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path

import firmware
import pytest
from firmware import (
    BACKUP,
    BOARD_STEPS,
    COMMANDS,
    ESPTOOL_VERSION,
    FLASH_SIZE,
    IDF_IMAGE,
    IDF_VERSION,
    MERGED,
    archive_elf,
    backup_problem,
    board_argv,
    board_command,
    coredump_argv,
    docker_argv,
    resolve_port,
    write_sha256,
)


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
    """Flashing stays a manual step. No --device, no port, no flash command."""
    for cmd in COMMANDS.values():
        argv = docker_argv(cmd, Path("/repo"), 1, 1)
        joined = " ".join(argv)
        # "flash_args" is a file the merge step reads; any other "flash" is not.
        assert "flash" not in joined.replace("flash_args", "")
        assert "--privileged" not in joined
        assert "--device" not in joined
        assert "/dev/" not in joined
        assert "--port" not in joined
        assert " -p " not in joined
        for word in ("write_flash", "read_flash", "erase", "idf.py flash", "monitor"):
            assert word not in joined


def test_merge_step_merges_the_build_from_flash_args() -> None:
    """One image for 0x0, made from the same file list the build writes."""
    cmd = COMMANDS["merge"]
    assert cmd.startswith("cd firmware/build && esptool.py --chip esp32c6 merge_bin")
    assert cmd.endswith(f"-o {Path(MERGED).name} @flash_args")
    assert Path(MERGED).parent.as_posix() == "firmware/build"


def test_readme_uses_the_same_image_pin() -> None:
    """The README flash commands and the build must use one image."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert f"export IDF_IMAGE={IDF_IMAGE}" in readme


def test_readme_repeats_every_version_fact_from_the_pin() -> None:
    """A pin bump fails here until the README follows it."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert IDF_IMAGE.startswith(f"espressif/idf:{IDF_VERSION}@")
    assert set(re.findall(r"espressif/idf:(v[0-9.]+)", readme)) == {IDF_VERSION}
    assert f"exactly {IDF_VERSION}" in readme
    assert f"ESP-IDF {IDF_VERSION}" in readme
    assert set(re.findall(r"esptool==([0-9.]+)", readme)) == {ESPTOOL_VERSION}
    assert f"({ESPTOOL_VERSION})" in readme


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


ESPTOOL = "esptool.py --chip esp32c6 -p /dev/ttyACM0 -b 460800"


def test_flash_writes_the_build_with_flash_args() -> None:
    assert board_command("flash", "/dev/ttyACM0") == (
        f"cd firmware/build && {ESPTOOL}"
        " --before default_reset --after hard_reset write_flash @flash_args"
    )


def test_flash_merged_writes_the_merged_image_at_zero() -> None:
    assert board_command("flash-merged", "/dev/ttyACM0") == (
        f"cd firmware/build && {ESPTOOL}"
        " --before default_reset --after hard_reset write_flash 0x0"
        f" {Path(MERGED).name}"
    )


def test_backup_reads_all_16_mb_then_verifies() -> None:
    assert FLASH_SIZE == 16 * 1024 * 1024
    part = f"{BACKUP}.part"
    assert board_command("backup", "/dev/ttyACM0") == " && ".join(
        [
            f"{ESPTOOL} flash_id",
            f"{ESPTOOL} get_security_info",
            f"{ESPTOOL} read_flash 0 0x1000000 {part}",
            f"{ESPTOOL} verify_flash 0 {part}",
        ]
    )
    assert BACKUP == "backup/factory-16MB.bin"
    for word in ("write_flash", "erase"):
        assert word not in board_command("backup", "/dev/ttyACM0")


def test_monitor_does_not_reset_the_chip() -> None:
    assert board_command("monitor", "/dev/ttyACM0") == (
        "cd firmware && idf.py -p /dev/ttyACM0 monitor --no-reset"
    )


def test_board_command_refuses_other_steps() -> None:
    with pytest.raises(ValueError):
        board_command("build", "/dev/ttyACM0")


@pytest.mark.parametrize("step", BOARD_STEPS)
def test_board_argv_passes_the_device_only_with_device(step: str) -> None:
    argv = board_argv(step, Path("/repo"), 1000, 1001, "/dev/ttyACM0", 20)
    assert argv[:4] == ["docker", "run", "--rm", "--init"]
    assert argv[4:7] == ["--device=/dev/ttyACM0", "--group-add", "20"]
    assert argv[argv.index("-u") + 1] == "1000:1001"
    assert IDF_IMAGE in argv
    assert argv[-3:-1] == ["bash", "-c"]
    assert argv[-1] == board_command(step, "/dev/ttyACM0")
    joined = shlex.join(argv)
    assert "--privileged" not in joined
    assert "sudo" not in joined
    assert sum(a.startswith("--device") for a in argv) == 1
    assert "-it" not in argv and "-t" not in argv


def test_monitor_argv_is_interactive_only_when_asked() -> None:
    argv = board_argv(
        "monitor", Path("/repo"), 1, 1, "/dev/ttyACM0", 20, interactive=True
    )
    assert argv[4:9] == ["--device=/dev/ttyACM0", "--group-add", "20", "-i", "-t"]


def test_resolve_port_refuses_what_is_not_a_usb_tty(tmp_path: Path) -> None:
    assert resolve_port(str(tmp_path / "missing")) is None
    plain = tmp_path / "ttyX"
    plain.touch()
    assert resolve_port(str(plain)) is None  # a file, not a device
    assert resolve_port("/dev/null") is None  # a device, but not a tty
    (tmp_path / "ttyY").touch()
    assert resolve_port(str(tmp_path / "tty*")) is None  # two matches


def test_resolve_port_takes_a_usb_tty_and_warns_off_by_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(os.path, "realpath", lambda p: "/dev/ttyACM0")
    monkeypatch.setattr(firmware, "is_char_device", lambda p: True)
    by_id = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_X-if00"
    with caplog.at_level(logging.WARNING, logger="firmware"):
        assert resolve_port(by_id) == "/dev/ttyACM0"
    assert not caplog.records
    with caplog.at_level(logging.WARNING, logger="firmware"):
        assert resolve_port("/dev/ttyACM0") == "/dev/ttyACM0"
    assert "not an Espressif port" in caplog.text
    monkeypatch.setattr(os.path, "realpath", lambda p: "/dev/ttyS0")
    assert resolve_port("/dev/ttyS0") is None


def test_board_steps_refuse_without_a_port() -> None:
    for step in BOARD_STEPS:
        with pytest.raises(SystemExit) as e:
            firmware.main([step])
        assert e.value.code == 2


Calls = list[list[str]]


@pytest.fixture
def board_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Calls:
    """A fake checkout and a ready fake port. No container can start: every
    subprocess.call is recorded, and returns 0."""
    calls: Calls = []

    def fake_call(cmd: list[str], **_: object) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(firmware, "REPO", tmp_path)
    monkeypatch.setattr(firmware, "service_active", lambda: False)
    monkeypatch.setattr(firmware, "resolve_port", lambda port: "/dev/ttyACM0")
    monkeypatch.setattr(firmware, "device_gid", lambda dev: 20)
    monkeypatch.setattr(subprocess, "call", fake_call)
    return calls


def make_build(repo: Path) -> None:
    (repo / "firmware/build").mkdir(parents=True)
    (repo / "firmware/build/flash_args").touch()
    (repo / MERGED).write_bytes(b"merged")


def make_backup(repo: Path, data: bytes = b"factory") -> Path:
    backup = repo / BACKUP
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_bytes(data)
    write_sha256(backup)
    return backup


def test_board_step_refuses_while_the_service_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, board_env: Calls
) -> None:
    monkeypatch.setattr(firmware, "service_active", lambda: True)
    make_build(tmp_path)
    make_backup(tmp_path)
    assert firmware.main(["flash", "--port", "/dev/ttyACM0"]) == 2
    assert board_env == []


def test_port_ready_refuses_a_bad_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, board_env: Calls
) -> None:
    monkeypatch.setattr(firmware, "resolve_port", lambda port: None)
    make_build(tmp_path)
    make_backup(tmp_path)
    assert firmware.main(["flash", "--port", "/dev/ttyACM0"]) == 2
    assert board_env == []


def test_flash_refuses_without_a_build(tmp_path: Path, board_env: Calls) -> None:
    make_backup(tmp_path)
    assert firmware.main(["flash", "--port", "/dev/ttyACM0"]) == 2
    assert firmware.main(["flash-merged", "--port", "/dev/ttyACM0"]) == 2
    assert board_env == []


@pytest.mark.parametrize("step", ["flash", "flash-merged"])
def test_flash_refuses_without_a_backup(
    tmp_path: Path, board_env: Calls, step: str
) -> None:
    make_build(tmp_path)
    assert backup_problem(tmp_path) is not None
    assert firmware.main([step, "--port", "/dev/ttyACM0"]) == 2
    # The backup without its .sha256 is not enough either.
    (tmp_path / "backup").mkdir()
    (tmp_path / BACKUP).write_bytes(b"factory")
    assert firmware.main([step, "--port", "/dev/ttyACM0"]) == 2
    assert board_env == []


@pytest.mark.parametrize("step", ["flash", "flash-merged"])
def test_flash_refuses_a_backup_that_does_not_match(
    tmp_path: Path, board_env: Calls, step: str
) -> None:
    make_build(tmp_path)
    backup = make_backup(tmp_path)
    backup.write_bytes(b"changed")
    assert "does not match" in (backup_problem(tmp_path) or "")
    assert firmware.main([step, "--port", "/dev/ttyACM0"]) == 2
    assert board_env == []


@pytest.mark.parametrize("step", ["flash", "flash-merged"])
def test_flash_runs_with_a_matching_backup(
    tmp_path: Path, board_env: Calls, step: str
) -> None:
    make_build(tmp_path)
    make_backup(tmp_path)
    assert backup_problem(tmp_path) is None
    assert firmware.main([step, "--port", "/dev/ttyACM0"]) == 0
    assert len(board_env) == 1
    assert board_env[0][-1] == board_command(step, "/dev/ttyACM0")


@pytest.mark.parametrize("step", ["flash", "flash-merged"])
def test_no_backup_overrides_the_backup_check(
    tmp_path: Path, board_env: Calls, step: str, caplog: pytest.LogCaptureFixture
) -> None:
    make_build(tmp_path)
    with caplog.at_level(logging.WARNING, logger="firmware"):
        assert firmware.main([step, "--port", "/dev/ttyACM0", "--no-backup"]) == 0
    assert "no factory backup" in caplog.text
    assert len(board_env) == 1


def test_no_backup_is_for_flash_steps_only() -> None:
    for argv in (["backup", "--port", "/dev/ttyACM0"], ["build"]):
        with pytest.raises(SystemExit):
            firmware.main([*argv, "--no-backup"])


def test_flash_merged_warns_that_it_erases_the_settings(
    tmp_path: Path, board_env: Calls, caplog: pytest.LogCaptureFixture
) -> None:
    make_build(tmp_path)
    make_backup(tmp_path)
    with caplog.at_level(logging.WARNING, logger="firmware"):
        assert firmware.main(["flash-merged", "--port", "/dev/ttyACM0"]) == 0
    assert "erases the board's settings (rotation, chart style)" in caplog.text


def test_backup_never_replaces_a_backup(tmp_path: Path, board_env: Calls) -> None:
    old = make_backup(tmp_path)
    assert firmware.main(["backup", "--port", "/dev/ttyACM0"]) == 2
    assert old.read_bytes() == b"factory"
    assert board_env == []


def full_image() -> bytes:
    return bytes(range(256)) * (FLASH_SIZE // 256)


def fake_read(monkeypatch: pytest.MonkeyPatch, data: bytes, calls: Calls) -> None:
    """subprocess.call that writes `data` to the .part file the command names."""

    def call(cmd: list[str], **_: object) -> int:
        calls.append(cmd)
        part = re.search(r"read_flash 0 0x1000000 (\S+)", cmd[-1])
        assert part is not None
        (firmware.REPO / part.group(1)).write_bytes(data)
        return 0

    monkeypatch.setattr(subprocess, "call", call)


def test_backup_keeps_a_full_read_and_its_sha256(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, board_env: Calls
) -> None:
    image = full_image()
    fake_read(monkeypatch, image, board_env)
    assert firmware.main(["backup", "--port", "/dev/ttyACM0"]) == 0
    kept = tmp_path / BACKUP
    assert kept.read_bytes() == image
    assert list((tmp_path / "backup").glob("*.part")) == []
    sums = (tmp_path / f"{BACKUP}.sha256").read_text()
    assert sums == f"{hashlib.sha256(image).hexdigest()}  factory-16MB.bin\n"
    # The .part name is unique to this run.
    assert f"{BACKUP}.{os.getpid()}.part" in board_env[0][-1]


def test_backup_never_replaces_one_that_appears_during_the_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, board_env: Calls
) -> None:
    image = full_image()

    def call(cmd: list[str], **_: object) -> int:
        board_env.append(cmd)
        part = re.search(r"read_flash 0 0x1000000 (\S+)", cmd[-1])
        assert part is not None
        (tmp_path / part.group(1)).write_bytes(image)
        (tmp_path / BACKUP).write_bytes(b"another run's backup")
        return 0

    monkeypatch.setattr(subprocess, "call", call)
    assert firmware.main(["backup", "--port", "/dev/ttyACM0"]) == 1
    assert (tmp_path / BACKUP).read_bytes() == b"another run's backup"
    # The read is kept, not lost.
    parts = list((tmp_path / "backup").glob("*.part"))
    assert len(parts) == 1
    assert parts[0].read_bytes() == image


def test_backup_refuses_a_short_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, board_env: Calls
) -> None:
    fake_read(monkeypatch, b"short", board_env)
    assert firmware.main(["backup", "--port", "/dev/ttyACM0"]) == 1
    assert not (tmp_path / BACKUP).exists()


def test_write_sha256_matches_sha256sum_format(tmp_path: Path) -> None:
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    out = write_sha256(f)
    assert out.name == "a.bin.sha256"
    assert out.read_text() == f"{hashlib.sha256(b'abc').hexdigest()}  a.bin\n"


def test_other_steps_take_no_port() -> None:
    with pytest.raises(SystemExit):
        firmware.main(["build", "--port", "/dev/ttyACM0"])
