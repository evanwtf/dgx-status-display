"""Build and test the firmware in the pinned ESP-IDF Docker image.

    uv run python scripts/firmware.py build   # idf.py build
    uv run python scripts/firmware.py size    # idf.py size (after build)
    uv run python scripts/firmware.py test    # host unit tests (make -C firmware/test)

The image is pinned by tag and by digest (IDF_IMAGE). It is multi-arch, so the
same pin builds on this aarch64 DGX and on the x86_64 CI runner. CI calls this
script too, so the pin lives in one place.

The ESP-IDF image needs root to write under /opt/esp. The container therefore
runs as root, and its last step gives the firmware tree back to the calling
user, so no root-owned file stays in the checkout.

This script never touches a serial port. Flashing is a separate, manual step
(README).
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("firmware")

IDF_IMAGE = (
    "espressif/idf:v5.5.6"
    "@sha256:bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84"
)
REPO = Path(__file__).resolve().parent.parent

COMMANDS = {
    "build": "cd firmware && idf.py build",
    "size": "cd firmware && idf.py size",
    "test": "make -C firmware/test",
}


def docker_argv(command: str, repo: Path, uid: int, gid: int) -> list[str]:
    """The `docker run` argv for one step. Pure, so tests can check it."""
    inner = f'{command}; rc=$?; chown -R {uid}:{gid} /project/firmware; exit "$rc"'
    return [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{repo}:/project",
        "-w",
        "/project",
        IDF_IMAGE,
        "bash",
        "-c",
        inner,
    ]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(message)s"
    )
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("step", choices=sorted(COMMANDS), help="what to run in the image")
    args = p.parse_args(argv)
    cmd = docker_argv(COMMANDS[args.step], REPO, os.getuid(), os.getgid())
    logger.info("running: %s", shlex.join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
