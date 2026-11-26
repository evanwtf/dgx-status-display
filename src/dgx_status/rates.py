"""Turn kernel counters into instant rates: the delta between two frames.

A rate is unknown (None) when there is no previous sample, when any counter
went down (a reset or a wrap), or when the set of counters changed (an
interface appeared or went away). It is never negative and never a spike
from a reset.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    """Counters read at one monotonic time, keyed by a stable name."""

    t: float
    counters: Mapping[str, int]


def rate(prev: Sample | None, cur: Sample | None) -> float | None:
    """Sum of the counter deltas per second, or None (see the module doc)."""
    if prev is None or cur is None or not cur.counters:
        return None
    if set(prev.counters) != set(cur.counters):
        return None
    dt = cur.t - prev.t
    if dt <= 0:
        return None
    total = 0
    for key, value in cur.counters.items():
        delta = value - prev.counters[key]
        if delta < 0:
            return None
        total += delta
    return total / dt


class RateTracker:
    """Keep the last sample of one reading and return the rate per update."""

    def __init__(self, scale: float = 1.0) -> None:
        self._scale = scale
        self._prev: Sample | None = None

    def update(self, cur: Sample | None) -> float | None:
        r = rate(self._prev, cur)
        self._prev = cur
        return None if r is None else r * self._scale
