"""Command line: arguments, dry run, exit codes."""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path

import pytest

from dgx_status import __version__, cli


def test_versions_agree() -> None:
    """pyproject.toml and __init__.py both carry the version; drift is silent."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == __version__


def test_version_flag(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    assert cli.main(["--version"]) == 0
    assert __version__ in caplog.text


def test_no_device_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(cli.ENV_DEVICE, raising=False)
    assert cli.main(["--once"]) == 2


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
