"""Assemble one Snapshot from /proc, /sys and the GPU reader.

The collector keeps the previous sample of each counter. CPU %, the network
rates and the SSD rates are deltas between this frame and the last one.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dgx_status import netio, procfs, storage, sysfs
from dgx_status.frame import Snapshot
from dgx_status.gpu import GpuReader
from dgx_status.llm import LlmReading
from dgx_status.occupant import OccupantNamer
from dgx_status.rates import RateTracker, Sample

logger = logging.getLogger(__name__)

# Installed memory changes only on a hotplug; re-read it this often.
MEMORY_REREAD_S = 600.0


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


@dataclass(frozen=True)
class Sources:
    """Overrides for the automatic choices. None means detect it each frame."""

    lan_iface: str | None = None
    roce_ports: tuple[str, ...] | None = None  # "<device>/<port>"
    cluster_ifaces: tuple[str, ...] | None = None
    ssd_disk: str | None = None
    disk_path: str = "/"


def _sum(pairs: dict[str, tuple[int, int]], index: int) -> dict[str, int]:
    return {k: v[index] for k, v in pairs.items()}


class Collector:
    def __init__(
        self,
        proc_root: Path,
        sys_root: Path,
        gpu: GpuReader,
        host: str,
        clock: Callable[[], float],
        monotonic: Callable[[], float] = time.monotonic,
        sources: Sources | None = None,
        statvfs: storage.Statvfs = os.statvfs,
        llm: Callable[[], LlmReading | None] | None = None,
    ) -> None:
        self._proc = proc_root
        self._sys = sys_root
        self._gpu = gpu
        self._host = host
        self._clock = clock
        self._monotonic = monotonic
        self._src = sources or Sources()
        self._statvfs = statvfs
        self._llm = llm
        self._mem_gib: float | None = None
        self._mem_at: float | None = None
        self._prev_cpu: procfs.CpuTimes | None = None
        self._occupant = OccupantNamer(proc_root)
        bits = 8.0
        self._lan_rx = RateTracker(bits)
        self._lan_tx = RateTracker(bits)
        self._roce_rx = RateTracker(bits * netio.ROCE_BYTES_PER_UNIT)
        self._roce_tx = RateTracker(bits * netio.ROCE_BYTES_PER_UNIT)
        self._cip = RateTracker(bits)
        self._ssd_rd = RateTracker(storage.SECTOR_BYTES)
        self._ssd_wr = RateTracker(storage.SECTOR_BYTES)

    def _installed_gib(self, now: float) -> float | None:
        """Installed memory, re-read every MEMORY_REREAD_S: it reads one
        file per memory block (about 1000 here, some 10 ms), and it changes
        only on a memory hotplug."""
        if self._mem_at is None or now - self._mem_at >= MEMORY_REREAD_S:
            self._mem_gib = sysfs.installed_memory_gib(self._sys)
            self._mem_at = now
        return self._mem_gib

    def _network(self, s: Snapshot, now: float) -> None:
        lan = self._src.lan_iface or netio.default_route_iface(self._proc)
        lan_rx = lan_tx = None
        if lan is not None:
            b = netio.netdev_bytes(self._sys, lan)
            if b is not None:
                # Key by name: a new default interface is a changed set.
                lan_rx = Sample(now, {lan: b[0]})
                lan_tx = Sample(now, {lan: b[1]})
            s.lan_mbps = netio.link_speed_mbps(self._sys, lan)
        s.lan_rx_bps = self._lan_rx.update(lan_rx)
        s.lan_tx_bps = self._lan_tx.update(lan_tx)

        ports = self._src.roce_ports
        if ports is None:
            ports = tuple(netio.roce_ports(self._sys))
        words = netio.roce_words(self._sys, ports) if ports else None
        s.roce_rx_bps = self._roce_rx.update(Sample(now, words[0]) if words else None)
        s.roce_tx_bps = self._roce_tx.update(Sample(now, words[1]) if words else None)

        ifaces = self._src.cluster_ifaces
        if ifaces is None:
            # Never count the LAN twice: if the default route uses a
            # ConnectX port, that port is the LAN, not the cluster.
            ifaces = tuple(i for i in netio.cluster_ifaces(self._sys) if i != lan)
        pairs: dict[str, tuple[int, int]] = {}
        for name in ifaces:
            b = netio.netdev_bytes(self._sys, name)
            if b is None:
                pairs = {}
                break
            pairs[name] = b
        cip = None
        if pairs:
            # In + out: one sum of both directions.
            counters = {f"{k}/rx": v for k, v in _sum(pairs, 0).items()}
            counters |= {f"{k}/tx": v for k, v in _sum(pairs, 1).items()}
            cip = Sample(now, counters)
        s.cip_bps = self._cip.update(cip)

    def _storage(self, s: Snapshot, now: float) -> None:
        disk = self._src.ssd_disk
        if disk is None:
            src = storage.mount_source(self._proc, self._src.disk_path)
            disk = storage.disk_for(self._sys, src)
        sec = storage.sectors(self._sys, disk) if disk else None
        key = disk or ""
        s.ssd_rd_bytes_s = self._ssd_rd.update(
            Sample(now, {key: sec[0]}) if sec else None
        )
        s.ssd_wr_bytes_s = self._ssd_wr.update(
            Sample(now, {key: sec[1]}) if sec else None
        )
        use = storage.disk_use_gb(self._src.disk_path, self._statvfs)
        if use is not None:
            s.disk_used_gb, s.disk_size_gb = use

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

        # Used is MemTotal - MemAvailable; the total is the memory installed.
        mem = procfs.parse_meminfo(_read(self._proc / "meminfo"))
        if mem is not None:
            s.mem_used_gib = mem.used_gib
        now = self._monotonic()
        s.mem_total_gib = self._installed_gib(now)

        g = self._gpu.read()
        s.gpu_pct = g.util_pct
        s.gpu_w = g.power_w
        s.gpu_c = g.temp_c
        s.occ = self._occupant.describe(g.pids)

        devices = sysfs.read_hwmon(self._sys)
        s.fans = sysfs.read_fans(devices)
        th = sysfs.summarize(sysfs.read_thermal_zones(self._sys), devices)
        s.board_c = th.board_c
        s.nvme_c = th.nvme_c
        s.nic_c = th.nic_c
        s.temps = th.detail

        self._network(s, now)
        self._storage(s, now)
        self._llm_fields(s)
        return s

    def _llm_fields(self, s: Snapshot) -> None:
        """Copy the poller's newest reading. It never blocks on the network."""
        r = self._llm() if self._llm is not None else None
        if r is None:
            return
        s.llm_up = r.engines_up
        if r.engines_up == 0:
            return
        s.llm_model = r.models
        s.llm_out_tps = r.out_tps
        s.llm_in_tps = r.in_tps
        s.llm_req = r.requests
        s.llm_kv_pct = r.kv_pct
        s.llm_spec_pct = r.spec_pct
        s.llm_ttft_ms = r.ttft_ms
