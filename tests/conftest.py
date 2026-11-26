"""Shared fixtures.

`tests/fixtures/spark/` holds files copied read-only from a DGX Spark (GB10)
on 2026-11-25: /proc files, the thermal zones and the
hwmon tree, with the nvfanread fans. `scripts/capture_fixtures.py` makes a
fresh copy.

Added on 2026-11-26 from the same machine: the memory blocks, the network,
RDMA and block counters (`sys/`, then `sys_t1/` two seconds later),
`proc/net/route` (default routes only, gateway zeroed) and `proc/mounts`
(`/` only).

One exception: `proc/4242/` is made by hand. Its comm is cut at 15 bytes in
the middle of a UTF-8 character, as the kernel can do.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def dgx_root() -> Path:
    """Root of the captured DGX tree: `proc/` and `sys/` live under it."""
    return FIXTURES / "spark"


@pytest.fixture
def proc_root(dgx_root: Path) -> Path:
    return dgx_root / "proc"


@pytest.fixture
def sys_root(dgx_root: Path) -> Path:
    return dgx_root / "sys"
