"""Which machine ``ward`` is talking to right now.

Every CLI invocation is a separate process, but the patient is not: it is a
VM that keeps running between commands. The session file is the small piece
of state that lets ``ward screenshot`` and ``ward type`` reach the same
machine.

It records the mode as well as the socket, because mode is the one thing no
caller may infer — see the top of ``CLAUDE.md``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ward.errors import TargetUnavailable
from ward.types import Mode, OsFamily


def state_dir() -> Path:
    """Where Ward keeps the current session.

    ``WARD_STATE_DIR`` overrides it; otherwise the XDG state directory, so a
    session survives between shells but never lands in the repository.
    """
    override = os.environ.get("WARD_STATE_DIR")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "ward"


def session_path() -> Path:
    return state_dir() / "session.json"


@dataclass(frozen=True)
class Session:
    """The machine ``ward`` commands act on until told otherwise."""

    name: str
    qmp_socket: str
    mode: str
    os_family: str
    #: Present when Ward launched the VM itself.
    disk: str | None = None
    pid: int | None = None
    serial_log: str | None = None
    attached_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def save(session: Session) -> Path:
    """Write the session, replacing whatever was there."""
    path = session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = session.to_dict()
    record["attached_at"] = session.attached_at or datetime.now(UTC).isoformat(
        timespec="seconds"
    )
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def load() -> Session:
    """Return the current session, or explain how to start one."""
    path = session_path()
    if not path.exists():
        raise TargetUnavailable(
            "no machine is attached. Start one with 'ward lab boot <vm>', or "
            "point Ward at a VM you already have with "
            "'ward attach --qmp <socket>'. Use '--target fake' to work "
            "against the fake target on purpose."
        )
    record = json.loads(path.read_text(encoding="utf-8"))
    return Session(**record)


def clear() -> bool:
    """Forget the current session. Returns whether there was one."""
    path = session_path()
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def mode_of(session: Session) -> Mode:
    return Mode(session.mode)


def os_family_of(session: Session) -> OsFamily:
    return OsFamily(session.os_family)
