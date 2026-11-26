"""Parsers for /proc files, against text captured on the DGX."""

from __future__ import annotations

from pathlib import Path

import pytest

from dgx_status.procfs import (
    CpuTimes,
    cpu_percent,
    parse_cpu_times,
    parse_loadavg,
    parse_meminfo,
    parse_uptime,
)

KIB_PER_GIB = 1024 * 1024


def test_meminfo_used_is_total_minus_available(proc_root: Path) -> None:
    mem = parse_meminfo((proc_root / "meminfo").read_text())
    assert mem is not None
    # Known values from the fixture: MemTotal 127598636 kB, MemAvailable
    # 119524860 kB. Used = 8073776 kB = 7.70 GiB.
    assert mem.total_gib == pytest.approx(127598636 / KIB_PER_GIB)
    assert mem.used_gib == pytest.approx(8073776 / KIB_PER_GIB)
    assert round(mem.used_gib, 1) == 7.7
    assert round(mem.total_gib, 1) == 121.7


def test_meminfo_never_uses_memfree(proc_root: Path) -> None:
    """MemTotal - MemFree is 22.8 GiB here. That number counts page cache."""
    mem = parse_meminfo((proc_root / "meminfo").read_text())
    assert mem is not None
    assert round(mem.used_gib, 1) != 22.8


def test_meminfo_without_memavailable_is_unknown() -> None:
    assert parse_meminfo("MemTotal: 100 kB\nMemFree: 50 kB\n") is None


def test_meminfo_garbage_is_unknown() -> None:
    assert parse_meminfo("") is None
    assert parse_meminfo("MemTotal: lots kB\nMemAvailable: 1 kB\n") is None


def test_meminfo_available_above_total_is_unknown() -> None:
    assert parse_meminfo("MemTotal: 10 kB\nMemAvailable: 20 kB\n") is None


def test_cpu_times_reads_the_aggregate_line(proc_root: Path) -> None:
    t0 = parse_cpu_times((proc_root / "stat.t0").read_text())
    assert t0 is not None
    # cpu  27465029 97784 10421118 2381646608 1399280 0 212846 0 0 0
    # idle = idle + iowait; busy = the rest of the first eight fields.
    assert t0.idle == 2381646608 + 1399280
    assert t0.busy == 27465029 + 97784 + 10421118 + 0 + 212846 + 0


def test_cpu_percent_between_two_captured_frames(proc_root: Path) -> None:
    t0 = parse_cpu_times((proc_root / "stat.t0").read_text())
    t1 = parse_cpu_times((proc_root / "stat.t1").read_text())
    assert t0 is not None
    assert t1 is not None
    # By hand: busy delta 155 + 52 = 207; idle delta 3792 + 2 = 3794.
    assert cpu_percent(t0, t1) == pytest.approx(100 * 207 / 4001)


def test_cpu_percent_with_no_elapsed_time_is_unknown() -> None:
    t = CpuTimes(busy=10, idle=10)
    assert cpu_percent(t, t) is None


def test_cpu_percent_with_counters_going_backwards_is_unknown() -> None:
    assert cpu_percent(CpuTimes(busy=10, idle=10), CpuTimes(busy=5, idle=20)) is None


def test_cpu_times_garbage_is_unknown() -> None:
    assert parse_cpu_times("") is None
    assert parse_cpu_times("cpu0 1 2 3 4\n") is None
    assert parse_cpu_times("cpu  a b c d e\n") is None


def test_loadavg(proc_root: Path) -> None:
    assert parse_loadavg((proc_root / "loadavg").read_text()) == (0.95, 0.54, 0.32)


def test_loadavg_garbage_is_unknown() -> None:
    assert parse_loadavg("") is None
    assert parse_loadavg("x y z") is None


def test_uptime(proc_root: Path) -> None:
    assert parse_uptime((proc_root / "uptime").read_text()) == 1214489


def test_uptime_garbage_is_unknown() -> None:
    assert parse_uptime("") is None
    assert parse_uptime("nan 1") is None
    assert parse_uptime("-5 1") is None
