"""The fixture capture script copies exactly the files the parsers read."""

from __future__ import annotations

from pathlib import Path

from capture_fixtures import capture


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
