from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import paths


@dataclass
class Config:
    dest_root: Path
    auth_state_path: Path
    chrome_profile_dir: Path
    cookies_path: Path
    disk_floor_gb: int = 50
    disk_check_interval: int = 10
    request_delay: float = 0.5
    max_retries: int = 3
    loom_web_version: str = "1"
    max_folder_depth: int = 10
    folders_only: bool = False

    @classmethod
    def default(cls, dest_root: Path | None = None) -> "Config":
        # No hardcoded archive location: the CLI requires --dest. This fallback
        # exists only for programmatic use and tests.
        root = Path(dest_root) if dest_root else Path.cwd() / "loom-archive"
        cfg_dir = paths.config_dir()
        return cls(
            dest_root=root,
            auth_state_path=cfg_dir / "auth_state.json",
            chrome_profile_dir=cfg_dir / "chrome-profile",
            cookies_path=cfg_dir / "cookies.txt",
        )
