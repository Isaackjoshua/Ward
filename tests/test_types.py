"""Tests for the value types."""

import json

import pytest

from ward.png import solid_png
from ward.types import (
    BandwidthClass,
    Capabilities,
    ExecResult,
    Image,
    Mode,
    OsFamily,
    TargetDescription,
    Tier,
    Transport,
    tier_rank,
)


def test_description_serialises_to_plain_strings():
    description = TargetDescription(
        transport=Transport.QEMU,
        mode=Mode.OFFLINE,
        os_family=OsFamily.LINUX,
        bandwidth_class=BandwidthClass.FAST,
        name="patient-1",
    )
    data = description.to_dict()

    assert data == {
        "transport": "qemu",
        "mode": "offline",
        "os_family": "linux",
        "bandwidth_class": "fast",
        "name": "patient-1",
    }
    # It must survive a round trip through JSON untouched.
    assert json.loads(json.dumps(data)) == data


def test_mode_values_are_the_three_disjoint_worlds():
    assert {str(mode) for mode in Mode} == {"live", "offline", "firmware"}


def test_tier_order_runs_observe_to_destroy():
    assert tier_rank(Tier.OBSERVE) < tier_rank(Tier.MODIFY) < tier_rank(Tier.DESTROY)


@pytest.mark.parametrize(
    ("max_tier", "requested", "expected"),
    [
        (Tier.OBSERVE, Tier.OBSERVE, True),
        (Tier.OBSERVE, Tier.MODIFY, False),
        (Tier.MODIFY, Tier.MODIFY, True),
        (Tier.MODIFY, Tier.DESTROY, False),
        (Tier.DESTROY, Tier.DESTROY, True),
    ],
)
def test_capabilities_allows_up_to_max_tier(max_tier, requested, expected):
    caps = Capabilities(
        can_screenshot=True,
        can_type=True,
        can_press=True,
        can_power=True,
        can_exec=False,
        max_tier=max_tier,
    )
    assert caps.allows(requested) is expected


def test_image_saves_bytes_and_hashes_them(tmp_path):
    png = solid_png(4, 3, (255, 0, 0))
    image = Image(png=png, width=4, height=3)

    written = image.save(tmp_path / "shots" / "screen.png")

    assert written.read_bytes() == png
    assert image.sha256() == Image(png=png, width=4, height=3).sha256()


def test_images_of_different_screens_hash_differently():
    red = Image(png=solid_png(4, 3, (255, 0, 0)), width=4, height=3)
    blue = Image(png=solid_png(4, 3, (0, 0, 255)), width=4, height=3)

    assert red.sha256() != blue.sha256()


def test_exec_result_ok_tracks_exit_code():
    assert ExecResult(0, "out", "").ok is True
    assert ExecResult(1, "", "boom").ok is False
