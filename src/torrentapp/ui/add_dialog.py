"""The dialog shown before a .torrent starts downloading.

File tree with tri-state checkboxes, save folder, and a free-space check against
the chosen drive.

Only .torrent files get this. A magnet has no file list until peers supply one,
so showing a dialog for one meant blocking on the network first -- the user
watched a countdown instead of a download. Magnets now go straight into the list
and start by themselves; see MainWindow._add_magnet.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from ..util import format_bytes, free_space

# libtorrent file priorities: 0 skips the file entirely, 4 is the normal default.
PRIORITY_SKIP = 0
PRIORITY_NORMAL = 4

FILE_INDEX_ROLE = Qt.ItemDataRole.UserRole + 1
FILE_SIZE_ROLE = Qt.ItemDataRole.UserRole + 2


@dataclass
class TorrentSource:
    """Everything the dialog needs to describe one incoming torrent."""

    name: str
    files: list[tuple[str, int]] = field(default_factory=list)  # (path, size)
    total_size: int = 0

    @property
    def has_files(self) -> bool:
        return bool(self.files)


@dataclass
class AddDecision:
    """What the user chose."""

    save_path: str
    file_priorities: list[int]
    paused: bool
    selected_size: int


def source_from_torrent_info(info) -> TorrentSource:
    """Build a TorrentSource from a libtorrent torrent_info."""
    storage = info.files()
    files: list[tuple[str, int]] = []
    for index in range(storage.num_files()):
        files.append((storage.file_path(index), storage.file_size(index)))
    return TorrentSource(
        name=str(info.name()),
        files=files,
        total_size=int(info.total_size()),
    )


# ------------------------------------------------------------------------ add dialog


class AddTorrentDialog(QDialog):
    def __init__(self, source: TorrentSource, config: Config, parent=None) -> None:
        super().__init__(parent)
        self._source = source
        self._config = config
        self._leaves: list[QTreeWidgetItem] = []
        self._updating = False

        self.setWindowTitle("Add torrent")
        self.setModal(True)
        self.resize(760, 620)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 14)

        # --- heading ---------------------------------------------------------
        title = QLabel(source.name or "Torrent")
        title.setObjectName("Heading")
        title.setWordWrap(True)
        layout.addWidget(title)

        # --- destination -----------------------------------------------------
        layout.addWidget(self._build_destination(config.default_save_path()))

        # --- files -----------------------------------------------------------
        if source.has_files:
            layout.addWidget(QLabel("Files to download"))
            layout.addWidget(self._build_tree(), 1)
            layout.addLayout(self._build_selection_controls())
        else:
            placeholder = QLabel(
                "The file list is not available yet. Everything will be downloaded; "
                "you can deselect files later from the torrent's details."
            )
            placeholder.setObjectName("Subtle")
            placeholder.setWordWrap(True)
            layout.addWidget(placeholder, 1)

        # --- footer ----------------------------------------------------------
        self._summary = QLabel()
        layout.addWidget(self._summary)

        self._warning = QLabel()
        self._warning.setObjectName("Warning")
        self._warning.setWordWrap(True)
        self._warning.hide()
        layout.addWidget(self._warning)

        self._paused = QCheckBox("Start paused")
        self._paused.setChecked(config.start_paused)
        layout.addWidget(self._paused)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText("Download")
        self._ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_summary()

    # ------------------------------------------------------------------ building

    def _build_destination(self, suggested_path: str) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Banner")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(9)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(QLabel("Save to"))
        self._path = QLineEdit(suggested_path)
        self._path.textChanged.connect(self._refresh_summary)
        path_row.addWidget(self._path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._on_browse)
        path_row.addWidget(browse)
        outer.addLayout(path_row)
        return frame

    def _build_tree(self) -> QTreeWidget:
        self._tree = QTreeWidget()
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["File", "Size"])
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setUniformRowHeights(True)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        self._populate_tree()
        self._tree.itemChanged.connect(self._on_item_changed)
        self._tree.expandToDepth(0)
        return self._tree

    def _populate_tree(self) -> None:
        """Turn flat 'a/b/c.mkv' paths into a real folder hierarchy."""
        folders: dict[str, QTreeWidgetItem] = {}

        def folder_for(parts: tuple[str, ...]) -> QTreeWidgetItem | None:
            if not parts:
                return None
            key = "/".join(parts)
            if key in folders:
                return folders[key]
            parent = folder_for(parts[:-1])
            node = QTreeWidgetItem(parent) if parent else QTreeWidgetItem(self._tree)
            node.setText(0, parts[-1])
            node.setFlags(
                node.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            node.setCheckState(0, Qt.CheckState.Checked)
            folders[key] = node
            return node

        for index, (path, size) in enumerate(self._source.files):
            parts = tuple(p for p in Path(path).parts if p not in ("/", "\\"))
            parent = folder_for(parts[:-1])
            leaf = QTreeWidgetItem(parent) if parent else QTreeWidgetItem(self._tree)
            leaf.setText(0, parts[-1] if parts else path)
            leaf.setText(1, format_bytes(size))
            leaf.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            leaf.setCheckState(0, Qt.CheckState.Checked)
            leaf.setData(0, FILE_INDEX_ROLE, index)
            leaf.setData(0, FILE_SIZE_ROLE, size)
            self._leaves.append(leaf)

    def _build_selection_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        for label, handler in (
            ("Select all", lambda: self._set_all(Qt.CheckState.Checked)),
            ("Select none", lambda: self._set_all(Qt.CheckState.Unchecked)),
            ("Invert", self._invert_selection),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            row.addWidget(button)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------------ behaviour

    def _on_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose save folder", self._path.text() or self._config.download_root
        )
        if chosen:
            self._path.setText(os.path.normpath(chosen))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        # Propagate a folder's new state down to its children; Qt's auto-tristate
        # handles the upward direction for us.
        self._updating = True
        try:
            if item.childCount():
                state = item.checkState(0)
                if state != Qt.CheckState.PartiallyChecked:
                    self._apply_to_children(item, state)
        finally:
            self._updating = False
        self._refresh_summary()

    def _apply_to_children(self, node: QTreeWidgetItem, state: Qt.CheckState) -> None:
        for i in range(node.childCount()):
            child = node.child(i)
            child.setCheckState(0, state)
            self._apply_to_children(child, state)

    def _set_all(self, state: Qt.CheckState) -> None:
        self._updating = True
        try:
            for i in range(self._tree.topLevelItemCount()):
                node = self._tree.topLevelItem(i)
                node.setCheckState(0, state)
                self._apply_to_children(node, state)
        finally:
            self._updating = False
        self._refresh_summary()

    def _invert_selection(self) -> None:
        self._updating = True
        try:
            for leaf in self._leaves:
                current = leaf.checkState(0)
                leaf.setCheckState(
                    0,
                    Qt.CheckState.Unchecked
                    if current == Qt.CheckState.Checked
                    else Qt.CheckState.Checked,
                )
        finally:
            self._updating = False
        self._refresh_summary()

    # ------------------------------------------------------------------ summary

    def selected_size(self) -> int:
        if not self._leaves:
            return self._source.total_size
        return sum(
            int(leaf.data(0, FILE_SIZE_ROLE) or 0)
            for leaf in self._leaves
            if leaf.checkState(0) == Qt.CheckState.Checked
        )

    def _refresh_summary(self) -> None:
        chosen = self.selected_size()
        total = self._source.total_size or chosen
        count = sum(1 for leaf in self._leaves if leaf.checkState(0) == Qt.CheckState.Checked)

        if self._leaves:
            text = (
                f"Selected {count} of {len(self._leaves)} files — "
                f"{format_bytes(chosen)} of {format_bytes(total)}"
            )
        else:
            text = f"Total size: {format_bytes(total)}"

        available = free_space(self._path.text() or self._config.download_root)
        if available is not None:
            text += f"   ·   {format_bytes(available)} free on target drive"
        self._summary.setText(text)

        problems = []
        if not self._path.text().strip():
            problems.append("Choose a save folder.")
        elif available is not None and chosen > available:
            problems.append(
                f"Not enough space: needs {format_bytes(chosen)}, "
                f"{format_bytes(available)} free."
            )
        if self._leaves and count == 0:
            problems.append("Select at least one file.")

        if problems:
            self._warning.setText("  ".join(problems))
            self._warning.show()
        else:
            self._warning.hide()

        # Space is a warning, not a veto -- the user may be about to free some.
        self._ok.setEnabled(bool(self._path.text().strip()) and (not self._leaves or count > 0))

    # ------------------------------------------------------------------ result

    def decision(self) -> AddDecision:
        priorities: list[int] = []
        if self._leaves:
            by_index = {
                int(leaf.data(0, FILE_INDEX_ROLE)): leaf.checkState(0) == Qt.CheckState.Checked
                for leaf in self._leaves
            }
            priorities = [
                PRIORITY_NORMAL if by_index.get(i, True) else PRIORITY_SKIP
                for i in range(len(self._source.files))
            ]
        return AddDecision(
            save_path=os.path.normpath(self._path.text().strip()),
            file_priorities=priorities,
            paused=self._paused.isChecked(),
            selected_size=self.selected_size(),
        )
