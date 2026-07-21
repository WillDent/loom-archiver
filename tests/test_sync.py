import pytest
from loom_archiver import run, sync, ledger, graphql
from loom_archiver.config import Config


class GoodApi:
    def execute(self, op, query, variables):
        return {}


# --- Part A: process_transcript refactor parity -----------------------------

def test_process_transcript_marks_done(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: True)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    out = run.process_transcript(row, GoodApi(), object(), cfg)
    assert out["transcript_status"] == "done"
    assert out["error"] == ""


def test_process_transcript_marks_unavailable(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: False)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    out = run.process_transcript(row, GoodApi(), object(), cfg)
    assert out["transcript_status"] == "unavailable"


def test_process_transcript_marks_failed(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("captions endpoint 500")

    monkeypatch.setattr(run.transcripts, "fetch_and_write", boom)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    out = run.process_transcript(row, GoodApi(), object(), cfg)
    assert out["transcript_status"] == "failed"
    assert out["error"].startswith("transcript:")
    assert "captions endpoint 500" in out["error"]


def test_process_transcript_reraises_auth_error(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)

    def boom(*a, **k):
        raise graphql.AuthError("expired")

    monkeypatch.setattr(run.transcripts, "fetch_and_write", boom)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    with pytest.raises(graphql.AuthError):
        run.process_transcript(row, GoodApi(), object(), cfg)


def test_process_transcript_skips_already_done(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    calls = {"n": 0}

    def t(*a, **k):
        calls["n"] += 1
        return True

    monkeypatch.setattr(run.transcripts, "fetch_and_write", t)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    row["transcript_status"] = "done"
    run.process_transcript(row, GoodApi(), object(), cfg)
    assert calls["n"] == 0


def test_process_transcript_skips_already_unavailable(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    calls = {"n": 0}

    def t(*a, **k):
        calls["n"] += 1
        return True

    monkeypatch.setattr(run.transcripts, "fetch_and_write", t)
    row = ledger.new_row("v1", "Demo", "main-library", "public", "u1")
    row["transcript_status"] = "unavailable"
    run.process_transcript(row, GoodApi(), object(), cfg)
    assert calls["n"] == 0


# --- sync_transcripts ---------------------------------------------------------

def _write_ledger(cfg, rows):
    ledger_path = cfg.dest_root / "loom_video_list_progress.csv"
    ledger.write_ledger(ledger_path, rows)
    return ledger_path


def test_sync_transcripts_skips_done_and_unavailable(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    rows = {
        "v1": ledger.new_row("v1", "A", "main-library", "public", "u1"),
        "v2": ledger.new_row("v2", "B", "main-library", "public", "u2"),
    }
    rows["v1"]["transcript_status"] = "done"
    rows["v2"]["transcript_status"] = "unavailable"
    _write_ledger(cfg, rows)

    calls = {"n": 0}

    def t(*a, **k):
        calls["n"] += 1
        return True

    monkeypatch.setattr(run.transcripts, "fetch_and_write", t)
    result = sync.sync_transcripts(cfg, GoodApi(), object())
    assert calls["n"] == 0
    assert result == {"synced": 0, "unavailable": 0, "failed": 0, "remaining": 0, "done": True}


def test_sync_transcripts_respects_limit_and_reports_remaining(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    rows = {
        f"v{i}": ledger.new_row(f"v{i}", f"Name{i}", "main-library", "public", f"u{i}")
        for i in range(5)
    }
    _write_ledger(cfg, rows)

    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: True)

    result = sync.sync_transcripts(cfg, GoodApi(), object(), limit=2)
    assert result["synced"] == 2
    assert result["remaining"] == 3
    assert result["done"] is False

    result2 = sync.sync_transcripts(cfg, GoodApi(), object(), limit=2)
    assert result2["synced"] == 2
    assert result2["remaining"] == 1
    assert result2["done"] is False

    result3 = sync.sync_transcripts(cfg, GoodApi(), object(), limit=2)
    assert result3["synced"] == 1
    assert result3["remaining"] == 0
    assert result3["done"] is True


def test_sync_transcripts_folder_filter(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    rows = {
        "v1": ledger.new_row("v1", "A", "MD Turbines/sub-folder-md", "public", "u1"),
        "v2": ledger.new_row("v2", "B", "main-library", "public", "u2"),
    }
    _write_ledger(cfg, rows)

    monkeypatch.setattr(run.transcripts, "fetch_and_write", lambda *a, **k: True)

    result = sync.sync_transcripts(cfg, GoodApi(), object(), folder="MD Turbines/sub-folder-md")
    assert result["synced"] == 1
    assert result["remaining"] == 0

    saved = ledger.read_ledger(cfg.dest_root / "loom_video_list_progress.csv")
    assert saved["v1"]["transcript_status"] == "done"
    assert saved["v2"]["transcript_status"] == "pending"


def test_sync_transcripts_persists_ledger_after_each_row(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    rows = {
        "v1": ledger.new_row("v1", "A", "main-library", "public", "u1"),
        "v2": ledger.new_row("v2", "B", "main-library", "public", "u2"),
        "v3": ledger.new_row("v3", "C", "main-library", "public", "u3"),
    }
    ledger_path = _write_ledger(cfg, rows)

    calls = {"n": 0}

    def maybe_boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise graphql.AuthError("expired")
        return True

    monkeypatch.setattr(run.transcripts, "fetch_and_write", maybe_boom)

    with pytest.raises(graphql.AuthError):
        sync.sync_transcripts(cfg, GoodApi(), object())

    saved = ledger.read_ledger(ledger_path)
    assert saved["v1"]["transcript_status"] == "done"
    assert saved["v2"]["transcript_status"] == "done"
    assert saved["v3"]["transcript_status"] == "pending"


def test_sync_transcripts_counts_failed_rows_as_remaining(monkeypatch, tmp_path):
    cfg = Config.default(tmp_path)
    rows = {
        "v1": ledger.new_row("v1", "A", "main-library", "public", "u1"),
        "v2": ledger.new_row("v2", "B", "main-library", "public", "u2"),
    }
    _write_ledger(cfg, rows)

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(run.transcripts, "fetch_and_write", boom)
    result = sync.sync_transcripts(cfg, GoodApi(), object())
    assert result["failed"] == 2
    assert result["remaining"] == 2
    assert result["done"] is False


def test_sync_transcripts_empty_ledger(tmp_path):
    cfg = Config.default(tmp_path)
    result = sync.sync_transcripts(cfg, GoodApi(), object())
    assert result["synced"] == 0
    assert result["unavailable"] == 0
    assert result["failed"] == 0
    assert result["remaining"] == 0
    assert result["done"] is True
    assert result["ledger_empty"] is True
