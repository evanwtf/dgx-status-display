"""Network counters: the LAN, RoCE and IP on the cluster NIC.

Every function takes the proc or sys root (`/proc`, `/sys` in production, a
fixture tree in tests). Nothing names an interface: the LAN is the one with
the default route, RoCE is every RDMA port on an Ethernet link layer, and the
cluster NIC is every interface whose driver is mlx5_core. The daemon's
environment can override each choice (cli.py).

See docs/protocol.md for what each counter counts.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

CLUSTER_DRIVER = "mlx5_core"
ROCE_LINK_LAYER = "Ethernet"
# port_rcv_data and port_xmit_data count 4-byte words.
ROCE_BYTES_PER_UNIT = 4


def _read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read(path)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _dirs(directory: Path) -> list[Path]:
    try:
        return sorted(p for p in directory.iterdir() if p.is_dir())
    except OSError:
        return []


def default_route_iface(proc_root: Path) -> str | None:
    """The interface of the default route with the lowest metric.

    /proc/net/route: Iface Destination Gateway Flags RefCnt Use Metric Mask...
    in hex. The default route has Destination and Mask 00000000 and the
    route is up (flag 0x1).
    """
    best: tuple[int, str] | None = None
    text = _read(proc_root / "net" / "route") or ""
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 8:
            continue
        iface, dest, _gw, flags, _ref, _use, metric, mask = parts[:8]
        try:
            up = int(flags, 16) & 0x1
            m = int(metric)
        except ValueError:
            continue
        default = dest == "00000000" and mask == "00000000" and up
        if default and (best is None or m < best[0]):
            best = (m, iface)
    return best[1] if best else None


def netdev_bytes(sys_root: Path, iface: str) -> tuple[int, int] | None:
    """(rx_bytes, tx_bytes) of one interface, or None."""
    stats = sys_root / "class" / "net" / iface / "statistics"
    rx = _read_int(stats / "rx_bytes")
    tx = _read_int(stats / "tx_bytes")
    if rx is None or tx is None:
        return None
    return rx, tx


def link_speed_mbps(sys_root: Path, iface: str) -> int | None:
    """Link speed in Mb/s; None when the link is down (the file reads -1)."""
    speed = _read_int(sys_root / "class" / "net" / iface / "speed")
    return speed if speed is not None and speed > 0 else None


def _driver(sys_root: Path, iface: str) -> str | None:
    """DRIVER= from the device's uevent: a plain file, no symlink to follow."""
    text = _read(sys_root / "class" / "net" / iface / "device" / "uevent") or ""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key == "DRIVER":
            return value
    return None


def cluster_ifaces(sys_root: Path) -> list[str]:
    """Interfaces driven by mlx5_core: the ConnectX-7."""
    return [
        p.name
        for p in _dirs(sys_root / "class" / "net")
        if _driver(sys_root, p.name) == CLUSTER_DRIVER
    ]


def roce_ports(sys_root: Path) -> list[str]:
    """`<device>/<port>` for every RDMA port on an Ethernet link layer."""
    found = []
    for dev in _dirs(sys_root / "class" / "infiniband"):
        for port in _dirs(dev / "ports"):
            if _read(port / "link_layer") == ROCE_LINK_LAYER:
                found.append(f"{dev.name}/{port.name}")
    return found


def roce_words(
    sys_root: Path, ports: Iterable[str]
) -> tuple[dict[str, int], dict[str, int]] | None:
    """(rcv, xmit) word counters per port, or None if any port is unreadable."""
    rcv: dict[str, int] = {}
    xmit: dict[str, int] = {}
    for port in ports:
        dev, _, num = port.partition("/")
        counters = sys_root / "class" / "infiniband" / dev / "ports" / num / "counters"
        r = _read_int(counters / "port_rcv_data")
        x = _read_int(counters / "port_xmit_data")
        if r is None or x is None:
            return None
        rcv[port] = r
        xmit[port] = x
    return rcv, xmit
