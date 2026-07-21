"""SDK-free implementations of the read-only MCP tools.

Imports ONLY existing loom_archiver modules + stdlib -- this module must NOT
import the `mcp` package, so the test suite (and thus CI) stays green even
where the optional `mcp` extra isn't installed. `loom_archiver/mcp/server.py`
is the thin async wrapper around these functions.
"""
from __future__ import annotations

import shutil

from .. import diskguard, folders as folders_mod, graphql, inventory, ledger, names, preflight
from .. import run as run_mod
from .. import search as search_mod
from .. import sync as sync_mod
from ..config import Config
from . import context

LEDGER_FILENAME = "loom_video_list_progress.csv"

_STATUS_FILTERS = {
    "has_transcript": lambda row: row.get("transcript_status") == "done",
    "has_mp4": lambda row: row.get("mp4_status") == "done",
    "pending": lambda row: (
        row.get("mp4_status") != "done"
        or row.get("transcript_status") not in ("done", "unavailable")
    ),
}

_VIDEO_SUMMARY_FIELDS = [
    "id", "name", "folder", "visibility", "share_url",
    "mp4_status", "transcript_status",
]


def _ledger_path(cfg: Config):
    return cfg.dest_root / LEDGER_FILENAME


def auth_status(cfg: Config) -> dict:
    """Probe the saved session with one cheap live call."""
    try:
        with context.open_session(cfg) as (api, _http):
            folders_mod.list_folders(api)
    except graphql.AuthError as e:
        return {"authenticated": False, **context.auth_error_payload(e)}
    return {"authenticated": True, "message": "Signed in."}


def archive_status(cfg: Config) -> dict:
    """Offline snapshot of the local archive: ledger counts + environment checks."""
    ledger_path = _ledger_path(cfg)
    ffmpeg_present = shutil.which("ffmpeg") is not None
    session_file_present = cfg.auth_state_path.exists()
    disk_free_gb = round(diskguard.free_gb(cfg.dest_root), 1)

    if not ledger_path.exists():
        return {
            "dest_root": str(cfg.dest_root),
            "total": 0,
            "with_transcript": 0,
            "with_mp4": 0,
            "pending_transcript": 0,
            "pending_mp4": 0,
            "disk_free_gb": disk_free_gb,
            "ffmpeg_present": ffmpeg_present,
            "session_file_present": session_file_present,
            "ledger_present": False,
        }

    rows = list(ledger.read_ledger(ledger_path).values())
    return {
        "dest_root": str(cfg.dest_root),
        "total": len(rows),
        "with_transcript": sum(1 for r in rows if r.get("transcript_status") == "done"),
        "with_mp4": sum(1 for r in rows if r.get("mp4_status") == "done"),
        "pending_transcript": sum(
            1 for r in rows if r.get("transcript_status") not in ("done", "unavailable")
        ),
        "pending_mp4": sum(1 for r in rows if r.get("mp4_status") != "done"),
        "disk_free_gb": disk_free_gb,
        "ffmpeg_present": ffmpeg_present,
        "session_file_present": session_file_present,
        "ledger_present": True,
    }


def list_folders(cfg: Config) -> list[dict]:
    """Distinct folder labels seen in the ledger, with depth and video counts.

    Offline (from the ledger) rather than a live walk -- ids/visibility for
    folders themselves aren't tracked in the ledger, only the video rows.
    """
    rows = ledger.read_ledger(_ledger_path(cfg))
    counts: dict[str, int] = {}
    for row in rows.values():
        label = row.get("folder", "")
        counts[label] = counts.get(label, 0) + 1
    return sorted(
        (
            {"path": label, "depth": label.count("/"), "video_count": count}
            for label, count in counts.items()
        ),
        key=lambda entry: entry["path"],
    )


def list_videos(cfg: Config, folder: str | None = None, status: str | None = None,
                 limit: int = 100, offset: int = 0) -> dict:
    """Offline listing of videos from the ledger with optional folder/status filters.

    `folder` is an exact match against the folder label (not a prefix match).
    """
    rows = list(ledger.read_ledger(_ledger_path(cfg)).values())

    if folder is not None:
        rows = [r for r in rows if r.get("folder") == folder]

    predicate = _STATUS_FILTERS.get(status) if status is not None else None
    if predicate is not None:
        rows = [r for r in rows if predicate(r)]

    total = len(rows)
    page = rows[offset:offset + limit]
    return {
        "total": total,
        "items": [{k: r.get(k, "") for k in _VIDEO_SUMMARY_FIELDS} for r in page],
    }


def search_transcripts(cfg: Config, query: str, folder: str | None = None,
                        limit: int = 20) -> dict:
    """Grep archived transcripts for a literal, case-insensitive query.

    Returns {"hits": [...], "count": N} always; when there are no hits, adds
    a "message" distinguishing "nothing synced yet" (hints sync_transcripts)
    from "synced, but nothing matched this query" (no such hint).
    """
    hits = search_mod.search_transcripts(cfg, query, folder=folder, limit=limit)
    result = {"hits": hits, "count": len(hits)}
    if not hits:
        if not search_mod.any_transcripts(cfg):
            result["message"] = (
                "No transcripts have been synced yet. Run sync_transcripts "
                "(optionally scoped to a folder) to fetch transcript text -- this "
                "downloads text only, no video -- then search again."
            )
        else:
            result["message"] = f"No transcripts matched {query!r}."
    return result


