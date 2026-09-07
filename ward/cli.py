"""Ward's command line.

Claude Code drives Ward through a shell, so the CLI is the product surface:
it can already run commands and read PNG files, which is all an agent needs
to sit in front of a machine. There is no MCP server.

Convention, kept everywhere: machine-readable output goes to stdout,
plain-English narration of what happened goes to stderr. Every command takes
``--json``.

The default target is the *current session* — the machine attached with
``ward lab boot`` or ``ward attach``. There is deliberately no silent
fallback to the fake target: an agent that believes it is looking at a
patient while actually looking at a canned image is the worst failure this
project can produce.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ward import audit, evaluate, policy
from ward import session as session_state
from ward.errors import WardError
from ward.fake import FakeTarget
from ward.lab.cli import add_lab_parser
from ward.qemu_target import QemuTarget, wait_for_change
from ward.replay import render_timeline
from ward.target import Target
from ward.types import Mode, OsFamily, PowerAction, Tier


def build_target(args: argparse.Namespace) -> Target:
    """Return the target a command should act on, if it is allowed to.

    ``session`` is the default and means the attached machine. ``fake`` has
    to be asked for by name. The capability check happens here so no command
    can forget it.
    """
    name = args.target
    if name == "fake":
        target: Target = FakeTarget()
    elif name == "session":
        current = session_state.load()
        target = QemuTarget.attach(
            Path(current.qmp_socket),
            name=current.name,
            mode=session_state.mode_of(current),
            os_family=session_state.os_family_of(current),
        )
    else:
        raise SystemExit(f"ward: unknown target {name!r}; known targets: fake, session")
    policy.check_capability(target.capabilities(), tier_of(args))
    return target


def tier_of(args: argparse.Namespace) -> Tier:
    """Return the tier of the command being run."""
    return policy.tier_for(args.command, getattr(args, "action", None))


def scope_of(args: argparse.Namespace) -> str:
    """Return the confirmation scope for a command, e.g. ``power off``."""
    action = getattr(args, "action", None)
    return f"{args.command} {action}" if action else args.command


def build_parser() -> argparse.ArgumentParser:
    """Return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="ward",
        description="Drive a broken computer through fake peripherals.",
    )
    parser.add_argument(
        "--target",
        default="session",
        help="which target to talk to: session (default) or fake",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    describe = subcommands.add_parser(
        "describe",
        help="print the target's description and capabilities as JSON",
    )
    describe.add_argument("--json", action="store_true", help="emit JSON")
    describe.set_defaults(func=cmd_describe)

    attach = subcommands.add_parser(
        "attach",
        help="point ward at a QEMU machine that is already running",
    )
    attach.add_argument("--qmp", required=True, help="path to the QMP unix socket")
    attach.add_argument("--name", default="patient", help="what to call it")
    attach.add_argument(
        "--mode",
        choices=[str(mode) for mode in Mode],
        default=str(Mode.LIVE),
        help="which world a repair happens in; Ward will not guess this",
    )
    attach.add_argument(
        "--os-family",
        choices=[str(family) for family in OsFamily],
        default=str(OsFamily.UNKNOWN),
    )
    attach.add_argument("--json", action="store_true", help="emit JSON")
    attach.set_defaults(func=cmd_attach)

    detach = subcommands.add_parser("detach", help="forget the current machine")
    detach.add_argument("--json", action="store_true", help="emit JSON")
    detach.set_defaults(func=cmd_detach)

    screenshot = subcommands.add_parser(
        "screenshot",
        help="write a PNG of what is on the screen and print its path",
    )
    screenshot.add_argument("--out", help="where to write the PNG")
    screenshot.add_argument("--json", action="store_true", help="emit JSON")
    screenshot.set_defaults(func=cmd_screenshot)

    typing = subcommands.add_parser("type", help="type text at the machine")
    typing.add_argument("text", help="the text to type")
    typing.add_argument(
        "--enter",
        action="store_true",
        help="press enter afterwards",
    )
    typing.add_argument("--json", action="store_true", help="emit JSON")
    typing.set_defaults(func=cmd_type)

    key = subcommands.add_parser("key", help="press a key or chord, e.g. ctrl-alt-f2")
    key.add_argument("combo", help="the chord, as ctrl-alt-f2 or a single key")
    key.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="press it this many times (default: 1)",
    )
    key.add_argument("--json", action="store_true", help="emit JSON")
    key.set_defaults(func=cmd_key)

    waiting = subcommands.add_parser(
        "wait-for-change",
        help="block until the screen changes (note: on a text console a "
        "blinking cursor counts as a change)",
    )
    waiting.add_argument("--timeout", type=float, default=30.0)
    waiting.add_argument("--poll", type=float, default=0.5)
    waiting.add_argument("--out", help="write the final screenshot here")
    waiting.add_argument("--json", action="store_true", help="emit JSON")
    waiting.set_defaults(func=cmd_wait_for_change)

    power = subcommands.add_parser("power", help="work the power button")
    power.add_argument("action", choices=[str(action) for action in PowerAction])
    power.add_argument(
        "--confirm",
        help="a token from 'ward confirm', required for off and reset",
    )
    power.add_argument("--json", action="store_true", help="emit JSON")
    power.set_defaults(func=cmd_power)

    confirm = subcommands.add_parser(
        "confirm",
        help="mint a one-shot token authorising a destroy-tier action",
    )
    confirm.add_argument(
        "scope",
        help="the action to authorise, e.g. 'power off'",
    )
    confirm.add_argument(
        "--ttl",
        type=int,
        default=policy.TOKEN_TTL_SECONDS,
        help="how many seconds the token is good for",
    )
    confirm.add_argument("--json", action="store_true", help="emit JSON")
    confirm.set_defaults(func=cmd_confirm)

    audit_cmd = subcommands.add_parser("audit", help="print the audit log")
    audit_cmd.add_argument(
        "--tail",
        type=int,
        default=0,
        help="only the last N entries (default: all of them)",
    )
    audit_cmd.add_argument("--json", action="store_true", help="emit JSON")
    audit_cmd.set_defaults(func=cmd_audit)

    replay = subcommands.add_parser(
        "replay",
        help="render a session's audit log as an HTML timeline",
    )
    replay.add_argument(
        "session",
        nargs="?",
        help="path to an audit log (default: the current session's)",
    )
    replay.add_argument("--out", help="where to write the HTML")
    replay.add_argument("--json", action="store_true", help="emit JSON")
    replay.set_defaults(func=cmd_replay)

    add_eval_parser(subcommands)
    add_lab_parser(subcommands)

    return parser


