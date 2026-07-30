"""Normalised events emitted by the engine.

libtorrent alerts are C++ objects whose lifetime and attribute set vary by type.
Translating them into these frozen dataclasses at the boundary means the UI layer
never touches a raw alert, and the event stream can be asserted on in tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from .torrent import TorrentItem


@dataclass(frozen=True)
class EngineEvent:
    """Base class -- lets callers annotate ``list[EngineEvent]``."""


@dataclass(frozen=True)
class TorrentAdded(EngineEvent):
    info_hash: str
    name: str
    has_metadata: bool


@dataclass(frozen=True)
class TorrentsUpdated(EngineEvent):
    items: tuple[TorrentItem, ...]


@dataclass(frozen=True)
class MetadataReceived(EngineEvent):
    info_hash: str
    name: str


@dataclass(frozen=True)
class TorrentFinished(EngineEvent):
    info_hash: str
    name: str
    save_path: str


@dataclass(frozen=True)
class TorrentRemoved(EngineEvent):
    info_hash: str


@dataclass(frozen=True)
class TorrentFailed(EngineEvent):
    info_hash: str
    message: str


@dataclass(frozen=True)
class SessionMessage(EngineEvent):
    level: str  # "info" | "warning" | "error"
    message: str
