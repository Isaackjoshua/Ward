"""The breaker library: named ways to break a machine, reproducibly.

Every fault records three things, because a fault that only knows how to
break something is useless as a test:

1. **apply** — the shell that breaks a patient disk, run inside the surgeon VM
2. **verify** — what a broken boot looks like on the serial console
3. **repair** — what a correct fix looks like, in prose, for the human or
   agent grading a run

The apply scripts run as root inside a throwaway VM (see D2 in
``docs/DECISIONS.md``) with the patient's disk attached as ``/dev/vdb`` and
its root filesystem already mounted at ``/mnt``. A script that needs the raw
device unmounts ``/mnt`` itself first. The harness always unmounts afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ward.types import Mode

#: The patient and the surgeon are copies of the same base image, so they
#: have the same filesystem UUID and ``/dev/vdb`` is not a promise about
#: which is which. The patient is hot-plugged with a serial number, and the
#: breakers address it by that name and nothing else.
PATIENT_SERIAL = "wardpatient"
PATIENT_DEVICE = f"/dev/disk/by-id/virtio-{PATIENT_SERIAL}"
PATIENT_ROOT_PARTITION = f"{PATIENT_DEVICE}-part1"
PATIENT_MOUNT = "/mnt"


@dataclass(frozen=True)
class Fault:
    """One reproducible way to break a machine."""

    name: str
    summary: str
    #: Which world a repairer will find themselves in. This is the project's
    #: central distinction, so every fault states it up front.
    repair_mode: Mode
    #: Shell run as root in the surgeon VM. Must exit non-zero if it did not
    #: actually break anything — a breaker that silently no-ops would give us
    #: a healthy VM we believe is broken, which is the worst outcome here.
    apply_script: str
    #: A broken boot is confirmed if *any* of these appear on the serial
    #: console. Empty means the fault is confirmed by silence instead.
    failure_any: tuple[str, ...]
    #: None of these may appear. For a machine that should never reach the
    #: kernel, this is the real signal.
    failure_absent: tuple[str, ...]
    #: Prose, for whoever grades a repair attempt.
    repair: str
    #: How long to let the patient run before judging it, in seconds. A
    #: machine that is meant to hang is judged by what it printed before the
    #: clock ran out.
    observe_seconds: float = 90.0

    def matches_failure(self, serial_log: str) -> bool:
        """Whether this serial console output is the failure we expect."""
        for pattern in self.failure_absent:
            if re.search(pattern, serial_log, re.IGNORECASE):
                return False
        if not self.failure_any:
            return True
        return any(
            re.search(pattern, serial_log, re.IGNORECASE)
            for pattern in self.failure_any
        )

    def to_dict(self) -> dict[str, object]:
        """A JSON-friendly view, for ``ward lab list --json``."""
        return {
            "name": self.name,
            "summary": self.summary,
            "repair_mode": str(self.repair_mode),
            "repair": self.repair,
            "observe_seconds": self.observe_seconds,
        }


FSTAB_BAD_UUID = Fault(
    name="fstab-bad-uuid",
    summary="/etc/fstab names a root UUID that does not exist; boot stops in "
    "emergency mode",
    repair_mode=Mode.LIVE,
    # Matched by what the line *is* — the entry whose mount point is / —
    # rather than by how this particular image happens to name the device.
    # Debian has shipped UUID=, LABEL= and /dev/ names here over the years.
    apply_script=rf"""
set -eu
fstab={PATIENT_MOUNT}/etc/fstab
cat "$fstab"
awk '$0 !~ /^[[:space:]]*#/ && NF >= 2 && $2 == "/" \
       {{ $1 = "UUID=deadbeef-0000-0000-0000-000000000000"; found = 1 }}
     {{ print }}
     END {{ if (!found) exit 1 }}' "$fstab" > /tmp/fstab.broken
cat /tmp/fstab.broken > "$fstab"
grep -q 'deadbeef-0000-0000-0000-000000000000' "$fstab"
""",
    failure_any=(
        r"emergency mode",
        r"Failed to mount",
        r"dependency failed for .*local file systems",
    ),
    failure_absent=(),
    repair=(
        "Read the real root filesystem UUID (blkid, or lsblk -f), put it back "
        "in /etc/fstab, then systemctl daemon-reload and reboot. A repair that "
        "deletes the root line instead of fixing it is wrong: the machine will "
        "boot but / will be mounted read-only on the next fsck."
    ),
)

GRUB_MISSING = Fault(
    name="grub-missing",
    summary="the boot sector is wiped and /boot/grub is gone; the firmware "
    "finds nothing to boot",
    repair_mode=Mode.OFFLINE,
    apply_script=rf"""