def get_transcript(cfg: Config, video_id: str, max_chars: int | None = None) -> dict:
    """Return the full (or truncated) transcript text for one archived video."""
    rows = ledger.read_ledger(_ledger_path(cfg))
    row = rows.get(video_id)
    if row is None:
        return {
            "error": "not_found",
            "message": (
                f"No video with id {video_id} in the ledger. Run refresh_inventory first."
            ),
        }

    name = row.get("name", "")
    share_url = row.get("share_url", "")
    dest_dir = run_mod._dest_dir(cfg, row.get("folder", ""))
    txt_path = dest_dir / f"{names.stem(video_id, name)}.txt"

    if not txt_path.exists():
        return {
            "id": video_id,
            "name": name,
            "share_url": share_url,
            "text": None,
            "available": False,
            "message": "Transcript not synced yet. Run sync_transcripts.",
        }

    text = txt_path.read_text(encoding="utf-8", errors="replace")
    truncated = max_chars is not None and len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "id": video_id,
        "name": name,
        "share_url": share_url,
        "text": text,
        "available": True,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Mutating / network tools
# ---------------------------------------------------------------------------

def refresh_inventory(cfg: Config) -> dict:
    """Enumerate the Loom library (live) and merge it into the local ledger.

    Opens its own session. Lets `graphql.AuthError` propagate -- the server
    harness maps it to a structured `not_authenticated` payload.
    """
    with context.open_session(cfg) as (api, _http):
        merged = inventory.run_inventory(api, cfg)
    folders_seen = {
        row.get("folder", "") for row in merged.values()
        if row.get("folder", "") != "main-library"
    }
    return {
        "total": len(merged),
        "folders": len(folders_seen),
        "message": f"Inventoried {len(merged)} videos across {len(folders_seen)} folders.",
    }


def sync_transcripts(cfg: Config, folder: str | None = None, limit: int = 200) -> dict:
    """Fetch transcripts for ledger rows that don't have one yet (resumable).

    Opens its own session and delegates verbatim to `sync.sync_transcripts`.
    Lets `graphql.AuthError` propagate.
    """
    with context.open_session(cfg) as (api, http):
        return sync_mod.sync_transcripts(cfg, api, http, folder=folder, limit=limit)


def download_videos(cfg: Config, ids: list[str] | None = None, folder: str | None = None,
                     max: int = 10, force: bool = False) -> dict:
    """Download mp4s (+ any missing transcripts) for specific videos or a folder.

    Provide `ids` OR `folder`. If both are given, `ids` takes precedence.

    Target resolution happens OFFLINE, against the ledger, before any session
    is opened -- so the `too_many` guardrail and `no_target`/`ffmpeg_missing`
    errors below are guaranteed to touch no network. Lets `graphql.AuthError`
    propagate; `diskguard.DiskFullError` is caught here so progress already
    made this call is returned instead of surfacing as a protocol error.

    `force=True` does two independent things, in both `ids` and `folder`
    mode: (a) re-downloads videos whose `mp4_status` is already "done", and
    (b) lifts the `too_many` count guardrail so the request isn't blocked.
    """
    ledger_path = _ledger_path(cfg)
    rows = ledger.read_ledger(ledger_path)

    unknown_ids: list[str] = []
    if ids is not None:
        targets = []
        for vid in ids:
            row = rows.get(vid)
            if row is None:
                unknown_ids.append(vid)
                continue
            if force or row.get("mp4_status") != "done":
                targets.append(row)
    elif folder is not None:
        targets = [
            r for r in rows.values()
            if r.get("folder") == folder and (force or r.get("mp4_status") != "done")
        ]
    else:
        return {
            "error": "no_target",
            "message": "Specify `ids` (a list of video ids) or `folder`.",
        }

    if len(targets) > max and not force:
        return {
            "error": "too_many",
            "requested": len(targets),
            "max": max,
            "message": (
                f"This would download {len(targets)} videos. Narrow to a smaller "
                "folder or explicit ids, pass force=true, or for a full archive run "
                "`loom-archiver run --dest <path>` in your terminal."
            ),
        }

    try:
        preflight.assert_ffmpeg()
    except preflight.MissingDependencyError as e:
        return {"error": "ffmpeg_missing", "message": str(e)}

    results = []
    with context.open_session(cfg) as (api, http):
        for row in targets:
            try:
                run_mod.process_video(row, api, http, cfg)
            except diskguard.DiskFullError as e:
                ledger.write_ledger(ledger_path, rows)
                downloaded = sum(1 for r in results if r["mp4_status"] == "done")
                disk_full_out = {
                    "error": "disk_full",
                    "downloaded": downloaded,
                    "message": str(e),
                }
                if unknown_ids:
                    disk_full_out["unknown_ids"] = unknown_ids
                return disk_full_out
            ledger.write_ledger(ledger_path, rows)
            results.append({
                "id": row["id"],
                "name": row.get("name", ""),
                "mp4_status": row.get("mp4_status"),
                "error": row.get("error", ""),
            })

    out = {
        "requested": len(targets),
        "downloaded": sum(1 for r in results if r["mp4_status"] == "done"),
        "failed": sum(1 for r in results if r["mp4_status"] == "failed"),
        "results": results,
    }
    if unknown_ids:
        out["unknown_ids"] = unknown_ids
    return out
