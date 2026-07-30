"""First-run import of an existing qBittorrent library.

Shows exactly what was found and what will happen before anything is copied.
The import is read-only with respect to qBittorrent's own files, which is worth
saying out loud in the UI -- it makes the step safe to try.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..engine.migrate import MigrationCandidate
from ..util import format_bytes


class MigrationDialog(QDialog):
    def __init__(self, candidates: list[MigrationCandidate], source_dir, parent=None) -> None:
        super().__init__(parent)
        self._candidates = candidates
        self.setWindowTitle("Import from qBittorrent")
        self.setModal(True)
        self.resize(820, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(18, 18, 18, 14)

        heading = QLabel(f"Found {len(candidates)} torrent(s) in qBittorrent")
        heading.setObjectName("Heading")
        layout.addWidget(heading)

        explain = QLabel(
            f"Read from <code>{source_dir}</code>.<br>"
            "Progress, save paths and file selections are preserved — nothing needs "
            "re-downloading or re-checking. qBittorrent's own files are only read, "
            "never modified, so this is safe to run before uninstalling it."
        )
        explain.setObjectName("Subtle")
        explain.setWordWrap(True)
        explain.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(explain)

        self._tree = self._build_tree()
        layout.addWidget(self._tree, 1)

        controls = QHBoxLayout()
        for label, state in (("Select all", True), ("Select none", False)):
            button = QPushButton(label)
            button.clicked.connect(lambda _=False, s=state: self._set_all(s))
            controls.addWidget(button)
        controls.addStretch(1)
        self._summary = QLabel()
        controls.addWidget(self._summary)
        layout.addLayout(controls)

        notice = QLabel(
            "Imported torrents start <b>paused</b>. Close qBittorrent before "
            "resuming them — two clients writing the same unfinished files "
            "would corrupt both copies."
        )
        notice.setObjectName("Reason")
        notice.setWordWrap(True)
        notice.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(notice)

        missing = [c for c in candidates if not c.has_metadata]
        if missing:
            warning = QLabel(
                f"{len(missing)} torrent(s) have no .torrent file alongside their resume "
                "data. They will still import, but their metadata gets re-fetched from "
                "peers on first start."
            )
            warning.setObjectName("Subtle")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText("Import")
        self._ok.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_summary()

    # ------------------------------------------------------------------ building

    def _build_tree(self) -> QTreeWidget:
        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["Torrent", "Size", "Save path"])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        tree.setUniformRowHeights(True)

        header = tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        for candidate in self._candidates:
            row = QTreeWidgetItem(tree)
            row.setText(0, candidate.name)
            row.setText(1, format_bytes(candidate.total_size) if candidate.total_size else "—")
            row.setTextAlignment(
                1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            row.setText(2, candidate.save_path or "—")
            row.setToolTip(0, f"{candidate.name}\n{candidate.info_hash}")
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(0, Qt.CheckState.Checked)
            self._tree_item_candidate(row, candidate)

        tree.itemChanged.connect(lambda *_: self._refresh_summary())
        return tree

    @staticmethod
    def _tree_item_candidate(item: QTreeWidgetItem, candidate: MigrationCandidate) -> None:
        item.setData(0, Qt.ItemDataRole.UserRole, candidate.info_hash)

    # ------------------------------------------------------------------ behaviour

    def _rows(self):
        for index in range(self._tree.topLevelItemCount()):
            yield self._tree.topLevelItem(index)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in self._rows():
            row.setCheckState(0, state)

    def _refresh_summary(self) -> None:
        chosen = self.selected()
        total = sum(c.total_size for c in chosen)
        self._summary.setText(f"{len(chosen)} selected · {format_bytes(total)}")
        self._ok.setEnabled(bool(chosen))

    def selected(self) -> list[MigrationCandidate]:
        wanted = {
            row.data(0, Qt.ItemDataRole.UserRole)
            for row in self._rows()
            if row.checkState(0) == Qt.CheckState.Checked
        }
        return [c for c in self._candidates if c.info_hash in wanted]
