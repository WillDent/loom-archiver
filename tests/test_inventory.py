from loom_archiver import inventory
from loom_archiver import folders as folders_mod


# Shape matches a real authenticated GetLoomsForLibrary response
# (data.getLooms.videos.edges[].node with id/name/visibility; no share url).
RESPONSE_PAGE = {
    "getLooms": {
        "videos": {
            "edges": [
                {"node": {"id": "v1", "name": "Demo One", "visibility": "owner"}},
                {"node": {"id": "v2", "name": "Demo/Two", "visibility": "public"}},
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
    }
}


def test_parse_page_extracts_rows_and_cursor():
    rows, cursor = inventory.parse_page(RESPONSE_PAGE)
    assert cursor is None
    assert rows[0] == {"id": "v1", "name": "Demo One", "folder": "",
                       "visibility": "owner", "share_url": "https://www.loom.com/share/v1"}
    assert rows[1]["id"] == "v2"
    assert rows[1]["share_url"] == "https://www.loom.com/share/v2"


def _folder_page(nodes, cursor=None):
    return {
        "getPublishedFolders": {
            "folders": {
                "edges": [{"node": n} for n in nodes],
                "pageInfo": {"endCursor": cursor, "hasNextPage": cursor is not None},
            }
        }
    }


class RoutingApi:
    """Routes by operation name: videos vs folders."""

    def __init__(self, folder_tree=None):
        self.folder_tree = folder_tree or {}
        self.video_calls = []

    def execute(self, op, query, variables):
        if op == "GetPublishedFolders":
            if variables.get("filters") is None:
                return _folder_page([])  # cross-check: nothing extra
            return _folder_page(self.folder_tree.get(variables.get("parentFolderId"), []))
        self.video_calls.append(variables)
        return RESPONSE_PAGE


def test_discover_covers_loose_videos_and_every_folder():
    api = RoutingApi({
        None: [{"id": "f1", "name": "Clients", "visibility": "owner"}],
        "f1": [{"id": "f2", "name": "Acme", "visibility": "owner"}],
    })
    rows, tree = inventory.discover(api)
    # loose + 2 folders = 3 video queries, 2 rows each
    assert len(rows) == 6
    assert {f["id"] for f in tree} == {"f1", "f2"}
    labels = {r["folder"] for r in rows}
    assert labels == {"main-library", "Clients", "Clients/Acme"}


def test_discover_handles_account_with_no_folders():
    api = RoutingApi({})
    rows, tree = inventory.discover(api)
    assert tree == []
    assert {r["folder"] for r in rows} == {"main-library"}


def test_discover_queries_each_folder_by_id():
    api = RoutingApi({None: [{"id": "f1", "name": "Clients", "visibility": "owner"}]})
    inventory.discover(api)
    folder_ids = [v.get("folderId") for v in api.video_calls]
    assert None in folder_ids and "f1" in folder_ids


def test_discover_folders_only_skips_the_main_library_pass():
    """--folders-only exists so a sample run touches ~13 videos, not 1,667."""
    api = RoutingApi({
        None: [{"id": "f1", "name": "Clients", "visibility": "owner"}],
        "f1": [{"id": "f2", "name": "Acme", "visibility": "owner"}],
    })
    rows, tree = inventory.discover(api, folders_only=True)
    assert {r["folder"] for r in rows} == {"Clients", "Clients/Acme"}
    assert {f["id"] for f in tree} == {"f1", "f2"}
    assert None not in [v.get("folderId") for v in api.video_calls]


def test_discover_folders_only_still_walks_folders_recursively():
    api = RoutingApi({
        None: [{"id": "f1", "name": "Clients", "visibility": "owner"}],
        "f1": [{"id": "f2", "name": "Acme", "visibility": "owner"}],
    })
    _, tree = inventory.discover(api, folders_only=True)
    assert [f["path"] for f in tree] == ["Clients", "Clients/Acme"]


def test_discover_defaults_to_including_the_main_library():
    """The flag must be opt-in: the default archive stays complete."""
    api = RoutingApi({})
    rows, _ = inventory.discover(api)
    assert {r["folder"] for r in rows} == {"main-library"}


def test_report_shape_does_not_claim_main_library_coverage_under_folders_only(capsys):
    """Under --folders-only, the main library pass never ran -- the summary
    header must not tell the user it did (the project's worst failure mode:
    an archive silently reported as more complete than it is)."""
    rows = [
        {"id": "v1", "name": "A", "folder": "Clients", "visibility": "owner",
         "share_url": "https://www.loom.com/share/v1"},
    ]
    tree = [{"id": "f1", "name": "Clients", "path": "Clients", "depth": 0,
             "parent_id": None, "segments": ["Clients"]}]
    inventory._report_shape(rows, tree, folders_only=True)
    out = capsys.readouterr().out
    header = out.splitlines()[0]
    assert "main library" not in header.lower() or "not enumerated" in header.lower()
    assert "main-library" not in out


def test_report_shape_claims_main_library_coverage_when_enumerated(capsys):
    """When the main library pass DID run and produced rows, the header
    should still say so -- the fix must not regress the default (complete)
    archive's summary."""
    rows = [
        {"id": "v1", "name": "A", "folder": "main-library", "visibility": "owner",
         "share_url": "https://www.loom.com/share/v1"},
        {"id": "v2", "name": "B", "folder": "Clients", "visibility": "owner",
         "share_url": "https://www.loom.com/share/v2"},
    ]
    tree = [{"id": "f1", "name": "Clients", "path": "Clients", "depth": 0,
             "parent_id": None, "segments": ["Clients"]}]
    inventory._report_shape(rows, tree, folders_only=False)
    out = capsys.readouterr().out
    header = out.splitlines()[0]
    assert "+ main library" in header
    assert "main-library" in out  # per-folder breakdown line still lists it


def test_report_shape_claims_main_library_coverage_even_with_zero_main_library_rows(capsys):
    """A full (non-folders-only) run whose main-library pass returns zero rows
    (e.g. an account with no loose videos) must still report the main library
    as enumerated. Whether that pass ran is a fact about the run (folders_only),
    not something inferable from the rows it happened to produce -- a run that
    legitimately found nothing must not be mistaken for a run that skipped the
    pass entirely."""
    rows = [
        {"id": "v1", "name": "A", "folder": "Clients", "visibility": "owner",
         "share_url": "https://www.loom.com/share/v1"},
    ]
    tree = [{"id": "f1", "name": "Clients", "path": "Clients", "depth": 0,
             "parent_id": None, "segments": ["Clients"]}]
    inventory._report_shape(rows, tree, folders_only=False)
    out = capsys.readouterr().out
    header = out.splitlines()[0]
    assert "+ main library" in header


def test_folder_label_from_segments_cannot_escape_the_destination():
    assert inventory.folder_label_from_segments(["..", "etc"]) == "untitled/etc"


def test_folder_label_from_segments_sanitizes_a_slash_in_a_name():
    """The collision this task exists for: a folder named "A/B" vs nested A > B."""
    assert inventory.folder_label_from_segments(["A/B"]) == "A_B"
    assert inventory.folder_label_from_segments(["A", "B"]) == "A/B"


def test_folder_label_from_segments_keeps_empty_names_distinct():
    assert inventory.folder_label_from_segments(["", "Videos"]) == "untitled/Videos"


class RepeatedCursorApi:
    """Always returns the same endCursor, as if the server is looping."""

    def execute(self, op, query, variables):
        return {
            "getLooms": {
                "videos": {
                    "edges": [{"node": {"id": "v1", "name": "Demo", "visibility": "owner"}}],
                    "pageInfo": {"hasNextPage": True, "endCursor": "same-cursor"},
                }
            }
        }


def test_paginate_raises_on_repeated_cursor():
    """A short archive must never be reported as success."""
    api = RepeatedCursorApi()
    try:
        inventory._paginate(api, "main-library", {})
    except folders_mod.FolderWalkError:
        pass
    else:
        raise AssertionError("expected FolderWalkError on a repeated pagination cursor")


def test_discover_does_not_collide_a_slash_named_folder_with_real_nesting():
    """End-to-end: a top-level folder literally named "A/B" vs nested "A" > "B"."""
    api = RoutingApi({
        None: [
            {"id": "f1", "name": "A/B", "visibility": "owner"},
            {"id": "f2", "name": "A", "visibility": "owner"},
        ],
        "f2": [{"id": "f3", "name": "B", "visibility": "owner"}],
    })
    rows, tree = inventory.discover(api)
    folder_labels = [r["folder"] for r in rows]
    # Uniqueness must hold across the *distinct real folders* Loom returned
    # (one entry per tree node), not across video rows -- several videos
    # legitimately share one folder, so a raw per-row list always repeats.
    real_folder_labels = [inventory.folder_label_from_segments(f["segments"]) for f in tree]
    assert len(real_folder_labels) == len(set(real_folder_labels))
    assert "A_B" in folder_labels
    assert "A/B" in folder_labels
