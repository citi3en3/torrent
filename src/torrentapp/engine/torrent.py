"""Immutable snapshot of a torrent's state, taken from ``lt.torrent_status``.

Copying the fields we care about out of the status object matters: the C++
object is only valid for the duration of the alert that carried it, so holding a
reference and reading it later is a use-after-free waiting to happen.
"""

from __future__ import annotations

from dataclasses import dataclass

import libtorrent as lt

_ZERO_HASH_CHARS = {"0"}


def _resolve(obj: object, name: str) -> object | None:
    """Read ``name`` off ``obj``, calling it if the binding exposes a method.

    ``info_hashes`` is a method on ``torrent_handle`` but a plain attribute on
    ``torrent_status`` and ``add_torrent_params``; this papers over that.
    """
    attr = getattr(obj, name, None)
    if attr is None:
        return None
    if callable(attr):
        try:
            return attr()
        except Exception:
            return None
    return attr


def hash_string(source: object) -> str:
    """Canonical hex info-hash for a handle, status, alert or params object.

    Prefers the v1 hash so identifiers stay stable and match what trackers and
    other clients (including the qBittorrent data we import) use.
    """
    hashes = _resolve(source, "info_hashes")
    if hashes is not None:
        for part in ("v1", "v2"):
            value = getattr(hashes, part, None)
            if value is None:
                continue
            text = str(value)
            if text and set(text) != _ZERO_HASH_CHARS:
                return text
    legacy = _resolve(source, "info_hash")
    if legacy is not None:
        text = str(legacy)
        if text and set(text) != _ZERO_HASH_CHARS:
            return text
    return ""


def _build_state_names() -> dict[int, str]:
    wanted = {
        "checking_files": "checking",
        "downloading_metadata": "metadata",
        "downloading": "downloading",
        "finished": "finished",
        "seeding": "seeding",
        "allocating": "allocating",
        "checking_resume_data": "checking_resume",
    }
    mapping: dict[int, str] = {}
    for attr, label in wanted.items():
        value = getattr(lt.torrent_status, attr, None)
        if value is not None:
            mapping[int(value)] = label
    return mapping


_STATE_NAMES = _build_state_names()


def _error_text(status: "lt.torrent_status") -> str:
    error = getattr(status, "error", "") or ""
    if isinstance(error, bytes):
        error = error.decode("utf-8", "replace")
    return str(error).strip()


@dataclass(frozen=True)
class TorrentItem:
    info_hash: str
    name: str
    state: str
    progress: float  # 0.0 .. 1.0
    download_rate: int  # bytes/sec
    upload_rate: int  # bytes/sec
    total_wanted: int
    total_wanted_done: int
    num_seeds: int
    num_peers: int
    list_seeds: int
    list_peers: int
    save_path: str
    paused: bool
    finished: bool
    seeding: bool
    has_metadata: bool
    all_time_download: int
    all_time_upload: int
    added_time: int
    completed_time: int
    queue_position: int
    error: str

    @classmethod
    def from_status(cls, status: "lt.torrent_status") -> "TorrentItem":
        error = _error_text(status)
        paused = bool(getattr(status, "paused", False))
        if error:
            state = "error"
        elif paused:
            state = "paused"
        else:
            state = _STATE_NAMES.get(int(status.state), "unknown")

        return cls(
            info_hash=hash_string(status),
            name=str(status.name or ""),
            state=state,
            progress=float(status.progress or 0.0),
            download_rate=int(status.download_payload_rate or 0),
            upload_rate=int(status.upload_payload_rate or 0),
            total_wanted=int(status.total_wanted or 0),
            total_wanted_done=int(status.total_wanted_done or 0),
            num_seeds=int(status.num_seeds or 0),
            num_peers=int(status.num_peers or 0),
            list_seeds=int(getattr(status, "list_seeds", 0) or 0),
            list_peers=int(getattr(status, "list_peers", 0) or 0),
            save_path=str(status.save_path or ""),
            paused=paused,
            finished=bool(getattr(status, "is_finished", False)),
            seeding=bool(getattr(status, "is_seeding", False)),
            has_metadata=bool(getattr(status, "has_metadata", False)),
            all_time_download=int(getattr(status, "all_time_download", 0) or 0),
            all_time_upload=int(getattr(status, "all_time_upload", 0) or 0),
            added_time=int(getattr(status, "added_time", 0) or 0),
            completed_time=int(getattr(status, "completed_time", 0) or 0),
            queue_position=int(getattr(status, "queue_position", -1)),
            error=error,
        )

    # ------------------------------------------------------------------ derived

    @property
    def remaining(self) -> int:
        return max(0, self.total_wanted - self.total_wanted_done)

    @property
    def eta(self) -> int | None:
        """Seconds to completion, or None when it cannot be estimated."""
        if self.paused or self.finished or self.seeding:
            return None
        if self.remaining <= 0 or self.download_rate <= 0:
            return None
        return int(self.remaining / self.download_rate)

    @property
    def ratio(self) -> float:
        downloaded = self.all_time_download
        if downloaded <= 0:
            return 0.0
        return self.all_time_upload / downloaded

    @property
    def is_active(self) -> bool:
        return not self.paused and self.state not in ("error", "unknown")
