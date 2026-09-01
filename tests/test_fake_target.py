"""Tests for FakeTarget, and for its conformance to the Target protocol."""

import pytest

from ward.errors import CapabilityError
from ward.fake import Event, FakeTarget, default_frames
from ward.target import Target
from ward.types import (
    BandwidthClass,
    Capabilities,
    Mode,
    OsFamily,
    PowerAction,
    TargetDescription,
    Tier,
    Transport,
)


def test_fake_target_satisfies_the_target_protocol():
    assert isinstance(FakeTarget(), Target)


def test_default_description_is_a_fake_live_target():
    description = FakeTarget().describe()

    assert description.transport is Transport.FAKE
    assert description.mode is Mode.LIVE
    assert description.os_family is OsFamily.UNKNOWN
    assert description.bandwidth_class is BandwidthClass.FAST


def test_description_can_be_overridden():
    description = TargetDescription(
        transport=Transport.FAKE,
        mode=Mode.OFFLINE,
        os_family=OsFamily.LINUX,
        bandwidth_class=BandwidthClass.SLOW,
        name="rescue-shell",
    )
    assert FakeTarget(description=description).describe() is description


def test_screenshots_advance_then_hold_on_the_last_frame():
    target = FakeTarget()
    frames = target.frames

    seen = [target.screenshot() for _ in range(len(frames) + 2)]

    assert seen[: len(frames)] == frames
    assert all(shot is frames[-1] for shot in seen[len(frames) :])


def test_rewind_returns_to_the_first_frame():
    target = FakeTarget()
    first = target.screenshot()
    target.screenshot()

    target.rewind()

    assert target.screenshot() is first


def test_default_frames_are_distinguishable():
    hashes = {frame.sha256() for frame in default_frames()}
    assert len(hashes) == len(default_frames())


def test_input_is_recorded_in_order():
    target = FakeTarget()

    target.type_text("root")
    target.press(["ctrl", "alt", "f2"])
    target.power(PowerAction.RESET)

    assert target.events == [
        Event("type_text", "root"),
        Event("press", "ctrl-alt-f2"),
        Event("power", "reset"),
    ]


def test_screenshots_are_recorded_by_hash():
    target = FakeTarget()

    shot = target.screenshot()

    assert target.events == [Event("screenshot", shot.sha256())]


def test_press_rejects_an_empty_combo():
    target = FakeTarget()

    with pytest.raises(ValueError):
        target.press([])

    assert target.events == []


def test_exec_refuses_because_the_fake_has_no_shell():
    target = FakeTarget()

    assert target.capabilities().can_exec is False
    with pytest.raises(CapabilityError):
        target.exec("echo hello")


def test_disabled_capabilities_raise_instead_of_degrading():
    caps = Capabilities(
        can_screenshot=False,
        can_type=False,
        can_press=False,
        can_power=False,
        can_exec=False,
        max_tier=Tier.OBSERVE,
    )
    target = FakeTarget(caps=caps)

    with pytest.raises(CapabilityError):
        target.screenshot()
    with pytest.raises(CapabilityError):
        target.type_text("x")
    with pytest.raises(CapabilityError):
        target.press(["enter"])
    with pytest.raises(CapabilityError):
        target.power(PowerAction.OFF)


def test_a_target_with_no_frames_is_rejected():
    with pytest.raises(ValueError):
        FakeTarget(frames=[])
