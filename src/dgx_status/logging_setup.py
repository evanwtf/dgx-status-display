"""Logging for every entry point.

Every line goes through `logging`, frames included. The stream is stdout, so
`dgx-status --dry-run > out.txt` captures the output, and journald gets it
from the systemd unit.
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def configure(level: str = "INFO") -> None:
    logging.basicConfig(level=level.upper(), format=_FORMAT, stream=sys.stdout)
