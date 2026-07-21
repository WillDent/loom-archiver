"""Thin MCP SDK wiring for loom-archiver.

This is the ONLY module in the project that imports the `mcp` package. All
real logic lives in `loom_archiver.mcp.tools` (sync, SDK-free, independently
testable). This module just: builds the server, offloads each tools.py call
to a worker thread behind a shared lock (so mutating tools never touch the
ledger CSV concurrently), maps known exceptions to structured error
payloads, and registers the async `@mcp.tool()` wrappers Claude actually
calls.
"""
from __future__ import annotations

import asyncio
import functools

import anyio
from mcp.server.fastmcp import FastMCP

from .. import diskguard, folders, graphql
from . import context, tools

mcp = FastMCP("loom-archiver")

# Held by mutating tools (added in a later task) so two writers never touch
# the ledger CSV at the same time. Built now so the harness below is already
# lock-aware.
_LEDGER_LOCK = asyncio.Lock()


async def _call(impl, *args, mutating: bool = False):
    """Shared async harness: resolve config, run `impl` off the event loop
    thread, and map known exceptions to structured error payloads instead of
    letting them surface as protocol errors.
    """
    try:
        cfg = context.load_config()
    except context.ConfigError as e:
        return {"error": "not_configured", "message": str(e)}

    run = functools.partial(impl, cfg, *args)
    try:
        if mutating:
            async with _LEDGER_LOCK:
                return await anyio.to_thread.run_sync(run)
        return await anyio.to_thread.run_sync(run)
    except graphql.AuthError as e:
        return context.auth_error_payload(e)
    except diskguard.MountError as e:
        return {"error": "dest_unavailable", "message": str(e)}
    except folders.FolderWalkError as e:
        return {"error": "folder_walk_failed", "message": str(e)}


@mcp.tool()
async def auth_status() -> dict:
    """Check whether loom-archiver has a valid, working Loom session.

    Makes a lightweight authenticated call to Loom (lists your top-level
    folders) to check whether your saved session is valid. Use this first if
    any other tool reports it isn't authenticated, or to sanity-check the
    saved session before a long-running sync.
    """
    return await _call(tools.auth_status)


@mcp.tool()
async def archive_status() -> dict:
    """Offline summary of the local archive: how many videos are tracked,
    how many have a transcript/mp4 saved, disk space free at the
    destination, whether ffmpeg is on PATH, and whether a session file
    exists. Does not contact Loom -- reads only the local ledger and disk.
    """
    return await _call(tools.archive_status)


@mcp.tool()
async def list_folders() -> list[dict]:
    """List the distinct folder labels seen in the local archive, with each
    folder's nesting depth and video count.

    Offline (derived from the ledger, not a live Loom folder walk). Folder
    *names* are the only per-folder metadata available -- there is no
    folder-level date, owner, or description data to filter or sort on.
    """
    return await _call(tools.list_folders)


@mcp.tool()
async def list_videos(folder: str | None = None, status: str | None = None,
                       limit: int = 100, offset: int = 0) -> dict:
    """List archived videos from the local ledger, optionally filtered and paged.

    `folder`, if given, must be an EXACT folder label (e.g. "AIDemos") -- it
    is not a prefix match, so it will not also include "AIDemos/sub-folder".

    `status`, if given, is one of:
      - "has_transcript": transcript already saved
      - "has_mp4": video file already saved
      - "pending": still missing its mp4, or its transcript status isn't
        settled (not yet "done" and not confirmed "unavailable")

    The only filterable/sortable metadata is folder, status, and transcript
    text (via search_transcripts) -- there is NO date, duration, view-count,
    or description data recorded anywhere in the archive. Do not attempt
    filters like "videos from last month" or "longest videos"; that data
    does not exist here.

    Returns {"total": <count matching filters>, "items": [...]}; `items` is
    the `[offset:offset+limit]` page of that filtered set.
    """
    return await _call(tools.list_videos, folder, status, limit, offset)


@mcp.tool()
async def search_transcripts(query: str, folder: str | None = None, limit: int = 20) -> dict:
    """Full-text search over archived transcripts for a literal, case-insensitive
    substring (not a regex, not fuzzy/semantic search).

    `folder`, if given, is an EXACT folder label match, not a prefix match.
    This is the only way to search video *content* -- there is no metadata
    search over dates, duration, or descriptions, because none of that is
    recorded.

    Returns {"hits": [...], "count": N, "message"?: str}. Each hit has an id,
    name, folder, share_url, and a short snippet around the first match,
    ranked by match count. `message` is present only when `count` is 0: if
    no transcripts have been synced to disk yet, it tells you to run
    sync_transcripts (optionally scoped to a folder) first; otherwise it
    just notes that no transcript matched the query.
    """
    return await _call(tools.search_transcripts, query, folder, limit)


@mcp.tool()
async def get_transcript(video_id: str, max_chars: int | None = None) -> dict:
    """Fetch the full (or truncated) transcript text for one archived video by id.

    If the video isn't in the local ledger yet, returns an error asking you
    to run refresh_inventory first (a mutating tool, not available here). If
    the video is known but its transcript hasn't been synced to disk yet,
    returns available=False with a message to run sync_transcripts.
    """
    return await _call(tools.get_transcript, video_id, max_chars)


@mcp.tool()
async def refresh_inventory() -> dict:
    """Enumerate your Loom library into the local ledger. Run this first;
    other tools read the ledger.
    """
    return await _call(tools.refresh_inventory, mutating=True)


@mcp.tool()
async def sync_transcripts(folder: str | None = None, limit: int = 200) -> dict:
    """Fetch transcripts for archived videos that don't have one yet.

    Resumable and folder-scoped: pass `folder` (an exact label, not a
    prefix) to restrict the batch, and `limit` to cap how many are
    attempted in this call. Returns `remaining` and `done`; call again to
    continue until done. If `failed` stays high and `synced` is 0 across
    calls, STOP and report the failure rather than retrying endlessly --
    that pattern means a video is permanently failing, not that another
    call will help.
    """
    return await _call(tools.sync_transcripts, folder, limit, mutating=True)


@mcp.tool()
async def download_videos(ids: list[str] | None = None, folder: str | None = None,
                           max: int = 10, force: bool = False) -> dict:
    """Download mp4s (and any missing transcripts) for specific videos or one folder.

    Provide `ids` (a list of video ids) OR `folder` (an exact label, not a
    prefix). If both are given, `ids` takes precedence. Giving neither
    returns an error. Only folder/id filtering is available: there is no
    date, duration, or other metadata filter to narrow a request further.

    Guardrail: if the resolved target set is larger than `max` (default
    10), nothing is downloaded and a `too_many` error is returned instead --
    pass `force=true` to proceed anyway, or narrow the request. This tool is
    for small, targeted pulls; for archiving an entire library, use the
    `loom-archiver run` CLI in a terminal instead, not this tool.

    `force=true` does two things, whether using `ids` or `folder`: it
    re-downloads videos that are already marked done, and it lifts the
    `max` count guardrail above.
    """
    return await _call(tools.download_videos, ids, folder, max, force, mutating=True)


def main() -> None:
    """Entry point for the stdio MCP server.

    Must not crash when LOOM_ARCHIVER_DEST is unset: the server still starts
    fine and each tool call individually returns a `not_configured` payload
    via `_call` above.
    """
    mcp.run()


if __name__ == "__main__":
    main()
