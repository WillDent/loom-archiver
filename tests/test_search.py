from loom_archiver import ledger, search
from loom_archiver.config import Config


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_corpus(tmp_path):
    """Lay out a small tmp dest_root with folders, .txt/.vtt files, and a ledger."""
    root = tmp_path

    # main-library files
    _write(root / "main-library" / "vA__Widget Launch Plan.txt",
           "We announced the Widget Launch today. It went well.")
    # sibling .vtt with matching content + a marker only it contains -- must be ignored
    _write(root / "main-library" / "vA__Widget Launch Plan.vtt",
           "WEBVTT\n\nWidget Launch VTTONLYMARKER should never surface")
    _write(root / "main-library" / "vB__Random Notes.txt",
           "Nothing relevant here at all.")
    # name contains a single underscore -- must split id on "__" not "_"
    _write(root / "main-library" / "vC__Weekly_Sync notes.txt",
           "This has a SyncKeywordUnique mention only once.")
    _write(root / "main-library" / "vE__Repeated Widget Launch Talk.txt",
           "Widget Launch. WIDGET LAUNCH! widget launch again for good measure.")
    _write(root / "main-library" / "vH__Widget Launch Extra.txt",
           "Extra widget launch info, and widget launch again.")
    _write(root / "main-library" / "vOrphan__Some Orphan Video.txt",
           "OrphanUniqueQuery appears here only.")
    _write(root / "main-library" / "vSnip__Snippet Test.txt",
           ("A" * 50) + " snippetmarker " + ("B" * 50))

    # MD Turbines / sub-folder-md exact-match distinction
    _write(root / "MD Turbines" / "vF__Turbine Overview.txt",
           "TurbineQuery details for MD Turbines main line.")
    _write(root / "MD Turbines" / "sub-folder-md" / "vG__Sub Turbine Detail.txt",
           "TurbineQuery detail for the sub turbine.")

    rows = {
        "vA": ledger.new_row("vA", "Ledger Widget Name", "main-library", "public",
                              "https://ledger.example/vA"),
        "vC": ledger.new_row("vC", "Weekly Sync", "main-library", "public",
                              "https://ledger.example/vC"),
        "vE": ledger.new_row("vE", "Repeated Widget Launch Talk", "main-library", "public",
                              "https://ledger.example/vE"),
        "vH": ledger.new_row("vH", "Widget Launch Extra", "main-library", "public",
                              "https://ledger.example/vH"),
        "vF": ledger.new_row("vF", "Turbine Overview", "MD Turbines", "public",
                              "https://ledger.example/vF"),
        "vG": ledger.new_row("vG", "Sub Turbine Detail", "MD Turbines/sub-folder-md", "public",
                              "https://ledger.example/vG"),
        "vSnip": ledger.new_row("vSnip", "Snippet Test", "main-library", "public",
                                 "https://ledger.example/vSnip"),
    }
    ledger.write_ledger(root / "loom_video_list_progress.csv", rows)
    return Config.default(root)


# 1 & 10 (partial): case-insensitive literal match, non-matching files excluded
def test_case_insensitive_match_skips_non_matching_files(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "widget launch")
    ids = {r["id"] for r in results}
    assert "vA" in ids
    assert "vE" in ids
    assert "vH" in ids
    assert "vB" not in ids  # "Random Notes" never matches


# 2: .vtt files are ignored even though they contain the query
def test_vtt_files_are_ignored(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "VTTONLYMARKER")
    assert results == []


# 3: id recovered from the prefix before the first "__", not the first "_"
def test_id_split_on_double_underscore_not_single(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "SyncKeywordUnique")
    assert len(results) == 1
    assert results[0]["id"] == "vC"


# 4: snippet window respects context_chars, includes the match, and marks truncation
def test_snippet_window_and_truncation_markers(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "snippetmarker", context_chars=10)
    assert len(results) == 1
    snippet = results[0]["snippet"]
    assert "snippetmarker" in snippet
    assert snippet.startswith("…")
    assert snippet.endswith("…")
    # window should be small: a handful of A's/B's plus the match, not all 100+50 chars
    assert len(snippet) < 40


