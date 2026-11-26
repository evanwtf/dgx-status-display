"""Serial link: reopen with backoff, log each state change once, read hello."""

from __future__ import annotations

import logging
import os
import select
import time
from pathlib import Path

import pytest

from dgx_status.serial_link import SerialLink, open_port, resolve_device


class FakePort:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.incoming = b""
        self.fail_write: Exception | None = None
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.fail_write is not None:
            raise self.fail_write
        self.written.append(data)
        return len(data)

    @property
    def in_waiting(self) -> int:
        return len(self.incoming)

    def read(self, size: int = 1) -> bytes:
        out, self.incoming = self.incoming[:size], self.incoming[size:]
        return out

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class Opener:
    """Fails while `.up` is False, like an unplugged board."""

    def __init__(self) -> None:
        self.up = True
        self.calls = 0
        self.ports: list[FakePort] = []

    def __call__(self, path: str) -> FakePort:
        self.calls += 1
        if not self.up:
            raise FileNotFoundError(2, "No such file or directory", path)
        port = FakePort()
        self.ports.append(port)
        return port


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _link(opener: Opener, clock: Clock) -> SerialLink:
    return SerialLink("/dev/fake", opener=opener, clock=clock)


def test_send_opens_and_writes() -> None:
    opener, clock = Opener(), Clock()
    link = _link(opener, clock)
    assert link.send(b'{"v":1}\n')
    assert opener.ports[0].written == [b'{"v":1}\n']


def test_unplug_then_replug_reconnects_with_backoff(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    opener, clock = Opener(), Clock()
    link = _link(opener, clock)
    assert link.send(b"a\n")

    opener.ports[0].fail_write = OSError(5, "Input/output error")
    opener.up = False
    assert not link.send(b"b\n")
    assert opener.ports[0].closed

    # Inside the backoff window: no open attempt at all.
    calls = opener.calls
    clock.now += 0.5
    assert not link.send(b"c\n")
    assert opener.calls == calls

    # Backoff doubles while the board stays gone: 1, 2, 4 ... capped at 30.
    gaps = []
    last = clock.now
    for _ in range(12):
        before = opener.calls
        while opener.calls == before:
            clock.now += 0.25
            link.send(b"x\n")
        gaps.append(clock.now - last)
        last = clock.now
    assert max(gaps) <= 30.25
    assert gaps[-1] == pytest.approx(30, abs=0.5)

    opener.up = True
    clock.now += 31
    assert link.send(b"d\n")
    assert opener.ports[-1].written == [b"d\n"]

    infos = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    # One line per state change, not one per second.
    assert sum("connected" in m and "dis" not in m for m in infos) == 2
    assert sum("lost" in m for m in infos) == 1
    # The "lost" line covers the whole outage; failed reopens stay at DEBUG.
    assert sum("cannot open" in m for m in infos) == 0


def test_board_absent_at_start_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    opener, clock = Opener(), Clock()
    opener.up = False
    link = _link(opener, clock)
    for _ in range(100):
        link.send(b"x\n")
        clock.now += 1
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("cannot open" in m for m in msgs) == 1


def test_hello_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    opener, clock = Opener(), Clock()
    link = _link(opener, clock)
    link.send(b"x\n")
    opener.ports[0].incoming = b'{"hello":"dgx-status-display","fw":"0.1.0"}\n'
    msgs = link.poll()
    assert msgs == [{"hello": "dgx-status-display", "fw": "0.1.0"}]
    assert any("fw 0.1.0" in r.getMessage() for r in caplog.records)


def test_hello_split_across_reads() -> None:
    opener, clock = Opener(), Clock()
    link = _link(opener, clock)
    link.send(b"x\n")
    opener.ports[0].incoming = b'{"hello":"d",'
    assert link.poll() == []
    opener.ports[0].incoming = b'"fw":"9"}\n'
    assert link.poll() == [{"hello": "d", "fw": "9"}]


def test_garbage_and_runaway_input_are_dropped() -> None:
    opener, clock = Opener(), Clock()
    link = _link(opener, clock)
    link.send(b"x\n")
    opener.ports[0].incoming = b"\x00\xffnot json\n" + b"y" * 10_000
    assert link.poll() == []
    opener.ports[0].incoming = b'\n{"hello":"d","fw":"1"}\n'
    assert link.poll() == [{"hello": "d", "fw": "1"}]


def test_pty_end_to_end() -> None:
    """Real pyserial on a pseudo-terminal: no hardware needed."""
    master, slave = os.openpty()
    try:
        link = SerialLink(os.ttyname(slave), opener=open_port, clock=time.monotonic)
        assert link.send(b'{"v":1,"t":1,"host":"test-host"}\n')
        got = b""
        deadline = time.monotonic() + 2
        while not got.endswith(b"\n") and time.monotonic() < deadline:
            r, _, _ = select.select([master], [], [], 0.1)
            if r:
                got += os.read(master, 4096)
        assert got == b'{"v":1,"t":1,"host":"test-host"}\n'

        os.write(master, b'{"hello":"dgx-status-display","fw":"0.1.0"}\n')
        msgs: list[dict[str, object]] = []
        deadline = time.monotonic() + 2
        while not msgs and time.monotonic() < deadline:
            msgs = link.poll()
            time.sleep(0.01)
        assert msgs == [{"hello": "dgx-status-display", "fw": "0.1.0"}]
        link.close()
    finally:
        os.close(master)
        os.close(slave)


def test_resolve_device_plain_path_is_kept(tmp_path: Path) -> None:
    assert resolve_device(str(tmp_path / "ttyX")) == str(tmp_path / "ttyX")


def test_resolve_device_glob_one_match(tmp_path: Path) -> None:
    (tmp_path / "usb-Espressif_a-if00").touch()
    (tmp_path / "usb-Other-if00").touch()
    got = resolve_device(str(tmp_path / "usb-Espressif_*-if00"))
    assert got == str(tmp_path / "usb-Espressif_a-if00")


def test_resolve_device_glob_no_match_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_device(str(tmp_path / "usb-Espressif_*-if00"))


def test_resolve_device_glob_two_matches_refuses(tmp_path: Path) -> None:
    """Two boards: refuse to guess which one gets the frames."""
    (tmp_path / "usb-Espressif_a-if00").touch()
    (tmp_path / "usb-Espressif_b-if00").touch()
    with pytest.raises(OSError, match="2 devices"):
        resolve_device(str(tmp_path / "usb-Espressif_*-if00"))


def test_link_resolves_the_glob_on_each_open(tmp_path: Path) -> None:
    """A replug can bring the board back; the glob finds it again."""
    opened: list[str] = []

    def opener(path: str) -> FakePort:
        opened.append(path)
        return FakePort()

    clock = Clock()
    link = SerialLink(str(tmp_path / "dev-*"), opener=opener, clock=clock)
    assert not link.send(b"x\n")  # nothing plugged in
    (tmp_path / "dev-1").touch()
    clock.now += 2
    assert link.send(b"x\n")
    assert opened == [str(tmp_path / "dev-1")]
