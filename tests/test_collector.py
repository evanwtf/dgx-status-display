"""The collector assembles one snapshot from /proc, /sys and the GPU reader."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from dgx_status.collector import Collector, Sources
from dgx_status.gpu import GpuReading


class FakeGpu:
    def __init__(self, reading: GpuReading) -> None:
        self.reading = reading

    def read(self) -> GpuReading:
        return self.reading


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def fake_statvfs(path: str) -> os.statvfs_result:
    """The root file system of the capture host: 4.03 TB, 3.34 TB used."""
    assert path == "/"
    frsize = 4096
    blocks = 984_343_641  # 4,031,871,553,536 B
    bfree = blocks - 815_350_484  # used 3,339,675,582,464 B
    return os.statvfs_result((frsize, frsize, blocks, bfree, 0, 0, 0, 0, 0, 255))


def _collector(
    dgx_root: Path,
    gpu: GpuReading,
    t: float = 1_795_000_000.4,
    mono: FakeClock | None = None,
    sources: Sources | None = None,
) -> Collector:
    return Collector(
        proc_root=dgx_root / "proc",
        sys_root=dgx_root / "sys",
        gpu=FakeGpu(gpu),
        host="test-host",
        clock=lambda: t,
        monotonic=mono or FakeClock(),
        sources=sources,
        statvfs=fake_statvfs,
    )


def _second_read(root: Path) -> None:
    """Lay the counters of the second capture (2 s later) over the tree."""
    shutil.copytree(root / "sys_t1", root / "sys", dirs_exist_ok=True)


def _bump(path: Path, by: int) -> None:
    path.write_text(f"{int(path.read_text()) + by}\n")


@pytest.fixture
def live_root(dgx_root: Path, tmp_path: Path) -> Path:
    """The fixture tree with /proc/stat set to the first captured sample."""
    root = tmp_path / "spark"
    shutil.copytree(dgx_root, root)
    shutil.copy(root / "proc" / "stat.t0", root / "proc" / "stat")
    return root


def test_first_snapshot(live_root: Path) -> None:
    c = _collector(live_root, GpuReading(util_pct=0, power_w=4.0, temp_c=43, pids=()))
    s = c.snapshot()
    assert s.t == 1_795_000_000
    assert s.host == "test-host"
    assert s.up_s == 1214489
    assert s.load == (0.95, 0.54, 0.32)
    # No previous /proc/stat sample yet: CPU % is unknown, not zero.
    assert s.cpu_pct is None
    assert s.mem_used_gib == pytest.approx(8073776 / 1024 / 1024)
    # Installed memory: 1023 online blocks of 128 MiB, not MemTotal (121.7).
    assert s.mem_total_gib == pytest.approx(127.875)
    # No previous counter sample: every rate is unknown.
    assert s.lan_rx_bps is None and s.lan_tx_bps is None
    assert s.roce_rx_bps is None and s.roce_tx_bps is None
    assert s.cip_bps is None
    assert s.ssd_rd_bytes_s is None and s.ssd_wr_bytes_s is None
    assert s.lan_mbps == 2500
    assert (s.disk_used_gb, s.disk_size_gb) == (3340, 4032)
    assert s.gpu_pct == 0
    assert s.gpu_w == 4.0
    assert s.gpu_c == 43
    assert s.occ == "idle"
    assert s.fans == {"fan0": 2790, "fan1": 4185}
    assert s.board_c == pytest.approx(49.5)
    assert s.nvme_c == pytest.approx(44.85)
    assert s.nic_c == pytest.approx(49.0)
    assert len(s.temps) == 7 + 3 + 4 + 1


def test_cpu_percent_comes_from_consecutive_frames(live_root: Path) -> None:
    c = _collector(live_root, GpuReading())
    c.snapshot()
    shutil.copy(live_root / "proc" / "stat.t1", live_root / "proc" / "stat")
    s = c.snapshot()
    assert s.cpu_pct == pytest.approx(100 * 207 / 4001)


def test_occupant_from_nvml_pids(live_root: Path) -> None:
    c = _collector(live_root, GpuReading(pids=(3558250,)))
    assert c.snapshot().occ == "ollama"


def test_nvml_down_leaves_gpu_unknown(live_root: Path) -> None:
    s = _collector(live_root, GpuReading()).snapshot()
    assert s.gpu_pct is None
    assert s.gpu_w is None
    assert s.gpu_c is None
    assert s.occ is None


def test_empty_roots_give_a_minimal_snapshot(tmp_path: Path) -> None:
    s = _collector(tmp_path, GpuReading()).snapshot()
    assert s.host == "test-host"
    assert s.up_s is None
    assert s.mem_used_gib is None
    assert s.fans == {}
    assert s.temps == []


def test_tz_offset_is_the_host_offset(
    live_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-18 in New York is EST, UTC-5: -18000 s."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        assert _collector(live_root, GpuReading()).snapshot().tz_s == -18000
    finally:
        monkeypatch.undo()
        time.tzset()


def test_rates_from_two_captures(live_root: Path) -> None:
    mono = FakeClock()
    c = _collector(live_root, GpuReading(), mono=mono)
    c.snapshot()
    _second_read(live_root)
    ib = live_root / "sys/class/infiniband"
    _bump(ib / "rocep1s0f1/ports/1/counters/port_xmit_data", 6_250_000_000)
    _bump(ib / "roceP2p1s0f0/ports/1/counters/port_rcv_data", 1_000)
    net = live_root / "sys/class/net"
    _bump(net / "enp1s0f1np1/statistics/tx_bytes", 500)
    _bump(net / "enP2p1s0f0np0/statistics/rx_bytes", 250)
    mono.now += 2.0
    s = c.snapshot()
    # enP7s7 (the default route): 12129 B in, 6765 B out in 2 s.
    assert s.lan_rx_bps == pytest.approx(12129 * 8 / 2)
    assert s.lan_tx_bps == pytest.approx(6765 * 8 / 2)
    # Words x 4 bytes x 8 bits: 6.25e9 words in 2 s is 100 Gb/s.
    assert s.roce_tx_bps == pytest.approx(100e9)
    assert s.roce_rx_bps == pytest.approx(1000 * 32 / 2)
    # Cluster IP is in + out over the four mlx5 interfaces.
    assert s.cip_bps == pytest.approx(750 * 8 / 2)
    # nvme0n1: 1632 sectors read, 18048 written.
    assert s.ssd_rd_bytes_s == pytest.approx(1632 * 512 / 2)
    assert s.ssd_wr_bytes_s == pytest.approx(18048 * 512 / 2)


def test_counter_reset_is_unknown_not_negative(live_root: Path) -> None:
    mono = FakeClock()
    c = _collector(live_root, GpuReading(), mono=mono)
    c.snapshot()
    _second_read(live_root)
    (live_root / "sys/class/net/enP7s7/statistics/rx_bytes").write_text("5\n")
    (
        live_root / "sys/class/infiniband/rocep1s0f0/ports/1/counters/port_rcv_data"
    ).write_text("0\n")
    mono.now += 1.0
    s = c.snapshot()
    assert s.lan_rx_bps is None
    assert s.roce_rx_bps is None
    assert s.lan_tx_bps is not None and s.lan_tx_bps >= 0
    # The next frame measures from the reset value again.
    _bump(live_root / "sys/class/net/enP7s7/statistics/rx_bytes", 1000)
    mono.now += 1.0
    assert c.snapshot().lan_rx_bps == pytest.approx(8000)


def test_interface_that_goes_away_is_unknown(live_root: Path) -> None:
    mono = FakeClock()
    c = _collector(live_root, GpuReading(), mono=mono)
    c.snapshot()
    shutil.rmtree(live_root / "sys/class/net/enP2p1s0f1np1")
    shutil.rmtree(live_root / "sys/class/infiniband/roceP2p1s0f1")
    mono.now += 1.0
    s = c.snapshot()
    assert s.cip_bps is None
    assert s.roce_rx_bps is None and s.roce_tx_bps is None
    # One frame later the smaller set is the new baseline.
    mono.now += 1.0
    s = c.snapshot()
    assert s.cip_bps == 0 and s.roce_tx_bps == 0


def test_new_default_route_restarts_the_lan_rate(live_root: Path) -> None:
    mono = FakeClock()
    c = _collector(live_root, GpuReading(), mono=mono)
    c.snapshot()
    route = live_root / "proc/net/route"
    route.write_text(route.read_text().replace("enP7s7", "wlP9s9"))
    mono.now += 1.0
    s = c.snapshot()
    assert s.lan_rx_bps is None
    assert s.lan_mbps is None  # wlP9s9 is down: speed is unreadable


def test_overrides_replace_detection(live_root: Path) -> None:
    mono = FakeClock()
    src = Sources(
        lan_iface="enp1s0f1np1",
        roce_ports=("rocep1s0f1/1",),
        cluster_ifaces=("enP7s7",),
        ssd_disk="loop0",
    )
    c = _collector(live_root, GpuReading(), mono=mono, sources=src)
    c.snapshot()
    _second_read(live_root)
    _bump(
        live_root / "sys/class/infiniband/rocep1s0f1/ports/1/counters/port_xmit_data",
        10,
    )
    _bump(
        live_root / "sys/class/infiniband/rocep1s0f0/ports/1/counters/port_xmit_data",
        99,
    )
    mono.now += 1.0
    s = c.snapshot()
    assert s.lan_mbps == 200000
    assert s.roce_tx_bps == pytest.approx(10 * 32)  # rocep1s0f0 is not counted
    assert s.cip_bps == pytest.approx((12129 + 6765) * 8)  # enP7s7 in + out
    assert s.ssd_rd_bytes_s == 0


def test_no_installed_memory_view_leaves_total_unknown(live_root: Path) -> None:
    shutil.rmtree(live_root / "sys/devices/system/memory")
    s = _collector(live_root, GpuReading()).snapshot()
    assert s.mem_total_gib is None
    assert s.mem_used_gib == pytest.approx(8073776 / 1024 / 1024)


def test_llm_reading_is_copied_into_the_frame(live_root: Path) -> None:
    from dgx_status.llm import LlmReading

    reading: LlmReading | None = LlmReading(
        engines_up=1, models="m", out_tps=45.0, in_tps=2000.0, requests=3, kv_pct=62.0
    )
    c = Collector(
        proc_root=live_root / "proc",
        sys_root=live_root / "sys",
        gpu=FakeGpu(GpuReading()),
        host="test-host",
        clock=lambda: 1.0,
        monotonic=FakeClock(),
        statvfs=fake_statvfs,
        llm=lambda: reading,
    )
    s = c.snapshot()
    assert (s.llm_up, s.llm_out_tps, s.llm_req, s.llm_kv_pct) == (1, 45.0, 3, 62.0)
    reading = LlmReading(engines_up=0)
    s = c.snapshot()
    assert s.llm_up == 0 and s.llm_out_tps is None
    reading = None
    assert c.snapshot().llm_up is None
