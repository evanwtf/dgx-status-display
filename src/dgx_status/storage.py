"""SSD counters and disk use.

The disk is the one that holds `/` (from /proc/mounts), unless the
environment names one (cli.py). /sys/block/<disk>/stat counts 512-byte
sectors whatever the drive's block size (Linux
Documentation/block/stat.rst).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

SECTOR_BYTES = 512
GB = 10**9

Statvfs = Callable[[str], os.statvfs_result]


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def mount_source(proc_root: Path, mount_point: str = "/") -> str | None:
    """The device behind a mount point; the last matching line wins."""
    found = None
    for line in (_read(proc_root / "mounts") or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == mount_point:
            found = parts[0]
    return found


def disk_for(sys_root: Path, source: str | None) -> str | None:
    """The whole disk (`nvme0n1`) for a /dev source (`/dev/nvme0n1p2`).

    /sys/block/<disk>/ holds a directory per partition. A source that is a
    disk is its own answer. LVM, md and other stacked devices give None.
    """
    if not source or not source.startswith("/dev/"):
        return None
    name = source.removeprefix("/dev/")
    block = sys_root / "block"
    try:
        disks = sorted(p for p in block.iterdir() if p.is_dir())
    except OSError:
        return None
    for disk in disks:
        if disk.name == name or (disk / name / "partition").exists():
            return disk.name
    return None


def sectors(sys_root: Path, disk: str) -> tuple[int, int] | None:
    """(sectors read, sectors written) from /sys/block/<disk>/stat."""
    parts = (_read(sys_root / "block" / disk / "stat") or "").split()
    if len(parts) < 7:
        return None
    try:
        return int(parts[2]), int(parts[6])
    except ValueError:
        return None


def disk_use_gb(path: str, statvfs: Statvfs = os.statvfs) -> tuple[int, int] | None:
    """(used, size) in decimal GB, counted as `df` counts them."""
    try:
        st = statvfs(path)
    except OSError:
        return None
    size = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    if size <= 0 or not 0 <= used <= size:
        return None
    return round(used / GB), round(size / GB)
