"""The fixture capture script copies exactly the files the parsers read."""

from __future__ import annotations

from pathlib import Path

from capture_fixtures import capture, redact_cmdline


def test_capture_round_trips_the_fixture_tree(dgx_root: Path, tmp_path: Path) -> None:
    proc = tmp_path / "src_proc"
    proc.mkdir()
    for name in ("meminfo", "loadavg", "uptime"):
        (proc / name).write_bytes((dgx_root / "proc" / name).read_bytes())
    (proc / "stat").write_bytes((dgx_root / "proc" / "stat.t0").read_bytes())

    out = tmp_path / "out"
    n = capture(proc, dgx_root / "sys", out, pids=[], delay=0)
    assert n > 0
    assert (out / "proc" / "stat.t0").read_bytes() == (proc / "stat").read_bytes()
    assert (out / "proc" / "stat.t1").exists()
    fan = out / "sys/class/hwmon/hwmon13/fan1_input"
    assert fan.read_text().strip() == "2790"
    for d in (dgx_root / "sys/class/hwmon").iterdir():
        for f in d.iterdir():
            assert (out / "sys/class/hwmon" / d.name / f.name).read_bytes() == (
                f.read_bytes()
            )


def test_missing_files_are_skipped(tmp_path: Path) -> None:
    n = capture(tmp_path / "none", tmp_path / "none", tmp_path / "out", [1], delay=0)
    assert n == 0


def test_redact_keeps_argv0_only() -> None:
    raw = b"/usr/bin/llama-server\0-m\0/srv/models/x.gguf\0--api-key\0s3cr3t\0"
    assert redact_cmdline(raw) == (
        b"/usr/bin/llama-server\0REDACTED\0REDACTED\0REDACTED\0REDACTED\0"
    )
    assert redact_cmdline(b"ollama\0") == b"ollama\0"
    assert redact_cmdline(b"") == b""


def _one_process(tmp_path: Path) -> Path:
    proc = tmp_path / "proc"
    (proc / "9").mkdir(parents=True)
    (proc / "9" / "comm").write_text("vllm\n")
    (proc / "9" / "cmdline").write_bytes(b"vllm\0serve\0/srv/model\0")
    return proc


def test_capture_redacts_cmdline_by_default(tmp_path: Path) -> None:
    proc = _one_process(tmp_path)
    capture(proc, tmp_path / "none", tmp_path / "out", [9], delay=0)
    got = (tmp_path / "out/proc/9/cmdline").read_bytes()
    assert got == b"vllm\0REDACTED\0REDACTED\0"


def test_capture_keep_args(tmp_path: Path) -> None:
    proc = _one_process(tmp_path)
    capture(proc, tmp_path / "none", tmp_path / "out", [9], delay=0, keep_args=True)
    got = (tmp_path / "out/proc/9/cmdline").read_bytes()
    assert got == b"vllm\0serve\0/srv/model\0"


def test_route_keeps_defaults_with_no_gateway() -> None:
    from capture_fixtures import sanitize_route

    text = (
        "Iface\tDestination\tGateway \tFlags\n"
        "eth0\t00000000\t0101A8C0\t0003\n"
        "eth0\t0001A8C0\t00000000\t0001\n"
    )
    assert sanitize_route(text) == (
        "Iface\tDestination\tGateway \tFlags\neth0\t00000000\t00000000\t0003\n"
    )


def test_mounts_keep_root_only() -> None:
    from capture_fixtures import sanitize_mounts

    text = "/dev/sda2 / ext4 rw 0 0\n/dev/sda3 /srv/private ext4 rw 0 0\n"
    assert sanitize_mounts(text) == "/dev/sda2 / ext4 rw 0 0\n"


def test_uevent_keeps_driver_only() -> None:
    from capture_fixtures import driver_only

    text = "DRIVER=mlx5_core\nPCI_SLOT_NAME=0000:01:00.0\nMODALIAS=pci:v1\n"
    assert driver_only(text) == "DRIVER=mlx5_core\n"


def test_capture_takes_counters_twice_and_no_identifiers(
    dgx_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"
    capture(dgx_root / "proc", dgx_root / "sys", out, pids=[], delay=0)
    rx = "class/net/enP7s7/statistics/rx_bytes"
    assert (out / "sys" / rx).read_text().strip() == "1445319700486"
    assert (out / "sys_t1" / rx).exists()
    assert (out / "sys/devices/system/memory/block_size_bytes").exists()
    assert (out / "sys/class/net/enp1s0f0np0/device/uevent").read_text() == (
        "DRIVER=mlx5_core\n"
    )
    names = {p.name for p in out.rglob("*")}
    assert not names & {"address", "node_guid", "sys_image_guid", "serial"}
