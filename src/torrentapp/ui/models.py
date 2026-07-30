"""Table model and delegates for the torrent list."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QRect,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ..engine import TorrentItem
from ..util import format_bytes, format_eta, format_rate
from .theme import COLORS, STATE_COLORS, STATE_LABELS

# Sorting must use the underlying number, not the formatted string, or "9 MiB"
# sorts above "10 GiB". Every column therefore also answers to SORT_ROLE.
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
HASH_ROLE = Qt.ItemDataRole.UserRole + 2

COLUMNS = (
    ("Name", "name"),
    ("Size", "size"),
    ("Progress", "progress"),
    ("Status", "status"),
    ("Seeds", "seeds"),
    ("Peers", "peers"),
    ("Down", "down"),
    ("Up", "up"),
    ("ETA", "eta"),
    ("Ratio", "ratio"),
    ("Added", "added"),
)

COLUMN_KEYS = [key for _, key in COLUMNS]
DEFAULT_WIDTHS = {
    "name": 340,
    "size": 90,
    "progress": 130,
    "status": 130,
    "seeds": 60,
    "peers": 60,
    "down": 95,
    "up": 95,
    "eta": 95,
    "ratio": 65,
    "added": 130,
}

NUMERIC_KEYS = {"size", "progress", "seeds", "peers", "down", "up", "eta", "ratio", "added"}


class TorrentTableModel(QAbstractTableModel):
    """Holds the current TorrentItem snapshots, keyed by info-hash."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._order: list[str] = []
        self._items: dict[str, TorrentItem] = {}
        self._rows: dict[str, int] = {}  # info_hash -> row, kept in sync with _order

    # ------------------------------------------------------------------ qt api

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._order)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][0]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self.item_at(index.row())
        if item is None:
            return None
        key = COLUMN_KEYS[index.column()]

        if role == HASH_ROLE:
            return item.info_hash
        if role == SORT_ROLE:
            return self._sort_value(item, key)
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(item, key)
        if role == Qt.ItemDataRole.ForegroundRole and key == "status":
            return QColor(STATE_COLORS.get(item.state, COLORS["fg"]))
        if role == Qt.ItemDataRole.TextAlignmentRole and key in NUMERIC_KEYS:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(item)
        return None

    # ------------------------------------------------------------------ rendering

    @staticmethod
    def _display(item: TorrentItem, key: str):
        if key == "name":
            return item.name or item.info_hash
        if key == "size":
            return format_bytes(item.total_wanted)
        if key == "progress":
            return item.progress  # drawn by ProgressDelegate
        if key == "status":
            return STATE_LABELS.get(item.state, item.state.title())
        if key == "seeds":
            return f"{item.num_seeds}" if not item.list_seeds else f"{item.num_seeds}/{item.list_seeds}"
        if key == "peers":
            return f"{item.num_peers}" if not item.list_peers else f"{item.num_peers}/{item.list_peers}"
        if key == "down":
            return format_rate(item.download_rate)
        if key == "up":
            return format_rate(item.upload_rate)
        if key == "eta":
            return "-" if item.eta is None else format_eta(item.eta)
        if key == "ratio":
            return f"{item.ratio:.2f}"
        if key == "added":
            if not item.added_time:
                return "-"
            return datetime.fromtimestamp(item.added_time).strftime("%Y-%m-%d %H:%M")
        return ""

    @staticmethod
    def _sort_value(item: TorrentItem, key: str):
        mapping = {
            "name": (item.name or "").lower(),
            "size": item.total_wanted,
            "progress": item.progress,
            "status": item.state,
            "seeds": item.num_seeds,
            "peers": item.num_peers,
            "down": item.download_rate,
            "up": item.upload_rate,
            # Unknown ETA sorts last rather than first.
            "eta": item.eta if item.eta is not None else float("inf"),
            "ratio": item.ratio,
            "added": item.added_time,
        }
        return mapping.get(key, "")

    @staticmethod
    def _tooltip(item: TorrentItem) -> str:
        lines = [
            item.name or item.info_hash,
            f"Hash: {item.info_hash}",
            f"Saved to: {item.save_path}",
            f"Done: {format_bytes(item.total_wanted_done)} of {format_bytes(item.total_wanted)}",
            f"Uploaded: {format_bytes(item.all_time_upload)}",
        ]
        if item.error:
            lines.append(f"Error: {item.error}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ mutation

    def apply_updates(self, items: list[TorrentItem]) -> None:
        """Merge a batch of snapshots, inserting rows for unseen torrents.

        Status arrives once a second for every torrent, so this runs constantly;
        row lookups go through ``self._rows`` rather than scanning ``_order``.
        """
        fresh: list[TorrentItem] = []
        touched: list[int] = []

        for item in items:
            if not item.info_hash:
                continue
            row = self._rows.get(item.info_hash)
            if row is None:
                fresh.append(item)
            else:
                self._items[item.info_hash] = item
                touched.append(row)

        if fresh:
            start = len(self._order)
            self.beginInsertRows(QModelIndex(), start, start + len(fresh) - 1)
            for item in fresh:
                self._rows[item.info_hash] = len(self._order)
                self._order.append(item.info_hash)
                self._items[item.info_hash] = item
            self.endInsertRows()

        if touched:
            # One span covering every changed row: the view repaints only what
            # is actually visible, so a coarse range is cheaper than N signals.
            self.dataChanged.emit(
                self.index(min(touched), 0),
                self.index(max(touched), len(COLUMNS) - 1),
            )

    def remove(self, info_hash: str) -> None:
        row = self._rows.get(info_hash)
        if row is None:
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        self._order.pop(row)
        self._items.pop(info_hash, None)
        # Every row after the removed one shifts up by one.
        self._rows = {h: i for i, h in enumerate(self._order)}
        self.endRemoveRows()

    def clear(self) -> None:
        self.beginResetModel()
        self._order.clear()
        self._items.clear()
        self._rows.clear()
        self.endResetModel()

    # ------------------------------------------------------------------ lookups

    def item_at(self, row: int) -> TorrentItem | None:
        if 0 <= row < len(self._order):
            return self._items.get(self._order[row])
        return None

    def item(self, info_hash: str) -> TorrentItem | None:
        return self._items.get(info_hash)

    def all_items(self) -> list[TorrentItem]:
        return [self._items[h] for h in self._order if h in self._items]


class TorrentFilterProxy(QSortFilterProxyModel):
    """Search box + status filter over the torrent table."""

    GROUPS = {
        "all": None,
        "downloading": {"downloading", "metadata", "allocating", "checking", "checking_resume"},
        "seeding": {"seeding", "finished"},
        "paused": {"paused"},
        "error": {"error"},
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._group = "all"
        self._search = ""

    def set_group(self, group: str) -> None:
        self._group = group if group in self.GROUPS else "all"
        self.invalidate()

    def set_search(self, text: str) -> None:
        self._search = (text or "").strip().lower()
        self.invalidate()

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if not isinstance(model, TorrentTableModel):
            return True
        item = model.item_at(row)
        if item is None:
            return False

        allowed = self.GROUPS.get(self._group)
        if allowed is not None and item.state not in allowed:
            return False
        if self._search and self._search not in (item.name or "").lower():
            return False
        return True


class ProgressDelegate(QStyledItemDelegate):
    """Draws the progress column as a bar rather than a bare number."""

    def paint(self, painter: QPainter, option, index) -> None:
        value = index.data(Qt.ItemDataRole.DisplayRole)
        if not isinstance(value, (int, float)):
            super().paint(painter, option, index)
            return

        fraction = max(0.0, min(1.0, float(value)))
        painter.save()

        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, QColor(COLORS["selection"]))

        rect = option.rect.adjusted(6, 6, -6, -6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["card"]))
        painter.drawRoundedRect(rect, 4, 4)

        if fraction > 0:
            filled = QRect(rect)
            filled.setWidth(max(2, int(rect.width() * fraction)))
            complete = fraction >= 0.999
            painter.setBrush(QColor(COLORS["success"] if complete else COLORS["accent_dim"]))
            painter.drawRoundedRect(filled, 4, 4)

        painter.setPen(QColor(COLORS["fg"]))
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, f"{fraction * 100:.1f}%")
        painter.restore()
