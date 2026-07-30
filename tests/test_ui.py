"""Widget behaviour, exercised headless via Qt's offscreen platform.

These focus on the logic embedded in the widgets -- file-priority mapping,
selection arithmetic, filtering and row bookkeeping -- rather than on pixels.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from torrentapp.engine import TorrentItem
from torrentapp.ui.add_dialog import (
    PRIORITY_NORMAL,
    PRIORITY_SKIP,
    AddTorrentDialog,
    TorrentSource,
)
from torrentapp.ui.models import TorrentFilterProxy, TorrentTableModel

pytestmark = pytest.mark.usefixtures("qapp")


def make_item(info_hash: str, name: str = "Test", **overrides) -> TorrentItem:
    defaults = dict(
        info_hash=info_hash,
        name=name,
        state="downloading",
        progress=0.5,
        download_rate=1000,
        upload_rate=100,
        total_wanted=2000,
        total_wanted_done=1000,
        num_seeds=3,
        num_peers=7,
        list_seeds=10,
        list_peers=20,
        save_path=r"C:\dl",
        paused=False,
        finished=False,
        seeding=False,
        has_metadata=True,
        all_time_download=1000,
        all_time_upload=500,
        added_time=1700000000,
        completed_time=0,
        queue_position=0,
        error="",
    )
    defaults.update(overrides)
    return TorrentItem(**defaults)


# ----------------------------------------------------------------------- model


def test_model_inserts_and_updates_without_duplicating_rows():
    model = TorrentTableModel()
    model.apply_updates([make_item("a"), make_item("b")])
    assert model.rowCount() == 2

    model.apply_updates([make_item("a", progress=0.9)])
    assert model.rowCount() == 2, "an update must not add a row"
    assert model.item("a").progress == pytest.approx(0.9)


def test_model_remove_keeps_row_map_consistent():
    model = TorrentTableModel()
    model.apply_updates([make_item(h) for h in ("a", "b", "c")])
    model.remove("a")

    assert model.rowCount() == 2
    # 'c' shifted from row 2 to row 1; updating it must still hit the right row.
    model.apply_updates([make_item("c", name="renamed")])
    assert model.rowCount() == 2
    assert model.item_at(1).name == "renamed"


def test_model_ignores_items_without_a_hash():
    model = TorrentTableModel()
    model.apply_updates([make_item("")])
    assert model.rowCount() == 0


def test_model_clear():
    model = TorrentTableModel()
    model.apply_updates([make_item("a")])
    model.clear()
    assert model.rowCount() == 0
    assert model.item("a") is None


def test_remove_unknown_hash_is_harmless():
    model = TorrentTableModel()
    model.remove("nope")
    assert model.rowCount() == 0


# ----------------------------------------------------------------------- filter


def test_filter_by_group():
    model = TorrentTableModel()
    model.apply_updates(
        [
            make_item("a", state="downloading"),
            make_item("b", state="seeding"),
            make_item("c", state="paused", paused=True),
        ]
    )
    proxy = TorrentFilterProxy()
    proxy.setSourceModel(model)

    proxy.set_group("all")
    assert proxy.rowCount() == 3
    proxy.set_group("downloading")
    assert proxy.rowCount() == 1
    proxy.set_group("paused")
    assert proxy.rowCount() == 1
    proxy.set_group("error")
    assert proxy.rowCount() == 0


def test_filter_by_search_is_case_insensitive():
    model = TorrentTableModel()
    model.apply_updates([make_item("a", name="Ubuntu ISO"), make_item("b", name="Debian")])
    proxy = TorrentFilterProxy()
    proxy.setSourceModel(model)

    proxy.set_search("ubuntu")
    assert proxy.rowCount() == 1
    proxy.set_search("")
    assert proxy.rowCount() == 2


# -------------------------------------------------------------------- add dialog


def make_source() -> TorrentSource:
    files = [
        ("Show.S01/Show.S01E01.mkv", 1_000_000_000),
        ("Show.S01/Show.S01E02.mkv", 1_000_000_000),
        ("Show.S01/extras/behind.mkv", 50_000_000),
        ("Show.S01/readme.nfo", 1_000),
    ]
    return TorrentSource(
        name="Show.S01.COMPLETE.1080p.WEB-DL",
        files=files,
        total_size=sum(size for _, size in files),
    )


def test_add_dialog_prefills_the_default_save_path(config):
    dialog = AddTorrentDialog(make_source(), config)
    assert dialog._path.text() == config.download_root


def test_add_dialog_prefills_the_last_used_folder(config, tmp_path):
    config.remember(str(tmp_path / "elsewhere"))
    dialog = AddTorrentDialog(make_source(), config)
    assert dialog._path.text() == str(tmp_path / "elsewhere")


def test_add_dialog_selects_everything_by_default(config):
    dialog = AddTorrentDialog(make_source(), config)
    decision = dialog.decision()
    assert decision.file_priorities == [PRIORITY_NORMAL] * 4
    assert decision.selected_size == make_source().total_size


def test_add_dialog_unchecked_files_become_skip_priority(config):
    dialog = AddTorrentDialog(make_source(), config)
    # Deselect the two smallest files (extras + nfo).
    for leaf in dialog._leaves:
        if leaf.text(0) in ("behind.mkv", "readme.nfo"):
            leaf.setCheckState(0, Qt.CheckState.Unchecked)

    decision = dialog.decision()
    assert decision.file_priorities == [
        PRIORITY_NORMAL,
        PRIORITY_NORMAL,
        PRIORITY_SKIP,
        PRIORITY_SKIP,
    ]
    assert decision.selected_size == 2_000_000_000


def test_add_dialog_select_none_then_all(config):
    dialog = AddTorrentDialog(make_source(), config)
    dialog._set_all(Qt.CheckState.Unchecked)
    assert dialog.selected_size() == 0
    assert dialog.decision().file_priorities == [PRIORITY_SKIP] * 4

    dialog._set_all(Qt.CheckState.Checked)
    assert dialog.decision().file_priorities == [PRIORITY_NORMAL] * 4


def test_add_dialog_invert_selection(config):
    dialog = AddTorrentDialog(make_source(), config)
    for leaf in dialog._leaves:
        if leaf.text(0) == "readme.nfo":
            leaf.setCheckState(0, Qt.CheckState.Unchecked)

    dialog._invert_selection()
    priorities = dialog.decision().file_priorities
    assert priorities == [PRIORITY_SKIP, PRIORITY_SKIP, PRIORITY_SKIP, PRIORITY_NORMAL]


def test_add_dialog_builds_folder_hierarchy(config):
    dialog = AddTorrentDialog(make_source(), config)
    # One top-level folder ("Show.S01") holding 3 files plus an "extras" folder.
    assert dialog._tree.topLevelItemCount() == 1
    root = dialog._tree.topLevelItem(0)
    assert root.text(0) == "Show.S01"
    children = {root.child(i).text(0) for i in range(root.childCount())}
    assert children == {"Show.S01E01.mkv", "Show.S01E02.mkv", "extras", "readme.nfo"}


def test_add_dialog_blocks_ok_when_nothing_selected(config):
    dialog = AddTorrentDialog(make_source(), config)
    dialog._set_all(Qt.CheckState.Unchecked)
    assert not dialog._ok.isEnabled()

    dialog._set_all(Qt.CheckState.Checked)
    assert dialog._ok.isEnabled()


def test_add_dialog_blocks_ok_without_a_save_path(config):
    dialog = AddTorrentDialog(make_source(), config)
    dialog._path.setText("")
    assert not dialog._ok.isEnabled()


def test_add_dialog_handles_metadata_free_source(config):
    """Defensive: a .torrent with no usable file list still yields a decision."""
    source = TorrentSource(name="Nothing Listed", files=[], total_size=0)
    dialog = AddTorrentDialog(source, config)
    decision = dialog.decision()
    assert decision.file_priorities == [], "no per-file choices to express"
    assert decision.save_path
