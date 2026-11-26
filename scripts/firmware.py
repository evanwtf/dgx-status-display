"""Build and test the firmware in the pinned ESP-IDF Docker image.

    uv run python scripts/firmware.py build   # idf.py build
    uv run python scripts/firmware.py size    # idf.py size (after build)
    uv run python scripts/firmware.py merge   # one image to flash at 0x0 (after build)
    uv run python scripts/firmware.py test    # host unit tests (make -C firmware/test)
    uv run python scripts/firmware.py preview # render the UI to firmware/preview/*.png
    uv run python scripts/firmware.py coredump --port /dev/ttyACM0
    uv run python scripts/firmware.py coredump --core firmware/coredump/core-X.elf

Board steps (each takes --port; esptool resets the chip; stop the service):

    uv run python scripts/firmware.py flash --port "$PORT"         # @flash_args
    uv run python scripts/firmware.py flash-merged --port "$PORT"  # 0x0; erases NVS
    uv run python scripts/firmware.py backup --port "$PORT"        # read all 16 MB
    uv run python scripts/firmware.py monitor --port "$PORT"       # idf.py monitor

The image is pinned by tag and by digest (IDF_IMAGE). It is multi-arch, so the
same pin builds on this aarch64 DGX and on the x86_64 CI runner. CI calls this
script too, so the pin lives in one place.

The container runs as the calling user (`-u UID:GID`, `HOME=/tmp`, `--init`),
so every file it writes in the checkout is owned by that user. The one place
under /opt/esp the component manager writes, root_managed_components, gets a
tmpfs owned by that user. Nothing else in the image needs to be writable.

`merge` writes firmware/build/dgx_status_display-merged.bin with
`esptool.py merge_bin` from the build's flash_args: the bootloader, the
partition table and the app in one file, 0xFF in the gaps, to flash at 0x0.
The release workflow publishes it.

The steps in COMMANDS leave serial ports alone. Only the board steps
(BOARD_STEPS, and `coredump --port`) pass a device into the container, with
`--device` and the port's group, never `--privileged` and never sudo. Each
refuses while the dgx-status service is active: the daemon holds the port.

`flash` writes the build's parts from firmware/build/flash_args, so the
board's stored settings (NVS) stay. `flash-merged` writes the merged image
at 0x0 (run `merge` first). It erases NVS: use it for a first install.

`backup` reads the whole 16 MB flash to backup/factory-16MB.bin, reads the
chip again to verify it, and writes factory-16MB.bin.sha256 next to it. It
refuses to replace an existing backup. Before the read it logs `flash_id`
and `get_security_info`: check for a 16 MB flash, and secure boot and flash
encryption off.

`monitor` runs `idf.py monitor --no-reset` (Ctrl-] quits).

`build` also keeps a copy of each ELF as firmware/elf/dgx_status_display-<sha>.elf,
named by the first 16 hex digits of its SHA-256. The board's hello line
carries the same 16 digits (`elf`), so the ELF that matches a core dump can
be found after later rebuilds.

`coredump --port DEV` reads the core dump partition with esptool (this resets
the chip; stop the daemon first) and decodes it with esp-coredump against an
ELF. It saves the raw core in firmware/coredump/, which `--core` decodes again
without the board.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("firmware")

IDF_IMAGE = (
    "espressif/idf:v5.5.6"
    "@sha256:bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84"
)
REPO = Path(__file__).resolve().parent.parent
ELF = "firmware/build/dgx_status_display.elf"
MERGED = "firmware/build/dgx_status_display-merged.bin"
ELF_ARCHIVE = "firmware/elf"
CORE_DIR = "firmware/coredump"
FLASH_ARGS = "firmware/build/flash_args"
BACKUP = "backup/factory-16MB.bin"
FLASH_SIZE = 0x1000000  # 16 MB
BAUD = "460800"
SERVICE = "dgx-status"

COMMANDS = {
    "build": "cd firmware && idf.py build",
    "size": "cd firmware && idf.py size",
    "test": "make -C firmware/test",
    # merge_bin opens no port: it only reads the build's files.
    "merge": (
        "cd firmware/build && esptool.py --chip esp32c6 merge_bin"
        " -o dgx_status_display-merged.bin @flash_args"
    ),
    # Host render of the real UI code into firmware/preview/*.png. Needs the
    # LVGL component and sdkconfig.h, which `idf.py reconfigure` provides.
    "preview": (
        "cd firmware && { test -f build/config/sdkconfig.h"
        " && test -d managed_components/lvgl__lvgl || idf.py reconfigure; }"
        ' && make -C test/preview -j"$(nproc)"'
    ),
}


def docker_argv(command: str, repo: Path, uid: int, gid: int) -> list[str]:
    """The `docker run` argv for one step. Pure, so tests can check it."""
    return [
        "docker",
        "run",
        "--rm",
        "--init",
        "-u",
        f"{uid}:{gid}",
        "-e",
        "HOME=/tmp",
        "--tmpfs",
        f"/opt/esp/root_managed_components:uid={uid},gid={gid},mode=0755",
        "-v",
        f"{repo}:/project",
        "-w",
        "/project",
        IDF_IMAGE,
        "bash",
        "-c",
        command,
    ]


def esptool(device: str, *args: str) -> str:
    """One esptool.py call for this chip on `device`, as a shell string."""
    return shlex.join(
        ["esptool.py", "--chip", "esp32c6", "-p", device, "-b", BAUD, *args]
    )


def board_command(step: str, device: str) -> str:
    """The shell command for one board step. Pure, so tests can check it."""
    reset = ("--before", "default_reset", "--after", "hard_reset")
    if step == "flash":
        return "cd firmware/build && " + esptool(
            device, *reset, "write_flash", "@flash_args"
        )
    if step == "flash-merged":
        return "cd firmware/build && " + esptool(
            device, *reset, "write_flash", "0x0", Path(MERGED).name
        )
    if step == "backup":
        part = f"{BACKUP}.part"
        size = f"{FLASH_SIZE:#x}"
        return " && ".join(
            [
                esptool(device, "flash_id"),
                esptool(device, "get_security_info"),
                esptool(device, "read_flash", "0", size, part),
                esptool(device, "verify_flash", "0", part),
            ]
        )
    if step == "monitor":
        return "cd firmware && " + shlex.join(
            ["idf.py", "-p", device, "monitor", "--no-reset"]
        )
    raise ValueError(f"not a board step: {step}")


BOARD_STEPS = ("backup", "flash", "flash-merged", "monitor")


def with_device(
    argv: list[str],
    device: str,
    device_gid: int | None,
    *,
    interactive: bool = False,
) -> list[str]:
    """Pass one serial device into a `docker run` argv from docker_argv."""
    # The port is root:dialout 0660; the container user needs its group.
    extra = [f"--device={device}"]
    if device_gid is not None:
        extra += ["--group-add", str(device_gid)]
    if interactive:
        extra += ["-i", "-t"]
    return [*argv[:4], *extra, *argv[4:]]


def board_argv(
    step: str,
    repo: Path,
    uid: int,
    gid: int,
    device: str,
    device_gid: int | None,
    *,
    interactive: bool = False,
) -> list[str]:
    """`docker run` for one board step, with the one device it needs."""
    argv = docker_argv(board_command(step, device), repo, uid, gid)
    return with_device(argv, device, device_gid, interactive=interactive)


def coredump_argv(
    repo: Path,
    uid: int,
    gid: int,
    elf: str,
    *,
    device: str | None = None,
    device_gid: int | None = None,
    core: str | None = None,
    save: str | None = None,
) -> list[str]:
    """`docker run` for esp-coredump. Reads flash only with `device`."""
    decode = ["esp-coredump", "--chip", "esp32c6"]
    if device is not None:
        decode += ["--port", device, "info_corefile"]
        if save is not None:
            decode += ["--save-core", save]
    else:
        if core is None:
            raise ValueError("need a device or a saved core")
        decode += ["info_corefile", "--core", core, "--core-format", "elf"]
    decode.append(elf)
    argv = docker_argv(shlex.join(decode), repo, uid, gid)
    if device is not None:
        argv = with_device(argv, device, device_gid)
    return argv


def resolve_port(port: str) -> str | None:
    """The one character device that `port` (a path or a glob) names."""
    matches = glob.glob(port) if glob.has_magic(port) else [port]
    if len(matches) != 1:
        logger.error("%s matches %d ports, not one", port, len(matches))
        return None
    dev = os.path.realpath(matches[0])
    if not Path(dev).is_char_device():
        logger.error("%s is not a serial device", port)
        return None
    return dev


def service_active() -> bool:
    """True when the dgx-status service runs, and so holds the port."""
    if shutil.which("systemctl") is None:
        return False
    rc = subprocess.call(
        ["systemctl", "is-active", "--quiet", SERVICE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return rc == 0


def port_ready(port: str) -> str | None:
    """The device to pass to the container, or None after a logged refusal."""
    dev = resolve_port(port)
    if dev is None:
        return None
    if service_active():
        logger.error(
            "the %s service holds the port: sudo systemctl stop %s", SERVICE, SERVICE
        )
        return None
    return dev


def write_sha256(path: Path) -> Path:
    """Write `<sha256>  <name>`, the format `sha256sum -c` reads, next to path."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    out = path.with_name(path.name + ".sha256")
    out.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return out


