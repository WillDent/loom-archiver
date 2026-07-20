"""Replay of real GetPublishedFolders responses captured 2026-07-20.

Loom's GraphQL introspection is disabled, so these shapes cannot be regenerated
from a schema -- the capture is the only record of what the API actually
returns. The synthetic tests in test_folders.py encode our assumptions about
the response; this one encodes Loom's behaviour.
"""
import json
from pathlib import Path

from loom_archiver import folders, inventory

FIXTURE = Path(__file__).parent / "fixtures" / "get_published_folders_live.json"


class ReplayApi:
    """Serves the captured response for whatever parentFolderId is asked for."""

    def __init__(self, captured):
        self.captured = captured
        self.asked = []

    def execute(self, op, query, variables):
        parent = variables.get("parentFolderId")
        self.asked.append(parent)
        key = "null" if parent is None else parent
        return self.captured.get(key, {
            "getPublishedFolders": {
                "folders": {"edges": [], "pageInfo": {"endCursor": None,
                                                      "hasNextPage": False}}
            }
        })


def _api():
    return ReplayApi(json.loads(FIXTURE.read_text()))


def test_walk_finds_the_real_nested_folder():
    """The recursive path, against real data -- this is what was never exercised."""
    tree = folders.walk_folders(_api(), sleep=lambda _: None)
    paths = {f["path"] for f in tree}
    assert "MD Turbines" in paths
    assert "MD Turbines/sub-folder-md" in paths


def test_nested_folder_reports_depth_and_parent():
    tree = folders.walk_folders(_api(), sleep=lambda _: None)
    child = next(f for f in tree if f["path"] == "MD Turbines/sub-folder-md")
    parent = next(f for f in tree if f["path"] == "MD Turbines")
    assert child["depth"] == 1
    assert parent["depth"] == 0
    assert child["parent_id"] == parent["id"]
    assert child["segments"] == ["MD Turbines", "sub-folder-md"]


def test_walk_recurses_into_every_discovered_folder():
    """Every folder found must itself be queried for children, or nesting is missed."""
    api = _api()
    tree = folders.walk_folders(api, sleep=lambda _: None)
    for folder in tree:
        assert folder["id"] in api.asked


def test_real_response_parses_into_the_expected_top_level_set():
    tree = folders.walk_folders(_api(), sleep=lambda _: None)
    assert {f["name"] for f in tree if f["depth"] == 0} == {
        "MD Turbines", "Artivo-Mkt", "AIDemos",
    }


def test_nested_video_is_filed_under_the_child_folder():
    """A video in a nested folder must not be filed under its parent.

    ledger.merge_inventory is first-wins by video id, and walk_folders yields
    parents before children -- so if the parent's listing ever includes the
    child's videos, the parent wins and the video is silently misplaced.
    """
    tree = folders.walk_folders(_api(), sleep=lambda _: None)
    child = next(f for f in tree if f["path"] == "MD Turbines/sub-folder-md")
    assert inventory.folder_label_from_segments(child["segments"]) == "MD Turbines/sub-folder-md"


def test_folder_label_maps_to_a_nested_directory_not_a_literal_name(tmp_path):
    from loom_archiver.config import Config
    from loom_archiver.run import _dest_dir

    tree = folders.walk_folders(_api(), sleep=lambda _: None)
    child = next(f for f in tree if f["path"] == "MD Turbines/sub-folder-md")
    label = inventory.folder_label_from_segments(child["segments"])
    cfg = Config.default(tmp_path)
    dest = _dest_dir(cfg, label)
    assert dest == tmp_path / "MD Turbines" / "sub-folder-md"
    assert dest.parent.name == "MD Turbines"
