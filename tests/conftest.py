"""Shared pytest setup.

Qt is forced onto the offscreen platform so widget tests run headless (in CI, or
over SSH, or while the real app is open) without opening windows. The app's data
directory is redirected into a temp folder so tests can never touch the real
%APPDATA% state.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_DATA_DIR = Path(tempfile.mkdtemp(prefix="torrentapp-tests-"))
os.environ["TORRENTAPP_DATA_DIR"] = str(_DATA_DIR)

import pytest  # noqa: E402


# Held at module scope on purpose. If the only reference lives in a fixture, the
# QApplication is collected while the interpreter is already tearing down, and
# Qt occasionally crashes the process on the way out -- an intermittent hard
# crash with no failing test, which is a miserable thing to debug later.
_QAPP = None


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session -- Qt allows only one."""
    global _QAPP
    from PySide6.QtWidgets import QApplication

    if _QAPP is None:
        _QAPP = QApplication.instance() or QApplication([])
    return _QAPP


@pytest.fixture
def config(tmp_path):
    from torrentapp.config import Config

    cfg = Config()
    cfg.download_root = str(tmp_path / "downloads")
    return cfg
