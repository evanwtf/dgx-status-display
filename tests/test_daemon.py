"""The send loop's schedule and counting."""

from __future__ import annotations

from pathlib import Path

from dgx_status.collector import Collector
from dgx_status.daemon import run
from dgx_status.gpu import GpuReading


class FakeGpu:
    def read(self) -> GpuReading:
        return GpuReading()


class Sink:
    def __init__(self, ok: bool = True) -> None:
        self.lines: list[bytes] = []
        self.ok = ok
        self.polls = 0

    def send(self, line: bytes) -> bool:
        self.lines.append(line)
        return self.ok

    def poll(self) -> object:
        self.polls += 1
        return []

    def close(self) -> None:
        pass


class Time:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


def _collector(root: Path, t: Time) -> Collector:
    return Collector(root / "proc", root / "sys", FakeGpu(), "test-host", t.clock)


def test_sends_count_frames_on_schedule(dgx_root: Path) -> None:
    t = Time()
    sink = Sink()
    assert run(_collector(dgx_root, t), sink, 1.0, 3, t.clock, t.sleep) == 3
    assert len(sink.lines) == 3
    assert sink.polls == 3
    assert t.sleeps == [1.0, 1.0, 1.0]
    assert all(line.endswith(b"\n") for line in sink.lines)


def test_failed_sends_are_not_counted(dgx_root: Path) -> None:
    t = Time()
    assert run(_collector(dgx_root, t), Sink(ok=False), 1.0, 2, t.clock, t.sleep) == 0


def test_a_late_tick_is_skipped_not_burst(dgx_root: Path) -> None:
    t = Time()

    class SlowSink(Sink):
        def send(self, line: bytes) -> bool:
            if len(self.lines) == 0:
                t.now += 5.0  # the first write blocks for 5 s
            return super().send(line)

    sink = SlowSink()
    run(_collector(dgx_root, t), sink, 1.0, 3, t.clock, t.sleep)
    # Tick 1 sleeps, stalls 5 s. Tick 2 goes out at once (it is late). Tick 3
    # waits a full interval: the four missed ticks are not sent in a burst.
    assert t.sleeps == [1.0, 1.0]
