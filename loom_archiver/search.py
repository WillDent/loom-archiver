from __future__ import annotations

import re
from pathlib import Path

from . import ledger as ledger_mod
from .config import Config

_WS = re.compile(r"\s+")


def search_transcripts(cfg: Config, query: str, *, folder: str | None = None,
                        limit: int = 20, context_chars: int = 160) -> list[dict]:
    """Grep archived transcript .txt files for a literal, case-insensitive query.

    Walks `<dest_root>/**/*.txt` (never `.vtt`), joins hits against the ledger
    for `name`/`folder`/`share_url`, and falls back to filename/path-derived
    metadata for transcripts of videos not (yet) in the ledger -- e.g. a search
    run before an inventory refresh has caught up with a freshly saved file.

    Folder filtering is an exact match against the video's folder label, not a
    prefix/substring match: "MD Turbines" must not pull in
    "MD Turbines/sub-folder-md".
    """
    dest_root = Path(cfg.dest_root)
    if not dest_root.exists():
        return []

    rows = ledger_mod.read_ledger(dest_root / "loom_video_list_progress.csv")
    query_lower = query.lower()

    hits = []
    for path in sorted(dest_root.rglob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        text_lower = text.lower()
        match_count = text_lower.count(query_lower)
        if match_count == 0:
            continue

        video_id, _, fallback_name = path.stem.partition("__")

        row = rows.get(video_id)
        if row:
            name = row.get("name", "")
            hit_folder = row.get("folder", "")
            share_url = row.get("share_url", "")
        else:
            name = fallback_name or path.stem
            hit_folder = path.parent.relative_to(dest_root).as_posix()
            share_url = f"https://www.loom.com/share/{video_id}"

        if folder is not None and hit_folder != folder:
            continue

        hits.append({
            "id": video_id,
            "name": name,
            "folder": hit_folder,
            "share_url": share_url,
            "snippet": _make_snippet(text, text_lower, query_lower, len(query), context_chars),
            "match_count": match_count,
        })

    hits.sort(key=lambda h: (-h["match_count"], h["name"].lower(), h["id"]))
    return hits[:limit]


def _make_snippet(text: str, text_lower: str, query_lower: str, query_len: int,
                   context_chars: int) -> str:
    first_idx = text_lower.find(query_lower)
    start = max(0, first_idx - context_chars)
    end = min(len(text), first_idx + query_len + context_chars)

    core = _WS.sub(" ", text[start:end]).strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{core}{suffix}"
