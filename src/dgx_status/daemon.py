"""The send loop: one frame per interval, on a fixed schedule."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from dgx_status.collector import Collector
from dgx_status.frame import encode_frame

logger = logging.getLogger(__name__)


class Sink(Protocol):
    def send(self, line: bytes) -> bool: ...
    def poll(self) -> object: ...
    def close(self) -> None: ...


class LogSink:
    """--dry-run: log each frame instead of writing it."""

    def send(self, line: bytes) -> bool:
        logger.info("frame %d bytes: %s", len(line), line.decode("ascii").rstrip())
        return True

    def poll(self) -> object:
        return []

    def close(self) -> None:
        pass


def run(
    collector: Collector,
    sink: Sink,
    interval: float,
    count: int | None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Send `count` frames (None: forever). Return how many were delivered.

    The first frame comes one interval after a priming read of /proc/stat, so
    it already carries a CPU %. The schedule is fixed: a slow tick does not
    push later ticks back. A tick that falls more than one interval behind
    is skipped, not sent in a burst.
    """
    collector.snapshot()  # prime the /proc/stat sample
    delivered = 0
    sent = 0
    next_tick = clock() + interval
    while count is None or sent < count:
        delay = next_tick - clock()
        if delay > 0:
            sleep(delay)
        elif -delay > interval:
            next_tick = clock()
        next_tick += interval
        line = encode_frame(collector.snapshot())
        if sink.send(line):
            delivered += 1
        sent += 1
        sink.poll()
    return delivered
