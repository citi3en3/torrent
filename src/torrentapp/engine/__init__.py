"""The download core.

Nothing in this package imports Qt. That keeps the engine runnable from
``scripts/smoke_engine.py`` and from pytest without a GUI, and it keeps the
alert-handling logic testable in isolation from the widgets that display it.
"""

from .events import (
    EngineEvent,
    ListenPortChanged,
    MetadataReceived,
    NetworkStatus,
    SessionMessage,
    TorrentAdded,
    TorrentFailed,
    TorrentFinished,
    TorrentRemoved,
    TorrentsUpdated,
)
from .resume import ResumeStore
from .session import TorrentEngine, params_from_file, params_from_magnet
from .torrent import TorrentItem, hash_string

__all__ = [
    "EngineEvent",
    "ListenPortChanged",
    "MetadataReceived",
    "NetworkStatus",
    "ResumeStore",
    "SessionMessage",
    "TorrentAdded",
    "TorrentEngine",
    "TorrentFailed",
    "TorrentFinished",
    "TorrentItem",
    "TorrentRemoved",
    "TorrentsUpdated",
    "hash_string",
    "params_from_file",
    "params_from_magnet",
]
