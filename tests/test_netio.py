"""Network discovery and counters, on files copied from a Spark."""

from __future__ import annotations

from pathlib import Path

from dgx_status import netio


def test_lan_is_the_default_route(proc_root: Path) -> None:
    assert netio.default_route_iface(proc_root) == "enP7s7"


def _route(tmp_path: Path, rows: list[str]) -> Path:
    head = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\n"
    (tmp_path / "net").mkdir()
    (tmp_path / "net" / "route").write_text(head + "\n".join(rows) + "\n")
    return tmp_path


def test_lowest_metric_default_wins_and_down_routes_are_skipped(
    tmp_path: Path,
) -> None:
    root = _route(
        tmp_path,
        [
            "wlan0\t00000000\t00000000\t0003\t0\t0\t600\t00000000\t0",
            "eth9\t00000000\t00000000\t0002\t0\t0\t1\t00000000\t0",  # not up
            "eth0\t00000000\t00000000\t0003\t0\t0\t100\t00000000\t0",
            "eth1\t0000000A\t00000000\t0001\t0\t0\t0\t000000FF\t0",  # not default
        ],
    )
    assert netio.default_route_iface(root) == "eth0"


def test_no_default_route(tmp_path: Path) -> None:
    assert netio.default_route_iface(_route(tmp_path, [])) is None
    assert netio.default_route_iface(tmp_path / "none") is None


def test_lan_counters_and_speed(sys_root: Path) -> None:
    assert netio.netdev_bytes(sys_root, "enP7s7") == (1445319700486, 777986263556)
    assert netio.link_speed_mbps(sys_root, "enP7s7") == 2500
    assert netio.link_speed_mbps(sys_root, "enp1s0f0np0") == 200000
    assert netio.netdev_bytes(sys_root, "nope") is None


def test_link_down_has_no_speed(tmp_path: Path) -> None:
    d = tmp_path / "class" / "net" / "eth0"
    d.mkdir(parents=True)
    (d / "speed").write_text("-1\n")
    assert netio.link_speed_mbps(tmp_path, "eth0") is None


def test_cluster_nic_is_found_by_driver(sys_root: Path) -> None:
    assert netio.cluster_ifaces(sys_root) == [
        "enP2p1s0f0np0",
        "enP2p1s0f1np1",
        "enp1s0f0np0",
        "enp1s0f1np1",
    ]


def test_roce_ports_are_the_ethernet_link_layer(sys_root: Path, tmp_path: Path) -> None:
    assert netio.roce_ports(sys_root) == [
        "roceP2p1s0f0/1",
        "roceP2p1s0f1/1",
        "rocep1s0f0/1",
        "rocep1s0f1/1",
    ]
    ib = tmp_path / "class" / "infiniband" / "mlx5_0" / "ports" / "1"
    ib.mkdir(parents=True)
    (ib / "link_layer").write_text("InfiniBand\n")
    assert netio.roce_ports(tmp_path) == []


def test_roce_words(sys_root: Path) -> None:
    words = netio.roce_words(sys_root, ["rocep1s0f1/1"])
    assert words == (
        {"rocep1s0f1/1": 1636251676505},
        {"rocep1s0f1/1": 1637381152560},
    )
    assert netio.roce_words(sys_root, ["missing/1"]) is None
