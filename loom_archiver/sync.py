from __future__ import annotations

from . import ledger, run
from .config import Config


def sync_transcripts(cfg: Config, api, http, *, folder: str | None = None, limit: int = 200) -> dict:
    """Fetch transcripts for ledger rows that don't have one yet, without touching MP4s.

    Candidate rows are those whose transcript_status is not "done" or
    "unavailable"; if `folder` is given, only rows whose folder matches
    exactly. At most `limit` candidates are processed (in ledger order); the
    whole ledger is rewritten after each row so a crash mid-run loses nothing.

    `graphql.AuthError` propagates (a bad session must stop the loop, matching
    run.py) -- any rows already processed this call remain persisted.

    `remaining` counts candidates not processed this call, INCLUDING rows that
    became "failed" this call: a genuine error (network blip, transient 500)
    doesn't mean the video has no transcript, so failed rows stay eligible for
    a future retry rather than being silently dropped from the count.
    """
    ledger_path = cfg.dest_root / "loom_video_list_progress.csv"
    rows = ledger.read_ledger(ledger_path)

    if not rows:
        return {
            "synced": 0, "unavailable": 0, "failed": 0,
            "remaining": 0, "done": True, "ledger_empty": True,
        }

    def is_candidate(row: dict) -> bool:
        if row.get("transcript_status") in ("done", "unavailable"):
            return False
        if folder is not None and row.get("folder") != folder:
            return False
        return True

    candidates = [row for row in rows.values() if is_candidate(row)]
    to_process = candidates[:limit]

    synced = unavailable = failed = 0
    for row in to_process:
        run.process_transcript(row, api, http, cfg)
        ledger.write_ledger(ledger_path, rows)
        status = row.get("transcript_status")
        if status == "done":
            synced += 1
        elif status == "unavailable":
            unavailable += 1
        elif status == "failed":
            failed += 1

    remaining = sum(1 for row in candidates if is_candidate(row))

    return {
        "synced": synced,
        "unavailable": unavailable,
        "failed": failed,
        "remaining": remaining,
        "done": remaining == 0,
    }
