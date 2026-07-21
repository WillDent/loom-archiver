"""Tests for the MCP SDK wiring in loom_archiver.mcp.server.

Skips cleanly wherever the optional `mcp` extra isn't installed.
"""
from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("mcp")

from loom_archiver import diskguard, folders, graphql, ledger  # noqa: E402
from loom_archiver.config import Config  # noqa: E402
from loom_archiver.mcp import context, server  # noqa: E402

EXPECTED_TOOL_NAMES = {
    "auth_status",
    "archive_status",
    "list_folders",
    "list_videos",
    "search_transcripts",
    "get_transcript",
    "refresh_inventory",
    "sync_transcripts",
    "download_videos",
}


def test_server_registers_expected_tools():
    registered = asyncio.run(server.mcp.list_tools())
    names = {tool.name for tool in registered}
    assert names == EXPECTED_TOOL_NAMES
    assert len(names) == 9


def test_call_maps_config_error_to_not_configured(monkeypatch):
    def boom():
        raise context.ConfigError("no dest configured")

    monkeypatch.setattr(server.context, "load_config", boom)

    result = asyncio.run(server._call(lambda cfg: {"ok": True}))

    assert result == {"error": "not_configured", "message": "no dest configured"}


def test_call_maps_auth_error_from_impl(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ARCHIVER_DEST", str(tmp_path / "archive"))

    def impl(cfg):
        raise graphql.AuthError("no session")

    result = asyncio.run(server._call(impl))

    assert result["error"] == "not_authenticated"
    assert "no session" in result["message"]


def test_call_maps_mount_error_from_impl(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ARCHIVER_DEST", str(tmp_path / "archive"))

    def impl(cfg):
        raise diskguard.MountError("/Volumes/NAS is not mounted")

    result = asyncio.run(server._call(impl))

    assert result["error"] == "dest_unavailable"
    assert "not mounted" in result["message"]


def test_call_maps_folder_walk_error_from_impl(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ARCHIVER_DEST", str(tmp_path / "archive"))

    def impl(cfg):
        raise folders.FolderWalkError("could not enumerate folder xyz")

    result = asyncio.run(server._call(impl))

    assert result["error"] == "folder_walk_failed"
    assert "could not enumerate" in result["message"]


def test_call_returns_normal_impl_result(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ARCHIVER_DEST", str(tmp_path / "archive"))

    def impl(cfg, a, b):
        return {"sum": a + b}

    result = asyncio.run(server._call(impl, 2, 3))

    assert result == {"sum": 5}


def test_ledger_lock_exists_and_is_asyncio_lock():
    assert isinstance(server._LEDGER_LOCK, asyncio.Lock)


def test_mutating_calls_serialize_under_the_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("LOOM_ARCHIVER_DEST", str(tmp_path / "archive"))
    order: list[str] = []

    def impl(cfg, tag):
        order.append(f"start-{tag}")
        time.sleep(0.05)
        order.append(f"end-{tag}")
        return tag

    async def run_both():
        await asyncio.gather(
            server._call(impl, "A", mutating=True),
            server._call(impl, "B", mutating=True),
        )

    asyncio.run(run_both())

    # Non-interleaving: each tag's start must be immediately followed by its
    # own end before the other tag starts -- proves the lock serialized the
    # two mutating calls rather than letting them overlap in the thread pool.
    assert order in (
        ["start-A", "end-A", "start-B", "end-B"],
        ["start-B", "end-B", "start-A", "end-A"],
    )


def test_main_does_not_crash_without_dest_configured(monkeypatch):
    monkeypatch.delenv("LOOM_ARCHIVER_DEST", raising=False)
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: None)

    server.main()  # must not raise


def test_download_videos_tool_routes_through_call_with_mutating_lock(monkeypatch, tmp_path):
    """End-to-end through the REAL registered tool -> _call(..., mutating=True)
    path, not by calling tools.download_videos directly. Uses the too_many
    guardrail as the scenario because it's the one case guaranteed to touch
    no network/session, so the test stays safe without further mocking.
    """
    cfg = Config.default(tmp_path / "archive")
    rows = {
        f"v{i}": ledger.new_row(f"v{i}", f"Name {i}", "AIDemos", "public", f"https://x/v{i}")
        for i in range(20)
    }
    ledger.write_ledger(cfg.dest_root / "loom_video_list_progress.csv", rows)

    monkeypatch.setattr(server.context, "load_config", lambda: cfg)

    tool = server.mcp._tool_manager.get_tool("download_videos")
    result = asyncio.run(tool.fn(folder="AIDemos", max=10, force=False))

    assert result["error"] == "too_many"
    assert result["requested"] == 20
    assert result["max"] == 10
