import pytest
import httpx
from loom_archiver import run, ledger, graphql, diskguard
from loom_archiver.config import Config


class GoodApi:
    def execute(self, op, query, variables):
        return {}


def test_process_video_marks_done(monkeypatch, tmp_path):
    cfg = Config.default()
    cfg.disk_floor_gb = 0
    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: True)
    monkeypatch.setattr(run.downloader, "download_mp4", lambda *a, **k: True)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    with httpx.Client() as http:
        out = run.process_video(row, GoodApi(), http, cfg)
    assert out["transcript_status"] == "done"
    assert out["mp4_status"] == "done"
    assert out["error"] == ""


def test_process_video_records_failure(monkeypatch, tmp_path):
    cfg = Config.default()
    cfg.disk_floor_gb = 0

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: True)
    monkeypatch.setattr(run.downloader, "download_mp4", boom)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    with httpx.Client() as http:
        out = run.process_video(row, GoodApi(), http, cfg)
    assert out["transcript_status"] == "done"
    assert out["mp4_status"] == "failed"
    assert "network down" in out["error"]


def test_process_video_skips_completed_legs(monkeypatch):
    cfg = Config.default()
    cfg.disk_floor_gb = 0
    called = {"t": 0, "m": 0}

    def t(*a, **k):
        called["t"] += 1
        return True

    def m(*a, **k):
        called["m"] += 1
        return True

    monkeypatch.setattr(run.transcripts, "fetch_and_write", t)
    monkeypatch.setattr(run.downloader, "download_mp4", m)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    row["transcript_status"] = "done"
    row["mp4_status"] = "done"
    with httpx.Client() as http:
        run.process_video(row, GoodApi(), http, cfg)
    assert called == {"t": 0, "m": 0}  # nothing re-run


def test_process_video_reraises_auth_error(monkeypatch):
    cfg = Config.default()
    cfg.disk_floor_gb = 0

    def boom(*a, **k):
        raise graphql.AuthError("expired")

    monkeypatch.setattr(run.transcripts, "fetch_and_write", boom)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    with httpx.Client() as http:
        with pytest.raises(graphql.AuthError):
            run.process_video(row, GoodApi(), http, cfg)


def test_process_video_reraises_disk_full_error(monkeypatch):
    cfg = Config.default()
    cfg.disk_floor_gb = 0

    def boom(*a, **k):
        raise diskguard.DiskFullError("full")

    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: True)
    monkeypatch.setattr(run.downloader, "download_mp4", boom)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    with httpx.Client() as http:
        with pytest.raises(diskguard.DiskFullError):
            run.process_video(row, GoodApi(), http, cfg)


def test_no_transcript_is_unavailable_not_failed(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    cfg.disk_floor_gb = 0
    monkeypatch.setattr(run.preflight, "assert_ffmpeg", lambda: None)
    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: False)
    monkeypatch.setattr(run.downloader, "download_mp4", lambda *a, **k: True)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    with httpx.Client() as http:
        out = run.process_video(row, GoodApi(), http, cfg)
    assert out["transcript_status"] == "unavailable"
    assert out["mp4_status"] == "done"
    assert out["error"] == ""


def test_transcript_error_is_still_failed(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    cfg.disk_floor_gb = 0

    def boom(*a, **k):
        raise RuntimeError("captions endpoint 500")

    monkeypatch.setattr(run.preflight, "assert_ffmpeg", lambda: None)
    monkeypatch.setattr(run.transcripts, "fetch_and_write", boom)
    monkeypatch.setattr(run.downloader, "download_mp4", lambda *a, **k: True)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    with httpx.Client() as http:
        out = run.process_video(row, GoodApi(), http, cfg)
    assert out["transcript_status"] == "failed"
    assert "captions endpoint 500" in out["error"]


def test_unavailable_transcript_is_not_retried(monkeypatch, tmp_path):
    """Otherwise every rerun re-requests transcripts that will never exist."""
    cfg = Config.default(tmp_path)
    cfg.disk_floor_gb = 0
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return False

    monkeypatch.setattr(run.preflight, "assert_ffmpeg", lambda: None)
    monkeypatch.setattr(run.transcripts, "fetch_and_write", counting)
    monkeypatch.setattr(run.downloader, "download_mp4", lambda *a, **k: True)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    with httpx.Client() as http:
        row = run.process_video(row, GoodApi(), http, cfg)
        run.process_video(row, GoodApi(), http, cfg)
    assert calls["n"] == 1


def test_run_smoke(monkeypatch, tmp_path):
    cfg = Config.default()
    cfg.dest_root = tmp_path
    cfg.disk_floor_gb = 0
    cfg.request_delay = 0

    rows = {
        "v1": ledger.new_row("v1", "Demo1", "main-library", "public", "u1"),
        "v2": ledger.new_row("v2", "Demo2", "main-library", "public", "u2"),
    }

    monkeypatch.setattr(run.inventory, "run_inventory", lambda *a, **k: rows)
    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: True)
    monkeypatch.setattr(run.downloader, "download_mp4", lambda *a, **k: True)
    monkeypatch.setattr(run.preflight, "assert_ffmpeg", lambda: None)

    out = run.run(cfg, api_factory=lambda: GoodApi(), sleep=lambda s: None)

    assert out is rows
    for row in out.values():
        assert row["mp4_status"] == "done"
        assert row["transcript_status"] == "done"

    ledger_path = cfg.dest_root / "loom_video_list_progress.csv"
    assert ledger_path.exists()


def test_run_aborts_cleanly_on_disk_full(monkeypatch, tmp_path, capsys):
    cfg = Config.default()
    cfg.dest_root = tmp_path
    cfg.disk_floor_gb = 0
    cfg.request_delay = 0

    rows = {
        "v1": ledger.new_row("v1", "A", "main-library", "public", "u1"),
        "v2": ledger.new_row("v2", "B", "main-library", "public", "u2"),
    }

    monkeypatch.setattr(run.inventory, "run_inventory", lambda *a, **k: rows)
    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: True)

    def boom(*a, **k):
        raise diskguard.DiskFullError("disk full")

    monkeypatch.setattr(run.downloader, "download_mp4", boom)
    monkeypatch.setattr(run.preflight, "assert_ffmpeg", lambda: None)

    with pytest.raises(diskguard.DiskFullError):
        run.run(cfg, api_factory=lambda: GoodApi(), sleep=lambda s: None)

    out = capsys.readouterr().out
    assert "Stopped:" in out
