from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "loom-archiver"

# Credentials must live outside the package: under `pipx install` the package
# directory is inside site-packages, where `pipx upgrade` would destroy them
# and where the repo .gitignore offers no protection.
ENV_OVERRIDE = "LOOM_ARCHIVER_CONFIG_DIR"


def config_dir() -> Path:
    """Platform directory for private state (session cookies, browser profile)."""
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / APP_NAME


def secure_write(path: Path, text: str) -> None:
    """Write a secret file readable only by its owner."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # O_CREAT's mode applies only when the file is created, so an existing
        # file keeps its old (possibly looser) mode. Tighten before writing any
        # secret bytes, not after.
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
