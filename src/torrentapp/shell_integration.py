"""Windows file-association and protocol registration.

Everything here writes under ``HKEY_CURRENT_USER`` only, so registration never
needs administrator rights and never touches other accounts on the machine.

An honest note about ``.torrent``: Windows 10 and 11 hash-sign the ``UserChoice``
key that decides the *default* handler for an extension. That signature is
deliberately not reproducible from user code, so **no application can silently
make itself the default** -- not this one, not qBittorrent, not anything. What
registration here does achieve:

* ``magnet:`` links -- claimed outright, because nothing else on this machine
  currently registers the protocol, and HKCU beats HKLM in the lookup order.
* ``.torrent`` -- we appear in "Open with" and as a proper entry in
  Settings -> Default apps, so making it the default is two clicks, once.

``is_default_for_torrent()`` reports which state we are in, so the UI can offer
that one-time nudge rather than pretending the association already happened.
"""

from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass

from .constants import (
    APP_DESCRIPTION,
    APP_DISPLAY_NAME,
    APP_ROOT_KEY,
    CAPABILITIES_KEY,
    PROGID,
    REGISTERED_APP,
    icon_resource,
    launch_command,
)

try:  # pragma: no cover - exercised only on Windows
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

CLASSES = r"Software\Classes"
TORRENT_EXT = ".torrent"
MAGNET_SCHEME = "magnet"

_USER_CHOICE_FILE = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
    r"\.torrent\UserChoice"
)
_USER_CHOICE_URL = (
    r"Software\Microsoft\Windows\CurrentVersion\Shell\Associations"
    r"\UrlAssociations\magnet\UserChoice"
)

SHCNE_ASSOCCHANGED = 0x08000000
SHCNF_IDLIST = 0x0000


@dataclass(frozen=True)
class RegistryWrite:
    """One planned registry value. ``name`` empty means the key's default."""

    key: str
    name: str
    value: str

    def __str__(self) -> str:
        target = f"HKCU\\{self.key}"
        label = self.name or "(Default)"
        return f"{target} :: {label} = {self.value}"


def planned_writes() -> list[RegistryWrite]:
    """Every value ``register()`` would write. Pure -- safe to print or test."""
    command = launch_command()
    icon = icon_resource()

    return [
        # --- the ProgID that describes how we open a torrent ------------------
        RegistryWrite(rf"{CLASSES}\{PROGID}", "", f"{APP_DISPLAY_NAME} Torrent File"),
        RegistryWrite(rf"{CLASSES}\{PROGID}", "FriendlyTypeName", "BitTorrent Torrent File"),
        RegistryWrite(rf"{CLASSES}\{PROGID}\DefaultIcon", "", icon),
        RegistryWrite(rf"{CLASSES}\{PROGID}\shell\open\command", "", command),
        # --- offer ourselves in the .torrent "Open with" list ------------------
        RegistryWrite(rf"{CLASSES}\{TORRENT_EXT}\OpenWithProgids", PROGID, ""),
        # --- claim the magnet: protocol ---------------------------------------
        RegistryWrite(rf"{CLASSES}\{MAGNET_SCHEME}", "", "URL:BitTorrent Magnet URI"),
        RegistryWrite(rf"{CLASSES}\{MAGNET_SCHEME}", "URL Protocol", ""),
        RegistryWrite(rf"{CLASSES}\{MAGNET_SCHEME}\DefaultIcon", "", icon),
        RegistryWrite(rf"{CLASSES}\{MAGNET_SCHEME}\shell\open\command", "", command),
        # --- appear in Settings -> Default apps as a real application ----------
        RegistryWrite(CAPABILITIES_KEY, "ApplicationName", APP_DISPLAY_NAME),
        RegistryWrite(CAPABILITIES_KEY, "ApplicationDescription", APP_DESCRIPTION),
        RegistryWrite(CAPABILITIES_KEY, "ApplicationIcon", icon),
        RegistryWrite(rf"{CAPABILITIES_KEY}\FileAssociations", TORRENT_EXT, PROGID),
        RegistryWrite(rf"{CAPABILITIES_KEY}\URLAssociations", MAGNET_SCHEME, PROGID),
        RegistryWrite(r"Software\RegisteredApplications", REGISTERED_APP, CAPABILITIES_KEY),
    ]


