"""A small QMP client.

QMP is QEMU's control channel: newline-delimited JSON over a socket. Ward
uses it for everything a person sitting at the machine could do — look at the
screen, press keys, hit the power button.

Every call has a hard timeout. A hung target must raise, never block: an
agent waiting forever on a dead machine is worse than one that is told the
machine is dead.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any

from ward.errors import TargetTimeout, TargetUnavailable, WardError

DEFAULT_TIMEOUT = 10.0


class QmpError(WardError):
    """QEMU accepted the command and refused it."""


class QmpClient:
    """One connection to one QEMU monitor socket.

    Not thread-safe, and deliberately so: the whole point of Ward is that
    exactly one thing is at the keyboard at a time.
    """

    def __init__(self, socket_path: str | Path, *, timeout: float = DEFAULT_TIMEOUT):
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._buffer = b""

    # -- connection ----------------------------------------------------

    def connect(self, *, wait: float = 0.0) -> None:
        """Open the socket and finish QMP's capability handshake.

        ``wait`` gives QEMU time to create the socket after being launched;
        polling for it beats sleeping a fixed guess.
        """
        deadline = time.monotonic() + wait
        while True:
            try:
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.settimeout(self.timeout)
                connection.connect(str(self.socket_path))
                break
            except OSError as exc:
                connection.close()
                if time.monotonic() >= deadline:
                    raise TargetUnavailable(
                        f"could not connect to the QEMU monitor at "
                        f"{self.socket_path}: {exc}"
                    ) from exc
                time.sleep(0.1)

        self._socket = connection
        self._buffer = b""
        greeting = self._read_message()
        if "QMP" not in greeting:
            raise TargetUnavailable(
                f"{self.socket_path} answered but did not greet as QMP; "
                "is something else listening on that socket?"
            )
        self.execute("qmp_capabilities")

    def close(self) -> None:
        """Close the socket. Safe to call twice."""
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def __enter__(self) -> QmpClient:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- messages ------------------------------------------------------

    def _read_message(self) -> dict[str, Any]:
        """Read one JSON object, raising if the target stops answering."""
        if self._socket is None:
            raise TargetUnavailable("not connected to a QEMU monitor")
        while b"\n" not in self._buffer:
            try:
                chunk = self._socket.recv(65536)
            except TimeoutError as exc:
                raise TargetTimeout(
                    f"the QEMU monitor at {self.socket_path} did not answer "
                    f"within {self.timeout}s"
                ) from exc
            except OSError as exc:
                raise TargetUnavailable(
                    f"lost the QEMU monitor at {self.socket_path}: {exc}"
                ) from exc
            if not chunk:
                raise TargetUnavailable(
                    f"the QEMU monitor at {self.socket_path} closed the "
                    "connection; the VM has probably exited"
                )
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Run one QMP command and return its ``return`` value.

        Asynchronous events arriving mid-call are skipped, not dropped on the
        floor by accident: QMP interleaves them with replies and only a reply
        answers the command we sent.
        """
        if self._socket is None:
            raise TargetUnavailable("not connected to a QEMU monitor")

        message: dict[str, Any] = {"execute": command}
        if arguments:
            message["arguments"] = arguments

        previous = self._socket.gettimeout()
        if timeout is not None:
            self._socket.settimeout(timeout)
        try:
            self._socket.sendall(json.dumps(message).encode("utf-8") + b"\n")
            while True:
                reply = self._read_message()
                if "error" in reply:
                    detail = reply["error"].get("desc", reply["error"])
                    raise QmpError(f"QEMU refused {command}: {detail}")
                if "return" in reply:
                    return reply["return"]
                # An event. Keep reading; our answer is still coming.
        finally:
            if self._socket is not None:
                self._socket.settimeout(previous)

    def human_monitor(self, command_line: str, *, timeout: float | None = None) -> str:
        """Run a command in QEMU's human monitor and return its text output.

        A few things Ward needs have no QMP command — ``sendkey`` with a hold
        time is the main one — and this is the supported way to reach them.
        """
        result = self.execute(
            "human-monitor-command",
            {"command-line": command_line},
            timeout=timeout,
        )
        return result if isinstance(result, str) else ""