# 5: match_count counts multiple occurrences within one file
def test_match_count_multiple_occurrences(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "widget launch")
    by_id = {r["id"]: r for r in results}
    assert by_id["vE"]["match_count"] == 3
    assert by_id["vH"]["match_count"] == 2
    assert by_id["vA"]["match_count"] == 1


# 6: ledger join supplies name/share_url over filename-derived values
def test_ledger_join_overrides_filename_derived_metadata(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "widget launch")
    by_id = {r["id"]: r for r in results}
    hit = by_id["vA"]
    assert hit["name"] == "Ledger Widget Name"
    assert hit["share_url"] == "https://ledger.example/vA"
    assert hit["folder"] == "main-library"


# 7: orphan file (id not in ledger) still returns a hit with best-effort fallbacks
def test_orphan_file_falls_back_to_derived_metadata(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "OrphanUniqueQuery")
    assert len(results) == 1
    hit = results[0]
    assert hit["id"] == "vOrphan"
    assert hit["name"] == "Some Orphan Video"
    assert hit["folder"] == "main-library"
    assert hit["share_url"] == "https://www.loom.com/share/vOrphan"


# 8: folder filter is EXACT -- "MD Turbines" must not match its sub-folder
def test_folder_filter_is_exact_not_prefix(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "TurbineQuery", folder="MD Turbines")
    ids = {r["id"] for r in results}
    assert ids == {"vF"}

    sub_results = search.search_transcripts(cfg, "TurbineQuery", folder="MD Turbines/sub-folder-md")
    sub_ids = {r["id"] for r in sub_results}
    assert sub_ids == {"vG"}


# 9: ranking by match_count desc, and limit caps results
def test_ranking_desc_and_limit(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "widget launch", limit=2)
    assert len(results) == 2
    assert [r["id"] for r in results] == ["vE", "vH"]
    assert results[0]["match_count"] >= results[1]["match_count"]


# 10: missing dest_root returns [], and a file with zero matches is excluded
def test_missing_dest_root_returns_empty_list(tmp_path):
    cfg = Config.default(tmp_path / "does-not-exist")
    assert search.search_transcripts(cfg, "anything") == []


def test_file_with_no_match_excluded(tmp_path):
    cfg = _build_corpus(tmp_path)
    results = search.search_transcripts(cfg, "no such phrase anywhere")
    assert results == []


# 11: an unreadable/undecodable .txt file must not abort the whole search --
# it's tolerated (via errors="replace") and other files' hits still come back.
def test_undecodable_file_does_not_abort_search(tmp_path):
    cfg = Config.default(tmp_path)
    bad_path = tmp_path / "main-library" / "vBad__Corrupted.txt"
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_bytes(b"\xff\xfe some \x80\x81 text query here")
    _write(tmp_path / "main-library" / "vGood__Good File.txt",
           "This transcript mentions GoodFileUniqueMarker clearly.")

    results = search.search_transcripts(cfg, "GoodFileUniqueMarker")

    ids = {r["id"] for r in results}
    assert "vGood" in ids
    assert results  # good hit was returned despite the undecodable sibling file


# 12: the query is matched as a literal substring, never compiled as a regex.
def test_query_is_matched_literally_not_as_regex(tmp_path):
    cfg = Config.default(tmp_path)
    _write(tmp_path / "main-library" / "vLit__Pricing Notes.txt",
           "Special tokens: a-c and a1c differ from the real a.c token. "
           "Standard discounts are 10% (net-30) a.b.c terms apply.")

    # Metacharacter-laden literal substrings match verbatim.
    paren_hits = search.search_transcripts(cfg, "(net-30)")
    assert {r["id"] for r in paren_hits} == {"vLit"}

    dotted_hits = search.search_transcripts(cfg, "a.b.c")
    assert {r["id"] for r in dotted_hits} == {"vLit"}
    assert dotted_hits[0]["match_count"] == 1

    # "a.c" is a substring of neither "a-c" nor "a1c" -- but as a *regex* the
    # "." wildcard would match either, so if search_transcripts ever started
    # compiling the query as a pattern, this count would jump from 1 to 3.
    literal_dot_hits = search.search_transcripts(cfg, "a.c")
    assert {r["id"] for r in literal_dot_hits} == {"vLit"}
    assert literal_dot_hits[0]["match_count"] == 1
