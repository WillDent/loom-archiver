from loom_archiver import ledger


def test_new_row_defaults_to_pending():
    r = ledger.new_row("v1", "Name", "main-library", "public", "https://x")
    assert r["mp4_status"] == "pending"
    assert r["transcript_status"] == "pending"
    assert r["error"] == ""


def test_write_then_read_roundtrip(tmp_path):
    path = tmp_path / "ledger.csv"
    rows = {"v1": ledger.new_row("v1", "A", "main-library", "public", "u1")}
    ledger.write_ledger(path, rows)
    back = ledger.read_ledger(path)
    assert back["v1"]["name"] == "A"
    assert back["v1"]["mp4_status"] == "pending"


def test_read_missing_file_returns_empty(tmp_path):
    assert ledger.read_ledger(tmp_path / "nope.csv") == {}


def test_merge_preserves_existing_status_and_adds_new():
    existing = {"v1": ledger.new_row("v1", "A", "main-library", "public", "u1")}
    existing["v1"]["mp4_status"] = "done"
    discovered = [
        {"id": "v1", "name": "A", "folder": "main-library", "visibility": "public", "share_url": "u1"},
        {"id": "v2", "name": "B", "folder": "AIDemos", "visibility": "private", "share_url": "u2"},
    ]
    merged = ledger.merge_inventory(existing, discovered)
    assert merged["v1"]["mp4_status"] == "done"   # preserved
    assert merged["v2"]["mp4_status"] == "pending"  # new
    assert len(merged) == 2


def test_merge_returns_independent_snapshot():
    existing = {"v1": ledger.new_row("v1", "A", "main-library", "public", "u1")}
    merged = ledger.merge_inventory(existing, [])
    merged["v1"]["mp4_status"] = "done"
    assert existing["v1"]["mp4_status"] == "pending"
