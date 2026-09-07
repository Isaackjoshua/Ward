"""Where the lab keeps its files.

Everything lives under ``lab/`` in the repository, and everything except the
scripts is gitignored. One place decides the layout so no other module has to
guess.
"""

from __future__ import annotations

import os
from pathlib import Path

# lab/paths.py -> lab/ -> ward/ -> repo root
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def lab_root() -> Path:
    """Return the directory holding the lab's images and VMs.

    ``WARD_LAB_ROOT`` overrides it, which is how the tests get a scratch lab
    instead of stamping on the developer's real one.
    """
    override = os.environ.get("WARD_LAB_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return _REPO_ROOT / "lab"


def images_dir() -> Path:
    """Where read-only base images live."""
    return lab_root() / "images"


def vms_dir() -> Path:
    """Where per-fault virtual machines live."""
    return lab_root() / "vms"


def vm_dir(name: str) -> Path:
    """Where one named VM lives."""
    return vms_dir() / name
