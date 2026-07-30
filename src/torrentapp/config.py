"""User settings, persisted as JSON in %APPDATA%\\TorrentApp\\settings.json.

Unknown keys in the file are ignored rather than fatal, and missing keys fall
back to the dataclass defaults -- so a settings file written by a future version
never bricks an older build, and vice versa.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .constants import config_file, data_dir

log = logging.getLogger(__name__)

# qBittorrent's *effective* default on this machine (Session\DefaultSavePath),
# which is also where all the existing torrents already store their data. Note
# the ini additionally carries a legacy Downloads\SavePath=F:/DOWNLOADS/ key
# that qBittorrent 5.x no longer uses, and which points at a folder that does
# not exist -- following it would have scattered new downloads onto a different
# drive from the entire existing library.
DEFAULT_DOWNLOAD_ROOT = r"A:\DOWNLOADS"

# Reusing qBittorrent's listen port: it is already proven through the router and
# the Windows firewall, so inbound connections work from the first launch.
DEFAULT_LISTEN_PORT = 58361


@dataclass
class Config:
    # --- storage ---
    download_root: str = DEFAULT_DOWNLOAD_ROOT
    incomplete_path: str = ""  # empty == download straight to the final folder

    # --- network ---
    listen_port: int = DEFAULT_LISTEN_PORT
    enable_dht: bool = True
    enable_lsd: bool = True
    enable_upnp: bool = True
    enable_natpmp: bool = True
    encryption: str = "enabled"  # enabled | forced | disabled

    # --- limits (bytes/sec; 0 == unlimited) ---
    download_rate_limit: int = 0
    upload_rate_limit: int = 0
    connections_limit: int = 500
    active_downloads: int = 5
    active_seeds: int = 8
    active_limit: int = 20

    # --- behaviour ---
    show_add_dialog: bool = True
    start_paused: bool = False
    seed_ratio_limit: float = 0.0  # 0 == seed forever
    minimize_to_tray: bool = True
    close_to_tray: bool = True
    start_minimized: bool = False
    notify_on_finish: bool = True
    remember_last_save_path: bool = True
    last_save_path: str = ""

    # --- one-time flags ---
    migration_done: bool = False
    shell_registered: bool = False

    # --- ui ---
    window_geometry: str = ""
    column_state: str = ""

    # ------------------------------------------------------------------ load/save

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        target = path or config_file()
        if not target.exists():
            return cls()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("settings unreadable (%s); falling back to defaults", exc)
            return cls()
        if not isinstance(raw, dict):
            return cls()

        known = {f.name for f in fields(cls)}
        kwargs = {key: value for key, value in raw.items() if key in known}
        try:
            config = cls(**kwargs)
        except TypeError as exc:
            log.warning("settings had incompatible types (%s); using defaults", exc)
            return cls()
        config.normalise()
        return config

    def save(self, path: Path | None = None) -> None:
        target = path or config_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), indent=2, ensure_ascii=False)
        # Write-then-replace so a crash mid-write cannot truncate the settings.
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, target)

    # ------------------------------------------------------------------ helpers

    def normalise(self) -> None:
        """Repair anything a hand-edited settings file could have broken."""
        if not isinstance(self.download_root, str) or not self.download_root.strip():
            self.download_root = DEFAULT_DOWNLOAD_ROOT
        if not 1 <= int(self.listen_port) <= 65535:
            self.listen_port = DEFAULT_LISTEN_PORT
        if self.encryption not in ("enabled", "forced", "disabled"):
            self.encryption = "enabled"
        for name in ("download_rate_limit", "upload_rate_limit"):
            if getattr(self, name) < 0:
                setattr(self, name, 0)

    def default_save_path(self) -> str:
        """Where the add dialog should start.

        Offering the folder used last is the cheap version of "smart" -- people
        tend to download several related things in a row.
        """
        if self.remember_last_save_path and self.last_save_path.strip():
            return self.last_save_path
        return self.download_root

    def remember(self, save_path: str) -> None:
        if self.remember_last_save_path and save_path.strip():
            self.last_save_path = save_path

    def state_dir(self) -> Path:
        return data_dir()
