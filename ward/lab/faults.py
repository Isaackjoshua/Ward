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
    summary="/etc/fstab requires a filesystem whose UUID does not exist; boot "
    "stops in emergency mode",
    repair_mode=Mode.LIVE,
    # Not the root line. Rewriting that does nothing here: the kernel has
    # already mounted root from the PARTUUID on its command line, and systemd
    # does not go back and re-check it. What does stop a boot is an ordinary
    # required entry that cannot be satisfied — local-fs.target fails, and
    # everything that wanted it gives up.
    apply_script=rf"""
set -eu
fstab={PATIENT_MOUNT}/etc/fstab
cat "$fstab"
mkdir -p {PATIENT_MOUNT}/srv/data
printf 'UUID=deadbeef-0000-0000-0000-000000000000 /srv/data ext4 defaults 0 2\n' \
  >> "$fstab"
grep -q 'deadbeef-0000-0000-0000-000000000000' "$fstab"
""",
    failure_any=(
        r"emergency mode",
        r"Failed to mount",
        r"dependency failed for .*local file systems",
    ),
    failure_absent=(),
    repair=(
        "Log into the emergency shell, read /etc/fstab, and find the entry "
        "whose UUID no filesystem has (compare against blkid or lsblk -f). "
        "Either point it at the right filesystem or remove the line, then "
        "systemctl daemon-reload and reboot. Marking it nofail hides the "
        "symptom without answering why the entry was there."
    ),
    # systemd waits its full 90 second device timeout before admitting the
    # filesystem is never turning up, and only then drops to emergency mode.
    # Watching for less than that sees a machine that looks merely slow.
    observe_seconds=210.0,
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
    summary="the root filesystem is inconsistent in a way fsck will not fix "
    "on its own; boot stops and asks for a manual check",
    repair_mode=Mode.OFFLINE,
    # Not the superblock. /boot lives on this same filesystem, so anything
    # bad enough to stop Linux mounting it also stops GRUB loading a kernel,
    # and the machine fails silently at the firmware instead — which is
    # grub-missing's failure, not this one. Corrupting /var leaves everything
    # GRUB reads intact and still makes fsck refuse to proceed unattended.
    apply_script=rf"""
set -eu
umount -R {PATIENT_MOUNT}
debugfs -w -R "sif /var mode 0100644" {PATIENT_ROOT_PARTITION}
debugfs -w -R "ssv state 0" {PATIENT_ROOT_PARTITION}
sync
! e2fsck -p -f {PATIENT_ROOT_PARTITION}
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
        "Run e2fsck manually on the root filesystem and answer its questions "
        "— it will want to reconnect /var. Do this from the initramfs shell "
        "or from rescue media, never on a mounted read-write root. Do not "
        "mkfs: that is a repair which destroys the patient, and counts as "
        "made worse rather than fixed."
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
