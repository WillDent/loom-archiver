from __future__ import annotations

import time
from pathlib import Path

import httpx

from . import graphql, inventory, transcripts, downloader, diskguard, ledger, preflight
from .config import Config
from .names import stem


def _dest_dir(cfg: Config, folder: str) -> Path:
    return cfg.dest_root / (folder or "main-library")


def process_transcript(row: dict, api, http, cfg: Config) -> dict:
    """Fetch+write this row's transcript, updating transcript_status/error in place.

    done = written, unavailable = video has no transcript (not an error),
    failed = genuine error. AuthError propagates (never swallowed).
    """
    file_stem = stem(row["id"], row["name"])
    dest_dir = _dest_dir(cfg, row["folder"])

    # `unavailable` means Loom has no transcript for this video -- a fact about
    # the video, not an error, and not worth retrying on every rerun. `failed`
    # is reserved for genuine errors.
    if row.get("transcript_status") not in ("done", "unavailable"):
        try:
            wrote = transcripts.fetch_and_write(api, http, row["id"], dest_dir, file_stem)
            row["transcript_status"] = "done" if wrote else "unavailable"
        except graphql.AuthError:
            raise
        except Exception as e:  # noqa: BLE001 - record and continue
            row["transcript_status"] = "failed"
            row["error"] = f"transcript: {e}"
    return row


def process_video(row: dict, api, http, cfg: Config) -> dict:
    file_stem = stem(row["id"], row["name"])
    dest_dir = _dest_dir(cfg, row["folder"])

    process_transcript(row, api, http, cfg)

    if row.get("mp4_status") != "done":
        try:
            ok = downloader.download_mp4(api, http, row["id"], dest_dir, file_stem, cfg)
            row["mp4_status"] = "done" if ok else "failed"
            if not ok:
                row["error"] = (row.get("error") + "; " if row.get("error") else "") + "no mp4 url"
        except (graphql.AuthError, diskguard.DiskFullError):
            raise
        except Exception as e:  # noqa: BLE001
            row["mp4_status"] = "failed"
            row["error"] = (row.get("error") + "; " if row.get("error") else "") + f"mp4: {e}"

    if row["transcript_status"] in ("done", "unavailable") and row["mp4_status"] == "done":
        row["error"] = ""
    return row


def _print_report(rows, processed) -> None:
    failures = [r for r in rows.values()
                if r["mp4_status"] == "failed" or r["transcript_status"] == "failed"]
    unavailable = sum(1 for r in rows.values() if r["transcript_status"] == "unavailable")
    print(f"\nDone. {processed} processed, {len(failures)} with failures.")
    if unavailable:
        print(f"{unavailable} video(s) have no transcript available (not an error).")
    for r in failures[:50]:
        print(f"  FAIL {r['id']} ({r['folder']}): {r['error']}")
    if len(failures) > 50:
        print(f"  ... and {len(failures) - 50} more (see ledger).")


def run(cfg: Config, api_factory=None, sleep=time.sleep) -> dict[str, dict]:
    api_factory = api_factory or (lambda: graphql.LoomGraphQL(
        graphql.load_cookie_header(cfg.auth_state_path), cfg.loom_web_version))
    api = api_factory()
    try:
        diskguard.assert_dest_mounted(cfg.dest_root)
        diskguard.assert_headroom(cfg.dest_root, cfg.disk_floor_gb)
        preflight.assert_ffmpeg()

        # Export cookies for yt-dlp (used to fetch HLS-only videos).
        if cfg.auth_state_path.exists():
            downloader.write_cookies_txt(cfg.auth_state_path, cfg.cookies_path)

        rows = inventory.run_inventory(api, cfg)
        ledger_path = cfg.dest_root / "loom_video_list_progress.csv"

        pending = [r for r in rows.values()
                   if r["mp4_status"] != "done"
                   or r["transcript_status"] not in ("done", "unavailable")]
        print(f"{len(pending)} videos to process.")

        processed = 0
        with httpx.Client(timeout=120) as http:
            try:
                for row in pending:
                    if processed % cfg.disk_check_interval == 0:
                        diskguard.assert_headroom(cfg.dest_root, cfg.disk_floor_gb)
                    process_video(row, api, http, cfg)
                    ledger.write_ledger(ledger_path, rows)
                    processed += 1
                    sleep(cfg.request_delay)
            except (graphql.AuthError, diskguard.DiskFullError) as e:
                print(f"\nStopped: {e}\nProgress saved to the ledger — rerun to resume.")
                _print_report(rows, processed)
                raise

        _print_report(rows, processed)
        return rows
    finally:
        close = getattr(api, "close", None)
        if callable(close):
            close()
