"""dgx-status: send one status frame per second to the USB AMOLED display.

The device path comes from --device or DGX_STATUS_DEVICE. Use the stable
/dev/serial/by-id/ path, not /dev/ttyACM0.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
from collections.abc import Sequence
from pathlib import Path

from dgx_status import __version__, logging_setup
from dgx_status.collector import Collector
from dgx_status.daemon import LogSink, Sink, run
from dgx_status.gpu import NvmlGpu
from dgx_status.serial_link import SerialLink, open_port

logger = logging.getLogger(__name__)

ENV_DEVICE = "DGX_STATUS_DEVICE"
ENV_LOG_LEVEL = "DGX_STATUS_LOG_LEVEL"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dgx-status", description=__doc__)
    p.add_argument("--version", action="store_true", help="log the version and exit")
    p.add_argument(
        "--device",
        default=os.environ.get(ENV_DEVICE),
        help=f"serial device path (default: ${ENV_DEVICE})",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="log each frame; do not open the device"
    )
    p.add_argument("--once", action="store_true", help="send one frame, then exit")
    p.add_argument(
        "--interval", type=float, default=1.0, help="seconds between frames (default 1)"
    )
    p.add_argument("--proc", type=Path, default=Path("/proc"), help=argparse.SUPPRESS)
    p.add_argument("--sys", type=Path, default=Path("/sys"), help=argparse.SUPPRESS)
    return p


def short_hostname() -> str:
    return socket.gethostname().split(".")[0]


def main(argv: Sequence[str] | None = None) -> int:
    logging_setup.configure(os.environ.get(ENV_LOG_LEVEL, "INFO"))
    args = _parser().parse_args(argv)
    if args.version:
        logger.info("dgx-status %s", __version__)
        return 0
    if args.interval <= 0:
        logger.error("--interval must be above zero")
        return 2

    sink: Sink
    if args.dry_run:
        sink = LogSink()
    elif not args.device:
        logger.error("no device: pass --device or set %s", ENV_DEVICE)
        return 2
    else:
        sink = SerialLink(args.device, opener=open_port, clock=time.monotonic)

    collector = Collector(
        proc_root=args.proc,
        sys_root=args.sys,
        gpu=NvmlGpu(),
        host=short_hostname(),
        clock=time.time,
    )
    logger.info(
        "dgx-status %s: %s every %.1f s",
        __version__,
        "dry run" if args.dry_run else args.device,
        args.interval,
    )
    try:
        delivered = run(collector, sink, args.interval, 1 if args.once else None)
    except KeyboardInterrupt:
        logger.info("stopped")
        return 0
    finally:
        sink.close()
    return 0 if delivered else 1
