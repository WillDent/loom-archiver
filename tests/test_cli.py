import pytest

from loom_archiver.__main__ import build_parser, main
from loom_archiver import graphql


def test_parser_accepts_subcommands():
    parser = build_parser()
    for cmd in ("auth", "inventory", "run"):
        ns = parser.parse_args([cmd, "--dest", "/tmp/x"])
        assert ns.command == cmd


def test_parser_overrides_floor_and_dest():
    ns = build_parser().parse_args(["run", "--floor", "100", "--dest", "/tmp/x"])
    assert ns.floor == 100
    assert ns.dest == "/tmp/x"


def test_main_run_exits_nonzero_on_auth_error(monkeypatch):
    import loom_archiver.run as run_module

    def boom(cfg):
        raise graphql.AuthError("expired")

    monkeypatch.setattr(run_module, "run", boom)
    assert main(["run", "--dest", "/tmp/x"]) == 1


def test_dest_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run"])


def test_dest_is_accepted():
    ns = build_parser().parse_args(["run", "--dest", "/tmp/archive"])
    assert ns.dest == "/tmp/archive"


def test_auth_error_names_the_console_script():
    from loom_archiver import graphql
    assert "loom-archiver auth" in graphql.AUTH_HINT


def test_parser_uses_the_console_script_name():
    assert build_parser().prog == "loom-archiver"


def test_parser_accepts_max_depth_flag():
    ns = build_parser().parse_args(["inventory", "--dest", "/tmp/x", "--max-depth", "3"])
    assert ns.max_depth == 3


def test_parser_max_depth_defaults_to_ten():
    ns = build_parser().parse_args(["inventory", "--dest", "/tmp/x"])
    assert ns.max_depth == 10


def test_parser_accepts_folders_only_flag():
    ns = build_parser().parse_args(["run", "--dest", "/tmp/x", "--folders-only"])
    assert ns.folders_only is True


def test_folders_only_defaults_to_false():
    ns = build_parser().parse_args(["run", "--dest", "/tmp/x"])
    assert ns.folders_only is False


def test_folders_only_is_available_on_inventory_too():
    ns = build_parser().parse_args(["inventory", "--dest", "/tmp/x", "--folders-only"])
    assert ns.folders_only is True


def test_folders_only_flag_reaches_discover_end_to_end(tmp_path, monkeypatch):
    """Closes the gap between test_parser_accepts_folders_only_flag (which
    only checks ns.folders_only) and test_discover_folders_only_skips_the_main_library_pass
    (which only checks discover(folders_only=True) directly): proves
    --folders-only actually survives _config_from_args and run_inventory to
    reach discover's `folders_only` kwarg. A polarity inversion at either
    __main__.py:_config_from_args (cfg.folders_only = ns.folders_only) or
    inventory.py:run_inventory (the discover(...) call) passes every other
    test in the suite but must fail this one."""
    from loom_archiver.__main__ import build_parser, _config_from_args
    from loom_archiver import inventory as inventory_mod

    ns = build_parser().parse_args(["inventory", "--dest", str(tmp_path), "--folders-only"])
    cfg = _config_from_args(ns)
    assert cfg.folders_only is True

    captured = {}

    def fake_discover(api, *, max_depth=10, folders_only=False):
        captured["folders_only"] = folders_only
        return [], []

    monkeypatch.setattr(inventory_mod, "discover", fake_discover)

    inventory_mod.run_inventory(object(), cfg)

    assert captured["folders_only"] is True


def test_main_inventory_exits_nonzero_without_a_traceback_when_unauthenticated(tmp_path, monkeypatch, capsys):
    missing_dir = tmp_path / "no-config-here"
    monkeypatch.setenv("LOOM_ARCHIVER_CONFIG_DIR", str(missing_dir))
    rc = main(["inventory", "--dest", str(tmp_path / "dest")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "auth" in out.lower()


def test_main_run_exits_nonzero_without_a_traceback_when_unauthenticated(tmp_path, monkeypatch, capsys):
    missing_dir = tmp_path / "no-config-here"
    monkeypatch.setenv("LOOM_ARCHIVER_CONFIG_DIR", str(missing_dir))
    rc = main(["run", "--dest", str(tmp_path / "dest")])
    out = capsys.readouterr().out
    assert rc == 1
    assert "auth" in out.lower()
