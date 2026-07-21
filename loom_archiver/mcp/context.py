"""Shared context helpers for the MCP tools: config resolution, a network
session context manager, and structured auth-error mapping.

Does not import the MCP SDK -- only existing loom_archiver modules.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import httpx

from .. import graphql
from ..config import Config

DEST_ENV_VAR = "LOOM_ARCHIVER_DEST"


class ConfigError(Exception):
    pass


def load_config() -> Config:
    """Resolve a Config from the environment (there is no per-call --dest)."""
    dest = os.environ.get(DEST_ENV_VAR)
    if not dest:
        raise ConfigError(
            f"Set {DEST_ENV_VAR} to the folder where your Loom archive lives "
            "(e.g. ~/loom-archive)."
        )
    return Config.default(Path(dest))


@contextmanager
def open_session(cfg: Config):
    """Build the authenticated GraphQL client and an httpx client, yield both,
    and guarantee both are closed -- even if the caller's body raises.

    `graphql.load_cookie_header` may raise `graphql.AuthError` (missing/corrupt
    session file); that propagates to the caller.
    """
    api = graphql.LoomGraphQL(graphql.load_cookie_header(cfg.auth_state_path), cfg.loom_web_version)
    http = httpx.Client(timeout=120)
    try:
        yield api, http
    finally:
        try:
            http.close()
        finally:
            api.close()


def auth_error_payload(exc: Exception | None = None) -> dict:
    """A JSON-serializable payload a tool can return instead of raising.

    Uses the neutral `AUTH_ACTION` (not `AUTH_HINT`) because this payload is
    also returned for users who have never signed in -- claiming their
    session "expired" would be factually wrong for them.
    """
    message = f"Not authenticated. {graphql.AUTH_ACTION}"
    if exc is not None:
        message = f"{message} ({exc})"
    return {"error": "not_authenticated", "message": message}
