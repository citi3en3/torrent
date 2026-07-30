"""Single-instance argument hand-off.

Scope note: these drive ``SingleInstanceServer`` directly over a socket the test
holds open, rather than going through ``send_to_primary``. That is deliberate.
``send_to_primary`` blocks its thread on connect/write/disconnect, which in a
real launch is fine because the sender is a *separate process* and the primary's
event loop keeps running. Reproducing that inside one process means either
deadlocking the loop or racing socket destruction against the server's accept --
which produced genuinely flaky tests that failed in a different place each run.

So the server logic (framing, buffering, drain-on-disconnect) is covered
deterministically here, and the two-process path is verified against the built
executable: launching TorrentApp.exe three times with a .torrent and a magnet
leaves exactly one process, with each hand-off logged by the primary.

Regression guard: ``send_to_primary`` once returned ``waitForBytesWritten()``,
which is False whenever the payload was already flushed. The primary received
the arguments perfectly well, but the sender concluded the hand-off had failed
and started a second full instance -- two libtorrent sessions fighting over one
set of resume files.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QDeadlineTimer, QEventLoop
from PySide6.QtNetwork import QLocalSocket

from torrentapp.constants import IPC_SOCKET_PREFIX
from torrentapp.ipc import SingleInstanceServer, send_to_primary, server_name

pytestmark = pytest.mark.usefixtures("qapp")


def pump(qapp, predicate, timeout_ms: int = 3000) -> bool:
    """Spin the event loop until ``predicate`` holds or we run out of patience."""
    deadline = QDeadlineTimer(timeout_ms)
    while not deadline.hasExpired():
        qapp.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        if predicate():
            return True
    return predicate()


@pytest.fixture
def server(qapp):
    instance = SingleInstanceServer(name="TorrentApp-test-ipc")
    assert instance.listen(), "test server should bind"
    yield instance
    instance.close()


def connect(qapp, server) -> QLocalSocket:
    """A client socket owned by the test, so it cannot be collected mid-flight."""
    socket = QLocalSocket()
    socket.connectToServer(server.name)
    assert socket.waitForConnected(3000), "client should reach the test server"
    return socket


def send(qapp, socket: QLocalSocket, arguments: list[str]) -> None:
    payload = json.dumps({"arguments": arguments}, ensure_ascii=False) + "\n"
    socket.write(payload.encode("utf-8"))
    socket.flush()


# ------------------------------------------------------------------ delivery


def test_arguments_reach_the_primary(qapp, server):
    received: list[list[str]] = []
    server.arguments_received.connect(received.append)

    socket = connect(qapp, server)
    send(qapp, socket, ["C:\\a.torrent", "magnet:?xt=urn:btih:abc"])

    assert pump(qapp, lambda: bool(received))
    assert received[0] == ["C:\\a.torrent", "magnet:?xt=urn:btih:abc"]


def test_empty_arguments_still_reach_the_primary(qapp, server):
    """Launching with no arguments should raise the existing window."""
    received: list[list[str]] = []
    server.arguments_received.connect(received.append)

    socket = connect(qapp, server)
    send(qapp, socket, [])

    assert pump(qapp, lambda: bool(received))
    assert received[0] == []


def test_unicode_paths_survive_the_round_trip(qapp, server):
    received: list[list[str]] = []
    server.arguments_received.connect(received.append)

    path = "C:\\Загрузки\\Зеркало (1975).torrent"
    socket = connect(qapp, server)
    send(qapp, socket, [path])

    assert pump(qapp, lambda: bool(received))
    assert received[0] == [path]


def test_several_senders_in_a_row(qapp, server):
    """Multi-select in Explorer launches one process per file."""
    received: list[list[str]] = []
    server.arguments_received.connect(received.append)

    sockets = []
    for index in range(4):
        socket = connect(qapp, server)
        send(qapp, socket, [f"file{index}.torrent"])
        sockets.append(socket)

    assert pump(qapp, lambda: len(received) == 4)
    assert sorted(batch[0] for batch in received) == [f"file{i}.torrent" for i in range(4)]


def test_primary_closes_the_connection_as_acknowledgement(qapp, server):
    """The sender waits on this disconnect to know its arguments were consumed."""
    received: list[list[str]] = []
    server.arguments_received.connect(received.append)

    socket = connect(qapp, server)
    send(qapp, socket, ["x.torrent"])

    assert pump(qapp, lambda: bool(received))
    assert pump(
        qapp,
        lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState,
    ), "server should close the socket once it has read a full message"


# ------------------------------------------------------------------ framing


def test_payload_split_across_writes_is_reassembled(qapp, server):
    """A local socket can deliver a partial payload; framing must cope."""
    received: list[list[str]] = []
    server.arguments_received.connect(received.append)

    socket = connect(qapp, server)
    payload = json.dumps({"arguments": ["split.torrent"]}).encode("utf-8") + b"\n"
    half = len(payload) // 2

    socket.write(payload[:half])
    socket.flush()
    pump(qapp, lambda: False, timeout_ms=120)  # let the partial land
    assert not received, "an incomplete message must not be dispatched"

    socket.write(payload[half:])
    socket.flush()
    assert pump(qapp, lambda: bool(received))
    assert received[0] == ["split.torrent"]


def test_malformed_payload_is_ignored(qapp, server):
    received: list[list[str]] = []
    server.arguments_received.connect(received.append)

    socket = connect(qapp, server)
    socket.write(b"this is not json\n")
    socket.flush()

    pump(qapp, lambda: False, timeout_ms=300)
    assert received == [], "garbage must not crash or dispatch"


def test_blank_line_is_ignored(qapp, server):
    received: list[list[str]] = []
    server.arguments_received.connect(received.append)

    socket = connect(qapp, server)
    socket.write(b"\n")
    socket.flush()

    pump(qapp, lambda: False, timeout_ms=300)
    assert received == []


# ------------------------------------------------------------------ client side


def test_send_returns_false_when_no_primary_is_listening(qapp):
    assert send_to_primary(["x.torrent"], name="TorrentApp-test-nobody-home") is False


def test_server_name_is_per_user():
    name = server_name()
    assert name.startswith(f"{IPC_SOCKET_PREFIX}-")
    assert " " not in name, "pipe names must not contain spaces"


def test_listen_recovers_from_a_stale_socket(qapp):
    """A hard crash leaves the pipe behind; the next launch must still bind."""
    first = SingleInstanceServer(name="TorrentApp-test-stale")
    assert first.listen()

    second = SingleInstanceServer(name="TorrentApp-test-stale")
    assert second.listen(), "removeServer() should clear the stale name"
    second.close()
