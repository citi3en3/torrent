"""One-shot import of an existing qBittorrent library.

qBittorrent's ``.fastresume`` files *are* libtorrent resume data. It adds a few
private ``qBt-*`` keys (save path, category, tags) which libtorrent ignores, so
``lt.read_resume_data`` parses them directly -- meaning piece-level progress,
file priorities and save paths all survive the move. Nothing has to re-download
and nothing has to re-hash.

This reads qBittorrent's data and never writes to it, so running the import is
safe while qBittorrent is still installed, and repeatable if it goes wrong.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import libtorrent as lt

from .resume import ResumeStore
from .torrent import hash_string

log = logging.getLogger(__name__)

# qBittorrent 5.x keeps its torrent library here; 4.x used %APPDATA%.
BACKUP_LOCATIONS = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "qBittorrent" / "BT_backup",
    Path(os.environ.get("APPDATA", "")) / "qBittorrent" / "BT_backup",
)

@dataclass
class MigrationCandidate:
    info_hash: str
    name: str
    save_path: str
    total_size: int
    has_metadata: bool
    resume_payload: bytes
    torrent_path: Path | None


def find_backup_dir() -> Path | None:
    for candidate in BACKUP_LOCATIONS:
        if candidate.is_dir() and any(candidate.glob("*.fastresume")):
            return candidate
    return None


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value) if value is not None else ""


def _read_qbt_keys(payload: bytes) -> dict[str, str]:
    """Pull qBittorrent's private keys straight out of the bencoded resume file."""
    try:
        decoded = lt.bdecode(payload)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(decoded, dict):
        return {}
    wanted = (b"qBt-savePath", b"qBt-downloadPath", b"qBt-name")
    return {
        key.decode("ascii"): _decode(decoded.get(key))
        for key in wanted
        if decoded.get(key) is not None
    }


def scan(backup_dir: Path | None = None) -> list[MigrationCandidate]:
    """Inspect a qBittorrent backup folder. Read-only; never raises on bad files."""
    directory = backup_dir or find_backup_dir()
    if directory is None:
        return []

    candidates: list[MigrationCandidate] = []
    for resume_path in sorted(directory.glob("*.fastresume")):
        try:
            payload = resume_path.read_bytes()
        except OSError as exc:
            log.warning("cannot read %s: %s", resume_path.name, exc)
            continue

        try:
            params = lt.read_resume_data(payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("skipping unreadable %s: %s", resume_path.name, exc)
            continue

        info_hash = hash_string(params) or resume_path.stem
        extras = _read_qbt_keys(payload)

        torrent_path = directory / f"{resume_path.stem}.torrent"
        if not torrent_path.exists():
            torrent_path = None

        name = extras.get("qBt-name") or ""
        total_size = 0
        has_metadata = False
        if torrent_path is not None:
            try:
                info = lt.torrent_info(str(torrent_path))
                name = name or str(info.name())
                total_size = int(info.total_size())
                has_metadata = True
            except Exception as exc:  # noqa: BLE001
                log.warning("metadata for %s unreadable: %s", resume_path.stem, exc)

        candidates.append(
            MigrationCandidate(
                info_hash=info_hash,
                name=name or info_hash,
                save_path=extras.get("qBt-savePath") or str(params.save_path or ""),
                total_size=total_size,
                has_metadata=has_metadata,
                resume_payload=payload,
                torrent_path=torrent_path,
            )
        )
    return candidates


def import_candidates(
    candidates: list[MigrationCandidate], store: ResumeStore
) -> tuple[int, list[str]]:
    """Copy the chosen torrents into our own state directory.

    Returns (imported count, list of human-readable failures). Existing entries
    are skipped rather than overwritten, so re-running is harmless.
    """
    imported = 0
    failures: list[str] = []
    existing = store.known_hashes()

    for candidate in candidates:
        if candidate.info_hash in existing:
            log.info("%s already present; skipping", candidate.info_hash)
            continue
        try:
            store.write_resume(candidate.info_hash, candidate.resume_payload)
            if candidate.torrent_path is not None:
                target = store.torrent_file(candidate.info_hash)
                if not target.exists():
                    target.write_bytes(candidate.torrent_path.read_bytes())
            imported += 1
        except OSError as exc:
            failures.append(f"{candidate.name}: {exc}")
            log.warning("could not import %s: %s", candidate.name, exc)

    log.info("imported %d torrent(s) from qBittorrent", imported)
    return imported, failures
