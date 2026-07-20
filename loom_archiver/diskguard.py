from __future__ import annotations

import os
import shutil
from pathlib import Path


class DiskFullError(Exception):
    pass


class MountError(Exception):
    pass


def free_gb(path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / (1024 ** 3)


def has_headroom(path, floor_gb: int) -> bool:
    return free_gb(path) >= floor_gb


def assert_headroom(path, floor_gb: int) -> None:
    free = free_gb(path)
    if free < floor_gb:
        raise DiskFullError(
            f"Only {free:.1f} GB free at {path}; floor is {floor_gb} GB. "
            f"Free space and rerun to resume."
        )


def assert_dest_mounted(path) -> None:
    """If the destination is under /Volumes (an external/network mount), ensure that
    volume is actually mounted, so writes don't silently land on the local boot disk."""
    p = Path(path).resolve()
    parts = p.parts
    if len(parts) >= 3 and parts[1] == "Volumes":
        volume_root = Path("/") / parts[1] / parts[2]  # /Volumes/<name>
        if not volume_root.exists() or not os.path.ismount(volume_root):
            raise MountError(
                f"Destination volume {volume_root} is not mounted. "
                f"Mount it and rerun, or pass --dest to a mounted location."
            )
