"""Application assembly and lifecycle."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import shell_integration
from .bridge import EngineBridge
from .config import Config
from .constants import (
    APP_DISPLAY_NAME,
    APP_USER_MODEL_ID,
    APP_VERSION,
    ensure_dirs,
    icon_path,
    logs_dir,
)
from .engine import ResumeStore, TorrentEngine
from .ipc import SingleInstanceServer
from .ui import theme
from .ui.main_window import MainWindow
from .ui.tray import TrayIcon

log = logging.getLogger(__name__)


def setup_logging(verbose: bool = False) -> None:
    ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir() / "torrentapp.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # A frozen --windowed build has no usable stdout to write to.
    if sys.stderr is not None:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)


def load_icon() -> QIcon:
    path = icon_path()
    if path.exists():
        return QIcon(str(path))
    log.warning("icon missing at %s; using a blank one", path)
    return QIcon()


def set_app_user_model_id() -> None:
    """Give Windows a stable identity so taskbar grouping and pinning work."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            APP_USER_MODEL_ID
        )
    except (AttributeError, OSError) as exc:  # pragma: no cover
        log.debug("could not set AppUserModelID: %s", exc)


class Application:
    """Owns the QApplication, the engine and the window, and shuts them down in order."""

    def __init__(self, arguments: list[str], start_hidden: bool = False) -> None:
        self.qt = QApplication(sys.argv[:1])
        self.qt.setApplicationName(APP_DISPLAY_NAME)
        self.qt.setApplicationVersion(APP_VERSION)
        self.qt.setOrganizationName(APP_DISPLAY_NAME)
        # Closing the last window must not quit -- we live in the tray.
        self.qt.setQuitOnLastWindowClosed(False)

        set_app_user_model_id()
        theme.apply(self.qt)

        self.config = Config.load()
        self.icon = load_icon()
        self.qt.setWindowIcon(self.icon)

        self.engine = TorrentEngine(self.config, ResumeStore())
        self.engine.start()
        self.engine.restore_torrents()

        self.bridge = EngineBridge(self.engine)
        self.window = MainWindow(self.bridge, self.config, self.icon)

        self.tray = TrayIcon(self.icon)
        self._wire_tray()

        self.ipc = SingleInstanceServer()
        self.ipc.arguments_received.connect(self._on_arguments)
        self.ipc.listen()

        self.bridge.start()

        if self.tray.available:
            self.tray.show()
        if not (start_hidden or self.config.start_minimized):
            self.window.show()

        self._register_shell_once()

        if arguments:
            self.window.open_sources(arguments)

    # ------------------------------------------------------------------ wiring

    def _wire_tray(self) -> None:
        self.tray.show_requested.connect(self._show_window)
        self.tray.add_torrent_requested.connect(self.window.add_torrent_file)
        self.tray.add_magnet_requested.connect(self.window.add_magnet_prompt)
        self.tray.pause_all_requested.connect(self.engine.pause_all)
        self.tray.resume_all_requested.connect(self.engine.resume_all)
        self.tray.quit_requested.connect(self.quit)
        self.bridge.torrents_updated.connect(self._update_tray_tooltip)
        self.bridge.torrent_finished.connect(
            lambda _h, name, _p: self.tray.notify("Download complete", name)
            if self.config.notify_on_finish
            else None
        )

    def _update_tray_tooltip(self, items) -> None:
        down = sum(i.download_rate for i in items)
        up = sum(i.upload_rate for i in items)
        active = sum(1 for i in items if i.is_active and not i.finished)
        self.tray.set_rates(down, up, active)

    def _show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _on_arguments(self, arguments: list[str]) -> None:
        self._show_window()
        self.window.open_sources(arguments)

    def _register_shell_once(self) -> None:
        """Claim magnet: and appear in Open With, the first time we ever run."""
        if self.config.shell_registered and shell_integration.is_registered():
            return
        try:
            shell_integration.register()
            self.config.shell_registered = True
            self.config.save()
        except Exception as exc:  # noqa: BLE001
            log.warning("shell registration failed: %s", exc)

    # ------------------------------------------------------------------ lifecycle

    def run(self) -> int:
        return self.qt.exec()

    def quit(self) -> None:
        """Stop cleanly: flush resume data before the process goes away."""
        self.window.prepare_quit()
        self.bridge.stop()
        self.ipc.close()
        self.tray.hide()
        try:
            self.config.save()
        except OSError as exc:
            log.warning("could not save settings: %s", exc)
        try:
            self.engine.stop(timeout=15.0)
        except Exception:  # noqa: BLE001
            log.exception("engine shutdown failed")
        self.qt.quit()
