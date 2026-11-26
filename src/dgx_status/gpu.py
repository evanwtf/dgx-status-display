"""GPU readings through NVML (`nvidia-ml-py`), behind a small interface.

Every NVML error becomes "unknown" for that one field. The daemon never
crashes on NVML. Initialization retries on the next read, so a driver that
loads late still gets picked up.

The SM clock is not read: Evan dropped it as a useless indicator. NVML
memory is not read either: on GB10 the pool is unified and NVML reports N/A.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

_MAX_POWER_W = 2000.0
_MIN_TEMP_C = -40
_MAX_TEMP_C = 150


@dataclass(frozen=True)
class GpuReading:
    util_pct: int | None = None
    power_w: float | None = None
    temp_c: int | None = None
    # None: NVML could not list processes. (): it listed none.
    pids: tuple[int, ...] | None = None


class GpuReader(Protocol):
    def read(self) -> GpuReading: ...


def _load_pynvml() -> Any:
    try:
        return importlib.import_module("pynvml")
    except ImportError:
        logger.warning("nvidia-ml-py is not installed; GPU readings are unknown")
        return None


_DEFAULT: Any = object()


class NvmlGpu:
    """Reads GPU 0. `nvml` is the `pynvml` module, or a fake in tests."""

    def __init__(self, nvml: Any = _DEFAULT, index: int = 0) -> None:
        self._nvml = _load_pynvml() if nvml is _DEFAULT else nvml
        self._index = index
        self._handle: Any = None
        self._failed_once = False

    def _ensure(self) -> Any:
        if self._nvml is None:
            return None
        if self._handle is None:
            try:
                self._nvml.nvmlInit()
                self._handle = self._nvml.nvmlDeviceGetHandleByIndex(self._index)
            except Exception as exc:  # NVMLError, or OSError from a missing lib
                if not self._failed_once:
                    logger.warning("NVML init failed: %s", exc)
                    self._failed_once = True
                self._handle = None
                return None
            if self._failed_once:
                logger.info("NVML init recovered")
                self._failed_once = False
        return self._handle

    def _call(self, name: str, *args: Any) -> Any:
        try:
            return getattr(self._nvml, name)(*args)
        except Exception as exc:
            logger.debug("NVML %s failed: %s", name, exc)
            return None

    def read(self) -> GpuReading:
        h = self._ensure()
        if h is None:
            return GpuReading()
        util = self._call("nvmlDeviceGetUtilizationRates", h)
        util_pct = getattr(util, "gpu", None) if util is not None else None
        if not isinstance(util_pct, int) or not 0 <= util_pct <= 100:
            util_pct = None

        mw = self._call("nvmlDeviceGetPowerUsage", h)
        power_w = mw / 1000 if isinstance(mw, int | float) else None
        if power_w is not None and not 0 <= power_w <= _MAX_POWER_W:
            power_w = None

        temp = self._call(
            "nvmlDeviceGetTemperature",
            h,
            getattr(self._nvml, "NVML_TEMPERATURE_GPU", 0),
        )
        if not isinstance(temp, int) or not _MIN_TEMP_C <= temp <= _MAX_TEMP_C:
            temp = None

        procs = self._call("nvmlDeviceGetComputeRunningProcesses", h)
        pids: tuple[int, ...] | None = None
        if procs is not None:
            try:
                pids = tuple(int(p.pid) for p in procs)
            except (TypeError, AttributeError, ValueError):
                pids = None
        return GpuReading(util_pct=util_pct, power_w=power_w, temp_c=temp, pids=pids)
