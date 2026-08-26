"""The value types every part of Ward passes around.

These are deliberately dumb: frozen dataclasses and string enums, no
behaviour beyond serialisation. Drivers differ; these do not.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path


class Transport(StrEnum):
    """How Ward is connected to the target."""

    QEMU = "qemu"
    PICO_HID = "pico-hid"
    SSH = "ssh"
    FAKE = "fake"


class Mode(StrEnum):
    """Which world the target is in.

    This is the field that matters most. LIVE and OFFLINE repair are disjoint
    playbooks: in LIVE the patient's own OS is running and its own tools
    apply, in OFFLINE the patient is a mounted disk in a rescue environment
    and every command needs a chroot or an explicit path. FIRMWARE means we
    are looking at a BIOS/UEFI screen and there is no OS to talk to at all.
    """

    LIVE = "live"
    OFFLINE = "offline"
    FIRMWARE = "firmware"


class OsFamily(StrEnum):
    """The patient's operating system family, as far as we can tell."""

    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class BandwidthClass(StrEnum):
    """How expensive it is to move a screenshot off the target.

    SLOW transports (a capture dongle, a serial line) mean fewer, more
    deliberate screenshots.
    """

    FAST = "fast"
    SLOW = "slow"


class PowerAction(StrEnum):
    """What to do to the target's power."""

    ON = "on"
    OFF = "off"
    RESET = "reset"


class Tier(StrEnum):
    """Capability tier of an action.

    OBSERVE reads the machine, MODIFY changes it recoverably, DESTROY can
    lose data. The gate that enforces these arrives in M4; the vocabulary is
    defined here so drivers can declare how far they are willing to go.
    """

    OBSERVE = "observe"
    MODIFY = "modify"
    DESTROY = "destroy"


# Ascending order of danger. Used by ``Tier.allows``.
_TIER_ORDER: tuple[Tier, ...] = (Tier.OBSERVE, Tier.MODIFY, Tier.DESTROY)


def tier_rank(tier: Tier) -> int:
    """Return the danger rank of ``tier``; higher means more dangerous."""
    return _TIER_ORDER.index(tier)


@dataclass(frozen=True)
class TargetDescription:
    """What kind of machine we are talking to, and how.

    Every field here changes what repair strategy is valid, which is why the
    description travels with the target rather than being inferred at the
    call site.
    """

    transport: Transport
    mode: Mode
    os_family: OsFamily
    bandwidth_class: BandwidthClass
    name: str

    def to_dict(self) -> dict[str, str]:
        """Return a plain JSON-serialisable dict (enums become strings)."""
        return {key: str(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class Capabilities:
    """What a given target can actually do.

    Callers check these instead of catching exceptions from probe calls.
    ``max_tier`` is the most dangerous action this target will accept at all,
    independent of any per-command gate added later.
    """

    can_screenshot: bool
    can_type: bool
    can_press: bool
    can_power: bool
    can_exec: bool
    max_tier: Tier

    def allows(self, tier: Tier) -> bool:
        """Return True if an action at ``tier`` is within this target's limit."""
        return tier_rank(tier) <= tier_rank(self.max_tier)

    def to_dict(self) -> dict[str, object]:
        """Return a plain JSON-serialisable dict."""
        data = asdict(self)
        data["max_tier"] = str(self.max_tier)
        return data


@dataclass(frozen=True)
class Image:
    """A screenshot: PNG bytes plus the dimensions we were told.

    Ward keeps the encoded bytes rather than a pixel buffer, because every
    consumer either writes it to disk for an agent to read or hashes it for
    the audit log.
    """

    png: bytes
    width: int
    height: int

    def save(self, path: str | Path) -> Path:
        """Write the PNG to ``path`` and return the path written."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.png)
        return target

    def sha256(self) -> str:
        """Return the hex SHA-256 of the PNG bytes.

        Used to tell "the screen changed" from "we took another picture of
        the same screen", and later as the audit log's screenshot identity.
        """
        return hashlib.sha256(self.png).hexdigest()


@dataclass(frozen=True)
class ExecResult:
    """The result of running a command on a target that supports it."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True if the command exited zero."""
        return self.exit_code == 0

    def to_dict(self) -> dict[str, object]:
        """Return a plain JSON-serialisable dict."""
        return asdict(self)