def _emit(payload: object, narration: str) -> int:
    _emitted.payload = payload
    print(json.dumps(payload, indent=2))
    print(narration, file=sys.stderr)
    return 0


class _Emitted:
    """The last payload a command printed, so ``main`` can audit it.

    Threading it through every return value would mean touching every command
    for the benefit of one caller; this keeps the commands readable and the
    audit honest, because it records exactly what the user was shown.
    """

    payload: object = None


_emitted = _Emitted()


def cmd_describe(args: argparse.Namespace) -> int:
    """Print what the target is and what it can do."""
    target = build_target(args)
    description = target.describe()
    payload = {
        "description": description.to_dict(),
        "capabilities": target.capabilities().to_dict(),
    }
    return _emit(
        payload,
        f"described target {description.name!r}: {description.transport} transport, "
        f"{description.mode} mode, {description.os_family} OS",
    )


def cmd_attach(args: argparse.Namespace) -> int:
    """Record which machine later commands act on."""
    socket_path = Path(args.qmp).expanduser()
    # Connect before saving, so a session file never points at nothing.
    target = QemuTarget.attach(
        socket_path,
        name=args.name,
        mode=Mode(args.mode),
        os_family=OsFamily(args.os_family),
    )
    target.close()
    record = session_state.Session(
        name=args.name,
        qmp_socket=str(socket_path),
        mode=args.mode,
        os_family=args.os_family,
    )
    session_state.save(record)
    return _emit(
        record.to_dict(),
        f"attached to {args.name!r} over {socket_path} in {args.mode} mode",
    )


