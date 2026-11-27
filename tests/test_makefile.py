"""The Makefile: a thin wrapper that lists its targets, guards the board, and
never repeats the image pin. Every run is `make -n`: no recipe runs."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from firmware import IDF_IMAGE

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
BOARD_TARGETS = ("backup", "flash", "flash-merged", "coredump", "monitor")


# Variables the Makefile reads; a value in the caller's environment must not
# leak into a test.
MAKE_VARS = {"PORT", "ELF", "VERSION", "NO_BACKUP", "FORCE", "SERVICE_USER"}


def make(
    cwd: Path, *args: str, makefile: Path = MAKEFILE, **env: str
) -> subprocess.CompletedProcess[str]:
    """`make -f Makefile ARGS` from cwd, with none of MAKE_VARS set."""
    clean = {k: v for k, v in os.environ.items() if k not in MAKE_VARS}
    clean.pop("MAKEFLAGS", None)
    clean.pop("MAKELEVEL", None)
    return subprocess.run(
        ["make", "-f", str(makefile), *args],
        cwd=cwd,
        env={**clean, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def help_targets(tmp_path: Path) -> list[str]:
    out = make(tmp_path, "help")
    assert out.returncode == 0, out.stderr
    return re.findall(r"^  ([a-z][a-z0-9-]*) ", out.stdout, re.MULTILINE)


def phony_targets() -> set[str]:
    text = MAKEFILE.read_text().replace("\\\n", " ")
    line = next(ln for ln in text.splitlines() if ln.startswith(".PHONY:"))
    return set(line.removeprefix(".PHONY:").split())


@pytest.fixture
def fake_port(tmp_path: Path) -> str:
    port = tmp_path / "ttyFAKE"
    port.touch()
    return str(port)


def test_help_is_the_default_target(tmp_path: Path) -> None:
    bare = make(tmp_path)
    assert bare.returncode == 0, bare.stderr
    assert bare.stdout == make(tmp_path, "help").stdout


def test_help_lists_every_phony_target_once(tmp_path: Path) -> None:
    listed = help_targets(tmp_path)
    assert len(listed) == len(set(listed))
    assert set(listed) == phony_targets()
    for wanted in (
        "setup",
        "check",
        "test",
        "fw-test",
        "fw-build",
        "fw-preview",
        "fw-merge",
        "fonts-check",
        "readme-images",
        *BOARD_TARGETS,
        "run",
        "dry-run",
        "install-service",
        "uninstall-service",
        "release-check",
        "clean",
    ):
        assert wanted in listed


def test_every_target_dry_runs_from_another_directory(
    tmp_path: Path, fake_port: str
) -> None:
    for target in help_targets(tmp_path):
        out = make(
            tmp_path,
            "-n",
            target,
            PORT=fake_port,
            VERSION="0.0.2",
            SERVICE_USER="someone",
        )
        assert out.returncode == 0, f"{target}: {out.stderr}"


@pytest.mark.parametrize("target", BOARD_TARGETS)
def test_board_targets_refuse_without_a_port(tmp_path: Path, target: str) -> None:
    out = make(tmp_path, "-n", target)
    assert out.returncode != 0
    assert "PORT is not set" in out.stderr
    # The guard stops the target before any line, even under -n.
    assert "firmware.py" not in out.stdout


@pytest.mark.parametrize("target", BOARD_TARGETS)
def test_board_targets_refuse_a_port_that_is_not_one_device(
    tmp_path: Path, target: str
) -> None:
    missing = make(tmp_path, "-n", target, PORT=str(tmp_path / "nothing"))
    assert missing.returncode != 0
    assert "matches 0 devices" in missing.stderr
    (tmp_path / "ttyA").touch()
    (tmp_path / "ttyB").touch()
    two = make(tmp_path, "-n", target, PORT=str(tmp_path / "tty*"))
    assert two.returncode != 0
    assert "matches 2 devices" in two.stderr


def test_board_targets_pass_the_port_to_the_script(
    tmp_path: Path, fake_port: str
) -> None:
    out = make(tmp_path, "-n", "flash", PORT=fake_port)
    assert f'scripts/firmware.py flash --port "{fake_port}"' in out.stdout
    glob = make(tmp_path, "-n", "flash", PORT=str(tmp_path / "ttyF*"))
    assert f'--port "{fake_port}"' in glob.stdout
    elf = make(tmp_path, "-n", "coredump", PORT=fake_port, ELF="firmware/elf/x.elf")
    assert '--elf "firmware/elf/x.elf"' in elf.stdout


def test_release_check_refuses_without_a_version(tmp_path: Path) -> None:
    out = make(tmp_path, "-n", "release-check")
    assert out.returncode != 0
    assert "VERSION is not set" in out.stderr


def test_makefile_never_repeats_the_image_pin() -> None:
    text = MAKEFILE.read_text()
    digest = IDF_IMAGE.split("@sha256:")[1]
    assert digest not in text
    assert "sha256:" not in text
    assert "espressif/idf:" not in text


def test_clean_keeps_backups_and_kept_elfs(tmp_path: Path) -> None:
    out = make(tmp_path, "-n", "clean")
    assert out.returncode == 0, out.stderr
    assert "backup" not in out.stdout
    assert "firmware/elf" not in out.stdout
    assert "firmware/coredump" not in out.stdout
    # git clean only with explicit paths, and only git-ignored files (-X).
    assert "git clean -f -d -X -- firmware/build" in out.stdout
    assert " -x" not in out.stdout


def test_install_service_renders_with_the_script_and_prints_sudo(
    tmp_path: Path,
) -> None:
    out = make(tmp_path, "-n", "install-service", SERVICE_USER="someone")
    assert out.returncode == 0, out.stderr
    assert f'scripts/service.py render --user "someone" --repo "{ROOT}"' in out.stdout
    assert '--installed "/etc/systemd/system/dgx-status.service"' in out.stdout
    assert "--force" not in out.stdout
    assert 'echo "+ sudo install -m 0644 $unit' in out.stdout
    assert 'sudo install -m 0644 "$unit" "/etc/systemd/system/' in out.stdout
    # A new unit reaches a running service: enable, then restart.
    lines = out.stdout.splitlines()
    assert lines[-3:-1] == [
        "sudo systemctl enable dgx-status",
        "sudo systemctl restart dgx-status",
    ]
    assert "enable --now" not in out.stdout
    # Make echoes each other sudo line: none is hidden behind @.
    for line in MAKEFILE.read_text().splitlines():
        if line.startswith("\tsudo") or line.startswith("\t@sudo"):
            assert not line.startswith("\t@"), line
    forced = make(tmp_path, "-n", "install-service", SERVICE_USER="someone", FORCE="1")
    assert '--out "$unit" --force' in forced.stdout


@pytest.mark.parametrize("user", ["", "root"])
def test_install_service_refuses_root_or_no_user(tmp_path: Path, user: str) -> None:
    out = make(tmp_path, "-n", "install-service", SERVICE_USER=user)
    assert out.returncode != 0
    assert "not under sudo" in out.stderr
    assert "sudo systemctl" not in out.stdout


def test_flash_passes_no_backup_only_when_asked(tmp_path: Path, fake_port: str) -> None:
    for target in ("flash", "flash-merged"):
        plain = make(tmp_path, "-n", target, PORT=fake_port)
        assert "--no-backup" not in plain.stdout
        forced = make(tmp_path, "-n", target, PORT=fake_port, NO_BACKUP="1")
        assert f'--port "{fake_port}" --no-backup' in forced.stdout


def test_run_checks_a_port_when_one_is_set(tmp_path: Path, fake_port: str) -> None:
    bare = make(tmp_path, "-n", "run")
    assert bare.returncode == 0, bare.stderr
    assert "--device" not in bare.stdout
    web = make(tmp_path, "-n", "run", PORT="3000")  # a web tool's PORT
    assert web.returncode != 0
    assert "matches 0 devices" in web.stderr
    good = make(tmp_path, "-n", "run", PORT=fake_port)
    assert f'dgx-status --device "{fake_port}"' in good.stdout


def test_readme_images_renders_the_preview_first(tmp_path: Path) -> None:
    out = make(tmp_path, "-n", "readme-images")
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert lines[0].endswith("python scripts/firmware.py preview")
    assert lines[1].endswith("python scripts/readme_images.py")


def test_check_runs_the_tested_script(tmp_path: Path) -> None:
    out = make(tmp_path, "-n", "check")
    assert out.returncode == 0, out.stderr
    assert "uv run --frozen python scripts/run_checks.py" in out.stdout


def test_refuses_a_checkout_path_with_a_space(tmp_path: Path) -> None:
    spaced = tmp_path / "a b"
    (spaced / "scripts").mkdir(parents=True)
    shutil.copy(MAKEFILE, spaced / "Makefile")
    (spaced / "scripts" / "firmware.py").touch()
    out = make(tmp_path, "help", makefile=spaced / "Makefile")
    assert out.returncode != 0
    assert "holds a space" in out.stderr
    via_c = make(tmp_path, "-C", str(spaced), "help", makefile=Path("Makefile"))
    assert via_c.returncode != 0
    assert "holds a space" in via_c.stderr


def test_refuses_a_makefile_outside_a_checkout(tmp_path: Path) -> None:
    lone = tmp_path / "lone"
    lone.mkdir()
    shutil.copy(MAKEFILE, lone / "Makefile")
    out = make(tmp_path, "help", makefile=lone / "Makefile")
    assert out.returncode != 0
    assert "scripts/firmware.py" in out.stderr
