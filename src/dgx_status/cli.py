"""dgx-status: send one status frame per second to the USB AMOLED display.

The device comes from --device or DGX_STATUS_DEVICE: a path or a glob. With
neither, the daemon looks for the one Espressif USB-Serial-JTAG port under
/dev/serial/by-id/. A glob must match exactly one port.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from dgx_status import __version__, logging_setup
from dgx_status.collector import Collector, Sources
from dgx_status.daemon import LogSink, Sink, run
from dgx_status.gpu import NvmlGpu
from dgx_status.llm import DEFAULT_URL as LLM_DEFAULT_URL
from dgx_status.llm import LlmPoller, LlmReading, check_url
from dgx_status.serial_link import DEFAULT_DEVICE_GLOB, SerialLink, open_port

logger = logging.getLogger(__name__)

ENV_DEVICE = "DGX_STATUS_DEVICE"
ENV_LOG_LEVEL = "DGX_STATUS_LOG_LEVEL"
ENV_LAN = "DGX_STATUS_LAN_IFACE"
ENV_ROCE = "DGX_STATUS_ROCE_PORTS"
ENV_CLUSTER = "DGX_STATUS_CLUSTER_IFACES"
ENV_SSD = "DGX_STATUS_SSD_DISK"
ENV_DISK_PATH = "DGX_STATUS_DISK_PATH"
ENV_LLM_URL = "DGX_STATUS_LLM_URL"


def _names(value: str | None) -> tuple[str, ...] | None:
    """A comma list; None (detect) when unset or empty."""
    if not value:
        return None
    names = tuple(n.strip() for n in value.split(",") if n.strip())
    return names or None


def llm_source(env: Mapping[str, str]) -> Callable[[], LlmReading | None]:
    """Start the LLM exporter poller, or report idle when the URL is `off`.

    `off` sends llm_up 0. A URL that is not http(s) on loopback is refused
    at start: the LLM fields stay unknown, so the board hides the page.
    """
    url = (env.get(ENV_LLM_URL) or LLM_DEFAULT_URL).strip()
    if url.lower() == "off":
        idle = LlmReading(engines_up=0)
        return lambda: idle
    why = check_url(url)
    if why is not None:
        logger.error(
            "%s=%s refused: %s; the LLM page stays hidden", ENV_LLM_URL, url, why
        )
        return lambda: None
    poller = LlmPoller(url)
    poller.start()
    return poller.reading


def sources_from_env(env: Mapping[str, str]) -> Sources:
    """The overrides for the automatic choices (README, .env.example)."""
    roce = _names(env.get(ENV_ROCE))
    if roce is not None:
        # A bare device name means its port 1.
        roce = tuple(p if "/" in p else f"{p}/1" for p in roce)
    return Sources(
        lan_iface=env.get(ENV_LAN) or None,
        roce_ports=roce,
        cluster_ifaces=_names(env.get(ENV_CLUSTER)),
        ssd_disk=env.get(ENV_SSD) or None,
        disk_path=env.get(ENV_DISK_PATH) or "/",
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dgx-status", description=__doc__)
    p.add_argument("--version", action="store_true", help="log the version and exit")
    p.add_argument(
        "--device",
        default=os.environ.get(ENV_DEVICE) or DEFAULT_DEVICE_GLOB,
        help=f"serial device path or glob (default: ${ENV_DEVICE}, else %(default)s)",
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
    else:
        sink = SerialLink(args.device, opener=open_port, clock=time.monotonic)

    llm = llm_source(os.environ)
    collector = Collector(
        proc_root=args.proc,
        sys_root=args.sys,
        gpu=NvmlGpu(),
        host=short_hostname(),
        clock=time.time,
        sources=sources_from_env(os.environ),
        llm=llm,
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
