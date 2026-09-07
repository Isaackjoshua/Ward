"""The breaker library must be self-consistent.

None of this boots a VM. These tests protect against the failure that would
hurt most: a fault whose signature is so loose it would call a healthy
machine broken, or so tight it can never match.
"""

from __future__ import annotations

import pytest

from ward.lab.faults import FAULTS, get_fault
from ward.types import Mode

# A plausible healthy boot. No fault may accept this as its own failure.
HEALTHY_BOOT = """
[    0.000000] Linux version 6.1.0-18-amd64
[    1.204000] EXT4-fs (vda1): mounted filesystem with ordered data mode
[    2.100000] systemd[1]: Detected virtualization kvm.
Debian GNU/Linux 12 patient ttyS0
patient login:
"""


def test_every_roadmap_fault_exists() -> None:
    assert set(FAULTS) == {
        "fstab-bad-uuid",
        "grub-missing",
        "initramfs-corrupt",
        "rootfs-errors",
        "network-down",
    }


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_fault_records_all_three_parts(name: str) -> None:
    """Break it, spot it, fix it. A fault missing any of those is useless."""
    fault = FAULTS[name]
    assert fault.apply_script.strip(), "no way to apply the fault"
    assert fault.repair.strip(), "no description of a correct repair"
    assert fault.failure_any or fault.failure_absent, "no way to spot the failure"


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_apply_script_fails_loudly(name: str) -> None:
    """Every breaker runs under 'set -e' so a failed step aborts the surgery."""
    assert "set -eu" in FAULTS[name].apply_script


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_fault_names_the_world_a_repair_happens_in(name: str) -> None:
    assert FAULTS[name].repair_mode in (Mode.LIVE, Mode.OFFLINE, Mode.FIRMWARE)


@pytest.mark.parametrize(
    "name",
    sorted(set(FAULTS) - {"network-down"}),
)
def test_healthy_boot_is_not_mistaken_for_a_failure(name: str) -> None:
    """A fault that matches a healthy boot would silently pass forever."""
    assert not FAULTS[name].matches_failure(HEALTHY_BOOT)


def test_network_down_is_the_deliberate_exception() -> None:
    """network-down boots fine on purpose; that is the whole point of it.

    Its symptom is invisible on the serial console, so the breaker asserts
    its own effect instead and the boot check only confirms the machine came
    up at all.
    """
    fault = get_fault("network-down")
    assert fault.matches_failure(HEALTHY_BOOT)
    assert "[ -L " in fault.apply_script


def test_grub_missing_is_confirmed_by_silence() -> None:
    fault = get_fault("grub-missing")
    assert fault.failure_any == ()
    assert fault.matches_failure("")
    assert not fault.matches_failure("[    0.000000] Linux version 6.1.0")


def test_fstab_fault_matches_an_emergency_shell() -> None:
    log = "You are in emergency mode. After logging in, type journalctl -xb"
    assert get_fault("fstab-bad-uuid").matches_failure(log)


def test_initramfs_fault_matches_a_panic() -> None:
    log = "Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block"
    assert get_fault("initramfs-corrupt").matches_failure(log)


def test_unknown_fault_lists_the_real_ones() -> None:
    with pytest.raises(KeyError, match="grub-missing"):
        get_fault("no-such-fault")
