"""The append-only record of everything Ward did.

One JSON object per line, appended and never rewritten. The point is that a
session can be reconstructed afterwards: what was on the screen, what was
typed, what happened next. That is what makes a run reviewable, and it is
what ``ward replay`` reads.

Screenshots are recorded by hash and path, not inline. A log you cannot open
in a text editor is a log nobody reads.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AUDIT_FILENAME = "audit.jsonl"


@dataclass(frozen=True)
class AuditEntry:
    """One thing Ward was asked to do, and how it went."""

    timestamp: str
    command: str
    tier: str
    arguments: dict[str, object]
    target: str
    mode: str
    exit_code: int
    result: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    screenshot_sha256: str | None = None
    screenshot_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def audit_path(state_dir: Path) -> Path:
    return state_dir / AUDIT_FILENAME


def record(state_dir: Path, entry: AuditEntry) -> Path:
    """Append one entry. Opened in append mode so nothing can be rewritten."""
    path = audit_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return path


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read(state_dir: Path) -> list[dict]:
    """Return every entry, oldest first."""
    return list(iter_entries(audit_path(state_dir)))


def iter_entries(path: Path) -> Iterator[dict]:
    """Yield entries from a log file, skipping nothing silently.

    A malformed line is surfaced as an entry of its own rather than dropped:
    a log that quietly hides what it could not parse is worse than no log.
    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                yield {
                    "timestamp": "",
                    "command": "<unreadable>",
                    "tier": "observe",
                    "arguments": {"line": number},
                    "target": "",
                    "mode": "",
                    "exit_code": -1,
                    "error": f"could not parse this line: {exc}",
                }
