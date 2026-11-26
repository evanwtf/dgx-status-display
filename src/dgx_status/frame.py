"""Build one protocol v1 frame: a single line of compact JSON.

See docs/protocol.md. Every reading is optional except `v`, `t` and `host`.
A reading that is None, NaN or infinite is left out, and the display draws
it as a dash. A frame never exceeds MAX_LINE_BYTES, newline included.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024
MAX_HOST_CHARS = 32
MAX_OCC_CHARS = 48
MAX_LABEL_CHARS = 15
MAX_TZ_S = 14 * 3600
# Above any real link or disk (10 Tb/s, 10 TB/s): a larger rate is a bug.
MAX_RATE = 10**13


@dataclass
class Snapshot:
    """One instantaneous set of readings. None means unknown."""

    t: int
    host: str
    tz_s: int | None = None
    up_s: int | None = None
    load: tuple[float, float, float] | None = None
    cpu_pct: float | None = None
    mem_used_gib: float | None = None
    mem_total_gib: float | None = None
    gpu_pct: int | None = None
    gpu_w: float | None = None
    gpu_c: int | None = None
    occ: str | None = None
    fans: dict[str, int] = field(default_factory=dict)
    board_c: float | None = None
    nvme_c: float | None = None
    nic_c: float | None = None
    temps: list[tuple[str, float]] = field(default_factory=list)
    # Rates are deltas between two frames: bits/s for networks, bytes/s for
    # the SSD.
    lan_rx_bps: float | None = None
    lan_tx_bps: float | None = None
    lan_mbps: int | None = None
    roce_rx_bps: float | None = None
    roce_tx_bps: float | None = None
    cip_bps: float | None = None
    ssd_rd_bytes_s: float | None = None
    ssd_wr_bytes_s: float | None = None
    disk_used_gb: int | None = None
    disk_size_gb: int | None = None


def _num(value: float | None, digits: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _int(value: float | None) -> int | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value)


def _rate(value: float | None) -> int | None:
    """A rate as a whole number. A negative rate cannot happen; drop it."""
    r = _int(value)
    return r if r is not None and 0 <= r <= MAX_RATE else None


def _fields(s: Snapshot) -> dict[str, Any]:
    f: dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "t": int(s.t),
        "host": s.host[:MAX_HOST_CHARS],
    }
    if s.tz_s is not None and abs(s.tz_s) <= MAX_TZ_S:
        f["tz_s"] = int(s.tz_s)
    f["up_s"] = s.up_s
    if s.load is not None:
        f["load1"], f["load5"], f["load15"] = (_num(x, 2) for x in s.load)
    f["cpu_pct"] = _num(s.cpu_pct, 1)
    f["mem_used_gib"] = _num(s.mem_used_gib, 1)
    f["mem_total_gib"] = _num(s.mem_total_gib, 1)
    f["gpu_pct"] = _int(s.gpu_pct)
    f["gpu_w"] = _int(s.gpu_w)
    f["gpu_c"] = _int(s.gpu_c)
    f["occ"] = s.occ[:MAX_OCC_CHARS] if s.occ is not None else None
    for name in ("fan0", "fan1"):
        f[f"{name}_rpm"] = s.fans.get(name)
    f["board_c"] = _num(s.board_c, 1)
    f["nvme_c"] = _num(s.nvme_c, 1)
    f["nic_c"] = _num(s.nic_c, 1)
    f["lan_rx_bps"] = _rate(s.lan_rx_bps)
    f["lan_tx_bps"] = _rate(s.lan_tx_bps)
    f["lan_mbps"] = s.lan_mbps if s.lan_mbps is not None and s.lan_mbps > 0 else None
    f["roce_rx_bps"] = _rate(s.roce_rx_bps)
    f["roce_tx_bps"] = _rate(s.roce_tx_bps)
    f["cip_bps"] = _rate(s.cip_bps)
    f["ssd_rd_Bps"] = _rate(s.ssd_rd_bytes_s)
    f["ssd_wr_Bps"] = _rate(s.ssd_wr_bytes_s)
    if (
        s.disk_used_gb is not None
        and s.disk_size_gb is not None
        and 0 <= s.disk_used_gb <= s.disk_size_gb
    ):
        f["disk_used_gb"] = int(s.disk_used_gb)
        f["disk_size_gb"] = int(s.disk_size_gb)
    th = [[label[:MAX_LABEL_CHARS], _num(c, 1)] for label, c in s.temps]
    f["th"] = [x for x in th if x[1] is not None] or None
    return {k: v for k, v in f.items() if v is not None}


def _dump(f: dict[str, Any]) -> bytes:
    # allow_nan=False: a NaN that slipped past _num raises here, in tests.
    text = json.dumps(f, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return text.encode("ascii") + b"\n"


def encode_frame(s: Snapshot) -> bytes:
    """Encode a snapshot. Shorten the detail list until the line fits the cap."""
    f = _fields(s)
    line = _dump(f)
    th = f.get("th")
    while len(line) > MAX_LINE_BYTES and th:
        th = th[:-1]
        if th:
            f["th"] = th
        else:
            f.pop("th")
        line = _dump(f)
    if len(line) > MAX_LINE_BYTES:
        # Cannot happen with the per-field caps above. Fail loud in tests.
        raise ValueError(f"frame is {len(line)} bytes, over {MAX_LINE_BYTES}")
    return line