def cmd_detach(args: argparse.Namespace) -> int:
    """Forget the current machine without touching it."""
    had_one = session_state.clear()
    return _emit(
        {"detached": had_one},
        "detached; the machine is still running" if had_one else "nothing was attached",
    )


def _default_screenshot_path() -> Path:
    directory = session_state.state_dir() / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    existing = len(list(directory.glob("shot-*.png")))
    return directory / f"shot-{existing:04d}.png"


def cmd_screenshot(args: argparse.Namespace) -> int:
    """Take a picture of the screen and say where it landed."""
    target = build_target(args)
    image = target.screenshot()
    path = Path(args.out).expanduser() if args.out else _default_screenshot_path()
    image.save(path)
    payload = {
        "path": str(path),
        "width": image.width,
        "height": image.height,
        "sha256": image.sha256(),
    }
    return _emit(
        payload,
        f"wrote a {image.width}x{image.height} screenshot to {path}",
    )


def cmd_type(args: argparse.Namespace) -> int:
    """Type at the machine."""
    target = build_target(args)
    text = args.text + ("\n" if args.enter else "")
    target.type_text(text)
    return _emit(
        {"typed": text, "characters": len(text)},
        f"typed {len(text)} characters at {target.describe().name!r}",
    )


def cmd_key(args: argparse.Namespace) -> int:
    """Press a chord."""
    target = build_target(args)
    for _ in range(args.repeat):
        target.press(args.combo)
    return _emit(
        {"combo": args.combo, "repeat": args.repeat},
        f"pressed {args.combo} {args.repeat} time(s)",
    )


def cmd_wait_for_change(args: argparse.Namespace) -> int:
    """Block until the screen changes, or give up and say so."""
    target = build_target(args)
    if not isinstance(target, QemuTarget):
        raise WardError(
            "wait-for-change needs a live target; the fake target's screen "
            "only moves when something asks it to"
        )
    changed, image, waited = wait_for_change(
        target, timeout=args.timeout, poll=args.poll
    )
    path = Path(args.out).expanduser() if args.out else _default_screenshot_path()
    image.save(path)
    payload = {
        "changed": changed,
        "waited_seconds": round(waited, 1),
        "path": str(path),
        "sha256": image.sha256(),
    }
    _emitted.payload = payload
    print(json.dumps(payload, indent=2))
    if changed:
        print(
            f"the screen changed after {waited:.1f}s; latest shot: {path}",
            file=sys.stderr,
        )
        return 0
    print(
        f"the screen did not change in {args.timeout:.0f}s. The machine may be "
        f"hung, or waiting for input it has not been given. Latest shot: {path}",
        file=sys.stderr,
    )
    return 1


def cmd_power(args: argparse.Namespace) -> int:
    """Work the power button."""
    target = build_target(args)
    action = PowerAction(args.action)
    target.power(action)
    if action is PowerAction.OFF:
        session_state.clear()
    return _emit(
        {"power": str(action)},
        f"sent power {action} to the machine"
        + (" and detached, since it is off now" if action is PowerAction.OFF else ""),
    )


