"""NVML reader: errors become unknown, never a crash."""

from __future__ import annotations

import os
from typing import Any

import pytest

from dgx_status import gpu
from dgx_status.gpu import GpuReading, NvmlGpu


class FakeNvmlError(Exception):
    pass


class FakeNvml:
    """Stands in for the `pynvml` module. Set a field to an exception to fail it."""

    NVMLError = FakeNvmlError
    NVML_TEMPERATURE_GPU = 0

    def __init__(self) -> None:
        self.init_error: Exception | None = None
        self.util: Any = type("U", (), {"gpu": 7, "memory": 0})()
        self.power_mw: Any = 4612
        self.temp: Any = 43
        self.procs: Any = [type("P", (), {"pid": 11})(), type("P", (), {"pid": 12})()]
        self.inits = 0
        self.shutdowns = 0

    def nvmlInit(self) -> None:  # noqa: N802 - NVML's own name
        self.inits += 1
        if self.init_error is not None:
            raise self.init_error

    def nvmlShutdown(self) -> None:  # noqa: N802
        self.shutdowns += 1

    def nvmlDeviceGetHandleByIndex(self, index: int) -> str:  # noqa: N802
        return f"dev{index}"

    def _get(self, value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value

    def nvmlDeviceGetUtilizationRates(self, h: str) -> Any:  # noqa: N802
        return self._get(self.util)

    def nvmlDeviceGetPowerUsage(self, h: str) -> Any:  # noqa: N802
        return self._get(self.power_mw)

    def nvmlDeviceGetTemperature(self, h: str, sensor: int) -> Any:  # noqa: N802
        return self._get(self.temp)

    def nvmlDeviceGetComputeRunningProcesses(self, h: str) -> Any:  # noqa: N802
        return self._get(self.procs)


def test_reads_every_field() -> None:
    fake = FakeNvml()
    r = NvmlGpu(nvml=fake).read()
    assert r == GpuReading(util_pct=7, power_w=4.612, temp_c=43, pids=(11, 12))


def test_each_failed_field_is_unknown_alone() -> None:
    fake = FakeNvml()
    fake.power_mw = FakeNvmlError("Not Supported")
    fake.procs = FakeNvmlError("boom")
    r = NvmlGpu(nvml=fake).read()
    assert r.util_pct == 7
    assert r.temp_c == 43
    assert r.power_w is None
    assert r.pids is None


def test_init_failure_is_all_unknown_and_retried() -> None:
    fake = FakeNvml()
    fake.init_error = FakeNvmlError("Driver Not Loaded")
    reader = NvmlGpu(nvml=fake)
    assert reader.read() == GpuReading()
    fake.init_error = None
    assert reader.read().temp_c == 43
    assert fake.inits == 2


def test_missing_library_is_all_unknown() -> None:
    reader = NvmlGpu(nvml=None)
    assert reader.read() == GpuReading()


def test_absurd_values_are_unknown() -> None:
    fake = FakeNvml()
    fake.util = type("U", (), {"gpu": 4000})()
    fake.temp = 255
    fake.power_mw = -5
    r = NvmlGpu(nvml=fake).read()
    assert r.util_pct is None
    assert r.temp_c is None
    assert r.power_w is None


@pytest.mark.integration
def test_real_nvml_on_this_host() -> None:
    """Runs only where NVML loads (the DGX). Skips cleanly elsewhere."""
    if not os.path.exists("/dev/nvidiactl"):
        pytest.skip("no NVIDIA driver on this host")
    r = gpu.NvmlGpu().read()
    assert r.temp_c is not None
    assert r.pids is not None


def test_reinitializes_after_repeated_total_failure() -> None:
    fake = FakeNvml()
    boom = FakeNvmlError("GPU is lost")
    fake.util = fake.power_mw = fake.temp = fake.procs = boom
    reader = NvmlGpu(nvml=fake)
    for _ in range(gpu.REINIT_AFTER_FAILED_READS):
        assert reader.read() == GpuReading()
    assert fake.shutdowns == 1
    assert fake.inits == 1
    fake.util = type("U", (), {"gpu": 7})()
    fake.temp = 43
    assert reader.read().temp_c == 43
    assert fake.inits == 2  # a fresh init after the shutdown


def test_one_good_field_resets_the_failure_count() -> None:
    fake = FakeNvml()
    fake.util = fake.power_mw = fake.procs = FakeNvmlError("x")
    reader = NvmlGpu(nvml=fake)
    for _ in range(3 * gpu.REINIT_AFTER_FAILED_READS):
        reader.read()
    assert fake.shutdowns == 0
