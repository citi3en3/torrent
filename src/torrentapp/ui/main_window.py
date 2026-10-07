"""Main application window.

Also the coordinator for adding torrents, because the flow differs by source:

* **.torrent file** -- metadata is already on disk, so parse it and show the add
  dialog immediately.
* **magnet link** -- there is no file list yet. The torrent is added paused and
  in upload-mode (so nothing is written to disk), a cancellable wait is shown,
  and only once ``metadata_received`` fires does the real dialog appear. If the
  user cancels, the placeholder torrent is removed along with its files.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import libtorrent as lt
from PySide6.QtCore import QModelIndex, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import shell_integration
from ..bridge import EngineBridge
from ..config import Config
from ..constants import APP_DISPLAY_NAME, PROGID
from ..engine import NetworkStatus, TorrentItem
from ..engine.migrate import find_backup_dir, import_candidates, scan
from ..engine.session import params_from_file, params_from_magnet, params_hash
from ..util import format_rate
from .add_dialog import AddTorrentDialog, source_from_torrent_info
from .migrate_dialog import MigrationDialog
from .theme import COLORS
from .models import (
    COLUMN_KEYS,
    DEFAULT_WIDTHS,
    ProgressDelegate,
    TorrentFilterProxy,
    TorrentTableModel,
)

log = logging.getLogger(__name__)

FILTER_GROUPS = (
    ("All", "all"),
    ("Downloading", "downloading"),
    ("Seeding", "seeding"),
    ("Paused", "paused"),
    ("Errored", "error"),
)


def describe_network(status: NetworkStatus, dht_enabled: bool) -> tuple[str, str, str]:
    """Status-bar text, a COLORS key and a tooltip for session connectivity."""
    if not status.listening:
        return (
            "⚠ Not connected",
            "error",
            "No network port could be opened, so trackers, DHT and peers are "
            "unreachable. The app keeps retrying on a new port.",
        )
    summary = f"Port {status.port} · {status.peers} peers"
    if dht_enabled:
        summary += f" · DHT {status.dht_nodes}"
        if status.dht_nodes == 0:
            return summary, "warning", "Listening, but DHT has not found any nodes yet."
    return summary, "fg_dim", f"Listening on port {status.port}."


class MainWindow(QMainWindow):
    def __init__(self, bridge: EngineBridge, config: Config, icon: QIcon) -> None:
        super().__init__()
        self._bridge = bridge
        self._engine = bridge.engine
        self._config = config
        self._icon = icon
        self._quitting = False
        self._queue: list[str] = []

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(icon)
        self.resize(1180, 680)

        self._model = TorrentTableModel(self)
        self._proxy = TorrentFilterProxy(self)
        self._proxy.setSourceModel(self._model)

        self._build_ui()
        self._connect_bridge()
        self._restore_geometry()
        QTimer.singleShot(0, self._post_start_checks)

    # ------------------------------------------------------------------ building

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.addToolBar(self._build_toolbar())

        self._banner = self._build_banner()
        layout.addWidget(self._banner)

        layout.addWidget(self._build_filter_bar())
        layout.addWidget(self._build_table(), 1)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._status_label = QLabel("Starting…")
        self.statusBar().addWidget(self._status_label)
        # Always visible: a session without a listen socket looks exactly like
        # "no seeders" otherwise, which is how a dead port once went unnoticed.
        self._net_label = QLabel("Connecting…")
        self.statusBar().addPermanentWidget(self._net_label)

    def _build_toolbar(self) -> QToolBar:
        bar = QToolBar("Main")
        bar.setMovable(False)
        actions = (
            ("Add torrent", "Ctrl+O", self.add_torrent_file),
            ("Add magnet", "Ctrl+M", self.add_magnet_prompt),
            (None, None, None),
            ("Resume", None, self.resume_selected),
            ("Pause", None, self.pause_selected),
            ("Remove", "Delete", self.remove_selected),
            (None, None, None),
            ("Open folder", None, self.open_selected_folder),
        )
        for label, shortcut, handler in actions:
            if label is None:
                bar.addSeparator()
                continue
            action = QAction(label, self)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(handler)
            bar.addAction(action)
        return bar

    def _build_banner(self) -> QFrame:
        """One-time nudge to finish the .torrent association (see shell_integration)."""
        frame = QFrame()
        frame.setObjectName("Banner")
        row = QHBoxLayout(frame)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        self._banner_text = QLabel()
        self._banner_text.setWordWrap(True)
        row.addWidget(self._banner_text, 1)

        settings_button = QPushButton("Open Default apps")
        settings_button.clicked.connect(shell_integration.open_default_apps_settings)
        row.addWidget(settings_button)

        dismiss = QPushButton("Dismiss")
        dismiss.clicked.connect(frame.hide)
        row.addWidget(dismiss)

        frame.hide()
        return frame

    def _build_filter_bar(self) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(12, 10, 12, 8)
        row.setSpacing(8)

        row.addWidget(QLabel("Show"))
        self._filter = QComboBox()
        for label, key in FILTER_GROUPS:
            self._filter.addItem(label, key)
        self._filter.currentIndexChanged.connect(
            lambda: self._proxy.set_group(self._filter.currentData())
        )
        row.addWidget(self._filter)

        row.addStretch(1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search torrents…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(280)
        self._search.textChanged.connect(self._proxy.set_search)
        row.addWidget(self._search)
        return holder

    def _build_table(self) -> QTableView:
        table = QTableView()
        table.setModel(self._proxy)
        table.setSortingEnabled(True)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        table.setItemDelegateForColumn(COLUMN_KEYS.index("progress"), ProgressDelegate(table))
        table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        table.customContextMenuRequested.connect(self._show_context_menu)
        table.doubleClicked.connect(self._on_double_click)

        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for index, key in enumerate(COLUMN_KEYS):
            table.setColumnWidth(index, DEFAULT_WIDTHS.get(key, 100))
        header.setStretchLastSection(True)

        self._table = table
        return table

    # ------------------------------------------------------------------ wiring

    def _connect_bridge(self) -> None:
        self._bridge.torrents_updated.connect(self._on_torrents_updated)
        self._bridge.metadata_received.connect(self._on_metadata_received)
        self._bridge.torrent_removed.connect(self._model.remove)
        self._bridge.torrent_finished.connect(self._on_finished)
        self._bridge.torrent_failed.connect(self._on_failed)
        self._bridge.message.connect(self._on_message)
        self._bridge.network_status.connect(self._on_network_status)

    def _restore_geometry(self) -> None:
        if self._config.window_geometry:
            try:
                self.restoreGeometry(bytes.fromhex(self._config.window_geometry))
            except ValueError:
                log.debug("stored geometry was unusable")

    def _post_start_checks(self) -> None:
        self._maybe_offer_migration()
        self._refresh_banner()

    # ------------------------------------------------------------------ events

    def _on_torrents_updated(self, items: list[TorrentItem]) -> None:
        self._model.apply_updates(items)
        self._refresh_status()

    def _on_metadata_received(self, info_hash: str, name: str) -> None:
        # The torrent is already in the list; it just gained its file list and
        # will start downloading by itself. Say so and get out of the way.
        self.statusBar().showMessage(f"Got details for {name} — starting", 5000)
        log.info("metadata arrived for %s", name)

    def _on_finished(self, info_hash: str, name: str, save_path: str) -> None:
        if self._config.notify_on_finish:
            self.statusBar().showMessage(f"Finished: {name}", 8000)
        log.info("finished %s -> %s", name, save_path)

    def _on_failed(self, info_hash: str, message: str) -> None:
        self.statusBar().showMessage(f"Error: {message}", 10000)
        log.warning("torrent error (%s): %s", info_hash, message)

    def _on_network_status(self, status: NetworkStatus) -> None:
        text, level, tooltip = describe_network(status, self._config.enable_dht)
        self._net_label.setText(text)
        self._net_label.setToolTip(tooltip)
        self._net_label.setStyleSheet(f"color: {COLORS[level]};")

    def _on_message(self, level: str, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        if level == "error":
            log.error(message)

    def _refresh_status(self) -> None:
        items = self._model.all_items()
        down = sum(i.download_rate for i in items)
        up = sum(i.upload_rate for i in items)
        active = sum(1 for i in items if i.is_active and not i.finished)
        self._status_label.setText(
            f"{len(items)} torrent(s) · {active} active   "
            f"↓ {format_rate(down)}   ↑ {format_rate(up)}"
        )

    # ------------------------------------------------------------------ adding

    def open_source(self, source: str) -> None:
        """Entry point for a .torrent path or magnet URI from any origin."""
        source = (source or "").strip().strip('"')
        if not source:
            return
        if source.lower().startswith("magnet:"):
            self._add_magnet(source)
        else:
            self._add_file(source)

    def open_sources(self, sources: list[str]) -> None:
        """Several at once (e.g. multi-select in Explorer): handle one at a time."""
        self._queue.extend(s for s in sources if s)
        self._drain_queue()

    def _drain_queue(self) -> None:
        while self._queue:
            self.open_source(self._queue.pop(0))

    def _add_file(self, path: str) -> None:
        target = Path(path)
        if not target.is_file():
            QMessageBox.warning(self, APP_DISPLAY_NAME, f"File not found:\n{path}")
            return
        try:
            info = lt.torrent_info(str(target))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self, APP_DISPLAY_NAME, f"This does not look like a valid .torrent file:\n{exc}"
            )
            return

        source = source_from_torrent_info(info)
        if not self._config.show_add_dialog:
            self._engine.add(
                params_from_file(
                    str(target),
                    self._config.default_save_path(),
                    paused=self._config.start_paused,
                )
            )
            return

        dialog = AddTorrentDialog(source, self._config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        decision = dialog.decision()
        Path(decision.save_path).mkdir(parents=True, exist_ok=True)
        self._engine.add(
            params_from_file(
                str(target),
                decision.save_path,
                file_priorities=decision.file_priorities or None,
                paused=decision.paused,
            )
        )
        self._config.remember(decision.save_path)

    def _add_magnet(self, uri: str) -> None:
        try:
            probe = lt.parse_magnet_uri(uri)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, APP_DISPLAY_NAME, f"Not a usable magnet link:\n{exc}")
            return

        info_hash = params_hash(probe)
        if info_hash and info_hash in self._engine:
            QMessageBox.information(
                self, APP_DISPLAY_NAME, "That torrent is already in the list."
            )
            return

        display_name = str(getattr(probe, "name", "") or "") or "magnet link"

        # No dialog and no waiting. A magnet carries no file list, so anything
        # that wanted to show one first had to block on peers -- which meant
        # staring at a countdown before the download even began. Instead the
        # torrent joins the list immediately, shows "Fetching metadata", and
        # starts on its own the moment the details arrive.
        save_path = self._config.default_save_path()
        Path(save_path).mkdir(parents=True, exist_ok=True)
        self._engine.add(
            params_from_magnet(uri, save_path, paused=self._config.start_paused)
        )
        self.statusBar().showMessage(f"Added {display_name} — fetching details…", 6000)

    # ------------------------------------------------------------------ actions

    def add_torrent_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add torrent files", self._config.download_root, "Torrent files (*.torrent)"
        )
        if paths:
            self.open_sources(list(paths))

    def add_magnet_prompt(self) -> None:
        uri, ok = QInputDialog.getText(self, "Add magnet link", "Magnet URI:")
        if ok and uri.strip():
            self.open_source(uri.strip())

    def selected_hashes(self) -> list[str]:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        hashes = []
        for index in rows:
            item = self._model.item_at(self._proxy.mapToSource(index).row())
            if item is not None:
                hashes.append(item.info_hash)
        return hashes

    def pause_selected(self) -> None:
        for info_hash in self.selected_hashes():
            self._engine.pause(info_hash)

    def resume_selected(self) -> None:
        for info_hash in self.selected_hashes():
            self._engine.resume(info_hash)

    def remove_selected(self) -> None:
        hashes = self.selected_hashes()
        if not hashes:
            return

        names = [self._model.item(h).name for h in hashes if self._model.item(h)]
        preview = "\n".join(f"  • {n}" for n in names[:6])
        if len(names) > 6:
            preview += f"\n  … and {len(names) - 6} more"

        box = QMessageBox(self)
        box.setWindowTitle("Remove torrents")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Remove {len(hashes)} torrent(s)?")
        box.setInformativeText(preview)
        delete_files = QCheckBox("Also delete the downloaded files from disk")
        box.setCheckBox(delete_files)
        box.setStandardButtons(QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        for info_hash in hashes:
            self._engine.remove(info_hash, delete_files=delete_files.isChecked())
            self._model.remove(info_hash)

    def open_selected_folder(self) -> None:
        for info_hash in self.selected_hashes()[:1]:
            item = self._model.item(info_hash)
            if item and item.save_path:
                self._open_in_explorer(Path(item.save_path) / item.name)

    @staticmethod
    def _open_in_explorer(target: Path) -> None:
        try:
            if target.exists():
                subprocess.Popen(["explorer", "/select,", os.path.normpath(str(target))])
            elif target.parent.exists():
                os.startfile(str(target.parent))  # noqa: S606
        except OSError as exc:
            log.warning("could not open explorer: %s", exc)

    def force_recheck_selected(self) -> None:
        for info_hash in self.selected_hashes():
            self._engine.force_recheck(info_hash)

    def copy_magnet_selected(self) -> None:
        parts = []
        for info_hash in self.selected_hashes():
            item = self._model.item(info_hash)
            name = f"&dn={item.name}" if item else ""
            parts.append(f"magnet:?xt=urn:btih:{info_hash}{name}")
        if parts:
            QApplication.clipboard().setText("\n".join(parts))
            self.statusBar().showMessage("Magnet link copied", 4000)

    # ------------------------------------------------------------------ menus

    def _show_context_menu(self, position) -> None:
        if not self.selected_hashes():
            return
        menu = QMenu(self)
        for label, handler in (
            ("Resume", self.resume_selected),
            ("Pause", self.pause_selected),
            (None, None),
            ("Open containing folder", self.open_selected_folder),
            ("Copy magnet link", self.copy_magnet_selected),
            ("Force recheck", self.force_recheck_selected),
            (None, None),
            ("Remove…", self.remove_selected),
        ):
            if label is None:
                menu.addSeparator()
                continue
            action = QAction(label, menu)
            action.triggered.connect(handler)
            menu.addAction(action)
        menu.exec(self._table.viewport().mapToGlobal(position))

    def _on_double_click(self, index: QModelIndex) -> None:
        self.open_selected_folder()

    # ------------------------------------------------------------------ migration

    def _maybe_offer_migration(self) -> None:
        if self._config.migration_done:
            return
        backup = find_backup_dir()
        if backup is None:
            self._config.migration_done = True
            self._config.save()
            return

        candidates = scan(backup)
        if not candidates:
            self._config.migration_done = True
            self._config.save()
            return

        dialog = MigrationDialog(candidates, backup, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return  # ask again next launch

        chosen = dialog.selected()
        imported, failures = import_candidates(chosen, self._engine.store)
        # Paused on purpose: qBittorrent may still be running these, and two
        # clients writing the same incomplete files corrupts both copies.
        self._engine.restore_torrents(force_paused=True)

        self._config.migration_done = True
        self._config.save()

        text = (
            f"Imported {imported} torrent(s) from qBittorrent.\n\n"
            "They have been added paused. Close qBittorrent first, then select "
            "them here and press Resume — otherwise both programs would write to "
            "the same files."
        )
        if failures:
            text += f"\n\n{len(failures)} could not be imported:\n" + "\n".join(failures[:5])
        QMessageBox.information(self, "Import complete", text)

    # ------------------------------------------------------------------ banner

    def _refresh_banner(self) -> None:
        if shell_integration.is_default_for_torrent():
            self._banner.hide()
            return
        owner = shell_integration.torrent_default_progid()
        if owner and owner != PROGID:
            detail = (
                f"<b>.torrent</b> files currently open with <code>{owner}</code>. "
                f"{APP_DISPLAY_NAME} is registered and available under "
                "<i>Open with</i> — Windows only lets you switch the default by hand."
            )
        else:
            detail = (
                f"<b>.torrent</b> files are not yet associated with {APP_DISPLAY_NAME}. "
                "Windows only lets you set the default by hand."
            )
        magnet_state = (
            "Magnet links are already handled."
            if shell_integration.is_magnet_handler()
            else "Magnet links are not yet handled."
        )
        self._banner_text.setText(f"{detail}<br><span>{magnet_state}</span>")
        self._banner.show()

    # ------------------------------------------------------------------ closing

    def prepare_quit(self) -> None:
        self._quitting = True

    def closeEvent(self, event: QCloseEvent) -> None:
        self._config.window_geometry = bytes(self.saveGeometry()).hex()
        try:
            self._config.save()
        except OSError as exc:
            log.warning("could not save settings: %s", exc)

        if self._quitting or not self._config.close_to_tray:
            event.accept()
            return
        event.ignore()
        self.hide()
        self.statusBar().clearMessage()
