"""Qt adapter around the (deliberately Qt-free) engine.

The engine exposes ``tick()`` and ``poll()``; this turns them into Qt signals
driven by two timers:

* a 200 ms timer drains the alert queue, which keeps the UI responsive to
  events like "metadata arrived" without waiting a whole second;
* a 1 s timer asks libtorrent to post fresh status for every torrent.

Both run on the GUI thread on purpose. ``pop_alerts()`` does not block and the
bindings release the GIL, so a worker thread would buy nothing but races.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from .engine import (
    ListenPortChanged,
    MetadataReceived,
    NetworkStatus,
    SessionMessage,
    TorrentAdded,
    TorrentEngine,
    TorrentFailed,
    TorrentFinished,
    TorrentRemoved,
    TorrentsUpdated,
)

log = logging.getLogger(__name__)

ALERT_INTERVAL_MS = 200
STATUS_INTERVAL_MS = 1000


class EngineBridge(QObject):
    torrents_updated = Signal(list)  # list[TorrentItem]
    torrent_added = Signal(str, str, bool)  # info_hash, name, has_metadata
    metadata_received = Signal(str, str)  # info_hash, name
    torrent_finished = Signal(str, str, str)  # info_hash, name, save_path
    torrent_removed = Signal(str)  # info_hash
    torrent_failed = Signal(str, str)  # info_hash, message
    message = Signal(str, str)  # level, text
    network_status = Signal(object)  # NetworkStatus
    listen_port_changed = Signal(int)  # port

    def __init__(self, engine: TorrentEngine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._engine = engine

        self._alert_timer = QTimer(self)
        self._alert_timer.setInterval(ALERT_INTERVAL_MS)
        self._alert_timer.timeout.connect(self._drain)

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_INTERVAL_MS)
        self._status_timer.timeout.connect(self._tick)

    @property
    def engine(self) -> TorrentEngine:
        return self._engine

    def start(self) -> None:
        self._alert_timer.start()
        self._status_timer.start()

    def stop(self) -> None:
        self._alert_timer.stop()
        self._status_timer.stop()

    # ------------------------------------------------------------------ internals

    def _tick(self) -> None:
        try:
            self._engine.tick()
        except Exception:  # noqa: BLE001 - a timer callback must never die
            log.exception("engine tick failed")

    def _drain(self) -> None:
        try:
            events = self._engine.poll()
        except Exception:  # noqa: BLE001
            log.exception("engine poll failed")
            return

        for event in events:
            if isinstance(event, TorrentsUpdated):
                self.torrents_updated.emit(list(event.items))
            elif isinstance(event, TorrentAdded):
                self.torrent_added.emit(event.info_hash, event.name, event.has_metadata)
            elif isinstance(event, MetadataReceived):
                self.metadata_received.emit(event.info_hash, event.name)
            elif isinstance(event, TorrentFinished):
                self.torrent_finished.emit(event.info_hash, event.name, event.save_path)
            elif isinstance(event, TorrentRemoved):
                self.torrent_removed.emit(event.info_hash)
            elif isinstance(event, TorrentFailed):
                self.torrent_failed.emit(event.info_hash, event.message)
            elif isinstance(event, SessionMessage):
                self.message.emit(event.level, event.message)
            elif isinstance(event, NetworkStatus):
                self.network_status.emit(event)
            elif isinstance(event, ListenPortChanged):
                self.listen_port_changed.emit(event.port)
