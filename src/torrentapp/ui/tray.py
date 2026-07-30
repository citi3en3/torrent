"""System tray icon and its menu."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..constants import APP_DISPLAY_NAME
from ..util import format_rate


class TrayIcon(QObject):
    show_requested = Signal()
    add_torrent_requested = Signal()
    add_magnet_requested = Signal()
    pause_all_requested = Signal()
    resume_all_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip(APP_DISPLAY_NAME)
        self._tray.activated.connect(self._on_activated)
        self._tray.setContextMenu(self._build_menu())

    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def notify(self, title: str, message: str) -> None:
        if self._tray.isVisible():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)

    def set_rates(self, download: int, upload: int, active: int) -> None:
        self._tray.setToolTip(
            f"{APP_DISPLAY_NAME}\n"
            f"{active} active   ↓ {format_rate(download)}   ↑ {format_rate(upload)}"
        )

    # ------------------------------------------------------------------ internals

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        entries = (
            (f"Open {APP_DISPLAY_NAME}", self.show_requested),
            None,
            ("Add torrent file…", self.add_torrent_requested),
            ("Add magnet link…", self.add_magnet_requested),
            None,
            ("Pause all", self.pause_all_requested),
            ("Resume all", self.resume_all_requested),
            None,
            ("Quit", self.quit_requested),
        )
        for entry in entries:
            if entry is None:
                menu.addSeparator()
                continue
            label, signal = entry
            action = QAction(label, menu)
            action.triggered.connect(signal.emit)
            menu.addAction(action)
        return menu

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()