def board(step: str, port: str) -> int:
    """Run one board step. Each refusal logs why and returns 2."""
    if step == "flash" and not (REPO / FLASH_ARGS).exists():
        logger.error("no %s: build first", FLASH_ARGS)
        return 2
    if step == "flash-merged" and not (REPO / MERGED).exists():
        logger.error("no %s: build, then merge, first", MERGED)
        return 2
    backup = REPO / BACKUP
    part = backup.with_name(backup.name + ".part")
    if step == "backup":
        if backup.exists():
            logger.error("%s exists; move it away to take a new backup", BACKUP)
            return 2
        backup.parent.mkdir(parents=True, exist_ok=True)
        part.unlink(missing_ok=True)
    dev = port_ready(port)
    if dev is None:
        return 2
    cmd = board_argv(
        step,
        REPO,
        os.getuid(),
        os.getgid(),
        dev,
        os.stat(dev).st_gid,
        interactive=step == "monitor" and sys.stdin.isatty(),
    )
    logger.info("running: %s", shlex.join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0 or step != "backup":
        return rc
    size = part.stat().st_size
    if size != FLASH_SIZE:
        logger.error("%s holds %d bytes, not %d", part, size, FLASH_SIZE)
        return 1
    part.rename(backup)
    sums = write_sha256(backup)
    logger.info("backed up the flash to %s and %s", BACKUP, sums.relative_to(REPO))
    logger.info("copy both files off this machine")
    return 0


def archive_elf(repo: Path) -> Path | None:
    """Copy the build's ELF to firmware/elf/, named by its SHA-256 prefix."""
    src = repo / ELF
    if not src.exists():
        return None
    digest = hashlib.sha256(src.read_bytes()).hexdigest()[:16]
    dest = repo / ELF_ARCHIVE / f"dgx_status_display-{digest}.elf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest


def coredump(args: argparse.Namespace) -> int:
    elf = args.elf or ELF
    if not (REPO / elf).exists():
        logger.error("no ELF at %s: build first, or pass --elf", elf)
        return 2
    if args.core:
        cmd = coredump_argv(REPO, os.getuid(), os.getgid(), elf, core=args.core)
    else:
        dev = port_ready(args.port)
        if dev is None:
            return 2
        (REPO / CORE_DIR).mkdir(parents=True, exist_ok=True)
        save = f"{CORE_DIR}/core-{time.strftime('%Y%m%dT%H%M%S')}.elf"
        cmd = coredump_argv(
            REPO,
            os.getuid(),
            os.getgid(),
            elf,
            device=dev,
            device_gid=os.stat(dev).st_gid,
            save=save,
        )
        logger.info("saving the raw core to %s", save)
    logger.info("running: %s", shlex.join(cmd))
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(message)s"
    )
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "step",
        choices=[*sorted(COMMANDS), *BOARD_STEPS, "coredump"],
        help="what to run in the image",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument(
        "--port", help="the board's serial port (board steps; coredump from flash)"
    )
    src.add_argument("--core", help="coredump: decode a saved core file instead")
    p.add_argument("--elf", help=f"coredump: the flashed build's ELF (default {ELF})")
    args = p.parse_args(argv)
    if args.step == "coredump":
        if not (args.port or args.core):
            p.error("coredump needs --port or --core")
        return coredump(args)
    if args.step in BOARD_STEPS:
        if not args.port:
            p.error(f"{args.step} needs --port")
        if args.core or args.elf:
            p.error("--core and --elf are for coredump only")
        return board(args.step, args.port)
    if args.port or args.core or args.elf:
        p.error("--port, --core and --elf are for board steps and coredump only")
    cmd = docker_argv(COMMANDS[args.step], REPO, os.getuid(), os.getgid())
    logger.info("running: %s", shlex.join(cmd))
    rc = subprocess.call(cmd)
    if rc == 0 and args.step == "build":
        kept = archive_elf(REPO)
        if kept is not None:
            logger.info("kept the ELF as %s", kept.relative_to(REPO))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
