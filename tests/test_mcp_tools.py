"""Tests for the SDK-free MCP tool implementations in loom_archiver.mcp.tools.

Must NOT import the mcp SDK -- these run always, even in CI where the `mcp`
extra isn't installed.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from loom_archiver import diskguard, graphql, ledger
from loom_archiver.config import Config
from loom_archiver.mcp import tools


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _cfg(tmp_path, monkeypatch=None) -> Config:
    if monkeypatch is not None:
        monkeypatch.setenv("LOOM_ARCHIVER_CONFIG_DIR", str(tmp_path / "cfg"))
    return Config.default(tmp_path / "archive")


def _ledger_path(cfg: Config) -> Path:
    return cfg.dest_root / "loom_video_list_progress.csv"


# ---------------------------------------------------------------------------
# archive_status
# ---------------------------------------------------------------------------

def _write_status_ledger(cfg: Config) -> None:
    rows = {
        "v1": ledger.new_row("v1", "One", "main-library", "public", "https://x/v1"),
        "v2": ledger.new_row("v2", "Two", "main-library", "public", "https://x/v2"),
        "v3": ledger.new_row("v3", "Three", "AIDemos", "private", "https://x/v3"),
    }
    rows["v1"]["mp4_status"] = "done"
    rows["v1"]["transcript_status"] = "done"
    rows["v2"]["mp4_status"] = "failed"
    rows["v2"]["transcript_status"] = "unavailable"
    rows["v3"]["mp4_status"] = "pending"
    rows["v3"]["transcript_status"] = "pending"
    ledger.write_ledger(_ledger_path(cfg), rows)


def test_archive_status_counts_and_flags(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    _write_status_ledger(cfg)
    cfg.dest_root.mkdir(parents=True, exist_ok=True)
    cfg.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.auth_state_path.write_text("{}")
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

    result = tools.archive_status(cfg)

    assert result["dest_root"] == str(cfg.dest_root)
    assert result["total"] == 3
    assert result["with_transcript"] == 1  # only v1 is "done"
    assert result["with_mp4"] == 1  # only v1 is "done"
    # pending_transcript: not in (done, unavailable) -> only v3
    assert result["pending_transcript"] == 1
    # pending_mp4: != done -> v2, v3
    assert result["pending_mp4"] == 2
    assert result["ffmpeg_present"] is True
    assert result["session_file_present"] is True
    assert isinstance(result["disk_free_gb"], float)


def test_archive_status_missing_ledger_returns_zeros(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)

    result = tools.archive_status(cfg)

    assert result["total"] == 0
    assert result["with_transcript"] == 0
    assert result["with_mp4"] == 0
    assert result["pending_transcript"] == 0
    assert result["pending_mp4"] == 0
    assert result["ledger_present"] is False
    assert result["ffmpeg_present"] is False
    assert result["session_file_present"] is False


# ---------------------------------------------------------------------------
# list_folders
# ---------------------------------------------------------------------------

def test_list_folders_distinct_labels_depth_and_counts(tmp_path):
    cfg = _cfg(tmp_path)
    rows = {
        "v1": ledger.new_row("v1", "A", "main-library", "public", "u1"),
        "v2": ledger.new_row("v2", "B", "main-library", "public", "u2"),
        "v3": ledger.new_row("v3", "C", "MD Turbines", "public", "u3"),
        "v4": ledger.new_row("v4", "D", "MD Turbines/sub-folder-md", "public", "u4"),
    }
    ledger.write_ledger(_ledger_path(cfg), rows)

    result = tools.list_folders(cfg)

    by_path = {f["path"]: f for f in result}
    assert set(by_path) == {"main-library", "MD Turbines", "MD Turbines/sub-folder-md"}
    assert by_path["main-library"]["video_count"] == 2
    assert by_path["main-library"]["depth"] == 0
    assert by_path["MD Turbines"]["video_count"] == 1
    assert by_path["MD Turbines"]["depth"] == 0
    assert by_path["MD Turbines/sub-folder-md"]["video_count"] == 1
    assert by_path["MD Turbines/sub-folder-md"]["depth"] == 1
    # sorted by path
    assert [f["path"] for f in result] == sorted(by_path)


def test_list_folders_empty_when_no_ledger(tmp_path):
    cfg = _cfg(tmp_path)
    assert tools.list_folders(cfg) == []


# ---------------------------------------------------------------------------
# list_videos
# ---------------------------------------------------------------------------

def _build_videos_ledger(cfg: Config) -> None:
    rows = {
        "v1": ledger.new_row("v1", "One", "main-library", "public", "u1"),
        "v2": ledger.new_row("v2", "Two", "main-library", "public", "u2"),
        "v3": ledger.new_row("v3", "Three", "AIDemos", "private", "u3"),
        "v4": ledger.new_row("v4", "Four", "AIDemos/sub", "private", "u4"),
    }
    rows["v1"]["mp4_status"] = "done"
    rows["v1"]["transcript_status"] = "done"
    rows["v2"]["mp4_status"] = "pending"
    rows["v2"]["transcript_status"] = "unavailable"
    rows["v3"]["mp4_status"] = "done"
    rows["v3"]["transcript_status"] = "pending"
    rows["v4"]["mp4_status"] = "failed"
    rows["v4"]["transcript_status"] = "done"
    ledger.write_ledger(_ledger_path(cfg), rows)


def test_list_videos_folder_exact_match_not_prefix(tmp_path):
    cfg = _cfg(tmp_path)
    _build_videos_ledger(cfg)

    result = tools.list_videos(cfg, folder="AIDemos")

    ids = {v["id"] for v in result["items"]}
    assert ids == {"v3"}  # not v4, which is AIDemos/sub
    assert result["total"] == 1


def test_list_videos_status_has_transcript(tmp_path):
    cfg = _cfg(tmp_path)
    _build_videos_ledger(cfg)
    result = tools.list_videos(cfg, status="has_transcript")
    assert {v["id"] for v in result["items"]} == {"v1", "v4"}


def test_list_videos_status_has_mp4(tmp_path):
    cfg = _cfg(tmp_path)
    _build_videos_ledger(cfg)
    result = tools.list_videos(cfg, status="has_mp4")
    assert {v["id"] for v in result["items"]} == {"v1", "v3"}


def test_list_videos_status_pending(tmp_path):
    cfg = _cfg(tmp_path)
    _build_videos_ledger(cfg)
    result = tools.list_videos(cfg, status="pending")
    # v2: mp4 pending -> pending. v3: transcript pending -> pending.
    # v4: mp4 failed -> pending. v1: both done -> not pending.
    assert {v["id"] for v in result["items"]} == {"v2", "v3", "v4"}


def test_list_videos_paging_total_vs_items(tmp_path):
    cfg = _cfg(tmp_path)
    _build_videos_ledger(cfg)

    result = tools.list_videos(cfg, limit=2, offset=0)
    assert result["total"] == 4
    assert len(result["items"]) == 2

    result2 = tools.list_videos(cfg, limit=2, offset=2)
    assert result2["total"] == 4
    assert len(result2["items"]) == 2

    # no overlap between the two pages
    ids_page1 = {v["id"] for v in result["items"]}
    ids_page2 = {v["id"] for v in result2["items"]}
    assert ids_page1.isdisjoint(ids_page2)

    result3 = tools.list_videos(cfg, limit=2, offset=4)
    assert result3["total"] == 4
    assert result3["items"] == []


def test_list_videos_item_shape(tmp_path):
    cfg = _cfg(tmp_path)
    _build_videos_ledger(cfg)
    result = tools.list_videos(cfg, folder="main-library", limit=1)
    item = result["items"][0]
    assert set(item) == {"id", "name", "folder", "visibility", "share_url",
                          "mp4_status", "transcript_status"}


# ---------------------------------------------------------------------------
# search_transcripts (delegates)
# ---------------------------------------------------------------------------

def test_search_transcripts_delegates_to_search_module(tmp_path):
    cfg = _cfg(tmp_path)
    _write(cfg.dest_root / "main-library" / "v1__One.txt",
           "This transcript mentions UniqueUniqueMarker exactly once.")
    ledger.write_ledger(_ledger_path(cfg), {
        "v1": ledger.new_row("v1", "One", "main-library", "public", "https://x/v1"),
    })

    results = tools.search_transcripts(cfg, "UniqueUniqueMarker")

    assert len(results) == 1
    assert results[0]["id"] == "v1"
    assert results[0]["share_url"] == "https://x/v1"


# ---------------------------------------------------------------------------
# get_transcript
# ---------------------------------------------------------------------------

def test_get_transcript_present(tmp_path):
    cfg = _cfg(tmp_path)
    ledger.write_ledger(_ledger_path(cfg), {
        "v1": ledger.new_row("v1", "My Video", "main-library", "public", "https://x/v1"),
    })
    _write(cfg.dest_root / "main-library" / "v1__My Video.txt", "Hello world transcript text.")

    result = tools.get_transcript(cfg, "v1")

    assert result["id"] == "v1"
    assert result["name"] == "My Video"
    assert result["share_url"] == "https://x/v1"
    assert result["text"] == "Hello world transcript text."
    assert result["available"] is True
    assert result["truncated"] is False


def test_get_transcript_truncates_with_max_chars(tmp_path):
    cfg = _cfg(tmp_path)
    ledger.write_ledger(_ledger_path(cfg), {
        "v1": ledger.new_row("v1", "My Video", "main-library", "public", "https://x/v1"),
    })
    _write(cfg.dest_root / "main-library" / "v1__My Video.txt", "0123456789" * 5)

    result = tools.get_transcript(cfg, "v1", max_chars=10)

    assert result["text"] == "0123456789"
    assert result["truncated"] is True
    assert result["available"] is True


def test_get_transcript_row_exists_but_txt_missing(tmp_path):
    cfg = _cfg(tmp_path)
    ledger.write_ledger(_ledger_path(cfg), {
        "v1": ledger.new_row("v1", "My Video", "main-library", "public", "https://x/v1"),
    })

    result = tools.get_transcript(cfg, "v1")

    assert result["id"] == "v1"
    assert result["name"] == "My Video"
    assert result["share_url"] == "https://x/v1"
    assert result["text"] is None
    assert result["available"] is False
    assert "sync_transcripts" in result["message"]


def test_get_transcript_id_not_in_ledger(tmp_path):
    cfg = _cfg(tmp_path)
    ledger.write_ledger(_ledger_path(cfg), {
        "v1": ledger.new_row("v1", "My Video", "main-library", "public", "https://x/v1"),
    })

    result = tools.get_transcript(cfg, "does-not-exist")

    assert result["error"] == "not_found"
    assert "does-not-exist" in result["message"]
    assert "refresh_inventory" in result["message"]


# ---------------------------------------------------------------------------
# auth_status
# ---------------------------------------------------------------------------

class _FakeApiOk:
    def execute(self, operation_name, query, variables):
        return {"getPublishedFolders": {"folders": {"edges": [], "pageInfo": {}}}}

    def close(self):
        pass


class _FakeApiAuthError:
    def execute(self, operation_name, query, variables):
        raise graphql.AuthError("no session")

    def close(self):
        pass


class _FakeHttp:
    def close(self):
        pass


def test_auth_status_success(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)

    from contextlib import contextmanager

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)

    result = tools.auth_status(cfg)

    assert result["authenticated"] is True
    assert result["message"] == "Signed in."


def test_auth_status_auth_error(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path)

    from contextlib import contextmanager

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiAuthError(), _FakeHttp()

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)

    result = tools.auth_status(cfg)

    assert result["authenticated"] is False
    assert result["error"] == "not_authenticated"
    assert "expired" not in result["message"].lower()


# ---------------------------------------------------------------------------
# refresh_inventory
# ---------------------------------------------------------------------------

def test_refresh_inventory_summary(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    session_calls = []

    @contextmanager
    def fake_open_session(cfg_arg):
        assert cfg_arg is cfg
        session_calls.append("open")
        yield _FakeApiOk(), _FakeHttp()
        session_calls.append("close")

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)

    merged = {
        "v1": ledger.new_row("v1", "One", "main-library", "public", "u1"),
        "v2": ledger.new_row("v2", "Two", "AIDemos", "public", "u2"),
        "v3": ledger.new_row("v3", "Three", "AIDemos/sub", "public", "u3"),
    }

    def fake_run_inventory(api, cfg_arg):
        assert cfg_arg is cfg
        return merged

    monkeypatch.setattr(tools.inventory, "run_inventory", fake_run_inventory)

    result = tools.refresh_inventory(cfg)

    assert result["total"] == 3
    assert result["folders"] == 2  # AIDemos, AIDemos/sub -- excludes main-library
    assert "3" in result["message"]
    assert session_calls == ["open", "close"]


# ---------------------------------------------------------------------------
# sync_transcripts
# ---------------------------------------------------------------------------

def test_sync_transcripts_passthrough_and_forwards_args(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)

    captured = {}
    sentinel = {"synced": 3, "unavailable": 1, "failed": 0, "remaining": 5, "done": False}

    def fake_sync(cfg_arg, api, http, *, folder=None, limit=200):
        captured["cfg"] = cfg_arg
        captured["folder"] = folder
        captured["limit"] = limit
        return sentinel

    monkeypatch.setattr(tools.sync_mod, "sync_transcripts", fake_sync)

    result = tools.sync_transcripts(cfg, folder="AIDemos", limit=50)

    assert result is sentinel
    assert captured["cfg"] is cfg
    assert captured["folder"] == "AIDemos"
    assert captured["limit"] == 50


# ---------------------------------------------------------------------------
# download_videos
# ---------------------------------------------------------------------------

def _make_pending_rows(n, folder="AIDemos", prefix="v"):
    rows = {}
    for i in range(n):
        vid = f"{prefix}{i}"
        rows[vid] = ledger.new_row(vid, f"Name {i}", folder, "public", f"https://x/{vid}")
    return rows


def test_download_videos_guardrail_too_many_blocks_everything(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = _make_pending_rows(20)
    ledger.write_ledger(_ledger_path(cfg), rows)

    def forbidden_open_session(cfg_arg):
        raise AssertionError("session must not be opened when guardrail trips")

    def forbidden_process_video(row, api, http, cfg_arg):
        raise AssertionError("process_video must not be called when guardrail trips")

    monkeypatch.setattr(tools.context, "open_session", forbidden_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", forbidden_process_video)

    result = tools.download_videos(cfg, folder="AIDemos", max=10, force=False)

    assert result["error"] == "too_many"
    assert result["requested"] == 20
    assert result["max"] == 10


def test_download_videos_force_bypasses_guardrail(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = _make_pending_rows(20)
    ledger.write_ledger(_ledger_path(cfg), rows)

    opened = []

    @contextmanager
    def fake_open_session(cfg_arg):
        opened.append(True)
        yield _FakeApiOk(), _FakeHttp()

    calls = []

    def fake_process_video(row, api, http, cfg_arg):
        calls.append(row["id"])
        row["mp4_status"] = "done"
        return row

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", fake_process_video)

    result = tools.download_videos(cfg, folder="AIDemos", max=10, force=True)

    assert "error" not in result
    assert result["requested"] == 20
    assert result["downloaded"] == 20
    assert len(calls) == 20
    assert opened == [True]


def test_download_videos_ffmpeg_missing_blocks_download(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = _make_pending_rows(2)
    ledger.write_ledger(_ledger_path(cfg), rows)

    def raise_ffmpeg_missing():
        raise tools.preflight.MissingDependencyError("ffmpeg was not found on PATH")

    def forbidden_open_session(cfg_arg):
        raise AssertionError("must not open a session when ffmpeg is missing")

    def forbidden_process_video(row, api, http, cfg_arg):
        raise AssertionError("must not process any video when ffmpeg is missing")

    monkeypatch.setattr(tools.preflight, "assert_ffmpeg", raise_ffmpeg_missing)
    monkeypatch.setattr(tools.context, "open_session", forbidden_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", forbidden_process_video)

    result = tools.download_videos(cfg, folder="AIDemos", max=10)

    assert result["error"] == "ffmpeg_missing"
    assert "ffmpeg" in result["message"].lower()


def test_download_videos_no_target_when_neither_ids_nor_folder(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    def forbidden(*a, **k):
        raise AssertionError("nothing should be called for no_target")

    monkeypatch.setattr(tools.context, "open_session", forbidden)
    monkeypatch.setattr(tools.run_mod, "process_video", forbidden)
    monkeypatch.setattr(tools.preflight, "assert_ffmpeg", forbidden)

    result = tools.download_videos(cfg)

    assert result == {
        "error": "no_target",
        "message": "Specify `ids` (a list of video ids) or `folder`.",
    }


def test_download_videos_happy_path_by_ids(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = {
        "a": ledger.new_row("a", "A", "main-library", "public", "https://x/a"),
        "b": ledger.new_row("b", "B", "main-library", "public", "https://x/b"),
    }
    ledger.write_ledger(_ledger_path(cfg), rows)

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    def fake_process_video(row, api, http, cfg_arg):
        row["mp4_status"] = "done"
        return row

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", fake_process_video)

    result = tools.download_videos(cfg, ids=["a", "b"], max=10)

    assert result["requested"] == 2
    assert result["downloaded"] == 2
    assert result["failed"] == 0
    assert {r["id"] for r in result["results"]} == {"a", "b"}
    for r in result["results"]:
        assert set(r) == {"id", "name", "mp4_status", "error"}

    persisted = ledger.read_ledger(_ledger_path(cfg))
    assert persisted["a"]["mp4_status"] == "done"
    assert persisted["b"]["mp4_status"] == "done"


def test_download_videos_disk_full_stops_and_persists_progress(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = {
        "a": ledger.new_row("a", "A", "main-library", "public", "https://x/a"),
        "b": ledger.new_row("b", "B", "main-library", "public", "https://x/b"),
        "c": ledger.new_row("c", "C", "main-library", "public", "https://x/c"),
    }
    ledger.write_ledger(_ledger_path(cfg), rows)

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    calls = []

    def fake_process_video(row, api, http, cfg_arg):
        calls.append(row["id"])
        if row["id"] == "b":
            raise diskguard.DiskFullError("disk is full")
        row["mp4_status"] = "done"
        return row

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", fake_process_video)

    result = tools.download_videos(cfg, ids=["a", "b", "c"], max=10)

    assert result["error"] == "disk_full"
    assert result["downloaded"] == 1
    assert calls == ["a", "b"]  # "c" never reached

    persisted = ledger.read_ledger(_ledger_path(cfg))
    assert persisted["a"]["mp4_status"] == "done"


def test_download_videos_unknown_ids_reported_known_still_processed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = {
        "a": ledger.new_row("a", "A", "main-library", "public", "https://x/a"),
    }
    ledger.write_ledger(_ledger_path(cfg), rows)

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    calls = []

    def fake_process_video(row, api, http, cfg_arg):
        calls.append(row["id"])
        row["mp4_status"] = "done"
        return row

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", fake_process_video)

    result = tools.download_videos(cfg, ids=["a", "does-not-exist"], max=10)

    assert result["unknown_ids"] == ["does-not-exist"]
    assert calls == ["a"]
    assert result["downloaded"] == 1


def test_download_videos_disk_full_includes_unknown_ids_when_present(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = {
        "a": ledger.new_row("a", "A", "main-library", "public", "https://x/a"),
    }
    ledger.write_ledger(_ledger_path(cfg), rows)

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    def fake_process_video(row, api, http, cfg_arg):
        raise diskguard.DiskFullError("disk is full")

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", fake_process_video)

    result = tools.download_videos(cfg, ids=["a", "does-not-exist"], max=10)

    assert result["error"] == "disk_full"
    assert result["unknown_ids"] == ["does-not-exist"]


def test_download_videos_folder_exact_match_not_prefix(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = {
        "v1": ledger.new_row("v1", "One", "MD Turbines", "public", "https://x/v1"),
        "v2": ledger.new_row("v2", "Two", "MD Turbines/sub-folder-md", "public", "https://x/v2"),
    }
    ledger.write_ledger(_ledger_path(cfg), rows)

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    calls = []

    def fake_process_video(row, api, http, cfg_arg):
        calls.append(row["id"])
        row["mp4_status"] = "done"
        return row

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", fake_process_video)

    result = tools.download_videos(cfg, folder="MD Turbines", max=10)

    assert calls == ["v1"]
    assert result["requested"] == 1


def test_download_videos_ids_force_redownloads_done_row(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = {
        "a": ledger.new_row("a", "A", "main-library", "public", "https://x/a"),
    }
    rows["a"]["mp4_status"] = "done"
    ledger.write_ledger(_ledger_path(cfg), rows)

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    calls = []

    def fake_process_video(row, api, http, cfg_arg):
        calls.append(row["id"])
        row["mp4_status"] = "done"
        return row

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", fake_process_video)

    # force=False: an already-done row is not a target.
    result = tools.download_videos(cfg, ids=["a"], max=10, force=False)
    assert calls == []
    assert result["requested"] == 0
    assert result["downloaded"] == 0

    # force=True: the same already-done row IS re-downloaded.
    result = tools.download_videos(cfg, ids=["a"], max=10, force=True)
    assert calls == ["a"]
    assert result["requested"] == 1
    assert result["downloaded"] == 1


def test_download_videos_folder_force_redownloads_done_rows(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rows = {
        "done1": ledger.new_row("done1", "Done", "AIDemos", "public", "https://x/done1"),
        "pending1": ledger.new_row("pending1", "Pending", "AIDemos", "public", "https://x/pending1"),
    }
    rows["done1"]["mp4_status"] = "done"
    ledger.write_ledger(_ledger_path(cfg), rows)

    @contextmanager
    def fake_open_session(cfg_arg):
        yield _FakeApiOk(), _FakeHttp()

    calls = []

    def fake_process_video(row, api, http, cfg_arg):
        calls.append(row["id"])
        row["mp4_status"] = "done"
        return row

    monkeypatch.setattr(tools.context, "open_session", fake_open_session)
    monkeypatch.setattr(tools.run_mod, "process_video", fake_process_video)

    # force=False: only the pending row is a target.
    result = tools.download_videos(cfg, folder="AIDemos", max=10, force=False)
    assert calls == ["pending1"]
    assert result["requested"] == 1

    calls.clear()

    # force=True: BOTH rows are targets, including the already-done one.
    result = tools.download_videos(cfg, folder="AIDemos", max=10, force=True)
    assert set(calls) == {"done1", "pending1"}
    assert result["requested"] == 2
