"""libtorrent session lifecycle and alert handling.

Design notes worth keeping in mind when editing this file:

* libtorrent is event-driven. Nothing here polls a torrent for its state; we ask
  the session to post updates (``post_torrent_updates``) and then drain the
  resulting alert queue. ``pop_alerts`` never blocks, which is why the caller can
  safely drive ``poll()`` from a GUI timer.
* Alerts are translated into the frozen dataclasses in ``events.py`` before they
  leave this module. Raw alert objects must never escape -- they are only valid
  until the next ``pop_alerts`` call.
* Auto-managed mode is deliberately switched off. The user's previous client had
  queueing disabled, so "added" should mean "downloading now", not "queued".
"""

from __future__ import annotations

import logging
import random
import socket
import time
from pathlib import Path

import libtorrent as lt

from ..config import Config
from ..constants import APP_NAME, APP_VERSION, PEER_ID_PREFIX
from .events import (
    EngineEvent,
    MetadataReceived,
    SessionMessage,
    TorrentAdded,
    TorrentFailed,
    TorrentFinished,
    TorrentRemoved,
    TorrentsUpdated,
)
from .resume import ResumeStore
from .torrent import TorrentItem, hash_string

log = logging.getLogger(__name__)

DHT_BOOTSTRAP_NODES = ",".join(
    (
        "router.bittorrent.com:6881",
        "dht.transmissionbt.com:6881",
        "router.utorrent.com:6881",
        "dht.libtorrent.org:25401",
    )
)

# libtorrent's settings_pack::enc_policy
_ENC_POLICY = {"forced": 0, "enabled": 1, "disabled": 2}
_ENC_LEVEL_BOTH = 3

_DELETE_FILES = getattr(lt.session, "delete_files", 1)
_SPARSE = getattr(lt.storage_mode_t, "storage_mode_sparse", None)

ALERT_MASK = int(
    lt.alert.category_t.status_notification
    | lt.alert.category_t.error_notification
    | lt.alert.category_t.storage_notification
    | lt.alert.category_t.tracker_notification
    | lt.alert.category_t.performance_warning
)

# How many one-second ticks between periodic resume-data checkpoints.
RESUME_INTERVAL_TICKS = 60

# Windows' dynamic port range is 49152-65535. Hyper-V / WSL / WinNAT reserve
# random blocks of it at every boot, so a port there that worked yesterday can be
# "access denied" today -- and libtorrent only falls back from "address in use",
# not from that, leaving the session with no sockets and zero peers. So we never
# keep a port in that range, even one that happens to bind right now.
DYNAMIC_PORT_START = 49152
SAFE_PORT_RANGE = (10000, DYNAMIC_PORT_START - 1)

# Ticks between checks that the session still has at least one listen socket.
WATCHDOG_INTERVAL_TICKS = 10


def port_is_bindable(port: int) -> bool:
    """True if both TCP and UDP can bind ``port`` on all interfaces."""
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        probe = socket.socket(socket.AF_INET, kind)
        try:
            probe.bind(("0.0.0.0", int(port)))
        except OSError:
            return False
        finally:
            probe.close()
    return True


def choose_listen_port(
    preferred: int, avoid: set[int] | frozenset[int] = frozenset(), attempts: int = 50
) -> int:
    """``preferred`` if usable, otherwise a random usable port in SAFE_PORT_RANGE.

    "Usable" means bindable now *and* outside the dynamic range, so it stays
    usable after a reboot. ``avoid`` excludes ports known to have just failed.
    """
    preferred = int(preferred)
    if (
        preferred not in avoid
        and 1024 <= preferred < DYNAMIC_PORT_START
        and port_is_bindable(preferred)
    ):
        return preferred
    for _ in range(attempts):
        candidate = random.randint(*SAFE_PORT_RANGE)
        if candidate not in avoid and port_is_bindable(candidate):
            return candidate
    # Nothing worked; let libtorrent try and report the failure itself.
    return int(preferred)


