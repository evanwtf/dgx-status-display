"""Frame building and the 1024-byte cap."""

from __future__ import annotations

import json
import math

import pytest

from dgx_status.frame import MAX_LINE_BYTES, Snapshot, encode_frame


def _full() -> Snapshot:
    return Snapshot(
        t=1_795_000_000,
        host="test-host",
        up_s=1214489,
        load=(0.95, 0.54, 0.32),
        cpu_pct=5.17,
        mem_used_gib=7.6998,
        mem_total_gib=121.6875,
        gpu_pct=3,
        gpu_w=4.6,
        gpu_c=43,
        occ="idle",
        fans={"fan0": 2790, "fan1": 4185},
        board_c=49.5,
        nvme_c=44.85,
        nic_c=49.0,
        temps=[("tz0", 49.5), ("nvme", 44.85), ("nic0", 49.0)],
    )


def _decode(line: bytes) -> dict[str, object]:
    assert line.endswith(b"\n")
    assert b"\n" not in line[:-1]
    obj = json.loads(line)
    assert isinstance(obj, dict)
    return obj


def test_full_frame_values() -> None:
    f = _decode(encode_frame(_full()))
    assert f == {
        "v": 1,
        "t": 1_795_000_000,
        "host": "test-host",
        "up_s": 1214489,
        "load1": 0.95,
        "load5": 0.54,
        "load15": 0.32,
        "cpu_pct": 5.2,
        "mem_used_gib": 7.7,
        "mem_total_gib": 121.7,
        "gpu_pct": 3,
        "gpu_w": 5,
        "gpu_c": 43,
        "occ": "idle",
        "fan0_rpm": 2790,
        "fan1_rpm": 4185,
        "board_c": 49.5,
        "nvme_c": 44.9,
        "nic_c": 49.0,
        "th": [["tz0", 49.5], ["nvme", 44.9], ["nic0", 49.0]],
    }


def test_no_sm_clock_ever() -> None:
    """Evan dropped the SM clock as a useless indicator."""
    line = encode_frame(_full()).decode()
    assert "clk" not in line
    assert "sm" not in json.loads(line)


def test_missing_readings_are_omitted() -> None:
    f = _decode(encode_frame(Snapshot(t=1, host="test-host")))
    assert f == {"v": 1, "t": 1, "host": "test-host"}


def test_nan_and_inf_are_omitted() -> None:
    s = Snapshot(t=1, host="test-host", cpu_pct=math.nan, gpu_w=math.inf, board_c=-math.inf)
    f = _decode(encode_frame(s))
    assert "cpu_pct" not in f
    assert "gpu_w" not in f
    assert "board_c" not in f


def test_gpu_power_rounds_to_an_integer() -> None:
    f = _decode(encode_frame(Snapshot(t=1, host="test-host", gpu_w=81.5)))
    assert f["gpu_w"] == 82
    assert isinstance(f["gpu_w"], int)


def test_frame_is_compact() -> None:
    line = encode_frame(_full())
    assert b": " not in line
    assert b", " not in line


def test_long_host_is_truncated() -> None:
    f = _decode(encode_frame(Snapshot(t=1, host="h" * 200)))
    assert f["host"] == "h" * 32


def test_long_occupant_is_truncated() -> None:
    f = _decode(encode_frame(Snapshot(t=1, host="test-host", occ="x" * 200)))
    assert f["occ"] == "x" * 48


def test_oversize_detail_is_dropped_first() -> None:
    temps = [(f"sensor{i:03d}", 40.0 + i / 10) for i in range(200)]
    s = Snapshot(t=1, host="test-host", gpu_c=43, temps=temps)
    line = encode_frame(s)
    assert len(line) <= MAX_LINE_BYTES
    f = _decode(line)
    assert f["gpu_c"] == 43
    th = f["th"]
    assert isinstance(th, list)
    assert 0 < len(th) < 200


def test_every_frame_fits_the_cap() -> None:
    s = _full()
    s.temps = [("x" * 40, 1.0)] * 100
    s.host = "y" * 500
    s.occ = "z" * 500
    assert len(encode_frame(s)) <= MAX_LINE_BYTES


def test_non_ascii_host_is_escaped() -> None:
    """The firmware reads bytes. Keep every frame plain ASCII."""
    line = encode_frame(Snapshot(t=1, host="test-host-é"))
    assert line.isascii()


@pytest.mark.parametrize("bad", ["a\nb", 'a"b'])
def test_control_and_quote_characters_stay_inside_json(bad: str) -> None:
    f = _decode(encode_frame(Snapshot(t=1, host=bad)))
    assert f["host"] == bad
