"""Persistence of fast-resume data, .torrent copies and session state.

This is what separates a real client from a toy. Without correct resume data a
restart re-hashes every file from scratch; without the .torrent copies a restart
has to re-fetch metadata from peers before it can do anything at all.

Resume files deliberately do *not* embed the info dict (libtorrent's
``save_info_dict`` flag). Keeping metadata in a sibling .torrent file instead
keeps resume writes small and cheap enough to do every 60 seconds, even for
torrents whose info dict is megabytes wide.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import libtorrent as lt

from ..constants import resume_dir, session_state_file, torrents_dir

log = logging.getLogger(__name__)


class ResumeStore:
    def __init__(
        self,
        resume_path: Path | None = None,
        torrent_path: Path | None = None,
        state_path: Path | None = None,
    ) -> None:
        self.resume_dir = Path(resume_path) if resume_path else resume_dir()
        self.torrent_dir = Path(torrent_path) if torrent_path else torrents_dir()
        self.state_path = Path(state_path) if state_path else session_state_file()
        for directory in (self.resume_dir, self.torrent_dir, self.state_path.parent):
            directory.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ paths

    def resume_file(self, info_hash: str) -> Path:
        return self.resume_dir / f"{info_hash}.fastresume"

    def torrent_file(self, info_hash: str) -> Path:
        return self.torrent_dir / f"{info_hash}.torrent"

    # ------------------------------------------------------------------ writes

    @staticmethod
    def _atomic_write(target: Path, payload: bytes) -> None:
        """Write via a temp file so an interrupted write cannot corrupt state."""
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            tmp.write_bytes(payload)
            os.replace(tmp, target)
        except OSError as exc:
            log.warning("could not write %s: %s", target.name, exc)
            tmp.unlink(missing_ok=True)

    def write_resume(self, info_hash: str, payload: bytes) -> None:
        if not info_hash or not payload:
            return
        self._atomic_write(self.resume_file(info_hash), payload)

    def write_torrent(self, info_hash: str, torrent_info: "lt.torrent_info") -> None:
        """Serialise metadata back out to a .torrent next to the resume file."""
        if not info_hash or torrent_info is None:
            return
        target = self.torrent_file(info_hash)
        if target.exists():
            return  # metadata is immutable once we have it
        try:
            payload = lt.bencode(lt.create_torrent(torrent_info).generate())
        except Exception as exc:  # noqa: BLE001 - binding raises assorted types
            log.warning("could not serialise metadata for %s: %s", info_hash, exc)
            return
        self._atomic_write(target, payload)

    def write_session_state(self, payload: bytes) -> None:
        if payload:
            self._atomic_write(self.state_path, payload)

    # ------------------------------------------------------------------ reads

    def read_session_state(self) -> bytes | None:
        try:
            return self.state_path.read_bytes()
        except OSError:
            return None

    def load_all(self) -> list["lt.add_torrent_params"]:
        """Rebuild add-params for every torrent we previously persisted.

        A resume file that fails to parse is skipped rather than fatal -- one
        corrupt entry must not stop the other torrents from coming back.
        """
        restored: list[lt.add_torrent_params] = []
        for path in sorted(self.resume_dir.glob("*.fastresume")):
            info_hash = path.stem
            try:
                params = lt.read_resume_data(path.read_bytes())
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping unreadable resume file %s: %s", path.name, exc)
                continue

            metadata = self.torrent_file(info_hash)
            if metadata.exists():
                try:
                    params.ti = lt.torrent_info(str(metadata))
                except Exception as exc:  # noqa: BLE001
                    # Without metadata libtorrent falls back to fetching it from
                    # peers, exactly as it would for a magnet link.
                    log.warning("metadata for %s unreadable: %s", info_hash, exc)
            restored.append(params)
        return restored

    def known_hashes(self) -> set[str]:
        return {path.stem for path in self.resume_dir.glob("*.fastresume")}

    # ------------------------------------------------------------------ delete

    def forget(self, info_hash: str) -> None:
        if not info_hash:
            return
        for path in (self.resume_file(info_hash), self.torrent_file(info_hash)):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not delete %s: %s", path.name, exc)
