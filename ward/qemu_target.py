"""``QemuTarget``: eyes, hands and a power button for a QEMU virtual machine.

This is the first real driver. Everything it does goes over QMP, QEMU's
control socket, which is the closest thing a VM has to a person standing next
to it: it can see the framebuffer and press keys whether or not the guest has
an operating system, a network, or drivers.

Nothing above this file knows it is talking to a VM. When the hardware driver
arrives in M6 it implements the same protocol and callers do not change.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from ward.errors import CapabilityError, TargetUnavailable
from ward.keymap import keys_for_text, parse_combo
from ward.png import encode_rgb
from ward.ppm import parse_ppm
from ward.qmp import QmpClient, QmpError
from ward.types import (
    BandwidthClass,
    Capabilities,
    ExecResult,
    Image,
    Mode,
    OsFamily,
    PowerAction,
    TargetDescription,
    Tier,
    Transport,
)

QEMU_SYSTEM = "qemu-system-x86_64"

#: How long one key press is held, in milliseconds. Firmware and boot loaders
#: poll the keyboard slowly; a press too short to survive a poll is a press
#: that never happened.
KEY_HOLD_MS = 60


def kvm_available() -> bool:
    """Whether hardware acceleration is usable on this host."""
    device = Path("/dev/kvm")
    try:
        with device.open("rb"):
            return True
    except OSError:
        return False


@dataclass
class QemuTarget:
    """A QEMU virtual machine, driven the way a person would drive it.

    Either launch one with :meth:`launch` or attach to a running one with
    :meth:`attach`. Both give back a plain ``Target``.
    """

    qmp_socket: Path
    name: str = "qemu"
    mode: Mode = Mode.LIVE
    os_family: OsFamily = OsFamily.UNKNOWN
    disk: Path | None = None
    memory_mb: int = 1024
    serial_log: Path | None = None
    journal_log: Path | None = None
    timeout: float = 15.0

    _process: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    _client: QmpClient | None = field(default=None, repr=False)
    _screendump_png: bool | None = field(default=None, repr=False)

    # -- lifecycle -----------------------------------------------------

    @classmethod
    def launch(
        cls,
        disk: Path,
        *,
        name: str = "qemu",
        mode: Mode = Mode.LIVE,
        os_family: OsFamily = OsFamily.UNKNOWN,
        memory_mb: int = 1024,
        serial_log: Path | None = None,
        journal_log: Path | None = None,
        run_dir: Path | None = None,
        timeout: float = 15.0,
        boot_timeout: float = 30.0,
    ) -> QemuTarget:
        """Start a VM on ``disk`` and return a target attached to it."""
        if shutil.which(QEMU_SYSTEM) is None:
            raise TargetUnavailable(
                f"{QEMU_SYSTEM} is not on PATH; there is no VM to drive"
            )
        directory = run_dir or Path(tempfile.mkdtemp(prefix="ward-qemu-"))
        directory.mkdir(parents=True, exist_ok=True)
        target = cls(
            qmp_socket=directory / "qmp.sock",
            name=name,
            mode=mode,
            os_family=os_family,
            disk=disk,
            memory_mb=memory_mb,
            serial_log=serial_log,
            journal_log=journal_log,
            timeout=timeout,
        )
        target._spawn(boot_timeout=boot_timeout)
        return target

    @classmethod
    def attach(
        cls,
        qmp_socket: Path,
        *,
        name: str = "qemu",
        mode: Mode = Mode.LIVE,
        os_family: OsFamily = OsFamily.UNKNOWN,
        timeout: float = 15.0,
    ) -> QemuTarget:
        """Attach to a VM someone else started."""
        target = cls(
            qmp_socket=qmp_socket,
            name=name,
            mode=mode,
            os_family=os_family,
            timeout=timeout,
        )
        target._connect(wait=timeout)
        return target

    def _argv(self) -> list[str]:
        """The QEMU command line for a patient.

        Note what is *not* here: no network. A patient is a machine we are
        fixing, not a service, and giving a broken machine a network is how
        you turn a broken machine into someone else's problem.
        """
        if self.disk is None:
            raise TargetUnavailable("cannot launch a VM without a disk")
        argv = [
            QEMU_SYSTEM,
            "-machine",
            "q35,accel=kvm" if kvm_available() else "q35",
            "-m",
            str(self.memory_mb),
            "-smp",
            "2",
            "-display",
            "none",
            "-vga",
            "std",
            "-nic",
            "none",
            "-qmp",
            f"unix:{self.qmp_socket},server,nowait",
            "-drive",
            f"file={self.disk},if=virtio,format=qcow2,cache=writeback",
        ]
        if self.serial_log is not None:
            self.serial_log.parent.mkdir(parents=True, exist_ok=True)
            argv += ["-serial", f"file:{self.serial_log}"]
        else:
            argv += ["-serial", "null"]
        # A second port, because a prepared lab patient copies its journal to
        # ttyS1. Without the device the copy fails and the guest logs about it
        # forever. Ward itself never reads this — it has eyes.
        if self.journal_log is not None:
            self.journal_log.parent.mkdir(parents=True, exist_ok=True)
            argv += ["-serial", f"file:{self.journal_log}"]
        else:
            argv += ["-serial", "null"]
        return argv

    def _spawn(self, *, boot_timeout: float) -> None:
        self.qmp_socket.parent.mkdir(parents=True, exist_ok=True)
        self.qmp_socket.unlink(missing_ok=True)
        self._process = subprocess.Popen(  # noqa: S603 - argv is built above
            self._argv(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            # Its own session, so the VM outlives the shell that started it.
            # Every CLI invocation is a separate process; the patient is not.
            start_new_session=True,
        )
        self._connect(wait=boot_timeout)

    def _connect(self, *, wait: float) -> None:
        client = QmpClient(self.qmp_socket, timeout=self.timeout)
        client.connect(wait=wait)
        self._client = client

    @property
    def client(self) -> QmpClient:
        """The live monitor connection, or a clear error if there is none."""
        if self._client is None:
            raise TargetUnavailable(
                f"{self.name} is not connected to a QEMU monitor; "
                "launch or attach first"
            )
        return self._client

    def is_running(self) -> bool:
        """Whether the VM process Ward launched is still alive.

        Always ``True`` for an attached VM: Ward did not start it and has no
        business claiming to know.
        """
        if self._process is None:
            return self._client is not None
        return self._process.poll() is None

    def close(self) -> None:
        """Drop the monitor connection, leaving the VM running."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def shutdown(self, *, timeout: float = 20.0) -> None:
        """Stop the VM, whether Ward launched it or attached to it.

        ``quit`` goes over the monitor, so this works from a later process
        that only has the socket — which is the normal case, because each CLI
        invocation is its own process.
        """
        if self._client is not None:
            try:
                self.client.execute("quit")
            except (QmpError, TargetUnavailable):
                # The VM is already gone. That is the outcome we wanted.
                pass
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        self.close()

    def __enter__(self) -> QemuTarget:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    # -- the Target protocol -------------------------------------------

    def describe(self) -> TargetDescription:
        """What this target is, including which world a repair happens in."""
        return TargetDescription(
            transport=Transport.QEMU,
            mode=self.mode,
            os_family=self.os_family,
            # A local socket to a local VM. Screenshots are cheap here in a
            # way they will not be over a capture dongle.
            bandwidth_class=BandwidthClass.FAST,
            name=self.name,
        )

    def capabilities(self) -> Capabilities:
        """What this target will do.

        No ``exec``: a QEMU target reaches the machine through its keyboard
        and screen, exactly like a person. Running commands would mean
        reaching around the patient's own boot process, which is the one
        thing Ward must never pretend it can do.
        """
        return Capabilities(
            can_screenshot=True,
            can_type=True,
            can_press=True,
            can_power=True,
            can_exec=False,
            max_tier=Tier.DESTROY,
        )

    def screenshot(self) -> Image:
        """Return what is on the screen right now."""
        with tempfile.TemporaryDirectory(prefix="ward-shot-") as scratch:
            path = Path(scratch) / "screen"
            data = self._screendump(path)
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            width, height = _png_size(data)
            return Image(png=data, width=width, height=height)
        width, height, pixels = parse_ppm(data)
        return Image(png=encode_rgb(width, height, pixels), width=width, height=height)

    def _screendump(self, path: Path) -> bytes:
        """Ask QEMU for the framebuffer, in PNG if this build can.

        QEMU 7.1 added ``format``. Older builds reject the argument, so the
        first call finds out which kind of QEMU we have and the answer is
        remembered.
        """
        if self._screendump_png is not False:
            try:
                self.client.execute(
                    "screendump", {"filename": str(path), "format": "png"}
                )
                self._screendump_png = True
                return path.read_bytes()
            except QmpError:
                self._screendump_png = False
        self.client.execute("screendump", {"filename": str(path)})
        return path.read_bytes()

    def type_text(self, text: str) -> None:
        """Type ``text`` at the machine, one keystroke at a time."""
        for keys in keys_for_text(text):
            self._send_keys(keys)

    def press(self, combo: list[str] | str) -> None:
        """Press a chord, such as ``["ctrl", "alt", "f2"]``."""
        self._send_keys(parse_combo(combo))

    def _send_keys(self, keys: list[str]) -> None:
        """Press keys together and release them together."""
        self.client.execute(
            "send-key",
            {
                "keys": [{"type": "qcode", "data": key} for key in keys],
                "hold-time": KEY_HOLD_MS,
            },
        )

    def power(self, action: PowerAction) -> None:
        """Work the power button.

        ``off`` is a hard power off, not an ACPI shutdown request: Ward's
        power button is the one on the case, and half the machines it will
        meet are too broken to answer a polite request.
        """
        if action is PowerAction.RESET:
            self.client.execute("system_reset")
            return
        if action is PowerAction.OFF:
            self.shutdown()
            return
        if action is PowerAction.ON:
            if self.is_running():
                return
            if self.disk is None:
                raise TargetUnavailable(
                    f"{self.name} was attached, not launched, so Ward cannot "
                    "power it back on; it does not know how it was started"
                )
            self._spawn(boot_timeout=30.0)
            return
        raise ValueError(f"unknown power action: {action!r}")

    def exec(self, cmd: str) -> ExecResult:
        """Never runs. A QEMU target has a keyboard, not a shell."""
        raise CapabilityError(
            "a QEMU target cannot run commands: it drives the machine through "
            "its keyboard and screen. Type the command instead, or check "
            "capabilities().can_exec first."
        )


def _png_size(data: bytes) -> tuple[int, int]:
    """Read width and height out of a PNG's IHDR."""
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ValueError("not a PNG, or a PNG without an IHDR where one belongs")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def wait_for_change(
    target: QemuTarget,
    *,
    timeout: float = 30.0,
    poll: float = 0.5,
    baseline: Image | None = None,
) -> tuple[bool, Image, float]:
    """Watch the screen until it changes, or until ``timeout`` runs out.

    Returns ``(changed, latest_screenshot, seconds_waited)``. Screens are
    compared by hash, so this notices any change at all but cannot say what
    changed — see D3 in ``docs/DECISIONS.md``.
    """
    first = baseline if baseline is not None else target.screenshot()
    reference = first.sha256()
    started = time.monotonic()
    latest = first
    while time.monotonic() - started < timeout:
        time.sleep(poll)
        latest = target.screenshot()
        if latest.sha256() != reference:
            return True, latest, time.monotonic() - started
    return False, latest, time.monotonic() - started
