"""Which commands are dangerous, and what it takes to run them.

Three tiers, from ``SPEC.md``:

- **observe** — looking. Cannot change the patient.
- **modify** — typing, pressing keys, powering a machine *on*.
- **destroy** — resetting or cutting power to a running machine. Unsaved
  state is gone and a filesystem mid-write may not come back.

The gate lives here, inside the CLI, and not in Claude Code's permission
prompts. Claude Code owns its own agent loop and has raw shell access, so any
gate it can route around is not a gate. This one cannot stop a determined
process either — it is a speed bump with a paper trail, and it is written
down as exactly that rather than sold as a security boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ward.errors import CapabilityError, WardError
from ward.types import Tier

#: Every CLI command, and the most dangerous thing it can do.
COMMAND_TIERS: dict[str, Tier] = {
    "describe": Tier.OBSERVE,
    "attach": Tier.OBSERVE,
    "detach": Tier.OBSERVE,
    "screenshot": Tier.OBSERVE,
    "wait-for-change": Tier.OBSERVE,
    "type": Tier.MODIFY,
    "key": Tier.MODIFY,
    "confirm": Tier.OBSERVE,
    "audit": Tier.OBSERVE,
    "replay": Tier.OBSERVE,
    "lab": Tier.MODIFY,
    "eval": Tier.DESTROY,
}

#: ``power`` is the one command whose tier depends on its argument.
POWER_TIERS: dict[str, Tier] = {
    "on": Tier.MODIFY,
    "off": Tier.DESTROY,
    "reset": Tier.DESTROY,
}

TOKEN_TTL_SECONDS = 300


class ConfirmationRequired(WardError):
    """A destroy-tier action was attempted without a valid token."""


def tier_for(command: str, action: str | None = None) -> Tier:
    """Return the tier of a command, taking its argument into account."""
    if command == "power":
        if action is None:
            raise ValueError("power needs an action to have a tier")
        return POWER_TIERS[action]
    return COMMAND_TIERS.get(command, Tier.MODIFY)


def _tokens_path(state_dir: Path) -> Path:
    return state_dir / "confirmations.json"


def _load_tokens(state_dir: Path) -> list[dict]:
    path = _tokens_path(state_dir)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save_tokens(state_dir: Path, tokens: list[dict]) -> None:
    path = _tokens_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2) + "\n", encoding="utf-8")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Confirmation:
    """A one-shot permission for one dangerous action."""

    token: str
    scope: str
    expires_at: str


def issue_confirmation(
    state_dir: Path,
    scope: str,
    *,
    ttl: int = TOKEN_TTL_SECONDS,
    require_tty: bool = True,
) -> Confirmation:
    """Mint a token for one ``destroy`` action.

    Meant to be run by the person, not the agent, so by default it refuses
    unless something is on the other end of stdin. ``WARD_UNATTENDED=1``
    turns that off for CI and for ``ward eval``, which breaks its own
    machines on purpose.
    """
    unattended = os.environ.get("WARD_UNATTENDED") == "1"
    if require_tty and not sys.stdin.isatty() and not unattended:
        raise ConfirmationRequired(
            "ward confirm is for a human at a terminal. If you meant to run "
            "unattended — a CI job, or ward eval breaking its own lab VMs — "
            "set WARD_UNATTENDED=1 and own that choice."
        )
    token = secrets.token_urlsafe(12)
    expires = datetime.now(UTC) + timedelta(seconds=ttl)
    tokens = [
        entry
        for entry in _load_tokens(state_dir)
        if entry["expires_at"] > datetime.now(UTC).isoformat()
    ]
    tokens.append(
        {
            "digest": _digest(token),
            "scope": scope,
            "expires_at": expires.isoformat(timespec="seconds"),
        }
    )
    _save_tokens(state_dir, tokens)
    return Confirmation(
        token=token, scope=scope, expires_at=expires.isoformat(timespec="seconds")
    )


def spend_confirmation(state_dir: Path, scope: str, token: str | None) -> None:
    """Consume a token for ``scope``, or explain how to get one.

    Tokens are single-use: spending one removes it. An agent that finds a
    stale token in a file cannot replay it.
    """
    if not token:
        raise ConfirmationRequired(
            f"'{scope}' is a destroy-tier action and needs a confirmation "
            f"token. Run 'ward confirm {scope}' and pass the token it prints "
            f"as --confirm."
        )
    tokens = _load_tokens(state_dir)
    now = datetime.now(UTC).isoformat()
    digest = _digest(token)
    for index, entry in enumerate(tokens):
        if entry["digest"] != digest:
            continue
        if entry["scope"] != scope:
            raise ConfirmationRequired(
                f"that token was issued for '{entry['scope']}', not '{scope}'. "
                "Tokens are scoped to one action on purpose."
            )
        if entry["expires_at"] <= now:
            del tokens[index]
            _save_tokens(state_dir, tokens)
            raise ConfirmationRequired(
                f"that token expired at {entry['expires_at']}. Confirmations "
                "are short-lived so an old approval cannot authorise a new "
                "decision."
            )
        del tokens[index]
        _save_tokens(state_dir, tokens)
        return
    raise ConfirmationRequired(
        f"that token is not valid for '{scope}'. Tokens are single use; if it "
        "worked once it will not work again."
    )


def check_capability(capabilities, tier: Tier) -> None:
    """Refuse an action the target itself will not accept.

    Never widen the tier to make something work — a target that says it will
    not be destroyed means it.
    """
    if not capabilities.allows(tier):
        raise CapabilityError(
            f"this target accepts at most {capabilities.max_tier} actions, and "
            f"this one is {tier}. Widening that would be a bug, not a fix."
        )
