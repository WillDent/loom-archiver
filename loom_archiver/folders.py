from __future__ import annotations

import time

from . import graphql

_LIMIT = 50
_PAGE_DELAY = 0.3

# Captured from live traffic; see graphql.GET_PUBLISHED_FOLDERS.
_SOURCE = "ACTIVE"
MINE_FILTER = [{"type": "CREATED_BY_ME"}]


class FolderWalkError(RuntimeError):
    """Folder enumeration hit something it refuses to guess past.

    Raised rather than warned: a folder we fail to enumerate is a folder whose
    videos are silently missing from the archive, and no independent total
    exists to catch that later.
    """


def list_folders(api, parent_id: str | None = None, *, filters=MINE_FILTER,
                 sleep=time.sleep) -> list[dict]:
    """Every folder directly under `parent_id` (None = top level)."""
    out: list[dict] = []
    cursor = None
    seen_cursors: set[str] = set()
    while True:
        variables = {
            "first": _LIMIT,
            "after": cursor,
            "source": _SOURCE,
            "sortType": "RECENT",
            "sortOrder": "DESC",
            "filters": filters,
            "parentFolderId": parent_id,
        }
        data = api.execute("GetPublishedFolders", graphql.GET_PUBLISHED_FOLDERS, variables)
        conn = ((data.get("getPublishedFolders") or {}).get("folders")) or {}
        for edge in conn.get("edges") or []:
            node = edge.get("node") or {}
            if node.get("id"):
                out.append({
                    "id": node["id"],
                    "name": node.get("name") or "",
                    "visibility": node.get("visibility") or "",
                })
        page_info = conn.get("pageInfo") or {}
        cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
        if not cursor:
            return out
        if cursor in seen_cursors:
            raise FolderWalkError(
                f"repeated pagination cursor {cursor!r} while listing folders under "
                f"{parent_id or 'top level'} — refusing to loop"
            )
        seen_cursors.add(cursor)
        sleep(_PAGE_DELAY)


_MAX_DEPTH = 10


def walk_folders(api, *, max_depth: int = _MAX_DEPTH, sleep=time.sleep) -> list[dict]:
    """Every folder the user created, recursively, parents before children.

    Verified against real nested data 2026-07-20; the captured responses are
    replayed in tests/test_folders_live_fixture.py. A cycle or runaway depth
    raises rather than silently truncating the archive.
    """
    found: list[dict] = []
    visited: set[str] = set()

    def visit(parent_id: str | None, depth: int, prefix: str, segments: list[str]) -> None:
        if depth > max_depth:
            raise FolderWalkError(
                f"folder nesting deeper than {max_depth} at {prefix!r} — refusing to "
                f"recurse further; rerun with a higher --max-depth if this is genuine"
            )
        for folder in list_folders(api, parent_id, sleep=sleep):
            folder_id = folder["id"]
            if folder_id in visited:
                raise FolderWalkError(
                    f"folder cycle detected: {folder_id!r} ({folder['name']!r}) appears "
                    f"more than once while walking {prefix or 'top level'!r}"
                )
            visited.add(folder_id)
            path = folder["name"] if parent_id is None else f"{prefix}/{folder['name']}"
            folder_segments = segments + [folder["name"]]
            found.append({
                **folder,
                "parent_id": parent_id,
                "depth": depth,
                "path": path,
                "segments": folder_segments,
            })
            visit(folder_id, depth + 1, path, folder_segments)

    visit(None, 0, "", [])
    return found


def find_unwalked_folders(api, walked: list[dict], *, sleep=time.sleep) -> list[dict]:
    """Top-level folders visible without the CREATED_BY_ME filter that the walk missed.

    Cheap insurance against a whole class of silent miss: a folder we never
    enumerate is a folder whose videos are absent with nothing to flag it.
    Top level only -- this is a smoke alarm, not a second walk.
    """
    walked_ids = {f["id"] for f in walked}
    everything = list_folders(api, None, filters=None, sleep=sleep)
    return [f for f in everything if f["id"] not in walked_ids]
