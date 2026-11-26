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
        tz_s=-18000,
        up_s=1214489,
        load=(0.95, 0.54, 0.32),
        cpu_pct=5.17,
        mem_used_gib=7.6998,
        mem_total_gib=127.875,
        gpu_pct=3,
        gpu_w=4.6,
        gpu_c=43,
        occ="idle",
        fans={"fan0": 2790, "fan1": 4185},
        board_c=49.5,
        nvme_c=44.85,
        nic_c=49.0,
        temps=[("tz0", 49.5), ("nvme", 44.85), ("nic0", 49.0)],
        lan_rx_bps=32_640.4,
        lan_tx_bps=67_400.6,
        lan_mbps=2500,
        roce_rx_bps=180e9,
        roce_tx_bps=0.0,
        cip_bps=3_680.0,
        ssd_rd_bytes_s=115_194_000.2,
        ssd_wr_bytes_s=767_000.0,
        disk_used_gb=3340,
        disk_size_gb=4032,
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
        "tz_s": -18000,
        "up_s": 1214489,
        "load1": 0.95,
        "load5": 0.54,
        "load15": 0.32,
        "cpu_pct": 5.2,
        "mem_used_gib": 7.7,
        "mem_total_gib": 127.9,
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
        "lan_rx_bps": 32640,
        "lan_tx_bps": 67401,
        "lan_mbps": 2500,
        "roce_rx_bps": 180_000_000_000,
        "roce_tx_bps": 0,
        "cip_bps": 3680,
        "ssd_rd_Bps": 115_194_000,
        "ssd_wr_Bps": 767_000,
        "disk_used_gb": 3340,
        "disk_size_gb": 4032,
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
    s = Snapshot(
        t=1, host="test-host", cpu_pct=math.nan, gpu_w=math.inf, board_c=-math.inf
    )
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


@pytest.mark.parametrize(("bad", "sent"), [("a\nb", "ab"), ('a"b', 'a"b')])
def test_control_characters_dropped_quotes_kept(bad: str, sent: str) -> None:
    f = _decode(encode_frame(Snapshot(t=1, host=bad)))
    assert f["host"] == sent


def test_absurd_tz_offset_is_omitted() -> None:
    f = _decode(encode_frame(Snapshot(t=1, host="test-host", tz_s=20 * 3600)))
    assert "tz_s" not in f


def _worst() -> Snapshot:
    """Every field present at its longest."""
    big = 9_999_999_999_999.0  # just under MAX_RATE: 13 digits
    return Snapshot(
        t=9_999_999_999,
        host="h" * 500,
        tz_s=-50400,
        up_s=9_999_999_999,
        load=(9999.99, 9999.99, 9999.99),
        cpu_pct=100.0,
        mem_used_gib=4095.9,
        mem_total_gib=4095.9,
        gpu_pct=100,
        gpu_w=2000.0,
        gpu_c=-40,
        occ="o" * 500,
        fans={"fan0": 30000, "fan1": 30000},
        board_c=-40.5,
        nvme_c=-40.5,
        nic_c=-40.5,
        temps=[("x" * 40, -40.5)] * 100,
        lan_rx_bps=big,
        lan_tx_bps=big,
        lan_mbps=9_999_999,
        roce_rx_bps=big,
        roce_tx_bps=big,
        cip_bps=big,
        ssd_rd_bytes_s=big,
        ssd_wr_bytes_s=big,
        disk_used_gb=999_999_999,
        disk_size_gb=999_999_999,
        llm_up=1000,
        llm_model="m" * 500,
        llm_out_tps=9_999_999.9,
        llm_in_tps=9_999_999.9,
        llm_req=1_000_000,
        llm_kv_pct=100.0,
        llm_spec_pct=100.0,
        llm_ttft_ms=9_999_999,
    )


def test_worst_case_frame_fits_with_every_overview_field() -> None:
    """Only `th` may be cut. Every overview field survives the worst case."""
    s = _worst()
    line = encode_frame(s)
    assert len(line) <= MAX_LINE_BYTES
    f = _decode(line)
    s.temps = []
    overview = _decode(encode_frame(s))
    assert {k: v for k, v in f.items() if k != "th"} == overview
    assert len(overview) == 38
    # Room left for the detail list: about ten short sensors always fit.
    assert len(encode_frame(s)) < MAX_LINE_BYTES - 150


