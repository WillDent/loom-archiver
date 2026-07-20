from __future__ import annotations

import csv
from pathlib import Path

FIELDS = ["id", "name", "folder", "visibility", "share_url",
          "mp4_status", "transcript_status", "error"]


def new_row(id: str, name: str, folder: str, visibility: str, share_url: str) -> dict:
    return {
        "id": id, "name": name, "folder": folder,
        "visibility": visibility, "share_url": share_url,
        "mp4_status": "pending", "transcript_status": "pending", "error": "",
    }


def read_ledger(path: Path) -> dict[str, dict]:
    if not Path(path).exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


def write_ledger(path: Path, rows: dict[str, dict]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows.values():
            writer.writerow({k: row.get(k, "") for k in FIELDS})
    tmp.replace(path)


def merge_inventory(existing: dict[str, dict], discovered: list[dict]) -> dict[str, dict]:
    merged = {k: dict(v) for k, v in existing.items()}
    for d in discovered:
        if d["id"] not in merged:
            merged[d["id"]] = new_row(
                d["id"], d["name"], d["folder"], d["visibility"], d["share_url"]
            )
    return merged
