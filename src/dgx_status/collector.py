"""Assemble one Snapshot from /proc, /sys and the GPU reader.

The collector keeps one piece of state: the previous /proc/stat sample. CPU %
is the delta between this frame and the last one.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from dgx_status import procfs, sysfs
from dgx_status.frame import Snapshot
from dgx_status.gpu import GpuReader
from dgx_status.occupant import describe_occupant

logger = logging.getLogger(__name__)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


class Collector:
    def __init__(
        self,
        proc_root: Path,
        sys_root: Path,
        gpu: GpuReader,
        host: str,
        clock: Callable[[], float],
    ) -> None:
        self._proc = proc_root
        self._sys = sys_root
        self._gpu = gpu
        self._host = host
        self._clock = clock
        self._prev_cpu: procfs.CpuTimes | None = None

    def snapshot(self) -> Snapshot:
        s = Snapshot(t=int(self._clock()), host=self._host)
        # The board has no time zone data. Send the host's own UTC offset.
        s.tz_s = time.localtime(s.t).tm_gmtoff
        s.up_s = procfs.parse_uptime(_read(self._proc / "uptime"))
        s.load = procfs.parse_loadavg(_read(self._proc / "loadavg"))

        cpu = procfs.parse_cpu_times(_read(self._proc / "stat"))
        if cpu is not None and self._prev_cpu is not None:
            s.cpu_pct = procfs.cpu_percent(self._prev_cpu, cpu)
        self._prev_cpu = cpu

        mem = procfs.parse_meminfo(_read(self._proc / "meminfo"))
        if mem is not None:
            s.mem_used_gib = mem.used_gib
            s.mem_total_gib = mem.total_gib

        g = self._gpu.read()
        s.gpu_pct = g.util_pct
        s.gpu_w = g.power_w
        s.gpu_c = g.temp_c
        s.occ = describe_occupant(self._proc, g.pids)

        devices = sysfs.read_hwmon(self._sys)
        s.fans = sysfs.read_fans(devices)
        th = sysfs.summarize(sysfs.read_thermal_zones(self._sys), devices)
        s.board_c = th.board_c
        s.nvme_c = th.nvme_c
        s.nic_c = th.nic_c
        s.temps = th.detail
        return s
