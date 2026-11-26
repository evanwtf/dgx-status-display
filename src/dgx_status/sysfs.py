"""Read thermal zones and hwmon devices from a sysfs root.

Every function takes the sysfs root (`/sys` in production, a fixture tree in
tests). A file that is missing or unreadable is skipped, never fatal.

Find a hwmon device by its `name` file. The `hwmonN` number changes across
boots and module loads.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

FAN_DEVICE = "nvfanread"
NVME_DEVICE = "nvme"
NIC_DEVICE = "mlx5"
WIFI_DEVICE = "mt7925_phy0"
BOARD_ZONE_TYPE = "acpitz"

_ZONE_RE = re.compile(r"^thermal_zone(\d+)$")
_HWMON_RE = re.compile(r"^hwmon(\d+)$")
_TEMP_RE = re.compile(r"^temp(\d+)_input$")
_FAN_RE = re.compile(r"^fan(\d+)_input$")


@dataclass(frozen=True)
class Zone:
    label: str
    kind: str
    celsius: float


@dataclass
class HwmonDevice:
    name: str
    index: int
    # Keyed by the sensor's label file, or "tempN" when it has none.
    temps: dict[str, float] = field(default_factory=dict)
    fans: dict[str, int] = field(default_factory=dict)


@dataclass
class ThermalSummary:
    board_c: float | None
    nvme_c: float | None
    nic_c: float | None
    detail: list[tuple[str, float]]


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read(path)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _numbered(directory: Path, pattern: re.Pattern[str]) -> list[tuple[int, Path]]:
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    found = []
    for entry in entries:
        m = pattern.match(entry.name)
        if m:
            found.append((int(m.group(1)), entry))
    return sorted(found)


def read_thermal_zones(sys_root: Path) -> list[Zone]:
    zones = []
    for n, path in _numbered(sys_root / "class" / "thermal", _ZONE_RE):
        kind = _read(path / "type")
        milli = _read_int(path / "temp")
        if kind is None or milli is None:
            continue
        zones.append(Zone(label=f"tz{n}", kind=kind, celsius=milli / 1000))
    return zones


def read_hwmon(sys_root: Path) -> list[HwmonDevice]:
    devices = []
    for n, path in _numbered(sys_root / "class" / "hwmon", _HWMON_RE):
        name = _read(path / "name")
        if not name:
            continue
        dev = HwmonDevice(name=name, index=n)
        for i, f in _numbered(path, _TEMP_RE):
            milli = _read_int(f)
            if milli is not None:
                label = _read(path / f"temp{i}_label") or f"temp{i}"
                dev.temps[label] = milli / 1000
        for i, f in _numbered(path, _FAN_RE):
            rpm = _read_int(f)
            if rpm is not None:
                label = _read(path / f"fan{i}_label") or f"fan{i - 1}"
                dev.fans[label] = rpm
        devices.append(dev)
    return devices


def read_fans(devices: list[HwmonDevice]) -> dict[str, int]:
    """Fan RPM from the nvfanread device, keyed fan0 and fan1. Empty if absent."""
    for dev in devices:
        if dev.name == FAN_DEVICE:
            return dict(dev.fans)
    return {}


def summarize(zones: list[Zone], devices: list[HwmonDevice]) -> ThermalSummary:
    """Overview figures plus one labelled entry per sensor for the detail page.

    - board: the max of the acpitz thermal zones.
    - NVMe: the Composite sensor of the nvme hwmon device.
    - NIC: the max of the mlx5 hwmon devices.

    The acpitz_N hwmon devices mirror the thermal zones, so the detail list
    takes the zones and skips those devices.
    """
    detail: list[tuple[str, float]] = []
    board = [z.celsius for z in zones if z.kind == BOARD_ZONE_TYPE]
    detail += [(z.label, z.celsius) for z in zones]

    nvme_c: float | None = None
    nics: list[float] = []
    for dev in sorted(devices, key=lambda d: d.index):
        if dev.name == NVME_DEVICE:
            for label, c in dev.temps.items():
                if label == "Composite":
                    nvme_c = c
                    detail.append(("nvme", c))
                else:
                    detail.append(("nvme " + label.replace("Sensor ", "s"), c))
            if nvme_c is None and dev.temps:
                nvme_c = max(dev.temps.values())
        elif dev.name == NIC_DEVICE:
            for c in dev.temps.values():
                detail.append((f"nic{len(nics)}", c))
                nics.append(c)
        elif dev.name == WIFI_DEVICE:
            detail += [("wifi", c) for c in dev.temps.values()]

    return ThermalSummary(
        board_c=max(board) if board else None,
        nvme_c=nvme_c,
        nic_c=max(nics) if nics else None,
        detail=detail,
    )


_MEMBLOCK_RE = re.compile(r"^memory(\d+)$")
BYTES_PER_GIB = 1024**3


def installed_memory_gib(sys_root: Path) -> float | None:
    """Online memory blocks x block size, in GiB: the memory installed.

    MemTotal is lower: firmware and the kernel keep about 6 GiB before it.
    block_size_bytes is hex without a prefix (8000000 = 128 MiB). None when
    the kernel has no memory hotplug view.
    """
    memory = sys_root / "devices" / "system" / "memory"
    text = _read(memory / "block_size_bytes")
    if not text:
        return None
    try:
        block = int(text, 16)
    except ValueError:
        return None
    online = sum(
        1
        for _, path in _numbered(memory, _MEMBLOCK_RE)
        if _read(path / "online") == "1"
    )
    if block <= 0 or online == 0:
        return None
    return online * block / BYTES_PER_GIB
