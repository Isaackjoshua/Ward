"""The ``ward lab`` verbs.

Kept out of ``ward/cli.py`` because the lab is scaffolding for testing Ward,
not part of driving a target. Same convention as everything else: JSON on
stdout, plain English on stderr.
"""

from __future__ import annotations

import argparse
import json
import sys

from ward import session
from ward.lab import lab
from ward.lab.base import DEFAULT_BASE, ensure_base_image
from ward.lab.faults import FAULTS, get_fault
from ward.lab.prepare import ROOT_PASSWORD
from ward.qemu_target import QemuTarget
from ward.types import Mode, OsFamily


def add_lab_parser(subcommands: argparse._SubParsersAction) -> None:
    """Register ``ward lab ...`` on the top-level parser."""
    parser = subcommands.add_parser(
        "lab",
        help="build and manage deliberately broken virtual machines",
    )
    verbs = parser.add_subparsers(dest="lab_command", required=True)

    faults = verbs.add_parser("faults", help="list the breaker library")
    faults.add_argument("--json", action="store_true", help="emit JSON")
    faults.set_defaults(func=cmd_faults)

    fetch = verbs.add_parser(
        "fetch-base",
        help="download and verify the read-only base image",
    )
    fetch.add_argument("--json", action="store_true", help="emit JSON")
    fetch.set_defaults(func=cmd_fetch_base)

    create = verbs.add_parser("create", help="create a VM broken by a fault")
    create.add_argument("fault", choices=sorted(FAULTS), help="which fault to apply")
    create.add_argument("--name", help="name the VM (default: the fault's name)")
    create.add_argument(
        "--force",
        action="store_true",
        help="rebuild the VM if it already exists",
    )
    create.add_argument("--json", action="store_true", help="emit JSON")
    create.set_defaults(func=cmd_create)

    listing = verbs.add_parser("list", help="list the VMs in the lab")
    listing.add_argument("--json", action="store_true", help="emit JSON")
    listing.set_defaults(func=cmd_list)

    reset = verbs.add_parser("reset", help="restore a VM to its freshly broken state")
    reset.add_argument("name", nargs="?", help="which VM (default: all of them)")
    reset.add_argument("--json", action="store_true", help="emit JSON")
    reset.set_defaults(func=cmd_reset)

    boot = verbs.add_parser(
        "boot",
        help="start a VM and attach ward to it",
    )
    boot.add_argument("name", help="which VM")
    boot.add_argument(
        "--mode",
        choices=[str(mode) for mode in Mode],
        help="which world a repair happens in "
        "(default: the mode the fault says its repair needs)",
    )
    boot.add_argument("--json", action="store_true", help="emit JSON")
    boot.set_defaults(func=cmd_boot)

    check = verbs.add_parser(
        "check",
        help="boot a VM and confirm it fails the way its fault says it should",
    )
    check.add_argument("name", help="which VM")
    check.add_argument(
        "--seconds",
        type=float,
        help="how long to watch (default: the fault's own observation window)",
    )
    check.add_argument("--json", action="store_true", help="emit JSON")
    check.set_defaults(func=cmd_check)

    destroy = verbs.add_parser("destroy", help="delete a VM and its disks")
    destroy.add_argument("name", help="which VM")
    destroy.add_argument("--json", action="store_true", help="emit JSON")
    destroy.set_defaults(func=cmd_destroy)


def _emit(payload: object, narration: str) -> int:
    print(json.dumps(payload, indent=2))
    print(narration, file=sys.stderr)
    return 0


def cmd_faults(args: argparse.Namespace) -> int:
    """List every fault the lab knows how to apply."""
    payload = [FAULTS[name].to_dict() for name in sorted(FAULTS)]
    return _emit(payload, f"the breaker library holds {len(payload)} faults")


def cmd_fetch_base(args: argparse.Namespace) -> int:
    """Download the base image if it is not already here."""
    already = DEFAULT_BASE.path.exists()
    path = ensure_base_image(DEFAULT_BASE)
    payload = {
        "base_image": DEFAULT_BASE.name,
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "downloaded": not already,
    }
    verb = "already had" if already else "downloaded and verified"
    return _emit(payload, f"{verb} the {DEFAULT_BASE.name} base image at {path}")


def cmd_create(args: argparse.Namespace) -> int:
    """Break a fresh machine."""
    fault = get_fault(args.fault)
    print(
        f"creating a VM broken by {fault.name}: {fault.summary}. "
        "This boots a short-lived VM to apply the fault, so give it a minute.",
        file=sys.stderr,
    )
    machine = lab.create(args.fault, name=args.name, force=args.force)
    return _emit(
        machine.to_dict(),
        f"created {machine.name!r}, broken by {machine.fault}. "
        f"Repairing it is a {fault.repair_mode.upper()} job.",
    )


def cmd_list(args: argparse.Namespace) -> int:
    """List the machines currently in the lab."""
    machines = [machine.to_dict() for machine in lab.list_vms()]
    return _emit(machines, f"the lab holds {len(machines)} VMs")


def cmd_reset(args: argparse.Namespace) -> int:
    """Put one machine, or every machine, back to freshly broken."""
    names = [args.name] if args.name else [vm.name for vm in lab.list_vms()]
    restored = [lab.reset(name).to_dict() for name in names]
    return _emit(
        restored,
        f"reset {len(restored)} VM(s) to the state they were in when broken",
    )


def cmd_boot(args: argparse.Namespace) -> int:
    """Start a lab VM and make it the machine ``ward`` commands act on."""
    machine = lab.load_vm(args.name)
    fault = get_fault(machine.fault)
    mode = Mode(args.mode) if args.mode else fault.repair_mode

    target = QemuTarget.launch(
        machine.patient_disk,
        name=machine.name,
        mode=mode,
        serial_log=machine.boot_log,
        journal_log=machine.journal_log,
        run_dir=machine.directory / "run",
    )
    record = session.Session(
        name=machine.name,
        qmp_socket=str(target.qmp_socket),
        mode=str(mode),
        os_family=str(OsFamily.LINUX),
        disk=str(machine.patient_disk),
        serial_log=str(machine.directory / "boot.log"),
    )
    session.save(record)
    # Leave the VM running: the whole point is that later ward commands, in
    # later processes, talk to the same machine.
    target.close()

    guessed = "" if args.mode else " (from the fault; override with --mode)"
    return _emit(
        record.to_dict(),
        f"booted {machine.name!r} and attached in {mode} mode{guessed}. "
        f"Take a look with 'ward screenshot'. The root password on lab "
        f"patients is {ROOT_PASSWORD!r}, which is how you get into a rescue "
        f"or emergency shell.",
    )


def cmd_check(args: argparse.Namespace) -> int:
    """Boot a machine and judge whether it broke the way it was meant to."""
    print(
        f"booting {args.name} and watching its serial console; "
        "this takes as long as the fault's observation window",
        file=sys.stderr,
    )
    result = lab.check(args.name, seconds=args.seconds)
    print(json.dumps(result.to_dict(), indent=2))
    if result.broke_as_expected:
        print(
            f"{result.vm} failed as {result.fault} says it should, "
            f"after {result.seconds_observed:.0f}s",
            file=sys.stderr,
        )
        return 0
    print(
        f"{result.vm} did NOT fail the way {result.fault} describes. "
        f"Last lines of {result.serial_log}:\n{result.tail}",
        file=sys.stderr,
    )
    return 1


def cmd_destroy(args: argparse.Namespace) -> int:
    """Delete a machine."""
    lab.destroy(args.name)
    return _emit({"destroyed": args.name}, f"destroyed lab VM {args.name!r}")
