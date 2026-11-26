"""Pure parsers for /proc text. Each returns None when it cannot read a value.

Every reading is instantaneous. CPU % is the delta of /proc/stat between two
consecutive frames, which is the instant reading, not a rolling window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

KIB_PER_GIB = 1024 * 1024


@dataclass(frozen=True)
class MemInfo:
    used_gib: float
    total_gib: float


@dataclass(frozen=True)
class CpuTimes:
    busy: int
    idle: int


def parse_meminfo(text: str) -> MemInfo | None:
    """Used = MemTotal - MemAvailable. Never MemFree: it counts page cache.

    NVML memory reads N/A on GB10, because the memory pool is unified. This is
    the memory figure for the whole box.
    """
    fields: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if key in ("MemTotal", "MemAvailable") and parts:
            try:
                fields[key] = int(parts[0])
            except ValueError:
                return None
    total = fields.get("MemTotal")
    avail = fields.get("MemAvailable")
    if total is None or avail is None or total <= 0 or not 0 <= avail <= total:
        return None
    return MemInfo(
        used_gib=(total - avail) / KIB_PER_GIB, total_gib=total / KIB_PER_GIB
    )


def parse_cpu_times(text: str) -> CpuTimes | None:
    """Read the aggregate `cpu ` line: idle = idle + iowait, busy = the rest.

    Fields: user nice system idle iowait irq softirq steal [guest guest_nice].
    Guest time is already inside user and nice, so it is not added again.
    """
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] != "cpu":
            continue
        try:
            nums = [int(x) for x in parts[1:9]]
        except ValueError:
            return None
        if len(nums) < 5:
            return None
        nums += [0] * (8 - len(nums))
        user, nice, system, idle, iowait, irq, softirq, steal = nums
        return CpuTimes(
            busy=user + nice + system + irq + softirq + steal, idle=idle + iowait
        )
    return None


def cpu_percent(prev: CpuTimes, cur: CpuTimes) -> float | None:
    """CPU % between two samples. None when no time passed or counters reset."""
    d_busy = cur.busy - prev.busy
    d_idle = cur.idle - prev.idle
    total = d_busy + d_idle
    if d_busy < 0 or d_idle < 0 or total <= 0:
        return None
    return 100.0 * d_busy / total


def parse_loadavg(text: str) -> tuple[float, float, float] | None:
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        one, five, fifteen = (float(x) for x in parts[:3])
    except ValueError:
        return None
    if not all(math.isfinite(x) and x >= 0 for x in (one, five, fifteen)):
        return None
    return (one, five, fifteen)


def parse_uptime(text: str) -> int | None:
    """Whole seconds since boot, from the first field of /proc/uptime."""
    parts = text.split()
    if not parts:
        return None
    try:
        value = float(parts[0])
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return int(value)
