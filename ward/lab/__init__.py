"""The broken machine lab: reproducibly broken VMs to test Ward against."""

from __future__ import annotations

from ward.lab.base import BASE_IMAGES, DEFAULT_BASE, BaseImage, ensure_base_image
from ward.lab.faults import FAULTS, Fault, get_fault
from ward.lab.lab import (
    BootCheck,
    LabError,
    LabVM,
    check,
    create,
    destroy,
    list_vms,
    load_vm,
    reset,
    vm_exists,
)

__all__ = [
    "BASE_IMAGES",
    "DEFAULT_BASE",
    "FAULTS",
    "BaseImage",
    "BootCheck",
    "Fault",
    "LabError",
    "LabVM",
    "check",
    "create",
    "destroy",
    "ensure_base_image",
    "get_fault",
    "list_vms",
    "load_vm",
    "reset",
    "vm_exists",
]
