"""Ward's command line.

Claude Code drives Ward through a shell, so the CLI is the product surface.
M0 ships one command, ``ward describe``, against the fake target only. Real
drivers and the rest of the verb list arrive in later milestones.

Convention, kept from here on: machine-readable output goes to stdout,
plain-English narration of what happened goes to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from ward.fake import FakeTarget
from ward.target import Target

# Only the fake target exists in M0. Real drivers register here as they land.
_TARGETS: dict[str, type] = {"fake": FakeTarget}


def build_target(name: str) -> Target:
    """Return a target by name, or fail loudly with the valid names."""
    try:
        factory = _TARGETS[name]
    except KeyError:
        known = ", ".join(sorted(_TARGETS))
        raise SystemExit(
            f"ward: unknown target {name!r}; known targets: {known}"
        ) from None
    return factory()


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="ward",
        description="Drive a broken computer through fake peripherals.",
    )
    parser.add_argument(
        "--target",
        default="fake",
        help="which target to talk to (default: fake)",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    describe = subcommands.add_parser(
        "describe",
        help="print the target's description and capabilities as JSON",
    )
    describe.add_argument(
        "--json",
        action="store_true",
        help="emit JSON (the only output format today; accepted for consistency)",
    )
    describe.set_defaults(func=cmd_describe)

    return parser


def cmd_describe(args: argparse.Namespace) -> int:
    """Print what the target is and what it can do."""
    target = build_target(args.target)
    description = target.describe()
    payload = {
        "description": description.to_dict(),
        "capabilities": target.capabilities().to_dict(),
    }
    print(json.dumps(payload, indent=2))
    print(
        f"described target {description.name!r}: {description.transport} transport, "
        f"{description.mode} mode, {description.os_family} OS",
        file=sys.stderr,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
