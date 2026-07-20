import os
import stat
from pathlib import Path

import pytest

from loom_archiver import paths


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ARCHIVER_CONFIG_DIR", str(tmp_path / "cfg"))
    assert paths.config_dir() == tmp_path / "cfg"


def test_macos_uses_application_support(monkeypatch):
    monkeypatch.delenv("LOOM_ARCHIVER_CONFIG_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/tester")))
    assert paths.config_dir() == Path("/Users/tester/Library/Application Support/loom-archiver")


def test_linux_uses_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("LOOM_ARCHIVER_CONFIG_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert paths.config_dir() == tmp_path / "xdg" / "loom-archiver"


def test_config_dir_is_never_inside_the_package(monkeypatch):
    """The pipx bug: credentials must not land in site-packages."""
    monkeypatch.delenv("LOOM_ARCHIVER_CONFIG_DIR", raising=False)
    package_dir = Path(paths.__file__).resolve().parent
    assert package_dir not in paths.config_dir().resolve().parents


def test_secure_write_sets_0600(tmp_path):
    target = tmp_path / "nested" / "auth_state.json"
    paths.secure_write(target, '{"cookies": []}')
    assert target.read_text() == '{"cookies": []}'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_secure_write_tightens_a_preexisting_loose_file(tmp_path):
    """A stale 0644 auth_state.json from before this fix must end up 0600."""
    target = tmp_path / "auth_state.json"
    target.write_text("old")
    target.chmod(0o644)
    paths.secure_write(target, "new")
    assert target.read_text() == "new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_secure_write_tightens_permissions_before_writing_content(tmp_path, monkeypatch):
    """The file must already be 0600 *while* content is being written, not just afterward.

    A stale pre-existing file keeps its old (loose) mode across os.open()
    with O_CREAT, since O_CREAT's mode argument only applies when the file
    is newly created. If permissions are tightened only after the write
    completes, secret bytes pass through the descriptor while the file on
    disk is still world/group readable. This test spies on the file object
    returned by os.fdopen so it can observe the mode of the underlying fd
    at the exact moment write() is invoked.
    """
    target = tmp_path / "auth_state.json"
    target.write_text("old")
    target.chmod(0o644)

    modes_seen_during_write = []
    real_fdopen = os.fdopen

    def spying_fdopen(fd, *args, **kwargs):
        f = real_fdopen(fd, *args, **kwargs)
        real_write = f.write

        def spying_write(s):
            modes_seen_during_write.append(stat.S_IMODE(os.fstat(f.fileno()).st_mode))
            return real_write(s)

        f.write = spying_write
        return f

    monkeypatch.setattr(paths.os, "fdopen", spying_fdopen)
    paths.secure_write(target, "new-secret")

    assert modes_seen_during_write == [0o600]


def test_graphql_query_files_are_packaged():
    """Queries live in the package dir and must ship inside the wheel."""
    from loom_archiver import graphql

    assert "GetLoomsForLibrary" in graphql.GET_LOOMS_FOR_LIBRARY
    query_dir = Path(paths.__file__).resolve().parent / "queries"
    assert (query_dir / "get_looms_for_library.graphql").exists()
    assert (query_dir / "get_published_folders.graphql").exists()


def test_secure_write_closes_fd_when_fchmod_fails(monkeypatch, tmp_path):
    """A failing fchmod must not leak the descriptor."""
    target = tmp_path / "auth_state.json"
    captured = {}
    real_open = os.open

    def spy_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        captured["fd"] = fd
        return fd

    def boom(fd, mode):
        raise PermissionError("EPERM")

    monkeypatch.setattr(paths.os, "open", spy_open)
    monkeypatch.setattr(paths.os, "fchmod", boom)

    with pytest.raises(PermissionError):
        paths.secure_write(target, "secret")

    # The descriptor must be closed: fstat on a closed fd raises EBADF.
    with pytest.raises(OSError):
        os.fstat(captured["fd"])
