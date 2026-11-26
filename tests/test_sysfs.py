"""Thermal zones and hwmon, against the tree captured on the DGX."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dgx_status.sysfs import read_fans, read_hwmon, read_thermal_zones, summarize


def test_thermal_zones(sys_root: Path) -> None:
    zones = read_thermal_zones(sys_root)
    assert [z.label for z in zones] == [f"tz{i}" for i in range(7)]
    assert all(z.kind == "acpitz" for z in zones)
    assert zones[0].celsius == pytest.approx(49.5)


def test_hwmon_finds_devices_by_name(sys_root: Path) -> None:
    names = sorted({d.name for d in read_hwmon(sys_root)})
    assert names == [
        "acpitz_0",
        "acpitz_1",
        "acpitz_2",
        "acpitz_3",
        "acpitz_4",
        "acpitz_5",
        "acpitz_6",
        "mlx5",
        "mt7925_phy0",
        "nvfanread",
        "nvme",
    ]


def test_fans_from_nvfanread(sys_root: Path) -> None:
    fans = read_fans(read_hwmon(sys_root))
    # fan1_input is labelled fan0, fan2_input is labelled fan1.
    assert fans == {"fan0": 2790, "fan1": 4185}


def test_fans_found_by_name_not_by_hwmon_number(sys_root: Path, tmp_path: Path) -> None:
    """The hwmonN number changes across boots. Only `name` is stable."""
    root = tmp_path / "sys"
    shutil.copytree(sys_root, root)
    hw = root / "class" / "hwmon"
    (hw / "hwmon13").rename(hw / "hwmon2_moved")
    (hw / "hwmon2").rename(hw / "hwmon13")
    (hw / "hwmon2_moved").rename(hw / "hwmon2")
    assert read_fans(read_hwmon(root)) == {"fan0": 2790, "fan1": 4185}


def test_fans_missing_module_is_unknown(sys_root: Path, tmp_path: Path) -> None:
    root = tmp_path / "sys"
    shutil.copytree(sys_root, root)
    shutil.rmtree(root / "class" / "hwmon" / "hwmon13")
    assert read_fans(read_hwmon(root)) == {}


def test_summary_board_nvme_nic(sys_root: Path) -> None:
    s = summarize(read_thermal_zones(sys_root), read_hwmon(sys_root))
    assert s.board_c == pytest.approx(49.5)  # max of the 7 acpitz zones
    assert s.nvme_c == pytest.approx(44.85)  # the Composite sensor
    assert s.nic_c == pytest.approx(49.0)  # max of the 4 mlx5 devices


def test_summary_detail_lists_every_sensor(sys_root: Path) -> None:
    s = summarize(read_thermal_zones(sys_root), read_hwmon(sys_root))
    labels = [label for label, _ in s.detail]
    assert labels[:7] == [f"tz{i}" for i in range(7)]
    assert "nvme" in labels
    assert "nvme s1" in labels
    assert "nvme s2" in labels
    assert [x for x in labels if x.startswith("nic")] == [
        "nic0",
        "nic1",
        "nic2",
        "nic3",
    ]
    assert "wifi" in labels
    # acpitz_N hwmon devices duplicate the thermal zones; list them once.
    assert not any(x.startswith("acpitz") for x in labels)


def test_empty_tree_is_unknown(tmp_path: Path) -> None:
    s = summarize(read_thermal_zones(tmp_path), read_hwmon(tmp_path))
    assert s.board_c is None
    assert s.nvme_c is None
    assert s.nic_c is None
    assert s.detail == []


def test_unreadable_values_are_skipped(tmp_path: Path) -> None:
    z = tmp_path / "class" / "thermal" / "thermal_zone0"
    z.mkdir(parents=True)
    (z / "type").write_text("acpitz\n")
    (z / "temp").write_text("garbage\n")
    h = tmp_path / "class" / "hwmon" / "hwmon0"
    h.mkdir(parents=True)
    (h / "name").write_text("nvfanread\n")
    (h / "fan1_input").write_text("\n")
    (h / "fan1_label").write_text("fan0\n")
    assert read_thermal_zones(tmp_path) == []
    assert read_fans(read_hwmon(tmp_path)) == {}
