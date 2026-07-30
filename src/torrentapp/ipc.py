"""Single-instance enforcement and argument hand-off.

Selecting five .torrent files in Explorer and pressing Enter launches five
processes. Without this module that means five windows, five libtorrent
sessions, and five processes fighting over the same resume files.

Instead the first process to start becomes the primary and listens on a named
pipe; every later process forwards its arguments to the primary and exits
immediately. The primary raises its window and queues the incoming torrents.

Messages are newline-delimited JSON. The newline framing matters -- a local
socket can deliver a partial payload, so the reader buffers until it sees a
terminator rather than assuming one read is one message.
"""

from __future__ import annotations

import getpass
import json
import logging
import re

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .constants import IPC_SOCKET_PREFIX

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_MS = 500
WRITE_TIMEOUT_MS = 2000


def server_name() -> str:
    """Per-user pipe name, so two accounts can each run their own instance."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - getuser consults several env vars
        user = "default"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", user)
    return f"{IPC_SOCKET_PREFIX}-{safe}"


def send_to_primary(arguments: list[str], name: str | None = None) -> bool:
    """Hand ``arguments`` to an already-running instance.

    Returns True if a primary accepted them (so this process should exit).
    """
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
        return False

    payload = (json.dumps({"arguments": list(arguments)}, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    written = socket.write(payload)
    if written != len(payload):
        log.warning("short write handing off to the primary instance")
        socket.abort()
        return False

    # Success is "we connected and wrote every byte", NOT the return of
    # waitForBytesWritten -- that reports False when the data has already been
    # flushed, which would make a perfectly good handoff look like a failure and
    # start a second full instance.
    socket.flush()
    socket.waitForBytesWritten(WRITE_TIMEOUT_MS)

    # The primary closes the connection once it has read a complete message, so
    # waiting for the disconnect both confirms delivery and keeps this socket
    # alive until the payload has actually been consumed. Dropping it earlier
    # can abort the connection before the primary ever accepts it.
    socket.waitForDisconnected(WRITE_TIMEOUT_MS)

    log.info("handed %d argument(s) to the running instance", len(arguments))
    return True


class SingleInstanceServer(QObject):
    """Listens for arguments forwarded by secondary instances."""

    arguments_received = Signal(list)

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or server_name()
        self._server = QLocalServer(self)
        self._buffers: dict[QLocalSocket, bytes] = {}
        self._server.newConnection.connect(self._on_connection)

    @property
    def name(self) -> str:
        return self._name

    def listen(self) -> bool:
        # A hard crash leaves the pipe behind; clearing it stops the next launch
        # from failing to bind and silently losing every forwarded torrent.
        QLocalServer.removeServer(self._name)
        if not self._server.listen(self._name):
            log.warning("could not listen on %s: %s", self._name, self._server.errorString())
            return False
        log.info("listening for secondary instances on %s", self._name)
        return True

    def close(self) -> None:
        self._server.close()
        QLocalServer.removeServer(self._name)

    # ------------------------------------------------------------------ internals

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                break
            self._buffers[socket] = b""
            socket.readyRead.connect(lambda s=socket: self._on_ready_read(s))
            socket.disconnected.connect(lambda s=socket: self._on_disconnected(s))
            # A secondary instance writes its arguments and exits immediately, so
            # the payload is often already sitting in the pipe by the time we
            # accept -- and then readyRead never fires. Drain what is here now.
            if socket.bytesAvailable():
                self._on_ready_read(socket)

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        self._buffers[socket] = self._buffers.get(socket, b"") + bytes(socket.readAll())
        delivered = False
        while b"\n" in self._buffers[socket]:
            line, _, rest = self._buffers[socket].partition(b"\n")
            self._buffers[socket] = rest
            self._dispatch(line)
            delivered = True
        if delivered:
            # One message per connection, so closing here is the acknowledgement
            # the sender waits on before it exits.
            socket.disconnectFromServer()

    def _dispatch(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            message = json.loads(line.decode("utf-8"))
            arguments = [str(a) for a in message.get("arguments", [])]
        except (UnicodeDecodeError, ValueError, AttributeError) as exc:
            log.warning("ignoring malformed IPC message: %s", exc)
            return
        log.info("received %d argument(s) from a secondary instance", len(arguments))
        self.arguments_received.emit(arguments)

    def _on_disconnected(self, socket: QLocalSocket) -> None:
        # The peer hanging up does not mean we have read everything it sent:
        # data can still be buffered. Draining before discarding is what stops a
        # write-then-exit secondary from losing its arguments when the primary
        # was busy (showing a modal dialog, say) at the moment it connected.
        if socket.bytesAvailable():
            self._on_ready_read(socket)
        self._buffers.pop(socket, None)
        socket.deleteLater()
