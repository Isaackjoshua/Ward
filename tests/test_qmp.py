"""The QMP client, against a socket that behaves like QEMU and one that hangs.

The hanging case matters most. Ward exists to work on machines that have
stopped answering, so the code that talks to them must always come back.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from ward.errors import TargetTimeout, TargetUnavailable
from ward.qmp import QmpClient, QmpError

GREETING = {"QMP": {"version": {"qemu": {"major": 8}}, "capabilities": []}}


class FakeQemu:
    """A unix socket that answers QMP the way QEMU does."""

    def __init__(self, path: Path, *, replies: list[dict] | None = None, hang=False):
        self.path = path
        self.replies = replies or []
        self.hang = hang
        self.received: list[dict] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._server.accept()
        except OSError:
            return
        with connection:
            connection.sendall(json.dumps(GREETING).encode() + b"\n")
            buffer = b""
            while True:
                try:
                    chunk = connection.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    self.received.append(json.loads(line))
                    if self.hang and self.received[-1]["execute"] != "qmp_capabilities":
                        return  # answer nothing, ever
                    # QEMU may emit any number of events before the reply that
                    # actually answers the command, so send queued events too.
                    while True:
                        reply = self.replies.pop(0) if self.replies else {"return": {}}
                        connection.sendall(json.dumps(reply).encode() + b"\n")
                        if "event" not in reply:
                            break

    def close(self) -> None:
        self._server.close()


@pytest.fixture
def socket_path(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path / "qmp.sock"


def test_connect_negotiates_capabilities(socket_path: Path) -> None:
    server = FakeQemu(socket_path)
    try:
        with QmpClient(socket_path) as client:
            assert client.execute("query-status") == {}
    finally:
        server.close()
    assert server.received[0]["execute"] == "qmp_capabilities"


def test_arguments_travel_with_the_command(socket_path: Path) -> None:
    server = FakeQemu(socket_path)
    try:
        with QmpClient(socket_path) as client:
            client.execute("screendump", {"filename": "/tmp/x.ppm"})
    finally:
        server.close()
    assert server.received[-1] == {
        "execute": "screendump",
        "arguments": {"filename": "/tmp/x.ppm"},
    }


def test_events_arriving_mid_call_do_not_get_mistaken_for_the_answer(
    socket_path: Path,
) -> None:
    server = FakeQemu(
        socket_path,
        replies=[
            {"return": {}},  # the capabilities handshake
            {"event": "RESET", "timestamp": {"seconds": 1, "microseconds": 0}},
            {"return": {"status": "running"}},
        ],
    )
    try:
        with QmpClient(socket_path) as client:
            assert client.execute("query-status") == {"status": "running"}
    finally:
        server.close()


def test_a_refused_command_raises_with_qemus_own_words(socket_path: Path) -> None:
    server = FakeQemu(
        socket_path,
        replies=[
            {"return": {}},
            {"error": {"class": "GenericError", "desc": "Invalid parameter 'format'"}},
        ],
    )
    try:
        with (
            QmpClient(socket_path) as client,
            pytest.raises(QmpError, match="Invalid parameter"),
        ):
            client.execute("screendump", {"format": "png"})
    finally:
        server.close()


def test_a_target_that_stops_answering_raises_rather_than_blocking(
    socket_path: Path,
) -> None:
    """The whole point: a hung machine must not hang Ward."""
    server = FakeQemu(socket_path, hang=True)
    try:
        with (
            QmpClient(socket_path, timeout=0.5) as client,
            pytest.raises((TargetTimeout, TargetUnavailable)),
        ):
            client.execute("query-status")
    finally:
        server.close()


def test_a_missing_socket_says_so_immediately(tmp_path: Path) -> None:
    client = QmpClient(tmp_path / "nothing-here.sock")
    with pytest.raises(TargetUnavailable, match="could not connect"):
        client.connect()


def test_using_a_closed_client_is_a_clear_error(socket_path: Path) -> None:
    server = FakeQemu(socket_path)
    try:
        client = QmpClient(socket_path)
        client.connect()
        client.close()
        with pytest.raises(TargetUnavailable, match="not connected"):
            client.execute("query-status")
    finally:
        server.close()