def build_settings(config: Config) -> dict:
    """Translate our Config into a libtorrent settings dict."""
    port = int(config.listen_port)
    return {
        "user_agent": f"{APP_NAME}/{APP_VERSION} libtorrent/{lt.__version__}",
        # Some trackers reject clients whose peer id they do not recognise, so we
        # present libtorrent's own well-known fingerprint.
        "peer_fingerprint": lt.generate_fingerprint(PEER_ID_PREFIX, 2, 0, 13, 0),
        "listen_interfaces": f"0.0.0.0:{port},[::]:{port}",
        "alert_mask": ALERT_MASK,
        "enable_dht": bool(config.enable_dht),
        "enable_lsd": bool(config.enable_lsd),
        "enable_upnp": bool(config.enable_upnp),
        "enable_natpmp": bool(config.enable_natpmp),
        "dht_bootstrap_nodes": DHT_BOOTSTRAP_NODES,
        "out_enc_policy": _ENC_POLICY.get(config.encryption, 1),
        "in_enc_policy": _ENC_POLICY.get(config.encryption, 1),
        "allowed_enc_level": _ENC_LEVEL_BOTH,
        "prefer_rc4": False,
        "announce_to_all_trackers": True,
        "announce_to_all_tiers": True,
        "connections_limit": int(config.connections_limit),
        "download_rate_limit": int(config.download_rate_limit),
        "upload_rate_limit": int(config.upload_rate_limit),
        # These only bite for auto-managed torrents, which we do not use, but
        # they are kept sane in case queueing is ever turned on.
        "active_downloads": int(config.active_downloads),
        "active_seeds": int(config.active_seeds),
        "active_limit": int(config.active_limit),
    }


# --------------------------------------------------------------------------- params


def _apply_common_flags(params: "lt.add_torrent_params", paused: bool) -> None:
    flags = params.flags
    # Queueing is off, so a torrent should never sit in auto-managed limbo.
    flags &= ~lt.torrent_flags.auto_managed
    # Re-adding something we already have should be a no-op, not an error.
    flags &= ~lt.torrent_flags.duplicate_is_error
    if paused:
        flags |= lt.torrent_flags.paused
    else:
        flags &= ~lt.torrent_flags.paused
    params.flags = flags
    if _SPARSE is not None:
        # Sparse allocation avoids stalling for minutes preallocating a 60 GB
        # file before the first byte is written.
        params.storage_mode = _SPARSE


def params_from_file(
    torrent_path: str | Path,
    save_path: str | Path,
    *,
    file_priorities: list[int] | None = None,
    paused: bool = False,
) -> "lt.add_torrent_params":
    params = lt.add_torrent_params()
    params.ti = lt.torrent_info(str(torrent_path))
    params.save_path = str(save_path)
    if file_priorities is not None:
        params.file_priorities = [int(p) for p in file_priorities]
    _apply_common_flags(params, paused)
    return params


def params_from_magnet(
    magnet_uri: str,
    save_path: str | Path,
    *,
    file_priorities: list[int] | None = None,
    paused: bool = False,
) -> "lt.add_torrent_params":
    params = lt.parse_magnet_uri(magnet_uri)
    params.save_path = str(save_path)
    if file_priorities is not None:
        params.file_priorities = [int(p) for p in file_priorities]
    _apply_common_flags(params, paused)
    return params


def params_hash(params: "lt.add_torrent_params") -> str:
    """Info-hash of a torrent we are about to add, before it has a handle."""
    if getattr(params, "ti", None) is not None:
        found = hash_string(params.ti)
        if found:
            return found
    return hash_string(params)


# --------------------------------------------------------------------------- engine


