from pathlib import Path

from loom_archiver import paths
from loom_archiver.config import Config


def test_default_config_values(tmp_path):
    cfg = Config.default(tmp_path / "archive")
    assert cfg.dest_root == tmp_path / "archive"
    assert cfg.disk_floor_gb == 50
    assert cfg.disk_check_interval == 10
    assert cfg.max_retries == 3


def test_no_hardcoded_nas_path(tmp_path):
    """The personal NAS default must not survive into the released tool."""
    cfg = Config.default(tmp_path)
    assert "WillBackUps" not in str(cfg.dest_root)


def test_credentials_live_in_the_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ARCHIVER_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = Config.default(tmp_path / "archive")
    assert cfg.auth_state_path == tmp_path / "cfg" / "auth_state.json"
    assert cfg.chrome_profile_dir == tmp_path / "cfg" / "chrome-profile"
    assert cfg.cookies_path == tmp_path / "cfg" / "cookies.txt"


def test_credentials_are_not_inside_the_package(monkeypatch):
    monkeypatch.delenv("LOOM_ARCHIVER_CONFIG_DIR", raising=False)
    package_dir = Path(paths.__file__).resolve().parent
    cfg = Config.default(Path("/tmp/archive"))
    assert package_dir not in cfg.auth_state_path.resolve().parents


def test_config_defaults_to_archiving_everything():
    from pathlib import Path
    from loom_archiver.config import Config
    assert Config.default(Path("/tmp/x")).folders_only is False
