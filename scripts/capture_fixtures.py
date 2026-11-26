"""Copy the /proc and /sys files the parsers read into a fixture tree.

Read-only on the host: it only reads files and writes under --out. Use it to
refresh tests/fixtures/spark/ on a DGX, or to capture a second machine.

    uv run python scripts/capture_fixtures.py --out "$(mktemp -d)"

It copies /proc/stat twice, about two seconds apart (stat.t0 and stat.t1), so
the CPU % test has a real delta. The fixture tests anchor known values from
the current capture; after a refresh, update those values in the tests.

It also copies the network, RDMA and block counters twice, at the same two
moments: the first read under sys/, the second under sys_t1/ (only the
counter files), so the rate tests have a real delta.

Nothing that identifies the machine is copied: no MAC (`address`), no RDMA
GUID, no mount other than `/`. /proc/net/route keeps only its default
routes, with the gateway zeroed; a device's uevent keeps only DRIVER=.

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
from collections.abc import Callable
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


def sanitize_route(text: str) -> str:
    """Keep the header and the default routes; zero every gateway."""
    lines = text.splitlines()
    kept = lines[:1]
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) > 2 and parts[1].strip() == "00000000":
            parts[2] = "00000000"
            kept.append("\t".join(parts))
    return "\n".join(kept) + "\n"


def sanitize_mounts(text: str) -> str:
    """Keep only the lines for the root file system."""
    kept = [
        ln for ln in text.splitlines() if len(ln.split()) > 1 and ln.split()[1] == "/"
    ]
    return "\n".join(kept) + "\n"


def driver_only(text: str) -> str:
    """A uevent reduced to its DRIVER= line."""
    kept = [ln for ln in text.splitlines() if ln.startswith("DRIVER=")]
    return "\n".join(kept) + "\n"


def _copy_filtered(src: Path, dst: Path, fn: Callable[[str], str]) -> bool:
    try:
        text = src.read_text()
    except OSError as exc:
        logger.warning("skip %s: %s", src, exc)
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(fn(text))
    return True


def _subdirs(path: Path) -> list[Path]:
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except OSError:
        return []


def _counter_files(sys_root: Path) -> list[Path]:
    """The files the rates are made of, relative to sys_root."""
    rel: list[Path] = []
    for iface in _subdirs(sys_root / "class" / "net"):
        for name in ("rx_bytes", "tx_bytes"):
            rel.append(Path("class/net") / iface.name / "statistics" / name)
    for dev in _subdirs(sys_root / "class" / "infiniband"):
        for port in _subdirs(dev / "ports"):
            for name in ("port_rcv_data", "port_xmit_data"):
                rel.append(
                    Path("class/infiniband")
                    / dev.name
                    / "ports"
                    / port.name
                    / "counters"
                    / name
                )
    for disk in _subdirs(sys_root / "block"):
        rel.append(Path("block") / disk.name / "stat")
    return rel


def _capture_static(sys_root: Path, out: Path) -> int:
    """Files that do not change between the two reads."""
    n = 0
    sys_out = out / "sys"
    memory = Path("devices/system/memory")
    n += _copy(
        sys_root / memory / "block_size_bytes", sys_out / memory / "block_size_bytes"
    )
    for block in _subdirs(sys_root / memory):
        if block.name.startswith("memory"):
            rel = memory / block.name / "online"
            n += _copy(sys_root / rel, sys_out / rel)
    for iface in _subdirs(sys_root / "class" / "net"):
        rel = Path("class/net") / iface.name
        if (sys_root / rel / "speed").exists():
            n += _copy(sys_root / rel / "speed", sys_out / rel / "speed")
        uevent = sys_root / rel / "device" / "uevent"
        if uevent.exists():
            n += _copy_filtered(
                uevent, sys_out / rel / "device" / "uevent", driver_only
            )
    for dev in _subdirs(sys_root / "class" / "infiniband"):
        for port in _subdirs(dev / "ports"):
            rel = (
                Path("class/infiniband") / dev.name / "ports" / port.name / "link_layer"
            )
            n += _copy(sys_root / rel, sys_out / rel)
    for disk in _subdirs(sys_root / "block"):
        for part in _subdirs(disk):
            if (part / "partition").exists():
                rel = Path("block") / disk.name / part.name / "partition"
                n += _copy(sys_root / rel, sys_out / rel)
    return n


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
    n += _copy_filtered(
        proc / "net" / "route", out / "proc" / "net" / "route", sanitize_route
    )
    n += _copy_filtered(proc / "mounts", out / "proc" / "mounts", sanitize_mounts)
    n += _capture_static(sys_root, out)
    counters = _counter_files(sys_root)
    n += _copy(proc / "stat", out / "proc" / "stat.t0")
    n += sum(_copy(sys_root / rel, out / "sys" / rel) for rel in counters)
    time.sleep(delay)
    n += _copy(proc / "stat", out / "proc" / "stat.t1")
    n += sum(_copy(sys_root / rel, out / "sys_t1" / rel) for rel in counters)
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
