import pytest
from loom_archiver import diskguard


def test_has_headroom_true_for_root(tmp_path):
    # tmp_path almost certainly has > 0 GB free
    assert diskguard.has_headroom(tmp_path, floor_gb=0) is True


def test_assert_headroom_raises_when_floor_impossibly_high(tmp_path):
    with pytest.raises(diskguard.DiskFullError) as exc:
        diskguard.assert_headroom(tmp_path, floor_gb=10_000_000)
    assert "free" in str(exc.value).lower()


def test_assert_dest_mounted_raises_for_unmounted_volume():
    with pytest.raises(diskguard.MountError):
        diskguard.assert_dest_mounted("/Volumes/NoSuchVol_zzz_test/loom-archive")


def test_assert_dest_mounted_allows_local_path(tmp_path):
    diskguard.assert_dest_mounted(tmp_path)  # must not raise
