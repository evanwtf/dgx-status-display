"""Shared fixtures.

`tests/fixtures/spark/` holds files copied read-only from the DGX Spark
(`a DGX Spark`, GB10) on 2026-11-25: /proc files, the thermal zones and the
hwmon tree, with the nvfanread fans. `scripts/capture_fixtures.py` makes a
fresh copy.
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
