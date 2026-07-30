"""Application identity and filesystem locations.

Every user-visible name and every path the app writes to funnels through this
module, so renaming the application or relocating its data is a single-file
change. Tests override the data root with ``TORRENTAPP_DATA_DIR`` so they never
touch the real profile.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# APP_NAME is the filesystem-safe identity (folders, executable, registry keys);
# APP_DISPLAY_NAME is what a human ever sees. The Python package stays
# `torrentapp` -- that name is internal and renaming it would churn every import
# for no visible benefit.
APP_NAME = "YYTorrent"
APP_DISPLAY_NAME = "YY Torrent"
APP_DESCRIPTION = "BitTorrent client"
APP_VERSION = "1.0.0"
APP_PUBLISHER = "YY Torrent"

# --- Windows shell identifiers -------------------------------------------------
PROGID = f"{APP_NAME}.Torrent"
REGISTERED_APP = APP_NAME
APP_ROOT_KEY = rf"Software\{APP_NAME}"
CAPABILITIES_KEY = rf"{APP_ROOT_KEY}\Capabilities"
APP_USER_MODEL_ID = f"{APP_NAME}.Client.1"
IPC_SOCKET_PREFIX = f"{APP_NAME}-ipc"

# --- BitTorrent identity -------------------------------------------------------
# Two-letter client id baked into the peer id. "LT" is libtorrent's own, which
# is widely recognised by trackers -- an unknown id gets rejected by some.
PEER_ID_PREFIX = "LT"

def data_dir() -> Path:
    """Root of everything the app persists."""
    override = os.environ.get("TORRENTAPP_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / APP_NAME


def resume_dir() -> Path:
    return data_dir() / "resume"


def torrents_dir() -> Path:
    return data_dir() / "torrents"


def logs_dir() -> Path:
    return data_dir() / "logs"


def config_file() -> Path:
    return data_dir() / "settings.json"


def rules_file() -> Path:
    return data_dir() / "rules.json"


def session_state_file() -> Path:
    return data_dir() / "session.state"


def ensure_dirs() -> None:
    for path in (data_dir(), resume_dir(), torrents_dir(), logs_dir()):
        path.mkdir(parents=True, exist_ok=True)


def project_root() -> Path:
    """Repository root -- src/torrentapp/constants.py -> up three."""
    return Path(__file__).resolve().parents[2]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable_path() -> Path:
    """The .exe when packaged, else the interpreter running us."""
    return Path(sys.executable)


def launch_command(argument: str = '"%1"') -> str:
    """Full command line the Windows shell should invoke to open ``argument``.

    Works both frozen (a single .exe) and from source, so shell registration can
    be exercised during development without building first.
    """
    if is_frozen():
        return f'"{executable_path()}" {argument}'.strip()
    launcher = project_root() / "run.py"
    # pythonw keeps a console window from flashing up on every file open.
    windowed = executable_path().with_name("pythonw.exe")
    interpreter = windowed if windowed.exists() else executable_path()
    return f'"{interpreter}" "{launcher}" {argument}'.strip()


def resources_dir() -> Path:
    """Bundled resources -- PyInstaller unpacks them to _MEIPASS at runtime."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "resources"
    return project_root() / "resources"


def icon_path() -> Path:
    return resources_dir() / "icon.ico"


def icon_resource() -> str:
    """Value for the registry ``DefaultIcon`` key."""
    if is_frozen():
        return f"{executable_path()},0"
    return str(icon_path())
