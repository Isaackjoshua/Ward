"""The lab's disk layout, cloud-init and QEMU argument building.

Still no VMs: these check the parts that are easy to get subtly wrong and
expensive to debug through a boot.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from ward.lab import lab, paths, qemu
from ward.lab.faults import PATIENT_ROOT_PARTITION, PATIENT_SERIAL, get_fault


@pytest.fixture(autouse=True)
def scratch_lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the lab at a throwaway directory, never the developer's own."""
    monkeypatch.setenv("WARD_LAB_ROOT", str(tmp_path / "lab"))
    return tmp_path


def test_lab_root_follows_the_environment(tmp_path: Path) -> None:
    assert paths.lab_root() == (tmp_path / "lab").resolve()
    assert paths.images_dir().parent == paths.lab_root()
    assert paths.vm_dir("x") == paths.vms_dir() / "x"


def test_surgeon_user_data_is_valid_cloud_config() -> None:
    fault = get_fault("fstab-bad-uuid")
    user_data = lab._surgeon_user_data(fault)
    assert user_data.startswith("#cloud-config\n")
    assert "power_state:" in user_data
    assert "poweroff" in user_data


def test_surgeon_carries_the_script_verbatim() -> None:
    """base64, so quoting in a breaker can never corrupt the cloud-config."""
    fault = get_fault("network-down")
    user_data = lab._surgeon_user_data(fault)
    line = next(
        row for row in user_data.splitlines() if row.strip().startswith("content:")
    )
    encoded = line.split("content:", 1)[1].strip()
    assert base64.b64decode(encoded).decode("utf-8") == fault.apply_script


def test_surgeon_reports_its_exit_status_on_the_serial_console() -> None:
    """Without this the host cannot tell a breaker that worked from a no-op."""
    user_data = lab._surgeon_user_data(get_fault("grub-missing"))
    runner = next(row for row in user_data.splitlines() if lab.SURGEON_MARKER in row)
    command = json.loads(runner.split("- ", 1)[1])
    assert f'echo "{lab.SURGEON_MARKER}:$rc" > /dev/ttyS0' in command
    assert f"mount {PATIENT_ROOT_PARTITION} /mnt" in command


def test_the_patient_is_addressed_by_serial_never_by_slot() -> None:
    """Patient and surgeon are the same image and share a filesystem UUID.

    /dev/vdb is not a promise about which disk is which, and getting it wrong
    means operating on the surgeon's own mounted root.
    """
    assert PATIENT_SERIAL in PATIENT_ROOT_PARTITION
    command = json.loads(
        next(
            row
            for row in lab._surgeon_user_data(get_fault("grub-missing")).splitlines()
            if lab.SURGEON_MARKER in row
        ).split("- ", 1)[1]
    )
    assert "/dev/vdb" not in command
    # And it waits to be handed the disk rather than assuming it is there.
    assert lab.SURGEON_READY_MARKER in command


def test_the_surgeon_traces_the_breaker_to_the_serial_console() -> None:
    """A failed breaker has no shell to debug in; the log has to be enough."""
    user_data = lab._surgeon_user_data(get_fault("rootfs-errors"))
    runner = next(row for row in user_data.splitlines() if lab.SURGEON_MARKER in row)
    command = json.loads(runner.split("- ", 1)[1])
    assert "sh -x /var/lib/ward-breaker.sh" in command
    assert "> /dev/ttyS0 2>&1" in command


def test_drive_arg_uses_virtio_and_honours_readonly() -> None:
    flag, spec = qemu.drive_arg(Path("/tmp/disk.qcow2"))
    assert flag == "-drive"
    assert "if=virtio" in spec
    assert "readonly" not in spec

    _, ro = qemu.drive_arg(Path("/tmp/seed.iso"), fmt="raw", readonly=True)
    assert "format=raw" in ro
    assert "readonly=on" in ro


def test_base_argv_is_headless_and_captures_serial(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "serial.log"
    argv = qemu.base_argv(memory_mb=512, serial_log=log)
    assert argv[0] == qemu.QEMU_SYSTEM
    assert "-display" in argv and argv[argv.index("-display") + 1] == "none"
    assert f"file:{log}" in argv
    # Nothing in the lab gets a network: not the patient we are repairing,
    # and not the surgeon either.
    assert argv[argv.index("-nic") + 1] == "none"
    assert log.parent.is_dir()


def test_listing_an_empty_lab_is_not_an_error() -> None:
    assert lab.list_vms() == []


def test_loading_a_missing_vm_says_what_does_exist() -> None:
    with pytest.raises(lab.LabError, match="no lab VM named 'ghost'"):
        lab.load_vm("ghost")


def test_reset_without_a_pristine_copy_refuses(tmp_path: Path) -> None:
    directory = paths.vm_dir("half-built")
    directory.mkdir(parents=True)
    (directory / "vm.json").write_text(
        json.dumps(
            {
                "name": "half-built",
                "fault": "network-down",
                "base_image": "debian-12",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(lab.LabError, match="no pristine copy"):
        lab.reset("half-built")
