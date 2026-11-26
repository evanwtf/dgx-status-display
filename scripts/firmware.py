"""Build and test the firmware in the pinned ESP-IDF Docker image.

    uv run python scripts/firmware.py build   # idf.py build
    uv run python scripts/firmware.py size    # idf.py size (after build)
    uv run python scripts/firmware.py test    # host unit tests (make -C firmware/test)
    uv run python scripts/firmware.py preview # render the UI to firmware/preview/*.png
    uv run python scripts/firmware.py coredump --port /dev/ttyACM0
    uv run python scripts/firmware.py coredump --core firmware/coredump/core-X.elf

The image is pinned by tag and by digest (IDF_IMAGE). It is multi-arch, so the
same pin builds on this aarch64 DGX and on the x86_64 CI runner. CI calls this
script too, so the pin lives in one place.

The container runs as the calling user (`-u UID:GID`, `HOME=/tmp`, `--init`),
so every file it writes in the checkout is owned by that user. The one place
under /opt/esp the component manager writes, root_managed_components, gets a
tmpfs owned by that user. Nothing else in the image needs to be writable.

Every step except `coredump --port` leaves serial ports alone. Flashing is a
separate, manual step (README).

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
ELF_ARCHIVE = "firmware/elf"
CORE_DIR = "firmware/coredump"

COMMANDS = {
    "build": "cd firmware && idf.py build",
    "size": "cd firmware && idf.py size",
    "test": "make -C firmware/test",
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
        # The port is root:dialout 0660; the container user needs its group.
        extra = [f"--device={device}"]
        if device_gid is not None:
            extra += ["--group-add", str(device_gid)]
        argv[4:4] = extra
    return argv


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
        dev = os.path.realpath(args.port)
        if not Path(dev).is_char_device():
            logger.error("%s is not a serial device", args.port)
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
        "step", choices=[*sorted(COMMANDS), "coredump"], help="what to run in the image"
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--port", help="coredump: read the dump from this serial port")
    src.add_argument("--core", help="coredump: decode a saved core file instead")
    p.add_argument("--elf", help=f"coredump: the flashed build's ELF (default {ELF})")
    args = p.parse_args(argv)
    if args.step == "coredump":
        if not (args.port or args.core):
            p.error("coredump needs --port or --core")
        return coredump(args)
    if args.port or args.core or args.elf:
        p.error("--port, --core and --elf are for coredump only")
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
