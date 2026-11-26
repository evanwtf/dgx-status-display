"""The root disk's counters and its use."""

from __future__ import annotations

import os
from pathlib import Path

from dgx_status import storage


def test_root_mount_and_its_disk(proc_root: Path, sys_root: Path) -> None:
    src = storage.mount_source(proc_root)
    assert src == "/dev/nvme0n1p2"
    assert storage.disk_for(sys_root, src) == "nvme0n1"
    assert storage.disk_for(sys_root, "/dev/nvme0n1") == "nvme0n1"


def test_stacked_or_odd_sources_have_no_disk(sys_root: Path) -> None:
    assert storage.disk_for(sys_root, "/dev/mapper/vg-root") is None
    assert storage.disk_for(sys_root, "overlay") is None
    assert storage.disk_for(sys_root, None) is None


def test_last_mount_of_a_point_wins(tmp_path: Path) -> None:
    (tmp_path / "mounts").write_text(
        "/dev/sda1 / ext4 rw 0 0\n/dev/nvme0n1p2 / ext4 rw 0 0\n"
        "/dev/sdb1 /data ext4 rw 0 0\n"
    )
    assert storage.mount_source(tmp_path) == "/dev/nvme0n1p2"
    assert storage.mount_source(tmp_path, "/data") == "/dev/sdb1"
    assert storage.mount_source(tmp_path, "/x") is None


def test_sectors(sys_root: Path) -> None:
    assert storage.sectors(sys_root, "nvme0n1") == (17052533219, 5338005196)
    assert storage.sectors(sys_root, "nope") is None


def _st(frsize: int, blocks: int, bfree: int) -> os.statvfs_result:
    return os.statvfs_result((frsize, frsize, blocks, bfree, 0, 0, 0, 0, 0, 255))


def test_disk_use_counts_like_df() -> None:
    use = storage.disk_use_gb("/", lambda _p: _st(4096, 984_343_641, 168_993_157))
    assert use == (3340, 4032)


def test_disk_use_failures() -> None:
    def broken(_p: str) -> os.statvfs_result:
        raise OSError("gone")

    assert storage.disk_use_gb("/", broken) is None
    assert storage.disk_use_gb("/", lambda _p: _st(4096, 0, 0)) is None
