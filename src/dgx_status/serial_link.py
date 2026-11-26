"""Write frames to the display over USB serial, and read what it sends back.

The link survives unplug and replug. On any error it closes the port and
tries again after a backoff: 1 s, doubling to 30 s. It logs each state change
once (connected, lost, cannot open), not once per frame.
"""

from __future__ import annotations

import contextlib
import glob
import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

import serial

from dgx_status.board_log import BoardLogRelay

logger = logging.getLogger(__name__)

MIN_BACKOFF_S = 1.0
MAX_BACKOFF_S = 30.0
MAX_INCOMING_LINE = 1024
WRITE_TIMEOUT_S = 1.0
# The board's USB-Serial-JTAG port, matched by vendor name. The serial number
# in the real path is the chip's MAC; it is found at run time, never written.
DEFAULT_DEVICE_GLOB = (
    "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00"
)
_GLOB_CHARS = frozenset("*?[")


def resolve_device(spec: str) -> str:
    """A plain path is returned as is. A glob must match exactly one path.

    No match raises FileNotFoundError (the board is unplugged). Several
    matches raise OSError: with two boards, refuse to guess.
    """
    if not _GLOB_CHARS & set(spec):
        return spec
    matches = sorted(glob.glob(spec))
    if not matches:
        raise FileNotFoundError(2, "no device matches", spec)
    if len(matches) > 1:
        raise OSError(f"{len(matches)} devices match {spec}; set the device path")
    return matches[0]


class Port(Protocol):
    def write(self, data: bytes, /) -> int | None: ...
    @property
    def in_waiting(self) -> int: ...
    def read(self, size: int = 1, /) -> bytes: ...
    def close(self) -> None: ...


def open_port(path: str) -> Port:
    """Open the board's USB-Serial-JTAG port without resetting the chip.

    USB-Serial-JTAG ignores the baud rate. The chip resets when RTS is set and
    DTR is clear. Linux sets both lines together on open. pyserial's default
    (dtr=True, rts=True) keeps both set, so the lines never pass through the
    reset state. Do not set dtr or rts to False here: pyserial applies DTR
    first, which gives RTS=1 DTR=0 for a moment, and that resets the board.
    """
    return serial.Serial(
        path, baudrate=115200, timeout=0, write_timeout=WRITE_TIMEOUT_S, exclusive=True
    )


# Reset reasons that mean the board crashed (docs/protocol.md, `rst`).
CRASH_RESETS = frozenset({"panic", "int_wdt", "task_wdt", "wdt", "cpu_lockup"})


def log_hello(msg: dict[str, Any]) -> None:
    """INFO for a normal boot; WARNING, with the panic reason, after a crash."""
    rst = msg.get("rst")
    logger.info(
        "display says hello: %s, fw %s, elf %s, reset %s",
        msg.get("hello"),
        msg.get("fw"),
        msg.get("elf", "?"),
        rst if rst is not None else "?",
    )
    if rst in CRASH_RESETS:
        logger.warning(
            "display rebooted after a crash (%s): %s; read the core dump with "
            "scripts/firmware.py coredump",
            rst,
            msg.get("why") or "no reason recorded",
        )


class SerialLink:
    def __init__(
        self,
        path: str,
        opener: Callable[[str], Port],
        clock: Callable[[], float],
    ) -> None:
        self._path = path
        self._opener = opener
        self._clock = clock
        self._board_log = BoardLogRelay(clock)
        self._port: Port | None = None
        self._backoff = 0.0
        self._next_try = 0.0
        # Logged-state flags, so an outage logs once, not every second.
        self._said_cannot_open = False
        self._rx = bytearray()
        self._discarding = False

    @property
    def connected(self) -> bool:
        return self._port is not None

    def _try_open(self) -> bool:
        now = self._clock()
        if now < self._next_try:
            return False
        try:
            self._port = self._opener(resolve_device(self._path))
        except (OSError, serial.SerialException) as exc:
            self._backoff = min(MAX_BACKOFF_S, max(MIN_BACKOFF_S, self._backoff * 2))
            self._next_try = now + self._backoff
            if not self._said_cannot_open:
                logger.warning(
                    "cannot open %s: %s; retrying with backoff", self._path, exc
                )
                self._said_cannot_open = True
            else:
                logger.debug(
                    "still cannot open %s; next try in %.0f s",
                    self._path,
                    self._backoff,
                )
            return False
        logger.info("connected to %s", self._path)
        self._backoff = 0.0
        self._said_cannot_open = False
        self._rx.clear()
        self._discarding = False
        return True

    def _drop(self, exc: BaseException) -> None:
        logger.warning("lost %s: %s", self._path, exc)
        self.close()
        # A port that fails at once must not be hammered: retry after the floor.
        self._backoff = MIN_BACKOFF_S
        self._next_try = self._clock() + self._backoff
        # The next failed open is part of this outage, already logged.
        self._said_cannot_open = True

    def send(self, line: bytes) -> bool:
        """Write one frame. False when the board is not there (it is retried)."""
        if self._port is None and not self._try_open():
            return False
        assert self._port is not None
        try:
            # No flush(): it calls tcdrain(), which can block forever on a
            # board that stops reading and raises termios.error on hang-up.
            # write_timeout already bounds the write.
            self._port.write(line)
        except (OSError, serial.SerialException) as exc:
            self._drop(exc)
            return False
        return True

    def poll(self) -> list[dict[str, Any]]:
        """Read whatever the board sent. Log a hello. Return the JSON objects."""
        if self._port is None:
            return []
        try:
            waiting = self._port.in_waiting
            data = self._port.read(waiting) if waiting else b""
        except (OSError, serial.SerialException) as exc:
            self._drop(exc)
            return []
        out: list[dict[str, Any]] = []
        for byte in data:
            if byte == 0x0A:
                if not self._discarding:
                    msg = self._parse(bytes(self._rx))
                    if msg is not None:
                        out.append(msg)
                self._rx.clear()
                self._discarding = False
            elif not self._discarding:
                self._rx.append(byte)
                if len(self._rx) > MAX_INCOMING_LINE:
                    self._rx.clear()
                    self._discarding = True
        return out

    def _parse(self, raw: bytes) -> dict[str, Any] | None:
        raw = raw.strip()
        if not raw:
            return None
        try:
            msg = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            msg = None
        if not isinstance(msg, dict):
            # A log line or the boot banner: never data, relayed to our log.
            self._board_log.relay(raw)
            return None
        if "hello" in msg:
            log_hello(msg)
        return msg

    def close(self) -> None:
        if self._port is not None:
            with contextlib.suppress(OSError, serial.SerialException):
                self._port.close()
            self._port = None
