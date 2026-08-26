"""A target that isn't a machine.

FakeTarget exists so the layers above the driver can be tested with no VM,
no QEMU and no hardware. It hands out canned screenshots and records every
input it is given, so a test can assert on what an agent actually did.

It is not a simulator. It does not pretend to boot, and it never changes its
own mode. Nothing here touches the host machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ward.errors import CapabilityError
from ward.png import solid_png
from ward.types import (
    BandwidthClass,
    Capabilities,
    ExecResult,
    Image,
    Mode,
    OsFamily,
    PowerAction,
    TargetDescription,
    Tier,
    Transport,
)

# Distinct flat colours, so a test can tell consecutive canned frames apart
# by hash without caring what the pixels mean.
_DEFAULT_FRAME_COLOURS: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (16, 16, 64),
    (64, 16, 16),
)

_DEFAULT_WIDTH = 640
_DEFAULT_HEIGHT = 480


def default_frames() -> list[Image]:
    """Return the canned screenshots a FakeTarget uses when given none."""
    return [
        Image(
            png=solid_png(_DEFAULT_WIDTH, _DEFAULT_HEIGHT, colour),
            width=_DEFAULT_WIDTH,
            height=_DEFAULT_HEIGHT,
        )
        for colour in _DEFAULT_FRAME_COLOURS
    ]


@dataclass(frozen=True)
class Event:
    """One thing that was done to the fake target.

    ``kind`` is one of ``screenshot``, ``type_text``, ``press`` or ``power``.
    ``payload`` is the argument, normalised to a string so a whole session
    reads as a flat list.
    """

    kind: str
    payload: str = ""


@dataclass
class FakeTarget:
    """An in-memory :class:`ward.target.Target` implementation.

    ``frames`` are returned by successive calls to :meth:`screenshot`; the
    last one is sticky, so a test that screenshots more times than there are
    frames keeps seeing the final screen rather than crashing.
    """

    description: TargetDescription = field(
        default_factory=lambda: TargetDescription(
            transport=Transport.FAKE,
            mode=Mode.LIVE,
            os_family=OsFamily.UNKNOWN,
            bandwidth_class=BandwidthClass.FAST,
            name="fake-target",
        )
    )
    caps: Capabilities = field(
        default_factory=lambda: Capabilities(
            can_screenshot=True,
            can_type=True,
            can_press=True,
            can_power=True,
            can_exec=False,
            max_tier=Tier.MODIFY,
        )
    )
    frames: list[Image] = field(default_factory=default_frames)
    events: list[Event] = field(default_factory=list)
    _frame_index: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.frames:
            raise ValueError("FakeTarget needs at least one canned frame")

    # -- Target protocol ---------------------------------------------------

    def describe(self) -> TargetDescription:
        """Return the canned description."""
        return self.description

    def capabilities(self) -> Capabilities:
        """Return the canned capabilities."""
        return self.caps

    def screenshot(self) -> Image:
        """Return the next canned frame, holding on the last one."""
        if not self.caps.can_screenshot:
            raise CapabilityError("this target cannot take screenshots")
        frame = self.frames[self._frame_index]
        self._frame_index = min(self._frame_index + 1, len(self.frames) - 1)
        self.events.append(Event("screenshot", frame.sha256()))
        return frame

    def type_text(self, text: str) -> None:
        """Record ``text`` as typed."""
        if not self.caps.can_type:
            raise CapabilityError("this target cannot accept typed text")
        self.events.append(Event("type_text", text))

    def press(self, combo: list[str]) -> None:
        """Record ``combo`` as pressed, joined with dashes."""
        if not self.caps.can_press:
            raise CapabilityError("this target cannot accept key presses")
        if not combo:
            raise ValueError("press() needs at least one key")
        self.events.append(Event("press", "-".join(combo)))

    def power(self, action: PowerAction) -> None:
        """Record a power action."""
        if not self.caps.can_power:
            raise CapabilityError("this target cannot be power-cycled")
        self.events.append(Event("power", str(action)))

    def exec(self, cmd: str) -> ExecResult:
        """Always refuse: the fake target has no shell.

        Returning a plausible-looking result here would let a bug above the
        driver layer pass its tests, so it raises instead.
        """
        raise CapabilityError(
            "FakeTarget cannot run commands; check capabilities().can_exec first"
        )

    # -- test helpers ------------------------------------------------------

    def rewind(self) -> None:
        """Point screenshots back at the first canned frame."""
        self._frame_index = 0