def add_eval_parser(subcommands: argparse._SubParsersAction) -> None:
    """Register ``ward eval``.

    Destroy tier: it resets and reboots every machine in the lab, so it needs
    a confirmation token like any other destructive action.
    """
    parser = subcommands.add_parser(
        "eval",
        help="run an agent against every fault and report fixed / not fixed / "
        "made worse",
    )
    parser.add_argument(
        "agent",
        help="shell command that drives ward; it gets WARD_FAULT and WARD_VM "
        "in its environment",
    )
    parser.add_argument(
        "--fault",
        action="append",
        dest="faults",
        help="only this fault (repeatable; default: all of them)",
    )
    parser.add_argument("--runs", type=int, default=1, help="runs per fault")
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="seconds each agent run may take",
    )
    parser.add_argument("--out", help="write the results JSON here")
    parser.add_argument(
        "--confirm",
        help="a token from 'ward confirm eval'",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.set_defaults(func=cmd_eval)


def cmd_eval(args: argparse.Namespace) -> int:
    """Run an agent against the lab and print the table."""
    state = session_state.state_dir()
    results = evaluate.run_evaluation(
        args.agent,
        faults=args.faults,
        runs=args.runs,
        timeout=args.timeout,
        state_dir=state,
    )
    destination = Path(args.out).expanduser() if args.out else state / "eval.json"
    evaluate.write_results(results, destination)
    _emitted.payload = results
    print(json.dumps(results, indent=2))
    print(evaluate.results_table(results), file=sys.stderr)
    print(f"\nwrote the full results to {destination}", file=sys.stderr)
    totals = results["totals"]
    # Any machine left worse than we found it fails the run, however many
    # others were fixed.
    return 1 if totals[evaluate.MADE_WORSE] else 0


def cmd_confirm(args: argparse.Namespace) -> int:
    """Mint a token for one dangerous action."""
    confirmation = policy.issue_confirmation(
        session_state.state_dir(), args.scope, ttl=args.ttl
    )
    return _emit(
        {
            "token": confirmation.token,
            "scope": confirmation.scope,
            "expires_at": confirmation.expires_at,
        },
        f"authorised one '{confirmation.scope}' until {confirmation.expires_at}. "
        "The token is single use.",
    )


def cmd_audit(args: argparse.Namespace) -> int:
    """Print the audit log."""
    entries = audit.read(session_state.state_dir())
    if args.tail:
        entries = entries[-args.tail :]
    return _emit(entries, f"{len(entries)} audit entries")


def cmd_replay(args: argparse.Namespace) -> int:
    """Render a session as an HTML timeline."""
    state = session_state.state_dir()
    log = Path(args.session).expanduser() if args.session else audit.audit_path(state)
    out = Path(args.out).expanduser() if args.out else state / "replay.html"
    entries = list(audit.iter_entries(log))
    if not entries:
        raise WardError(
            f"there is nothing to replay: {log} has no entries yet. Drive a "
            "machine first, then come back."
        )
    render_timeline(entries, out, title=f"Ward session — {log.name}")
    return _emit(
        {"path": str(out), "entries": len(entries), "log": str(log)},
        f"rendered {len(entries)} steps to {out}",
    )


def _audit_context(args: argparse.Namespace) -> tuple[str, str]:
    """Return the target name and mode to stamp on an audit entry."""
    if args.target == "fake":
        return "fake-target", str(Mode.LIVE)
    try:
        current = session_state.load()
    except WardError:
        return "<none>", "<none>"
    return current.name, current.mode


def _audited_arguments(args: argparse.Namespace) -> dict[str, object]:
    """Everything the command was given, minus the plumbing.

    The confirmation token is deliberately not recorded: the log says an
    action was authorised, not how to authorise another one.
    """
    skip = {"func", "confirm", "json"}
    return {key: value for key, value in vars(args).items() if key not in skip}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code.

    Every command passes through the same three steps: the tier gate, the
    command itself, and the audit entry. Putting them here rather than in
    each command is the only way to be sure none of them is skipped.

    Ward's own errors are failures of the machine or the lab, not bugs in
    Ward, so they print one clear line rather than a traceback. Anything else
    keeps its traceback, because that is a bug and we want to see it.
    """
    args = build_parser().parse_args(argv)
    state = session_state.state_dir()
    tier = tier_of(args)
    _emitted.payload = None

    exit_code = 1
    error: str | None = None
    try:
        if tier is Tier.DESTROY:
            policy.spend_confirmation(
                state, scope_of(args), getattr(args, "confirm", None)
            )
        exit_code = args.func(args)
    except WardError as exc:
        error = str(exc)
        print(f"ward: {exc}", file=sys.stderr)
    finally:
        payload = _emitted.payload
        result = payload if isinstance(payload, dict) else {}
        target_name, mode = _audit_context(args)
        audit.record(
            state,
            audit.AuditEntry(
                timestamp=audit.now(),
                command=scope_of(args),
                tier=str(tier),
                arguments=_audited_arguments(args),
                target=target_name,
                mode=mode,
                exit_code=exit_code,
                result=result,
                error=error,
                screenshot_sha256=result.get("sha256"),
                screenshot_path=result.get("path"),
            ),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
