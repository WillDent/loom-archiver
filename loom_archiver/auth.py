from __future__ import annotations

import time
from pathlib import Path

from .config import Config

LOGIN_URL = "https://www.loom.com/looms/videos"
# Loom sets `connect.sid` (an httpOnly session cookie) only once you're actually
# authenticated. Match it by EXACT name with a non-empty value — loose substring
# hints like "auth"/"sid" wrongly match anonymous/OAuth-flow cookies such as
# `loom_oauth_state_v6`, which are present before login completes.
SESSION_COOKIE_NAMES = ("connect.sid",)


def save_storage_state(context, out_path: Path) -> None:
    import json

    from . import paths

    state = context.storage_state()
    paths.secure_write(Path(out_path), json.dumps(state))


def _has_session_cookie(context) -> bool:
    for c in context.cookies():
        if (
            "loom.com" in c.get("domain", "")
            and c.get("name") in SESSION_COOKIE_NAMES
            and c.get("value")
        ):
            return True
    return False


def login(cfg: Config, timeout_s: int = 300) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover - depends on install extras
        raise SystemExit(
            "Browser login needs Playwright, which is an optional extra.\n"
            "  pipx install 'loom-archiver[auth]'   (or: pip install 'loom-archiver[auth]')\n"
            "Then run: pipx run playwright install chrome"
        ) from e

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(cfg.chrome_profile_dir),
            channel="chrome",
            headless=False,
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL)
        print("A Chrome window opened. Sign in to Loom with Google if prompted.")
        print("Waiting for an authenticated session (up to 5 minutes)...")

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if _has_session_cookie(context):
                save_storage_state(context, cfg.auth_state_path)
                context.close()
                print(f"Saved session to {cfg.auth_state_path}")
                return cfg.auth_state_path
            page.wait_for_timeout(2000)

        context.close()
        raise TimeoutError(
            "Did not detect a Loom session within the timeout. "
            "Run `loom-archiver auth` to try again."
        )
