"""Command line: arguments, dry run, exit codes."""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path

import pytest

from dgx_status import __version__, cli
from dgx_status.serial_link import DEFAULT_DEVICE_GLOB


def test_versions_agree() -> None:
    """pyproject.toml and __init__.py both carry the version; drift is silent."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == __version__


def test_version_flag(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    assert cli.main(["--version"]) == 0
    assert __version__ in caplog.text


def test_default_device_is_the_espressif_glob(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cli.ENV_DEVICE, raising=False)
    args = cli._parser().parse_args([])
    assert args.device == DEFAULT_DEVICE_GLOB
    assert args.device.startswith("/dev/serial/by-id/usb-Espressif_USB_JTAG")


def test_env_device_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cli.ENV_DEVICE, "/dev/ttyTEST0")
    assert cli._parser().parse_args([]).device == "/dev/ttyTEST0"


def test_bad_interval_is_an_error() -> None:
    assert cli.main(["--dry-run", "--interval", "0"]) == 2


def test_dry_run_once_on_fixtures(
    dgx_root: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end over the captured tree, with NVML forced unavailable."""
    monkeypatch.setattr(cli, "NvmlGpu", lambda: _NoGpu())
    caplog.set_level(logging.INFO)
    rc = cli.main(
        [
            "--dry-run",
            "--once",
            "--interval",
            "0.01",
            "--proc",
            str(dgx_root / "proc"),
            "--sys",
            str(dgx_root / "sys"),
        ]
    )
    assert rc == 0
    frames = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("frame ")
    ]
    assert len(frames) == 1
    obj = json.loads(frames[0].split(": ", 1)[1])
    assert obj["v"] == 1
    assert obj["mem_used_gib"] == 7.7
    assert obj["fan0_rpm"] == 2790
    assert "gpu_w" not in obj
    assert "occ" not in obj


def test_unopenable_device_once_fails(tmp_path: Path, dgx_root: Path) -> None:
    rc = cli.main(
        [
            "--device",
            str(tmp_path / "no-such-tty"),
            "--once",
            "--interval",
            "0.01",
            "--proc",
            str(dgx_root / "proc"),
            "--sys",
            str(dgx_root / "sys"),
        ]
    )
    assert rc == 1


class _NoGpu:
    def read(self) -> object:
        from dgx_status.gpu import GpuReading

        return GpuReading()


def test_sources_default_to_detection() -> None:
    from dgx_status.cli import sources_from_env
    from dgx_status.collector import Sources

    assert sources_from_env({}) == Sources()
    assert sources_from_env({"DGX_STATUS_ROCE_PORTS": " , "}) == Sources()


def test_sources_from_env() -> None:
    from dgx_status.cli import sources_from_env

    s = sources_from_env(
        {
            "DGX_STATUS_LAN_IFACE": "eth0",
            "DGX_STATUS_ROCE_PORTS": "mlx5_0, mlx5_1/2",
            "DGX_STATUS_CLUSTER_IFACES": "ib0,ib1",
            "DGX_STATUS_SSD_DISK": "sda",
            "DGX_STATUS_DISK_PATH": "/data",
        }
    )
    assert s.lan_iface == "eth0"
    assert s.roce_ports == ("mlx5_0/1", "mlx5_1/2")
    assert s.cluster_ifaces == ("ib0", "ib1")
    assert s.ssd_disk == "sda"
    assert s.disk_path == "/data"


def test_llm_off_reads_as_idle() -> None:
    from dgx_status.cli import ENV_LLM_URL, llm_source
    from dgx_status.llm import LlmReading

    assert llm_source({ENV_LLM_URL: " OFF "})() == LlmReading(engines_up=0)
