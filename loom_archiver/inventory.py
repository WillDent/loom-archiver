from __future__ import annotations

# Response-shape notes from live capture (2026-07-17, authenticated session):
#   videos array : data["getLooms"]["videos"]["edges"][*]["node"]
#   id/name      : node["id"], node["name"]
#   visibility   : node["visibility"]   (e.g. "owner")
#   share_url    : NOT in the query — derived as https://www.loom.com/share/<id>
#   next cursor  : data["getLooms"]["videos"]["pageInfo"]["endCursor"]
#                  (only when pageInfo["hasNextPage"] is true)
# The query maps $limit->first and $cursor->after; source/sortType/sortOrder
# are REQUIRED (non-null) variables — every request must supply them.

from .config import Config
from . import diskguard, folders, graphql, ledger, names

_LIMIT = 50

# Required, non-null variables Loom's schema demands on every GetLoomsForLibrary call.
_BASE_VARS = {
    "source": "MINE",
    "sortType": "RECENT",
    "sortOrder": "DESC",
}

_SHARE_URL = "https://www.loom.com/share/{id}"


def parse_page(data: dict) -> tuple[list[dict], str | None]:
    videos = data["getLooms"]["videos"]
    rows = []
    for edge in videos.get("edges", []):
        node = edge["node"]
        vid = node["id"]
        rows.append({
            "id": vid,
            "name": node.get("name") or "",
            "folder": "",
            "visibility": node.get("visibility") or "",
            "share_url": _SHARE_URL.format(id=vid),
        })
    page_info = videos.get("pageInfo") or {}
    cursor = page_info.get("endCursor") if page_info.get("hasNextPage") else None
    return rows, cursor


def _paginate(api, folder_label: str, variables_base: dict) -> list[dict]:
    out: list[dict] = []
    cursor = None
    seen_cursors: set = set()
    while True:
        variables = {**_BASE_VARS, **variables_base, "limit": _LIMIT, "cursor": cursor}
        data = api.execute("GetLoomsForLibrary", graphql.GET_LOOMS_FOR_LIBRARY, variables)
        rows, cursor = parse_page(data)
        for r in rows:
            r["folder"] = folder_label
        out.extend(rows)
        if not cursor:
            return out
        if cursor in seen_cursors:
            raise folders.FolderWalkError(
                f"repeated pagination cursor {cursor!r} while listing videos in "
                f"{folder_label!r} — refusing to report a partial archive as complete"
            )
        seen_cursors.add(cursor)


def folder_label_from_segments(segments: list[str]) -> str:
    """Filesystem-safe label from raw folder-name segments.

    Sanitizing per raw segment (rather than splitting a joined path) is what
    keeps a folder literally named "A/B" distinct from a nested "A" > "B":
    names.sanitize turns the illegal "/" into "_", so the two cannot collide.
    Empty names are NOT dropped -- sanitize("") returns "untitled" -- so an
    empty-named folder cannot collapse its children into their grandparent.
    """
    return "/".join(names.sanitize(seg) for seg in segments)


def discover(api, *, max_depth: int = 10, folders_only: bool = False) -> tuple[list[dict], list[dict]]:
    """Return (rows, folder_tree) for every video the user created.

    The archive set is exactly `loose ∪ (videos in every folder, recursively)`.
    Both passes are mandatory: an unfiltered CREATED_BY_ME query returns exactly
    the NOT_IN_FOLDER set and contains none of the foldered videos (verified
    against the live API 2026-07-20), so neither pass can stand in for the other.

    `folders_only` drops the loose-video pass, leaving only foldered videos --
    the population reachable solely through the recursive walk. It scopes
    DISCOVERY, not processing: `run` derives its work queue from the merged
    ledger, so against a dest with an existing ledger, previously-pending loose
    videos still get processed. Against a fresh dest the two are equivalent.
    """
    rows: list[dict] = []
    if not folders_only:
        rows = _paginate(api, "main-library", {
            "filters": [[{"type": "CREATED_BY_ME"}], [{"type": "NOT_IN_FOLDER"}]],
        })
    tree = folders.walk_folders(api, max_depth=max_depth)
    for folder in tree:
        rows.extend(_paginate(api, folder_label_from_segments(folder["segments"]), {
            "folderId": folder["id"],
            "filters": [[{"type": "CREATED_BY_ME"}]],
        }))
    return rows, tree


def run_inventory(api, cfg: Config) -> dict[str, dict]:
    diskguard.assert_dest_mounted(cfg.dest_root)
    ledger_path = cfg.dest_root / "loom_video_list_progress.csv"
    existing = ledger.read_ledger(ledger_path)
    discovered, tree = discover(api, max_depth=cfg.max_folder_depth,
                                folders_only=cfg.folders_only)
    merged = ledger.merge_inventory(existing, discovered)
    ledger.write_ledger(ledger_path, merged)
    _report_shape(discovered, tree, folders_only=cfg.folders_only)
    try:
        missed = folders.find_unwalked_folders(api, tree)
    except Exception as e:  # noqa: BLE001 - a failed cross-check must not abort the archive
        print(f"Warning: folder cross-check failed ({e}); archive continues.")
        missed = []
    if missed:
        print(f"Warning: {len(missed)} folder(s) exist that were not walked:")
        for folder in missed:
            print(f"  - {folder['name']} ({folder['id']})")
        print("  Videos you created inside them may be missing from this archive.")
    print(f"Inventory: {len(discovered)} discovered, {len(merged)} total in ledger.")
    return merged


def _report_shape(rows: list[dict], tree: list[dict], *, folders_only: bool) -> None:
    """Print what was enumerated, so incompleteness is visible rather than inferred."""
    per_folder: dict[str, int] = {}
    for row in rows:
        per_folder[row["folder"]] = per_folder.get(row["folder"], 0) + 1
    if not folders_only:
        header = f"Enumerated {len(rows)} videos across {len(tree)} folder(s) + main library:"
    else:
        header = (
            f"Enumerated {len(rows)} videos across {len(tree)} folder(s) "
            "(folders-only: main library NOT enumerated):"
        )
    print(header)
    for label in sorted(per_folder):
        print(f"  {per_folder[label]:>6}  {label}")