# --------------------------------------------------------------------- registry io


def _require_winreg() -> None:
    if winreg is None:  # pragma: no cover
        raise RuntimeError("shell registration is only supported on Windows")


def _write(entry: RegistryWrite) -> None:
    _require_winreg()
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, entry.key, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, entry.name, 0, winreg.REG_SZ, entry.value)


def _read(key_path: str, name: str = "") -> str | None:
    if winreg is None:  # pragma: no cover
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except OSError:
        return None


def _delete_tree(key_path: str) -> None:
    """Delete a key and everything under it, tolerating absence."""
    if winreg is None:  # pragma: no cover
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(f"{key_path}\\{child}")
    except FileNotFoundError:
        return
    except OSError as exc:
        log.debug("could not enumerate %s: %s", key_path, exc)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
    except OSError as exc:
        log.debug("could not delete %s: %s", key_path, exc)


def _delete_value(key_path: str, name: str) -> None:
    if winreg is None:  # pragma: no cover
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


# ------------------------------------------------------------------------- public


def register(dry_run: bool = False) -> list[RegistryWrite]:
    """Write every association. Returns what was (or would be) written."""
    entries = planned_writes()
    if dry_run:
        return entries
    for entry in entries:
        try:
            _write(entry)
        except OSError as exc:
            log.warning("could not write %s: %s", entry, exc)
    notify_shell()
    log.info("shell registration complete (%d values)", len(entries))
    return entries


def unregister() -> None:
    """Remove everything ``register()`` created."""
    _delete_value(rf"{CLASSES}\{TORRENT_EXT}\OpenWithProgids", PROGID)
    _delete_value(r"Software\RegisteredApplications", REGISTERED_APP)
    for key_path in (
        rf"{CLASSES}\{PROGID}",
        rf"{CLASSES}\{MAGNET_SCHEME}",
        APP_ROOT_KEY,
    ):
        _delete_tree(key_path)
    notify_shell()
    log.info("shell registration removed")


def notify_shell() -> None:
    """Tell Explorer associations changed, so icons refresh without a reboot."""
    try:
        ctypes.windll.shell32.SHChangeNotify(  # type: ignore[attr-defined]
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None
        )
    except (AttributeError, OSError) as exc:  # pragma: no cover
        log.debug("SHChangeNotify unavailable: %s", exc)


# ------------------------------------------------------------------------ queries


def is_registered() -> bool:
    """Have we written our ProgID at all?"""
    return _read(rf"{CLASSES}\{PROGID}\shell\open\command") is not None


def is_default_for_torrent() -> bool:
    """Are we the *default* handler for .torrent?

    Reads the protected UserChoice key. We can read it, we simply cannot forge
    the accompanying hash to write it.
    """
    return _read(_USER_CHOICE_FILE, "ProgId") == PROGID


def torrent_default_progid() -> str | None:
    """Whatever currently owns .torrent -- useful for explaining the situation."""
    return _read(_USER_CHOICE_FILE, "ProgId")


def is_magnet_handler() -> bool:
    """Will a magnet: link open us?

    An explicit UserChoice wins if present; otherwise our HKCU\\Software\\Classes
    entry is consulted before any machine-wide registration.
    """
    chosen = _read(_USER_CHOICE_URL, "ProgId")
    if chosen is not None:
        return chosen == PROGID
    command = _read(rf"{CLASSES}\{MAGNET_SCHEME}\shell\open\command")
    return bool(command) and command == launch_command()


def open_default_apps_settings() -> bool:
    """Deep-link the Settings page where the user can make us the default."""
    for target in (
        f"ms-settings:defaultapps?registeredAppUser={REGISTERED_APP}",
        "ms-settings:defaultapps",
    ):
        try:
            os.startfile(target)  # noqa: S606 - a documented ms-settings URI
            return True
        except OSError as exc:
            log.debug("could not open %s: %s", target, exc)
    return False
