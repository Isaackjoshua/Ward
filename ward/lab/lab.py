"""Creating, breaking, resetting and checking lab machines.

The lab is the most valuable thing in the project: without reproducibly
broken machines there is nothing to test Ward against and no way to know
whether it works.

Layout under ``lab/``::

    images/debian-12-genericcloud-amd64.qcow2   read-only, shared by everything
    vms/<name>/patient.qcow2                    the machine, an overlay
    vms/<name>/pristine.qcow2                   the same machine, freshly broken
    vms/<name>/vm.json                          which fault, which base, when
    vms/<name>/surgery.log                      serial console of the breaker
    vms/<name>/boot.log                         serial console of the last check

``pristine.qcow2`` is a copy of the overlay taken the moment the fault was
applied, so ``ward lab reset`` is a file copy and every run starts from a
byte-identical machine.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ward.errors import WardError
from ward.lab import qemu
from ward.lab.base import DEFAULT_BASE, BaseImage, ensure_base_image
from ward.lab.faults import (
    PATIENT_MOUNT,
    PATIENT_ROOT_PARTITION,
    PATIENT_SERIAL,
    Fault,
    get_fault,
)
from ward.lab.paths import vm_dir, vms_dir
from ward.lab.prepare import PREPARE_SCRIPT
from ward.qmp import QmpClient

#: The surgeon prints this on the serial console before powering off, so the
#: host can tell a breaker that worked from one that quietly did nothing.
SURGEON_MARKER = "WARD-SURGEON-RESULT"

#: The guest says this once it is far enough along to be handed the patient.
SURGEON_READY_MARKER = "WARD-SURGEON-READY"

_SURGEON_MEMORY_MB = 1024
_PATIENT_MEMORY_MB = 1024
_SURGEON_TIMEOUT = 420.0
_SURGEON_BOOT_TIMEOUT = 300.0
_PATIENT_WAIT_SECONDS = 60

#: The PCIe port the patient's disk is plugged into once the surgeon is up.
_HOTPLUG_PORT = "ward-hotplug"


class LabError(WardError):
    """Something went wrong setting up or checking a lab machine."""


@dataclass(frozen=True)
class LabVM:
    """One machine in the lab, on disk."""

    name: str
    fault: str
    base_image: str
    created_at: str

    @property
    def directory(self) -> Path:
        return vm_dir(self.name)

    @property
    def patient_disk(self) -> Path:
        return self.directory / "patient.qcow2"

    @property
    def pristine_disk(self) -> Path:
        return self.directory / "pristine.qcow2"

    @property
    def boot_log(self) -> Path:
        """Serial port one: what the kernel printed."""
        return self.directory / "boot.log"

    @property
    def journal_log(self) -> Path:
        """Serial port two: the journal, copied there by the patient itself.

        The screen belongs to the operator now, so this is how the lab reads
        what systemd had to say about a boot.
        """
        return self.directory / "journal.log"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "fault": self.fault,
            "base_image": self.base_image,
            "created_at": self.created_at,
            "patient_disk": str(self.patient_disk),
            "exists": self.patient_disk.exists(),
        }


def _metadata_path(name: str) -> Path:
    return vm_dir(name) / "vm.json"


def load_vm(name: str) -> LabVM:
    """Return an existing lab machine, or say which ones do exist."""
    path = _metadata_path(name)
    if not path.exists():
        existing = ", ".join(vm.name for vm in list_vms()) or "none"
        raise LabError(f"no lab VM named {name!r}; existing VMs: {existing}")
    record = json.loads(path.read_text(encoding="utf-8"))
    return LabVM(
        name=record["name"],
        fault=record["fault"],
        base_image=record["base_image"],
        created_at=record["created_at"],
    )


def vm_exists(name: str) -> bool:
    """Whether a lab machine of this name has been built."""
    return _metadata_path(name).exists()


def list_vms() -> list[LabVM]:
    """Return every lab machine, oldest name first."""
    root = vms_dir()
    if not root.is_dir():
        return []
    machines = []
    for candidate in sorted(root.iterdir()):
        if (candidate / "vm.json").exists():
            machines.append(load_vm(candidate.name))
    return machines


def _surgeon_user_data(fault: Fault) -> str:
    """Build the cloud-init that tells the surgeon how to break the patient.

    The breaker script goes in base64 so nothing in it has to survive YAML
    quoting — these scripts are full of quotes, backslashes and braces.

    The guest announces that it is up, then waits for the patient disk to be
    plugged in. It cannot be attached at boot: the patient and the surgeon
    are copies of the same image, so they share a filesystem UUID and the
    surgeon happily boots off the patient instead — which would mean
    operating on a mounted, running root filesystem.
    """
    encoded = base64.b64encode(fault.apply_script.encode("utf-8")).decode("ascii")
    prepared = base64.b64encode(PREPARE_SCRIPT.encode("utf-8")).decode("ascii")
    # Prepare first, break second. Preparing needs the filesystem mounted and
    # GRUB intact, and several breakers take one or both of those away.
    #
    # Everything both scripts do goes to the serial console, traced. When a
    # breaker fails there is no shell to log into and no second chance: the
    # surgery log has to be enough to say why on its own.
    runner = (
        f'echo "{SURGEON_READY_MARKER}" > /dev/ttyS0; '
        f"for _ in $(seq 1 {_PATIENT_WAIT_SECONDS}); do "
        f"[ -b {PATIENT_ROOT_PARTITION} ] && break; sleep 1; done; "
        "{ lsblk; "
        f"mount {PATIENT_ROOT_PARTITION} {PATIENT_MOUNT} && "
        "sh -x /var/lib/ward-prepare.sh && "
        "sh -x /var/lib/ward-breaker.sh; } > /dev/ttyS0 2>&1; rc=$?; "
        f"umount -R {PATIENT_MOUNT} 2>/dev/null; sync; "
        f'echo "{SURGEON_MARKER}:$rc" > /dev/ttyS0'
    )
    return "\n".join(
        [
            "#cloud-config",
            "write_files:",
            "  - path: /var/lib/ward-prepare.sh",
            "    permissions: '0755'",
            "    encoding: b64",
            f"    content: {prepared}",
            "  - path: /var/lib/ward-breaker.sh",
            "    permissions: '0755'",
            "    encoding: b64",
            f"    content: {encoded}",
            "runcmd:",
            "  - - sh",
            "    - -c",
            f"    - {json.dumps(runner)}",
            "power_state:",
            "  mode: poweroff",
            "  timeout: 30",
            "  condition: true",
            "",
        ]
    )


def _read_log(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _plug_in_patient(monitor: Path, patient_disk: Path) -> None:
    """Hand the patient's disk to a surgeon that is already running.

    The serial number is the only reliable way for the guest to tell the two
    disks apart, since they are copies of the same image and share a
    filesystem UUID.
    """
    with QmpClient(monitor, timeout=30.0) as client:
        client.execute(
            "blockdev-add",
            {
                "node-name": "patient",
                "driver": "qcow2",
                "file": {"driver": "file", "filename": str(patient_disk)},
            },
        )
        client.execute(
            "device_add",
            {
                "driver": "virtio-blk-pci",
                "drive": "patient",
                "id": "patient-disk",
                "bus": _HOTPLUG_PORT,
                "serial": PATIENT_SERIAL,
            },
        )


def _run_surgeon(fault: Fault, patient_disk: Path, base: Path, directory: Path) -> None:
    """Boot a throwaway VM that applies the fault to the patient's disk."""
    surgeon_disk = qemu.create_overlay(base, directory / "surgeon.qcow2")
    seed = qemu.build_seed_iso(
        directory / "surgeon-seed.iso",
        user_data=_surgeon_user_data(fault),
        instance_id=f"ward-surgeon-{fault.name}",
    )
    log = directory / "surgery.log"
    log.unlink(missing_ok=True)

    monitor = directory / "surgeon-qmp.sock"
    monitor.unlink(missing_ok=True)
    argv = qemu.base_argv(memory_mb=_SURGEON_MEMORY_MB, serial_log=log)
    # q35's root bus does not do hotplug, so the patient needs a port to be
    # plugged into. It is empty until the guest says it is ready.
    argv += ["-qmp", f"unix:{monitor},server,nowait"]
    argv += ["-device", f"pcie-root-port,id={_HOTPLUG_PORT},chassis=1"]
    argv += qemu.drive_arg(surgeon_disk)
    argv += qemu.drive_arg(seed, fmt="raw", readonly=True)

    process = qemu.start_vm(argv)
    try:
        if not qemu.wait_for_marker(
            log, SURGEON_READY_MARKER, timeout=_SURGEON_BOOT_TIMEOUT
        ):
            raise LabError(
                f"the surgeon VM never came up while applying {fault.name}. "
                f"Serial log: {log}"
            )
        _plug_in_patient(monitor, patient_disk)
        returncode = qemu.wait_for_exit(process, timeout=_SURGEON_TIMEOUT)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    output = _read_log(log)

    if returncode == -1:
        raise LabError(
            f"the surgeon VM did not power off within {_SURGEON_TIMEOUT:.0f}s "
            f"while applying {fault.name}. Serial log: {log}"
        )

    match = re.search(rf"{SURGEON_MARKER}:(\d+)", output)
    if match is None:
        raise LabError(
            f"the surgeon VM finished without reporting a result for "
            f"{fault.name}; the breaker may never have run. Serial log: {log}"
        )
    status = int(match.group(1))
    if status != 0:
        raise LabError(
            f"the breaker for {fault.name} exited with {status}: it did not "
            f"break the machine, so the VM was not created. Serial log: {log}"
        )

    # The surgeon's own disk is scratch; keep only the patient.
    surgeon_disk.unlink(missing_ok=True)
    seed.unlink(missing_ok=True)


