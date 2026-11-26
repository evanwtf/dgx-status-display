"""Relay the board's own log lines into the daemon's log.

The board sends ESP-IDF log lines and its ROM boot banner on the same USB
port as its JSON messages (docs/protocol.md). Lines that are not JSON come
here:

- `E` and `W` lines go to WARNING, at most once per distinct message per
  window; the next one after the window says how many were held back.
- The ROM's `rst:` line goes to WARNING every time: each reboot shows in the
  journal with its reason.
- `I`, `D`, `V`, the rest of the boot banner, and anything else go to DEBUG.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

WINDOW_S = 60.0
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# "E (11760) task_wdt: Task watchdog got triggered." The number is the
# board's uptime in ms; it is not part of the message.
_ESP_LOG = re.compile(r"^([EWIDV]) \((\d+)\) ([^:]+): ?(.*)$")
_ROM_RESET = re.compile(r"^rst:0x[0-9a-fA-F]+ ")


@dataclass
class _Seen:
    logged_at: float
    held: int = 0


def clean(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = _ANSI.sub("", text)
    return "".join(c if c.isprintable() else " " for c in text).strip()


class BoardLogRelay:
    def __init__(self, clock: Callable[[], float], window_s: float = WINDOW_S) -> None:
        self._clock = clock
        self._window = window_s
        self._seen: dict[str, _Seen] = {}

    def relay(self, raw: bytes) -> None:
        line = clean(raw)
        if not line:
            return
        if _ROM_RESET.match(line):
            logger.warning("board reset: %s", line)
            return
        m = _ESP_LOG.match(line)
        if m is None or m.group(1) not in "EW":
            logger.debug("board: %s", line)
            return
        level, _, tag, msg = m.groups()
        key = f"{level} {tag}: {msg}"
        now = self._clock()
        seen = self._seen.get(key)
        if seen is not None and now - seen.logged_at < self._window:
            seen.held += 1
            return
        held = seen.held if seen is not None else 0
        self._seen[key] = _Seen(logged_at=now)
        if held:
            logger.warning(
                "board: %s (and %d more in the last %.0f s)", key, held, self._window
            )
        else:
            logger.warning("board: %s", key)
        self._forget(now)

    def _forget(self, now: float) -> None:
        """Drop entries older than two windows, so the map stays small."""
        old = [
            k for k, s in self._seen.items() if now - s.logged_at >= 2 * self._window
        ]
        for k in old:
            del self._seen[k]
