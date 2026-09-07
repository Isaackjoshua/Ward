"""The capability gate: tiers, confirmation tokens, and what they are worth.

These tests are as much documentation as verification. The gate is a speed
bump with a paper trail, not a security boundary, and the tests say so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ward.errors import CapabilityError
from ward.policy import (
    COMMAND_TIERS,
    ConfirmationRequired,
    check_capability,
    issue_confirmation,
    spend_confirmation,
    tier_for,
)
from ward.types import Capabilities, Tier


def caps(max_tier: Tier) -> Capabilities:
    return Capabilities(
        can_screenshot=True,
        can_type=True,
        can_press=True,
        can_power=True,
        can_exec=False,
        max_tier=max_tier,
    )


def issue(state: Path, scope: str, **kwargs):
    return issue_confirmation(state, scope, require_tty=False, **kwargs)


def test_looking_at_a_machine_is_never_more_than_observe() -> None:
    assert tier_for("screenshot") is Tier.OBSERVE
    assert tier_for("describe") is Tier.OBSERVE
    assert tier_for("wait-for-change") is Tier.OBSERVE


def test_typing_is_modify_and_cutting_power_is_destroy() -> None:
    assert tier_for("type") is Tier.MODIFY
    assert tier_for("key") is Tier.MODIFY
    assert tier_for("power", "on") is Tier.MODIFY
    assert tier_for("power", "off") is Tier.DESTROY
    assert tier_for("power", "reset") is Tier.DESTROY


def test_an_unknown_command_is_assumed_dangerous() -> None:
    """The safe default for something we have not classified is not observe."""
    assert "not-a-command" not in COMMAND_TIERS
    assert tier_for("not-a-command") is Tier.MODIFY


def test_a_target_that_refuses_destruction_is_believed() -> None:
    with pytest.raises(CapabilityError, match="Widening that would be a bug"):
        check_capability(caps(Tier.MODIFY), Tier.DESTROY)
    check_capability(caps(Tier.MODIFY), Tier.MODIFY)
    check_capability(caps(Tier.DESTROY), Tier.DESTROY)


def test_a_destroy_action_without_a_token_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfirmationRequired, match="ward confirm power off"):
        spend_confirmation(tmp_path, "power off", None)


def test_a_valid_token_is_accepted_exactly_once(tmp_path: Path) -> None:
    """Single use, so a token left in a file cannot be replayed."""
    token = issue(tmp_path, "power off").token
    spend_confirmation(tmp_path, "power off", token)
    with pytest.raises(ConfirmationRequired, match="single use"):
        spend_confirmation(tmp_path, "power off", token)


def test_a_token_only_works_for_what_it_authorised(tmp_path: Path) -> None:
    token = issue(tmp_path, "power off").token
    with pytest.raises(ConfirmationRequired, match="issued for 'power off'"):
        spend_confirmation(tmp_path, "power reset", token)


def test_an_expired_token_is_refused_and_discarded(tmp_path: Path) -> None:
    """An old approval must not authorise a new decision."""
    token = issue(tmp_path, "power reset", ttl=-1).token
    with pytest.raises(ConfirmationRequired, match="expired"):
        spend_confirmation(tmp_path, "power reset", token)
    with pytest.raises(ConfirmationRequired, match="not valid"):
        spend_confirmation(tmp_path, "power reset", token)


def test_an_invented_token_does_not_work(tmp_path: Path) -> None:
    issue(tmp_path, "power off")
    with pytest.raises(ConfirmationRequired, match="not valid"):
        spend_confirmation(tmp_path, "power off", "made-this-up")


def test_the_token_itself_is_never_written_to_disk(tmp_path: Path) -> None:
    """Only its hash is stored, so reading the file does not grant anything."""
    token = issue(tmp_path, "power off").token
    stored = (tmp_path / "confirmations.json").read_text(encoding="utf-8")
    assert token not in stored


def test_confirm_wants_a_human_unless_told_otherwise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WARD_UNATTENDED", raising=False)
    with pytest.raises(ConfirmationRequired, match="human at a terminal"):
        issue_confirmation(tmp_path, "power off", require_tty=True)

    monkeypatch.setenv("WARD_UNATTENDED", "1")
    assert issue_confirmation(tmp_path, "power off", require_tty=True).token
