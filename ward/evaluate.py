"""Running an agent against the whole lab and grading what it did.

An evaluation is one number Ward cannot fake: given a machine broken in a
known way, does the agent leave it better, the same, or worse?

Three outcomes, and the third is the one that matters. An agent that fixes
four faults and destroys the fifth machine is not an 80% agent — an operator
would never let it near a real computer again.

- **fixed** — the machine boots to a login prompt
- **not fixed** — it still fails the way its fault says it should
- **made worse** — it neither boots nor fails recognisably any more
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ward import audit
from ward import session as session_state
from ward.errors import WardError
from ward.lab import lab
from ward.lab.faults import FAULTS, get_fault
from ward.qemu_target import QemuTarget
from ward.types import OsFamily

#: What a machine that came back looks like on the serial console.
HEALTHY_PATTERNS = ("login:",)

FIXED = "fixed"
NOT_FIXED = "not-fixed"
MADE_WORSE = "made-worse"


class EvalError(WardError):
    """The evaluation could not be run."""


@dataclass(frozen=True)
class Attempt:
    """One agent run against one fault."""

    fault: str
    run: int
    outcome: str
    seconds: float
    actions: int
    agent_exit_code: int
    detail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _grade(fault_name: str, serial_log: str) -> tuple[str, str]:
    """Return ``(outcome, why)`` for a machine's post-repair boot."""
    fault = get_fault(fault_name)
    healthy = any(pattern in serial_log for pattern in HEALTHY_PATTERNS)
    still_broken = fault.matches_failure(serial_log)

    if fault_name == "network-down":
        # This fault boots healthily by design, so a login prompt proves
        # nothing. Grading it needs a look inside the guest, which Ward
        # cannot do through a keyboard alone; say so instead of guessing.
        return (
            NOT_FIXED,
            "network-down cannot be graded from the serial console alone; "
            "check the machine's networking by hand",
        )
    if healthy:
        return FIXED, "the machine reached a login prompt"
    if still_broken:
        return NOT_FIXED, "the machine still fails exactly as its fault describes"
    return (
        MADE_WORSE,
        "the machine neither boots nor fails the way it did before; "
        "something else is broken now",
    )


def count_actions(state_dir: Path, since: str) -> int:
    """How many things the agent actually did to the machine.

    Counted from the audit log, and observe-tier entries do not count:
    looking at the screen is not an action, and an agent that takes a hundred
    screenshots is not being less careful than one that takes two.
    """
    return sum(
        1
        for entry in audit.read(state_dir)
        if entry.get("timestamp", "") >= since and entry.get("tier") != "observe"
    )


def run_attempt(
    fault_name: str,
    agent_command: str,
    *,
    run: int,
    timeout: float,
    state_dir: Path,
) -> Attempt:
    """Reset a machine, let the agent loose on it, then grade the result."""
    lab.reset(fault_name)
    machine = lab.load_vm(fault_name)
    fault = get_fault(fault_name)

    target = QemuTarget.launch(
        machine.patient_disk,
        name=machine.name,
        mode=fault.repair_mode,
        serial_log=machine.directory / "boot.log",
        run_dir=machine.directory / "run",
    )
    session_state.save(
        session_state.Session(
            name=machine.name,
            qmp_socket=str(target.qmp_socket),
            mode=str(fault.repair_mode),
            os_family=str(OsFamily.LINUX),
            disk=str(machine.patient_disk),
            serial_log=str(machine.directory / "boot.log"),
        )
    )
    target.close()

    started = time.monotonic()
    since = datetime.now(UTC).isoformat(timespec="seconds")
    environment = dict(os.environ)
    environment["WARD_FAULT"] = fault_name
    environment["WARD_VM"] = machine.name
    environment["WARD_UNATTENDED"] = "1"

    try:
        completed = subprocess.run(  # noqa: S602 - the operator supplies this
            agent_command,
            shell=True,
            timeout=timeout,
            capture_output=True,
            text=True,
            env=environment,
        )
        agent_exit = completed.returncode
    except subprocess.TimeoutExpired:
        agent_exit = -1

    elapsed = time.monotonic() - started
    actions = count_actions(state_dir, since)

    # Power-cycle and watch it come up, because the only repair that counts
    # is one that survives a reboot.
    attached = QemuTarget.attach(target.qmp_socket, name=machine.name)
    attached.shutdown()
    session_state.clear()

    check = lab.check(fault_name)
    serial = check.serial_log.read_text(encoding="utf-8", errors="replace")
    outcome, detail = _grade(fault_name, serial)
    if agent_exit == -1:
        detail = f"the agent ran out of time after {timeout:.0f}s; {detail}"

    return Attempt(
        fault=fault_name,
        run=run,
        outcome=outcome,
        seconds=round(elapsed, 1),
        actions=actions,
        agent_exit_code=agent_exit,
        detail=detail,
    )


def run_evaluation(
    agent_command: str,
    *,
    faults: list[str] | None = None,
    runs: int = 1,
    timeout: float = 600.0,
    state_dir: Path | None = None,
) -> dict[str, object]:
    """Run an agent against every fault, ``runs`` times each."""
    state = state_dir or session_state.state_dir()
    names = faults or sorted(FAULTS)
    missing = [name for name in names if not lab.vm_exists(name)]
    if missing:
        raise EvalError(
            "these lab VMs do not exist yet: "
            + ", ".join(missing)
            + ". Build them with 'ward lab create <fault>' first."
        )

    attempts: list[Attempt] = []
    for name in names:
        for run in range(1, runs + 1):
            attempts.append(
                run_attempt(
                    name, agent_command, run=run, timeout=timeout, state_dir=state
                )
            )

    tally = {FIXED: 0, NOT_FIXED: 0, MADE_WORSE: 0}
    for attempt in attempts:
        tally[attempt.outcome] += 1

    return {
        "agent": agent_command,
        "runs_per_fault": runs,
        "attempts": [attempt.to_dict() for attempt in attempts],
        "totals": tally,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def results_table(results: dict[str, object]) -> str:
    """Render an evaluation as the plain text table you would quote."""
    rows = [("fault", "run", "outcome", "seconds", "actions")]
    for attempt in results["attempts"]:  # type: ignore[index]
        rows.append(
            (
                str(attempt["fault"]),
                str(attempt["run"]),
                str(attempt["outcome"]),
                f"{attempt['seconds']:.0f}",
                str(attempt["actions"]),
            )
        )
    widths = [max(len(row[column]) for row in rows) for column in range(5)]
    lines = [
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    ]
    lines.insert(1, "  ".join("-" * width for width in widths))
    totals = results["totals"]  # type: ignore[index]
    lines.append("")
    lines.append(
        f"fixed {totals[FIXED]} · not fixed {totals[NOT_FIXED]} · "
        f"made worse {totals[MADE_WORSE]}"
    )
    return "\n".join(lines)


def write_results(results: dict[str, object], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return destination
