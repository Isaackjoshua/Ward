"""The one protocol everything in Ward talks to.

Drivers differ wildly below this line: a QEMU monitor socket, a Pico
pretending to be a USB keyboard, an SSH session. Nothing above this line may
know which one it has. If code above the driver layer has to change when a
new transport arrives, this protocol was wrong.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ward.types import Capabilities, ExecResult, Image, PowerAction, TargetDescription


@runtime_checkable
class Target(Protocol):
    """A machine Ward can see, type at, and power-cycle."""

    def describe(self) -> TargetDescription:
        """Return what kind of machine this is and what world it is in."""
        ...

    def capabilities(self) -> Capabilities:
        """Return what this target can be asked to do."""
        ...

    def screenshot(self) -> Image:
        """Return a picture of what is on the target's screen right now."""
        ...

    def type_text(self, text: str) -> None:
        """Type ``text`` as if at the keyboard, character by character."""
        ...

    def press(self, combo: list[str]) -> None:
        """Press a key combination, e.g. ``["ctrl", "alt", "f2"]``."""
        ...

    def power(self, action: PowerAction) -> None:
        """Turn the target on, off, or reset it."""
        ...

    def exec(self, cmd: str) -> ExecResult:
        """Run ``cmd`` on the target.

        Only valid when ``capabilities().can_exec`` is True; implementations
        must raise :class:`ward.errors.CapabilityError` otherwise. This never
        runs anything on the host.
        """
        ...
