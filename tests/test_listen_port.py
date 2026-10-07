"""Listen-port selection and the listening watchdog.

Windows (Hyper-V / WSL / WinNAT) reserves random blocks of the dynamic port
range at boot. Binding a reserved port fails with "access denied", which
libtorrent does not fall back from -- the session ends up with no sockets, so
no trackers, no DHT and no peers. The engine must notice and move elsewhere.
"""

from __future__ import annotations

import socket
import time
from pathlib import Path

import pytest

from torrentapp.engine import (
    ListenPortChanged,
    NetworkStatus,
    ResumeStore,
    TorrentEngine,
)
from torrentapp.engine.session import (
    DYNAMIC_PORT_START,
    SAFE_PORT_RANGE,
    choose_listen_port,
    port_is_bindable,
)


def free_safe_port() -> int:
    return choose_listen_port(DYNAMIC_PORT_START)  # forces a random safe pick


def test_free_safe_port_is_kept() -> None:
    port = free_safe_port()
    assert port_is_bindable(port)
    assert choose_listen_port(port) == port


def test_unbindable_port_is_replaced() -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("0.0.0.0", 0))
    blocker.listen()
    taken = blocker.getsockname()[1]
    try:
        assert not port_is_bindable(taken)
        chosen = choose_listen_port(taken)
        assert chosen != taken
        assert SAFE_PORT_RANGE[0] <= chosen <= SAFE_PORT_RANGE[1]
        assert port_is_bindable(chosen)
    finally:
        blocker.close()


def test_dynamic_range_port_is_moved_even_when_free_today() -> None:
    """It may bind now and be reserved by Windows after the next reboot."""
    chosen = choose_listen_port(DYNAMIC_PORT_START + 1000)
    assert SAFE_PORT_RANGE[0] <= chosen <= SAFE_PORT_RANGE[1]


def test_avoided_port_is_not_returned() -> None:
    port = free_safe_port()
    assert choose_listen_port(port, avoid={port}) != port


def test_default_port_is_outside_the_dynamic_range() -> None:
    from torrentapp.config import DEFAULT_LISTEN_PORT

    assert DEFAULT_LISTEN_PORT < DYNAMIC_PORT_START


# --------------------------------------------------------------------------- engine


@pytest.fixture
def engine(config, tmp_path: Path):
    config.enable_dht = False
    config.enable_lsd = False
    config.enable_upnp = False
    config.enable_natpmp = False
    config.listen_port = free_safe_port()
    store = ResumeStore(
        tmp_path / "resume", tmp_path / "torrents", tmp_path / "session.state"
    )
    eng = TorrentEngine(config, store)
    eng.start()
    yield eng
    eng.stop(timeout=1.0)


def drain(engine: TorrentEngine, kind: type, timeout: float = 3.0) -> list:
    deadline = time.monotonic() + timeout
    found: list = []
    while not found and time.monotonic() < deadline:
        engine.session.wait_for_alert(100)
        found = [e for e in engine.poll() if isinstance(e, kind)]
    return found


def test_startup_on_bad_port_reports_the_change(config, tmp_path: Path) -> None:
    config.enable_dht = False
    config.enable_upnp = False
    config.enable_natpmp = False
    config.listen_port = DYNAMIC_PORT_START + 1000
    store = ResumeStore(
        tmp_path / "resume", tmp_path / "torrents", tmp_path / "session.state"
    )
    eng = TorrentEngine(config, store)
    eng.start()
    try:
        changed = [e for e in eng.poll() if isinstance(e, ListenPortChanged)]
        assert changed and changed[0].port == config.listen_port
        assert config.listen_port < DYNAMIC_PORT_START
    finally:
        eng.stop(timeout=1.0)


def test_watchdog_rebinds_when_all_sockets_are_lost(engine: TorrentEngine) -> None:
    assert engine.session.is_listening()

    # Knock the session off the network the way a Windows port reservation
    # does: point it at a port it cannot bind, with no system fallback.
    blocker_tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Without this, Windows lets libtorrent's SO_REUSEADDR share the port.
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        blocker_tcp.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    blocker_tcp.bind(("0.0.0.0", 0))
    blocker_tcp.listen()
    blocked = blocker_tcp.getsockname()[1]
    try:
        engine.session.apply_settings(
            {
                "listen_interfaces": f"0.0.0.0:{blocked}",
                "listen_system_port_fallback": False,
            }
        )
        time.sleep(0.5)
        assert not engine.session.is_listening()

        assert engine.check_listening() is True
        time.sleep(0.5)
        assert engine.session.is_listening()
        assert engine.config.listen_port != blocked
        assert drain(engine, ListenPortChanged)
    finally:
        blocker_tcp.close()


def test_healthy_session_is_left_alone(engine: TorrentEngine) -> None:
    before = engine.config.listen_port
    assert engine.check_listening() is False
    assert engine.config.listen_port == before


def test_tick_publishes_network_status(engine: TorrentEngine) -> None:
    engine.tick()
    statuses = drain(engine, NetworkStatus)
    assert statuses
    status = statuses[-1]
    assert status.listening is True
    assert status.port == engine.config.listen_port
    assert status.dht_nodes == 0
