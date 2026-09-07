"""Thin wrappers over the QEMU command line tools the lab needs.

This module is lab plumbing, not a ``Target``. It builds disks and runs
short-lived virtual machines to *set up* the patients; driving a patient once
it exists is M2's job and goes through the ``Target`` protocol.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from ward.errors import WardError

QEMU_SYSTEM = "qemu-system-x86_64"
QEMU_IMG = "qemu-img"
XORRISO = "xorriso"


class LabToolError(WardError):
    """A tool the lab depends on is missing or failed."""


def require_tool(name: str) -> str:
    """Return the path to an external tool, or say plainly what to install."""
    found = shutil.which(name)
    if found is None:
        raise LabToolError(
            f"{name} is not on PATH. The lab needs qemu-system-x86_64, "
            "qemu-img and xorriso; on Debian these are in the qemu-system-x86, "
            "qemu-utils and xorriso packages."
        )
    return found


def kvm_available() -> bool:
    """Whether this host can use hardware acceleration.

    Without KVM everything still works, just several times slower, so this
    only picks the accelerator — it is never a reason to refuse.
    """
    device = Path("/dev/kvm")
    if not device.exists():
        return False
    try:
        with device.open("rb"):
            return True
    except OSError:
        return False


def _run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run a tool, capture both streams, and raise with them on failure."""
    try:
        result = subprocess.run(  # noqa: S603 - argv is built here, never shell
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LabToolError(f"{argv[0]} timed out after {timeout}s") from exc
    if result.returncode != 0:
        raise LabToolError(
            f"{' '.join(argv)} failed with exit code {result.returncode}\n"
            f"{result.stderr.strip()}"
        )
    return result


def create_overlay(base: Path, destination: Path, *, timeout: float = 60.0) -> Path:
    """Create a qcow2 that reads from ``base`` and writes only to itself."""
    require_tool(QEMU_IMG)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    _run(
        [
            QEMU_IMG,
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(base.resolve()),
            str(destination),
        ],
        timeout=timeout,
    )
    return destination


def build_seed_iso(
    destination: Path,
    *,
    user_data: str,
    instance_id: str,
    timeout: float = 60.0,
) -> Path:
    """Build a cloud-init NoCloud seed ISO.

    cloud-init looks for a filesystem labelled ``cidata`` holding
    ``user-data`` and ``meta-data``, so that is exactly what this writes.
    """
    require_tool(XORRISO)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f"{destination.stem}.cidata"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "user-data").write_text(user_data, encoding="utf-8")
    (staging / "meta-data").write_text(
        f"instance-id: {instance_id}\nlocal-hostname: {instance_id}\n",
        encoding="utf-8",
    )
    destination.unlink(missing_ok=True)
    _run(
        [
            XORRISO,
            "-as",
            "mkisofs",
            "-output",
            str(destination),
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            str(staging / "user-data"),
            str(staging / "meta-data"),
        ],
        timeout=timeout,
    )
    shutil.rmtree(staging, ignore_errors=True)
    return destination


def drive_arg(path: Path, *, fmt: str = "qcow2", readonly: bool = False) -> list[str]:
    """Return the ``-drive`` argument for one disk."""
    spec = f"file={path},if=virtio,format={fmt},cache=writeback"
    if readonly:
        spec += ",readonly=on"
    return ["-drive", spec]


def base_argv(*, memory_mb: int, serial_log: Path) -> list[str]:
    """Return the QEMU arguments every lab VM shares.

    Headless, no network, serial console captured to a file. The lab reads
    the serial log to tell whether a machine failed the way it was supposed
    to; screenshots are M2's business.

    Nothing in the lab gets a network. A machine we are repairing is not a
    machine that should be talking to anything, and the surgeon has no reason
    to either. The cost is that the guest spends its
    ``systemd-networkd-wait-online`` timeout on every boot before it gets
    going, which is the two minutes you will notice in ``ward lab create``.
    """
    require_tool(QEMU_SYSTEM)
    serial_log.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        QEMU_SYSTEM,
        "-machine",
        "q35,accel=kvm" if kvm_available() else "q35",
        "-m",
        str(memory_mb),
        "-smp",
        "2",
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        f"file:{serial_log}",
        "-nic",
        "none",
    ]
    if kvm_available():
        argv += ["-cpu", "host"]
    return argv


def start_vm(argv: list[str]) -> subprocess.Popen[bytes]:
    """Start a VM and return immediately, leaving it running."""
    return subprocess.Popen(  # noqa: S603 - argv is built by this module
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def wait_for_marker(log: Path, marker: str, *, timeout: float) -> bool:
    """Poll a serial log until ``marker`` appears, or give up.

    The serial console is the only channel a guest with no network has, so
    this is how the host and the guest agree on where they are in a sequence.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log.exists() and marker in log.read_text(encoding="utf-8", errors="replace"):
            return True
        time.sleep(0.5)
    return False


def wait_for_exit(process: subprocess.Popen[bytes], *, timeout: float) -> int:
    """Wait for a VM to stop, killing it if it overruns.

    Overrunning is not treated as an error here: a machine that is supposed
    to be broken may well never shut itself down. ``-1`` means it was killed.
    """
    try:
        process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return -1
    return process.returncode


def run_vm(argv: list[str], *, timeout: float) -> int:
    """Run a VM to completion, killing it if it overruns.

    A hung VM must never hang the lab. Overrunning is not an error here — a
    machine that is supposed to be broken may well never shut itself down —
    so the caller gets the return code and decides.
    """
    process = subprocess.Popen(  # noqa: S603 - argv is built here, never shell
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return -1
    if process.returncode != 0:
        raise LabToolError(
            f"qemu exited with {process.returncode}: "
            f"{stderr.decode('utf-8', 'replace').strip()}"
        )
    return process.returncode
