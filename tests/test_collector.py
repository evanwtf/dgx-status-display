"""The collector assembles one snapshot from /proc, /sys and the GPU reader."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from dgx_status.collector import Collector
from dgx_status.gpu import GpuReading


class FakeGpu:
    def __init__(self, reading: GpuReading) -> None:
        self.reading = reading

    def read(self) -> GpuReading:
        return self.reading


def _collector(
    dgx_root: Path, gpu: GpuReading, t: float = 1_795_000_000.4
) -> Collector:
    return Collector(
        proc_root=dgx_root / "proc",
        sys_root=dgx_root / "sys",
        gpu=FakeGpu(gpu),
        host="test-host",
        clock=lambda: t,
    )


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
