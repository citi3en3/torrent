"""Small formatting helpers shared by the GUI and the headless smoke scripts."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def format_bytes(value: float | int | None) -> str:
    if value is None or value < 0:
        return "-"
    size = float(value)
    for unit in _UNITS:
        if size < 1024.0 or unit == _UNITS[-1]:
            precision = 0 if unit == "B" else (1 if size >= 100 else 2)
            return f"{size:.{precision}f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PiB"


def format_rate(bytes_per_second: float | int | None) -> str:
    if not bytes_per_second:
        return "-"
    return f"{format_bytes(bytes_per_second)}/s"


def format_eta(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "∞"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    if days > 99:
        return "∞"
    return f"{days}d {hours}h"


def format_ratio(uploaded: int, downloaded: int) -> str:
    if downloaded <= 0:
        return "∞" if uploaded > 0 else "0.00"
    return f"{uploaded / downloaded:.2f}"


def free_space(path: str | os.PathLike[str]) -> int | None:
    """Bytes free on the volume holding ``path``.

    The target folder usually does not exist yet when the add dialog asks, so
    walk up to the nearest existing ancestor (ultimately the drive root).
    """
    candidate = Path(path)
    while True:
        try:
            return shutil.disk_usage(candidate).free
        except (OSError, ValueError):
            if candidate.parent == candidate:
                return None
            candidate = candidate.parent