def create(
    fault_name: str,
    *,
    name: str | None = None,
    base_image: BaseImage = DEFAULT_BASE,
    force: bool = False,
) -> LabVM:
    """Create a VM broken by the named fault, ready to be worked on."""
    fault = get_fault(fault_name)
    vm_name = name or fault.name
    directory = vm_dir(vm_name)

    if directory.exists() and not force:
        raise LabError(
            f"lab VM {vm_name!r} already exists. Use 'ward lab reset {vm_name}' "
            f"to restore it, or 'ward lab create --force' to rebuild it."
        )
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)

    base = ensure_base_image(base_image)
    patient = qemu.create_overlay(base, directory / "patient.qcow2")
    _run_surgeon(fault, patient, base, directory)

    # The freshly broken overlay is the reference copy every reset restores.
    shutil.copy2(patient, directory / "pristine.qcow2")

    machine = LabVM(
        name=vm_name,
        fault=fault.name,
        base_image=base_image.name,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    _metadata_path(vm_name).write_text(
        json.dumps(
            {
                "name": machine.name,
                "fault": machine.fault,
                "base_image": machine.base_image,
                "created_at": machine.created_at,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return machine


def reset(name: str) -> LabVM:
    """Restore a machine to the state it was in the moment it was broken."""
    machine = load_vm(name)
    if not machine.pristine_disk.exists():
        raise LabError(
            f"lab VM {name!r} has no pristine copy to restore from; "
            f"recreate it with 'ward lab create {machine.fault} --force'."
        )
    machine.patient_disk.unlink(missing_ok=True)
    shutil.copy2(machine.pristine_disk, machine.patient_disk)
    return machine


def destroy(name: str) -> None:
    """Delete a lab machine and everything it owns."""
    machine = load_vm(name)
    shutil.rmtree(machine.directory)


@dataclass(frozen=True)
class BootCheck:
    """What happened when the lab booted a patient and watched it."""

    vm: str
    fault: str
    broke_as_expected: bool
    seconds_observed: float
    serial_log: Path
    tail: str
    #: Everything the machine said, both serial ports together. Anyone
    #: judging this boot must read *this*, not one of the log files: since
    #: the screen became /dev/console, the kernel's channel and systemd's are
    #: different files and neither is the whole story on its own.
    transcript: str = ""

    def to_dict(self) -> dict[str, object]:
        # The transcript is tens of kilobytes and is on disk already, so it
        # stays out of the JSON. The log paths are how you go and read it.
        return {
            "vm": self.vm,
            "fault": self.fault,
            "broke_as_expected": self.broke_as_expected,
            "seconds_observed": round(self.seconds_observed, 1),
            "serial_log": str(self.serial_log),
            "journal_log": str(self.journal_log),
        }

    @property
    def journal_log(self) -> Path:
        return self.serial_log.with_name("journal.log")


def check(name: str, *, seconds: float | None = None) -> BootCheck:
    """Boot a patient, watch both serial ports, and judge the failure.

    This is how the lab proves a fault is reproducible: the machine must fail
    the same recognisable way every time. It is deliberately not a ``Target``
    — it reads serial logs, which real broken hardware will not give us. The
    screen is left alone for the operator and for Ward's own eyes.
    """
    machine = load_vm(name)
    fault = get_fault(machine.fault)
    observe = fault.observe_seconds if seconds is None else seconds

    log = machine.boot_log
    journal = machine.journal_log
    log.unlink(missing_ok=True)
    journal.unlink(missing_ok=True)
    argv = qemu.base_argv(
        memory_mb=_PATIENT_MEMORY_MB, serial_log=log, journal_log=journal
    )
    argv += qemu.drive_arg(machine.patient_disk)

    started = time.monotonic()
    qemu.run_vm(argv, timeout=observe)
    elapsed = time.monotonic() - started

    # Both channels count as "what the machine said". A fault may show itself
    # in the kernel's output, in systemd's, or in neither — silence is
    # grub-missing's whole signature.
    output = _read_log(log) + "\n" + _read_log(journal)
    return BootCheck(
        vm=machine.name,
        fault=fault.name,
        broke_as_expected=fault.matches_failure(output),
        seconds_observed=elapsed,
        serial_log=log,
        tail="\n".join(output.splitlines()[-20:]),
        transcript=output,
    )