@pytest.mark.parametrize(
    "field", ["lan_rx_bps", "roce_tx_bps", "cip_bps", "ssd_rd_bytes_s"]
)
@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf, 2e13])
def test_bad_rates_are_omitted(field: str, bad: float) -> None:
    s = _full()
    setattr(s, field, bad)
    f = _decode(encode_frame(s))
    key = field.replace("_bytes_s", "_Bps")
    assert key not in f


def test_disk_used_over_size_is_omitted() -> None:
    s = _full()
    s.disk_used_gb = 5000
    f = _decode(encode_frame(s))
    assert "disk_used_gb" not in f and "disk_size_gb" not in f


def test_link_speed_must_be_positive() -> None:
    s = _full()
    s.lan_mbps = -1  # /sys/class/net/X/speed reads -1 when the link is down
    assert "lan_mbps" not in _decode(encode_frame(s))


def test_llm_idle_sends_only_the_engine_count() -> None:
    s = _full()
    s.llm_up = 0
    f = _decode(encode_frame(s))
    assert f["llm_up"] == 0
    assert not [k for k in f if k.startswith("llm_") and k != "llm_up"]


def test_llm_unreachable_sends_nothing() -> None:
    s = _full()
    s.llm_out_tps = 45.0  # ignored without llm_up
    assert not [k for k in _decode(encode_frame(s)) if k.startswith("llm_")]


def test_llm_fields() -> None:
    s = _full()
    s.llm_up = 1
    s.llm_model = "MiMo-v2.6-Flash"
    s.llm_out_tps = 45.04
    s.llm_in_tps = 2000.0
    s.llm_req = 3
    s.llm_kv_pct = 62.04
    s.llm_spec_pct = 66.6
    s.llm_ttft_ms = 4668.7
    f = _decode(encode_frame(s))
    assert {k: v for k, v in f.items() if k.startswith("llm_")} == {
        "llm_up": 1,
        "llm_model": "MiMo-v2.6-Flash",
        "llm_out_tps": 45.0,
        "llm_in_tps": 2000.0,
        "llm_req": 3,
        "llm_kv_pct": 62.0,
        "llm_spec_pct": 66.6,
        "llm_ttft_ms": 4669,
    }
    s.llm_kv_pct = 100.4  # rounds to 100.4: over 100 is dropped
    s.llm_out_tps = -1.0
    f = _decode(encode_frame(s))
    assert "llm_kv_pct" not in f and "llm_out_tps" not in f


UGLY = [
    "\U0001f525" * 40,  # emoji: two \\uXXXX escapes each
    "中文模型名称" * 20,  # CJK: one \\uXXXX escape each
    "a\x00b\x1bc\nd\x7f" * 30,  # control characters
    'q"uote\\back' * 20,
]


@pytest.mark.parametrize("text", UGLY)
def test_unicode_and_control_strings_fit_at_the_worst_case(text: str) -> None:
    s = _worst()
    s.host = text
    s.occ = text
    s.llm_model = text
    s.temps = [(text, 99.9)] * 24
    line = encode_frame(s)
    assert len(line) <= MAX_LINE_BYTES
    f = _decode(line)
    # Each string fits the board's buffer as decoded UTF-8.
    for key, cap in (("host", 32), ("occ", 48), ("llm_model", 48)):
        if key in f:
            value = f[key]
            assert isinstance(value, str)
            assert len(value.encode()) <= cap
            assert not any(ord(c) < 0x20 or ord(c) == 0x7F for c in value)
    assert f["host"]
    th = f.get("th", [])
    assert isinstance(th, list)
    for label, _ in th:
        assert len(label.encode()) <= 15


def test_clip_counts_escaped_width() -> None:
    from dgx_status.frame import clip

    assert clip("abc", 2) == "ab"
    assert clip("中文", 6) == "中"  # \\u4e2d is 6 wide
    assert clip("\U0001f525x", 11) == ""  # the emoji is 12 wide
    assert clip("\U0001f525x", 13) == "\U0001f525x"
    assert clip("a\tb", 5) == "ab"
    assert clip("", 5) == ""


def test_model_then_occ_go_when_still_over() -> None:
    s = _worst()
    s.temps = []
    s.host = "\U0001f525" * 2  # 24 escaped characters
    line = encode_frame(s)
    assert len(line) <= MAX_LINE_BYTES


def test_link_speed_over_the_cap_is_left_out() -> None:
    s = _full()
    s.lan_mbps = 10**7 + 1
    assert "lan_mbps" not in _decode(encode_frame(s))
    s.lan_mbps = 10**7
    assert _decode(encode_frame(s))["lan_mbps"] == 10**7
