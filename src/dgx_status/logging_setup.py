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
    """Set up the stdout handler. An unknown level falls back to INFO, with a
    warning: a typo in the unit's environment must not stop the daemon."""
    name = level.strip().upper()
    valid = isinstance(logging.getLevelNamesMapping().get(name), int)
    logging.basicConfig(
        level=name if valid else logging.INFO, format=_FORMAT, stream=sys.stdout
    )
    if not valid:
        logging.getLogger(__name__).warning("unknown log level %r; using INFO", level)