class TorrentEngine:
    """Owns the libtorrent session and the torrents inside it."""

    def __init__(self, config: Config, store: ResumeStore | None = None) -> None:
        self.config = config
        self.store = store or ResumeStore()
        self._session: lt.session | None = None
        self._handles: dict[str, lt.torrent_handle] = {}
        self._items: dict[str, TorrentItem] = {}
        self._awaiting_resume: set[str] = set()
        self._ticks = 0
        # Set when start() had to move off the configured port; the caller
        # persists the config so the new port (and its UPnP mapping) sticks.
        self.port_changed = False
        self._handlers = {
            "add_torrent_alert": self._on_add_torrent,
            "state_update_alert": self._on_state_update,
            "metadata_received_alert": self._on_metadata,
            "torrent_finished_alert": self._on_finished,
            "save_resume_data_alert": self._on_resume_data,
            "save_resume_data_failed_alert": self._on_resume_failed,
            "torrent_removed_alert": self._on_removed,
            "torrent_error_alert": self._on_error,
            "file_error_alert": self._on_error,
            "torrent_paused_alert": self._on_pause_state,
            "torrent_resumed_alert": self._on_pause_state,
        }

    # ------------------------------------------------------------------ lifecycle

    @property
    def running(self) -> bool:
        return self._session is not None

    @property
    def session(self) -> "lt.session":
        if self._session is None:
            raise RuntimeError("engine is not started")
        return self._session

    def start(self) -> None:
        if self._session is not None:
            return
        port = choose_listen_port(self.config.listen_port)
        if port != self.config.listen_port:
            log.warning(
                "port %s cannot be bound (in use or reserved by Windows); "
                "switching to %s",
                self.config.listen_port,
                port,
            )
            self.config.listen_port = port
            self.port_changed = True
        settings = build_settings(self.config)
        session = lt.session(settings)

        blob = self.store.read_session_state()
        if blob:
            try:
                session.load_state(lt.bdecode(blob))
                # Saved state carries its own settings block, so re-apply ours on
                # top -- otherwise a changed listen port silently has no effect.
                session.apply_settings(settings)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not restore session state: %s", exc)

        self._session = session
        # Report the port actually bound, not the one requested: libtorrent
        # falls back to a free port when the configured one is taken, and
        # logging the request would hide that.
        try:
            bound = session.listen_port()
        except Exception:  # noqa: BLE001
            bound = self.config.listen_port
        if not bound:
            log.error(
                "no listen socket on port %s -- trackers, DHT and peers will not work",
                self.config.listen_port,
            )
        elif bound != self.config.listen_port:
            log.warning(
                "port %s unavailable; listening on %s instead",
                self.config.listen_port,
                bound,
            )
        else:
            log.info("session started on port %s", bound)

    def restore_torrents(self, force_paused: bool = False) -> int:
        """Re-add everything persisted by a previous run. Returns the count.

        ``force_paused`` matters right after importing another client's library:
        any torrent that is still incomplete would otherwise start writing to
        files that the other client may also still be writing to, which corrupts
        both copies. Paused means the user decides when it is safe to start.
        """
        params_list = self.store.load_all()
        for params in params_list:
            if force_paused:
                params.flags |= lt.torrent_flags.paused
                params.flags &= ~lt.torrent_flags.auto_managed
            try:
                self.session.async_add_torrent(params)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not restore a torrent: %s", exc)
        if params_list:
            log.info(
                "restoring %d torrent(s)%s",
                len(params_list),
                " (paused)" if force_paused else "",
            )
        return len(params_list)

    def stop(self, timeout: float = 10.0) -> None:
        """Shut down cleanly, flushing resume data first.

        Skipping the flush is the classic bug that makes a client re-hash every
        file on next launch, so we wait (bounded) for the alerts to come back.
        """
        session = self._session
        if session is None:
            return

        session.pause()
        try:
            self.store.write_session_state(lt.bencode(session.save_state()))
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save session state: %s", exc)

        outstanding = self.request_resume_all(force=True)
        if outstanding:
            log.info("flushing resume data for %d torrent(s)", outstanding)
        deadline = time.monotonic() + timeout
        while self._awaiting_resume and time.monotonic() < deadline:
            session.wait_for_alert(200)
            self.poll()

        if self._awaiting_resume:
            log.warning(
                "%d torrent(s) did not return resume data before shutdown",
                len(self._awaiting_resume),
            )

        self._session = None
        self._handles.clear()
        self._awaiting_resume.clear()
        log.info("session stopped")

    # ------------------------------------------------------------------ pumping

    def tick(self) -> None:
        """Drive periodic work. Call roughly once a second."""
        if self._session is None:
            return
        self._session.post_torrent_updates()
        self._ticks += 1
        if self._ticks % RESUME_INTERVAL_TICKS == 0:
            self.request_resume_all()

    def poll(self) -> list[EngineEvent]:
        """Drain the alert queue and return normalised events."""
        if self._session is None:
            return []
        events: list[EngineEvent] = []
        for alert in self._session.pop_alerts():
            handler = self._handlers.get(type(alert).__name__)
            if handler is None:
                continue
            try:
                handler(alert, events)
            except Exception:  # noqa: BLE001
                log.exception("failed handling %s", type(alert).__name__)
        return events

    # ------------------------------------------------------------------ commands

    def add(self, params: "lt.add_torrent_params") -> str:
        """Queue a torrent. The handle arrives later via ``add_torrent_alert``."""
        info_hash = params_hash(params)
        self.session.async_add_torrent(params)
        return info_hash

    def handle(self, info_hash: str) -> "lt.torrent_handle | None":
        handle = self._handles.get(info_hash)
        if handle is None or not handle.is_valid():
            return None
        return handle

    def remove(self, info_hash: str, delete_files: bool = False) -> bool:
        handle = self.handle(info_hash)
        if handle is None:
            # Not in the session (maybe it never loaded) -- still drop its state.
            self.store.forget(info_hash)
            self._items.pop(info_hash, None)
            return False
        self.session.remove_torrent(handle, _DELETE_FILES if delete_files else 0)
        self._awaiting_resume.discard(info_hash)
        self.store.forget(info_hash)
        return True

    def pause(self, info_hash: str) -> None:
        handle = self.handle(info_hash)
        if handle is not None:
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.pause()

    def resume(self, info_hash: str) -> None:
        handle = self.handle(info_hash)
        if handle is not None:
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.resume()

    def pause_all(self) -> None:
        for info_hash in list(self._handles):
            self.pause(info_hash)

    def resume_all(self) -> None:
        for info_hash in list(self._handles):
            self.resume(info_hash)

    def force_recheck(self, info_hash: str) -> None:
        handle = self.handle(info_hash)
        if handle is not None:
            handle.force_recheck()

    def force_reannounce(self, info_hash: str) -> None:
        handle = self.handle(info_hash)
        if handle is not None:
            handle.force_reannounce()

    def set_file_priorities(self, info_hash: str, priorities: list[int]) -> None:
        handle = self.handle(info_hash)
        if handle is not None:
            handle.prioritize_files([int(p) for p in priorities])
            self.request_resume(info_hash, force=True)

    def move_storage(self, info_hash: str, destination: str | Path) -> None:
        handle = self.handle(info_hash)
        if handle is not None:
            handle.move_storage(str(destination))

    def apply_config(self) -> None:
        """Push changed settings into a running session."""
        if self._session is not None:
            self._session.apply_settings(build_settings(self.config))

    # ------------------------------------------------------------------ resume

    def request_resume(self, info_hash: str, *, force: bool = False) -> bool:
        handle = self.handle(info_hash)
        if handle is None:
            return False
        if not force and not handle.need_save_resume_data():
            return False
        try:
            handle.save_resume_data()
        except Exception as exc:  # noqa: BLE001
            log.warning("save_resume_data failed for %s: %s", info_hash, exc)
            return False
        self._awaiting_resume.add(info_hash)
        return True

    def request_resume_all(self, *, force: bool = False) -> int:
        return sum(
            1 for h in list(self._handles) if self.request_resume(h, force=force)
        )

    # ------------------------------------------------------------------ state

    def items(self) -> list[TorrentItem]:
        return list(self._items.values())

    def item(self, info_hash: str) -> TorrentItem | None:
        return self._items.get(info_hash)

    def __contains__(self, info_hash: object) -> bool:
        return info_hash in self._handles

    # ------------------------------------------------------------------ handlers

    def _on_add_torrent(self, alert, events: list[EngineEvent]) -> None:
        error = getattr(alert, "error", None)
        if error is not None and error.value() != 0:
            events.append(SessionMessage("error", f"Could not add torrent: {error.message()}"))
            return

        handle = alert.handle
        info_hash = hash_string(handle)
        if not info_hash:
            return

        already_known = info_hash in self._handles
        self._handles[info_hash] = handle

        torrent_info = handle.torrent_file()
        if torrent_info is not None:
            self.store.write_torrent(info_hash, torrent_info)

        if not already_known:
            events.append(
                TorrentAdded(
                    info_hash=info_hash,
                    name=str(handle.status().name or info_hash),
                    has_metadata=torrent_info is not None,
                )
            )
        # Checkpoint immediately: a crash seconds after adding should not lose it.
        self.request_resume(info_hash, force=True)

    def _on_state_update(self, alert, events: list[EngineEvent]) -> None:
        updated: list[TorrentItem] = []
        for status in alert.status:
            item = TorrentItem.from_status(status)
            if not item.info_hash:
                continue
            self._items[item.info_hash] = item
            updated.append(item)
        if updated:
            events.append(TorrentsUpdated(tuple(updated)))

    def _on_metadata(self, alert, events: list[EngineEvent]) -> None:
        handle = alert.handle
        info_hash = hash_string(handle)
        torrent_info = handle.torrent_file()
        if torrent_info is not None:
            self.store.write_torrent(info_hash, torrent_info)
        events.append(
            MetadataReceived(
                info_hash=info_hash,
                name=str(torrent_info.name()) if torrent_info else info_hash,
            )
        )
        self.request_resume(info_hash, force=True)

    def _on_finished(self, alert, events: list[EngineEvent]) -> None:
        handle = alert.handle
        info_hash = hash_string(handle)
        status = handle.status()
        events.append(
            TorrentFinished(
                info_hash=info_hash,
                name=str(status.name or info_hash),
                save_path=str(status.save_path or ""),
            )
        )
        self.request_resume(info_hash, force=True)

    def _on_resume_data(self, alert, events: list[EngineEvent]) -> None:
        info_hash = hash_string(alert.handle)
        try:
            payload = lt.write_resume_data_buf(alert.params)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not serialise resume data for %s: %s", info_hash, exc)
        else:
            self.store.write_resume(info_hash, payload)
        self._awaiting_resume.discard(info_hash)

    def _on_resume_failed(self, alert, events: list[EngineEvent]) -> None:
        info_hash = hash_string(alert.handle)
        self._awaiting_resume.discard(info_hash)
        # Routine while a torrent is still checking; not worth alarming the user.
        log.debug("resume data unavailable for %s: %s", info_hash, alert.message())

    def _on_removed(self, alert, events: list[EngineEvent]) -> None:
        info_hash = hash_string(alert)
        self._handles.pop(info_hash, None)
        self._items.pop(info_hash, None)
        self._awaiting_resume.discard(info_hash)
        events.append(TorrentRemoved(info_hash))

    def _on_error(self, alert, events: list[EngineEvent]) -> None:
        info_hash = hash_string(getattr(alert, "handle", alert))
        events.append(TorrentFailed(info_hash, alert.message()))

    def _on_pause_state(self, alert, events: list[EngineEvent]) -> None:
        # Ask for a fresh status so the UI reflects the change immediately
        # rather than waiting for the next one-second tick.
        handle = getattr(alert, "handle", None)
        if handle is not None and handle.is_valid():
            handle.post_status()