set -eu
rm -rf {PATIENT_MOUNT}/boot/grub
umount -R {PATIENT_MOUNT}
dd if=/dev/zero of={PATIENT_DEVICE} bs=446 count=1 conv=notrunc
dd if={PATIENT_DEVICE} bs=446 count=1 2>/dev/null | tr -d '\0' | wc -c | grep -qx 0
""",
    failure_any=(),
    # Nothing may reach the kernel at all. Silence on the serial console is
    # the whole signature.
    failure_absent=(r"Linux version", r"systemd\[1\]", r"login:"),
    repair=(
        "Boot rescue media, mount the root filesystem, bind-mount /dev /proc "
        "/sys, chroot in, then grub-install /dev/vda and update-grub. This is "
        "an OFFLINE repair: there is no running patient OS to talk to."
    ),
    observe_seconds=45.0,
)

INITRAMFS_CORRUPT = Fault(
    name="initramfs-corrupt",
    summary="every initrd image is overwritten with zeroes; the kernel panics "
    "before it can mount root",
    repair_mode=Mode.OFFLINE,
    apply_script=rf"""
set -eu
found=0
for image in {PATIENT_MOUNT}/boot/initrd.img-*; do
  [ -f "$image" ] || continue
  head -c 4194304 /dev/zero > "$image"
  found=$((found + 1))
done
[ "$found" -gt 0 ]
""",
    failure_any=(
        r"Kernel panic",
        r"Unable to mount root fs",
        r"Initramfs unpacking failed",
    ),
    failure_absent=(r"login:",),
    repair=(
        "Boot rescue media, chroot into the root filesystem, and rebuild with "
        "update-initramfs -u -k all. Check /boot has enough space first — a "
        "truncated initrd is often a full /boot, not a corrupt file."
    ),
)

ROOTFS_ERRORS = Fault(
    name="rootfs-errors",
    summary="the root filesystem's primary superblock is zeroed; the disk "
    "will not mount until fsck rebuilds it from a backup",
    repair_mode=Mode.OFFLINE,
    apply_script=rf"""
set -eu
umount -R {PATIENT_MOUNT}
dd if=/dev/zero of={PATIENT_ROOT_PARTITION} bs=1024 seek=1 count=1 conv=notrunc
sync
! dumpe2fs -h {PATIENT_ROOT_PARTITION} >/dev/null 2>&1
""",
    failure_any=(
        r"unable to read superblock",
        r"cannot open root device",
        r"unable to mount root fs",
        r"\(initramfs\)",
        r"Kernel panic",
        r"fsck",
    ),
    failure_absent=(r"login:",),
    repair=(
        "Boot rescue media and run e2fsck -b 32768 /dev/vda1 to restore the "
        "superblock from a backup, then reboot. Do not mkfs — that is a repair "
        "that destroys the patient, which counts as made worse, not fixed."
    ),
)

NETWORK_DOWN = Fault(
    name="network-down",
    summary="the machine boots to a login prompt with every networking unit "
    "masked and no resolver",
    repair_mode=Mode.LIVE,
    apply_script=rf"""
set -eu
units={PATIENT_MOUNT}/etc/systemd/system
mkdir -p "$units"
for unit in systemd-networkd.service networking.service NetworkManager.service \
            systemd-networkd.socket ifup@.service; do
  ln -sf /dev/null "$units/$unit"
done
mkdir -p {PATIENT_MOUNT}/etc/cloud/cloud.cfg.d
printf 'network: {{config: disabled}}\n' \
  > {PATIENT_MOUNT}/etc/cloud/cloud.cfg.d/99-ward-network-down.cfg
rm -f {PATIENT_MOUNT}/etc/resolv.conf
: > {PATIENT_MOUNT}/etc/resolv.conf
[ -L "$units/systemd-networkd.service" ]
""",
    failure_any=(r"login:",),
    # This is the one fault where a healthy-looking boot *is* the symptom, so
    # the serial console only confirms the machine came up. What proves the
    # fault is the masked units, which the apply script asserts before exiting.
    failure_absent=(),
    repair=(
        "Unmask the networking units (systemctl unmask systemd-networkd "
        "networking), remove the cloud-init override in "
        "/etc/cloud/cloud.cfg.d, restore /etc/resolv.conf, and bring the "
        "interface up. This is a LIVE repair: the patient OS is running and "
        "has its own tools."
    ),
    observe_seconds=120.0,
)

FAULTS: dict[str, Fault] = {
    fault.name: fault
    for fault in (
        FSTAB_BAD_UUID,
        GRUB_MISSING,
        INITRAMFS_CORRUPT,
        ROOTFS_ERRORS,
        NETWORK_DOWN,
    )
}


def get_fault(name: str) -> Fault:
    """Return a fault by name, or fail with the list of real ones."""
    try:
        return FAULTS[name]
    except KeyError:
        known = ", ".join(sorted(FAULTS))
        raise KeyError(f"unknown fault {name!r}; known faults: {known}") from None
