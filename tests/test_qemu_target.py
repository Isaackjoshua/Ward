"""``QemuTarget`` against a monitor that behaves like QEMU.

No VM boots here. These tests pin the things that are easy to get wrong and
expensive to notice: that a screenshot is really the framebuffer, that keys
are pressed and released together, and that ``exec`` stays impossible.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from ward.errors import CapabilityError
from ward.png import encode_rgb
from ward.qemu_target import QemuTarget, _png_size, wait_for_change
from ward.target import Target
from ward.types import Mode, OsFamily, PowerAction, Tier, Transport

GREETING = {"QMP": {"version": {"qemu": {"major": 8}}, "capabilities": []}}


class ScriptedQemu:
    """A QMP monitor that answers like QEMU, including ``screendump``."""

    def __init__(
        self, path: Path, *, png: bool = True, frames: list[bytes] | None = None
    ):
        self.path = path
        self.png = png
        self.frames = frames or []
        self.commands: list[dict] = []
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(path))
        self._server.listen(1)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _screendump(self, arguments: dict) -> dict:
        if arguments.get("format") == "png" and not self.png:
            return {"error": {"class": "GenericError", "desc": "Invalid parameter"}}
        frame = self.frames.pop(0) if self.frames else _solid_frame(4, 2, (1, 2, 3))
        Path(arguments["filename"]).write_bytes(frame)
        return {"return": {}}

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
                    message = json.loads(line)
                    self.commands.append(message)
                    if message["execute"] == "screendump":
                        reply = self._screendump(message["arguments"])
                    else:
                        reply = {"return": {}}
                    connection.sendall(json.dumps(reply).encode() + b"\n")

    def close(self) -> None:
        self._server.close()

    def sent(self, command: str) -> list[dict]:
        return [m for m in self.commands if m["execute"] == command]


def _solid_frame(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    return encode_rgb(width, height, bytes(rgb) * width * height)


def _ppm(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    return f"P6\n{width} {height}\n255\n".encode() + bytes(rgb) * width * height


@pytest.fixture
def monitor(tmp_path: Path):
    server = ScriptedQemu(tmp_path / "qmp.sock")
    yield server
    server.close()


def attach(server: ScriptedQemu, **kwargs) -> QemuTarget:
    return QemuTarget.attach(server.path, timeout=2.0, **kwargs)


def test_a_qemu_target_is_a_target(monitor: ScriptedQemu) -> None:
    """The protocol is the whole point: nothing above here may care."""
    assert isinstance(attach(monitor), Target)


def test_describe_reports_qemu_and_the_mode_it_was_told(monitor: ScriptedQemu) -> None:
    target = attach(monitor, name="patient", mode=Mode.FIRMWARE)
    description = target.describe()
    assert description.transport is Transport.QEMU
    assert description.mode is Mode.FIRMWARE
    assert description.name == "patient"
    assert description.os_family is OsFamily.UNKNOWN


def test_a_qemu_target_will_not_run_commands(monitor: ScriptedQemu) -> None:
    """It has a keyboard, not a shell. Pretending otherwise is the bug."""
    target = attach(monitor)
    assert target.capabilities().can_exec is False
    with pytest.raises(CapabilityError, match="keyboard and screen"):
        target.exec("ls")


def test_it_accepts_destroy_because_a_power_button_destroys(
    monitor: ScriptedQemu,
) -> None:
    assert attach(monitor).capabilities().max_tier is Tier.DESTROY


def test_screenshot_returns_the_framebuffer_as_png(monitor: ScriptedQemu) -> None:
    monitor.frames = [_solid_frame(8, 4, (10, 20, 30))]
    image = attach(monitor).screenshot()
    assert (image.width, image.height) == (8, 4)
    assert image.png.startswith(b"\x89PNG\r\n\x1a\n")
    assert _png_size(image.png) == (8, 4)


def test_an_old_qemu_that_only_writes_ppm_still_works(tmp_path: Path) -> None:
    """Ward has to work on whatever QEMU the machine in front of it has."""
    server = ScriptedQemu(tmp_path / "qmp.sock", png=False)
    try:
        server.frames = [_ppm(3, 2, (200, 100, 50))]
        image = attach(server).screenshot()
    finally:
        server.close()
    assert (image.width, image.height) == (3, 2)
    assert image.png.startswith(b"\x89PNG\r\n\x1a\n")
    # It asked for PNG, was refused, and fell back rather than giving up.
    formats = [m["arguments"].get("format") for m in server.sent("screendump")]
    assert formats == ["png", None]


def test_typing_sends_one_keystroke_per_character(monitor: ScriptedQemu) -> None:
    attach(monitor).type_text("Hi!")
    keys = [
        [key["data"] for key in message["arguments"]["keys"]]
        for message in monitor.sent("send-key")
    ]
    assert keys == [["shift", "h"], ["i"], ["shift", "1"]]


def test_keys_are_held_long_enough_for_firmware_to_notice(
    monitor: ScriptedQemu,
) -> None:
    """A boot menu polls the keyboard slowly. A press too short never happened."""
    attach(monitor).press("ctrl-alt-f2")
    message = monitor.sent("send-key")[0]
    assert message["arguments"]["hold-time"] >= 50
    assert [k["data"] for k in message["arguments"]["keys"]] == ["ctrl", "alt", "f2"]


def test_press_accepts_a_list_as_the_protocol_says(monitor: ScriptedQemu) -> None:
    attach(monitor).press(["ctrl", "alt", "delete"])
    keys = [k["data"] for k in monitor.sent("send-key")[0]["arguments"]["keys"]]
    assert keys == ["ctrl", "alt", "delete"]


def test_reset_is_a_reset_not_a_polite_request(monitor: ScriptedQemu) -> None:
    attach(monitor).power(PowerAction.RESET)
    assert monitor.sent("system_reset")
    # Never the ACPI request: half these machines are too broken to answer it.
    assert not monitor.sent("system_powerdown")


def test_power_off_quits_the_machine(monitor: ScriptedQemu) -> None:
    attach(monitor).power(PowerAction.OFF)
    assert monitor.sent("quit")


def test_wait_for_change_notices_a_new_screen(monitor: ScriptedQemu) -> None:
    monitor.frames = [
        _solid_frame(2, 2, (0, 0, 0)),
        _solid_frame(2, 2, (0, 0, 0)),
        _solid_frame(2, 2, (255, 0, 0)),
    ]
    changed, image, waited = wait_for_change(attach(monitor), timeout=5.0, poll=0.01)
    assert changed is True
    assert waited < 5.0
    assert image.sha256() == image.sha256()


def test_wait_for_change_gives_up_on_a_frozen_screen(monitor: ScriptedQemu) -> None:
    """A hung machine must produce an answer, not an indefinite wait."""
    monitor.frames = []  # every frame identical
    changed, _, waited = wait_for_change(attach(monitor), timeout=0.3, poll=0.05)
    assert changed is False
    assert waited < 2.0
