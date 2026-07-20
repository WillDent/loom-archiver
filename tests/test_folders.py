import pytest

from loom_archiver import folders


def _page(nodes, cursor=None):
    """Shape captured from a live GetPublishedFolders response (2026-07-20)."""
    return {
        "getPublishedFolders": {
            "__typename": "GetPublishedFoldersPayload",
            "folders": {
                "edges": [{"cursor": "c", "node": n} for n in nodes],
                "pageInfo": {"endCursor": cursor, "hasNextPage": cursor is not None},
            },
        }
    }


class FakeApi:
    """Serves canned pages and records the variables it was called with."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def execute(self, op, query, variables):
        self.calls.append(variables)
        return self.pages.pop(0) if self.pages else _page([])


def test_list_folders_parses_nodes():
    api = FakeApi([_page([
        {"id": "f1", "name": "Clients", "visibility": "owner"},
        {"id": "f2", "name": "Archive", "visibility": "public"},
    ])])
    out = folders.list_folders(api)
    assert out == [
        {"id": "f1", "name": "Clients", "visibility": "owner"},
        {"id": "f2", "name": "Archive", "visibility": "public"},
    ]


def test_list_folders_sends_the_captured_api_constants():
    """FolderSource is ACTIVE -- MINE/OWNED/CREATED_BY_ME are all rejected by Loom."""
    api = FakeApi([_page([])])
    folders.list_folders(api)
    v = api.calls[0]
    assert v["source"] == "ACTIVE"
    assert v["sortType"] == "RECENT"
    assert v["sortOrder"] == "DESC"
    assert v["filters"] == [{"type": "CREATED_BY_ME"}]
    assert v["parentFolderId"] is None


def test_list_folders_follows_pagination():
    api = FakeApi([
        _page([{"id": "f1", "name": "A", "visibility": "owner"}], cursor="cur1"),
        _page([{"id": "f2", "name": "B", "visibility": "owner"}]),
    ])
    out = folders.list_folders(api)
    assert [f["id"] for f in out] == ["f1", "f2"]
    assert api.calls[1]["after"] == "cur1"


def test_list_folders_handles_an_account_with_no_folders():
    """The most common case, and one the hardcoded implementation never hit."""
    assert folders.list_folders(FakeApi([_page([])])) == []


def test_list_folders_raises_on_repeated_cursor():
    """A cursor loop must fail loudly rather than spin forever."""
    api = FakeApi([
        _page([{"id": "f1", "name": "A", "visibility": "owner"}], cursor="same"),
        _page([{"id": "f2", "name": "B", "visibility": "owner"}], cursor="same"),
    ])
    with pytest.raises(folders.FolderWalkError, match="repeated pagination cursor"):
        folders.list_folders(api)


def test_list_folders_passes_parent_id_for_children():
    api = FakeApi([_page([])])
    folders.list_folders(api, "parent-1")
    assert api.calls[0]["parentFolderId"] == "parent-1"


def test_list_folders_accepts_no_filter_for_cross_check():
    api = FakeApi([_page([])])
    folders.list_folders(api, filters=None)
    assert api.calls[0]["filters"] is None


class TreeApi:
    """Serves a folder tree keyed by parentFolderId."""

    def __init__(self, tree):
        self.tree = tree  # {parent_id_or_None: [node, ...]}
        self.calls = []

    def execute(self, op, query, variables):
        parent = variables.get("parentFolderId")
        self.calls.append(parent)
        return _page(self.tree.get(parent, []))


def _node(fid, name):
    return {"id": fid, "name": name, "visibility": "owner"}


def test_walk_returns_flat_list_for_flat_account():
    api = TreeApi({None: [_node("f1", "Clients"), _node("f2", "Archive")]})
    out = folders.walk_folders(api)
    assert [f["id"] for f in out] == ["f1", "f2"]
    assert all(f["depth"] == 0 for f in out)
    assert [f["path"] for f in out] == ["Clients", "Archive"]
    assert all(f["parent_id"] is None for f in out)


def test_walk_recurses_into_nested_folders():
    api = TreeApi({
        None: [_node("f1", "Clients")],
        "f1": [_node("f2", "Acme")],
        "f2": [_node("f3", "2026")],
    })
    out = folders.walk_folders(api)
    assert [f["id"] for f in out] == ["f1", "f2", "f3"]
    assert [f["depth"] for f in out] == [0, 1, 2]
    assert [f["path"] for f in out] == ["Clients", "Clients/Acme", "Clients/Acme/2026"]
    assert out[2]["parent_id"] == "f2"


def test_walk_orders_parents_before_children():
    """Callers create directories in order, so a child must never precede its parent."""
    api = TreeApi({None: [_node("f1", "A")], "f1": [_node("f2", "B")]})
    out = folders.walk_folders(api)
    seen = set()
    for f in out:
        assert f["parent_id"] is None or f["parent_id"] in seen
        seen.add(f["id"])


def test_walk_raises_on_cycle():
    """A folder that reappears under its own descendant must fail, not loop."""
    api = TreeApi({None: [_node("f1", "A")], "f1": [_node("f1", "A")]})
    with pytest.raises(folders.FolderWalkError, match="cycle"):
        folders.walk_folders(api)


def test_walk_raises_when_deeper_than_max_depth():
    tree = {None: [_node("f0", "d0")]}
    for i in range(12):
        tree[f"f{i}"] = [_node(f"f{i+1}", f"d{i+1}")]
    with pytest.raises(folders.FolderWalkError, match="deeper than"):
        folders.walk_folders(TreeApi(tree), max_depth=5)


def test_walk_handles_account_with_no_folders():
    assert folders.walk_folders(TreeApi({})) == []


def test_walk_path_preserves_an_empty_parent_name():
    """An empty folder name must not collapse its children to top level."""
    api = TreeApi({
        None: [_node("f1", "")],
        "f1": [_node("f2", "Videos")],
    })
    out = folders.walk_folders(api)
    assert [f["path"] for f in out] == ["", "/Videos"]


def test_walk_empty_named_folder_does_not_collide_with_a_real_folder():
    """The regression this fix exists for: two unrelated folders sharing a path."""
    api = TreeApi({
        None: [_node("f1", ""), _node("f3", "Videos")],
        "f1": [_node("f2", "Videos")],
    })
    out = folders.walk_folders(api)
    paths = [f["path"] for f in out]
    assert len(paths) == len(set(paths)), f"colliding paths: {paths}"
    assert sorted(paths) == ["", "/Videos", "Videos"]


def test_walk_carries_raw_name_segments():
    api = TreeApi({
        None: [_node("f1", "Clients")],
        "f1": [_node("f2", "Acme")],
    })
    out = folders.walk_folders(api, sleep=lambda _s: None)
    assert [f["segments"] for f in out] == [["Clients"], ["Clients", "Acme"]]


def test_walk_segments_are_not_aliased():
    """Each folder needs its own list; a shared one would corrupt siblings."""
    api = TreeApi({
        None: [_node("f1", "A")],
        "f1": [_node("f2", "B"), _node("f3", "C")],
    })
    out = folders.walk_folders(api, sleep=lambda _s: None)
    by_id = {f["id"]: f for f in out}
    assert by_id["f2"]["segments"] == ["A", "B"]
    assert by_id["f3"]["segments"] == ["A", "C"]


class FilterAwareApi:
    """Returns different top-level folders depending on whether a filter is sent."""

    def __init__(self, filtered, unfiltered):
        self.filtered = filtered
        self.unfiltered = unfiltered

    def execute(self, op, query, variables):
        if variables.get("parentFolderId") is not None:
            return _page([])
        nodes = self.filtered if variables.get("filters") else self.unfiltered
        return _page(nodes)


def test_find_unwalked_folders_reports_the_difference():
    api = FilterAwareApi(
        filtered=[_node("f1", "Mine")],
        unfiltered=[_node("f1", "Mine"), _node("f9", "Team Space")],
    )
    walked = folders.walk_folders(api)
    missed = folders.find_unwalked_folders(api, walked)
    assert [f["id"] for f in missed] == ["f9"]


def test_find_unwalked_folders_empty_when_lists_agree():
    api = FilterAwareApi(filtered=[_node("f1", "Mine")], unfiltered=[_node("f1", "Mine")])
    walked = folders.walk_folders(api)
    assert folders.find_unwalked_folders(api, walked) == []
