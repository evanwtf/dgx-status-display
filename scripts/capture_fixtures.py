"""Copy the /proc and /sys files the parsers read into a fixture tree.

Read-only on the host: it only reads files and writes under --out. Use it to
refresh tests/fixtures/spark/ on a DGX, or to capture a second machine.

    uv run python scripts/capture_fixtures.py --out "$(mktemp -d)"

It copies /proc/stat twice, about two seconds apart (stat.t0 and stat.t1), so
the CPU % test has a real delta. The fixture tests anchor known values from
the current capture; after a refresh, update those values in the tests.

A process cmdline (--pid) keeps argv[0] only; every later argument becomes
"REDACTED". Arguments carry model paths, home directories and tokens, and
this repo is public. --keep-args keeps them (then check the file by hand).
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger("capture_fixtures")

_PROC_FILES = ("meminfo", "loadavg", "uptime")
_HWMON_FILE = re.compile(r"^(name|temp\d+_(input|label)|fan\d+_(input|label))$")


def _copy(src: Path, dst: Path) -> bool:
    try:
        data = src.read_bytes()
    except OSError as exc:
        logger.warning("skip %s: %s", src, exc)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return True


REDACTED = b"REDACTED"


def redact_cmdline(raw: bytes) -> bytes:
    """Keep argv[0]; replace each later argument. NUL framing stays."""
    args = raw.split(b"\0")
    trailing = raw.endswith(b"\0")
    if trailing:
        args = args[:-1]
    kept = args[:1] + [REDACTED for _ in args[1:]]
    return b"\0".join(kept) + (b"\0" if trailing else b"")


def _copy_cmdline(src: Path, dst: Path, keep_args: bool) -> bool:
    if not _copy(src, dst):
        return False
    if not keep_args:
        dst.write_bytes(redact_cmdline(dst.read_bytes()))
    return True


def capture(
    proc: Path,
    sys_root: Path,
    out: Path,
    pids: list[int],
    delay: float = 2.0,
    keep_args: bool = False,
) -> int:
    n = 0
    for name in _PROC_FILES:
        n += _copy(proc / name, out / "proc" / name)
    n += _copy(proc / "stat", out / "proc" / "stat.t0")
    time.sleep(delay)
    n += _copy(proc / "stat", out / "proc" / "stat.t1")
    for pid in pids:
        n += _copy(proc / str(pid) / "comm", out / "proc" / str(pid) / "comm")
        n += _copy_cmdline(
            proc / str(pid) / "cmdline", out / "proc" / str(pid) / "cmdline", keep_args
        )

    thermal = sys_root / "class" / "thermal"
    for zone in sorted(thermal.glob("thermal_zone*")):
        for name in ("type", "temp"):
            n += _copy(zone / name, out / "sys/class/thermal" / zone.name / name)

    hwmon = sys_root / "class" / "hwmon"
    for dev in sorted(hwmon.glob("hwmon*")):
        for f in sorted(dev.iterdir()):
            if _HWMON_FILE.match(f.name):
                n += _copy(f, out / "sys/class/hwmon" / dev.name / f.name)
    return n


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout, format="%(levelname)s %(message)s"
    )
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, required=True, help="fixture root to write")
    p.add_argument("--proc", type=Path, default=Path("/proc"), help="proc root")
    p.add_argument("--sys", type=Path, default=Path("/sys"), help="sys root")
    p.add_argument(
        "--pid", type=int, action="append", default=[], help="also copy this process"
    )
    p.add_argument(
        "--keep-args",
        action="store_true",
        help="keep cmdline arguments after argv[0] (default: redact them)",
    )
    args = p.parse_args(argv)
    n = capture(args.proc, args.sys, args.out, args.pid, keep_args=args.keep_args)
    logger.info("copied %d files to %s", n, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
