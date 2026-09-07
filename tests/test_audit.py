"""The audit log has to reconstruct a session, and has to be append-only."""

from __future__ import annotations

import json
from pathlib import Path

from ward.audit import AuditEntry, audit_path, iter_entries, now, read, record


def entry(command: str, **kwargs) -> AuditEntry:
    defaults = {
        "timestamp": now(),
        "command": command,
        "tier": "observe",
        "arguments": {},
        "target": "patient",
        "mode": "live",
        "exit_code": 0,
    }
    defaults.update(kwargs)
    return AuditEntry(**defaults)


def test_entries_come_back_in_the_order_they_happened(tmp_path: Path) -> None:
    for command in ("screenshot", "type", "power reset"):
        record(tmp_path, entry(command))
    assert [item["command"] for item in read(tmp_path)] == [
        "screenshot",
        "type",
        "power reset",
    ]


def test_recording_appends_and_never_rewrites(tmp_path: Path) -> None:
    record(tmp_path, entry("screenshot"))
    first = audit_path(tmp_path).read_text(encoding="utf-8")
    record(tmp_path, entry("type"))
    second = audit_path(tmp_path).read_text(encoding="utf-8")
    assert second.startswith(first)


def test_a_session_can_be_reconstructed_from_the_log(tmp_path: Path) -> None:
    """The point of the log: what was on screen, what was done, what happened."""
    record(
        tmp_path,
        entry(
            "screenshot",
            screenshot_sha256="abc123",
            screenshot_path="/shots/0.png",
            result={"path": "/shots/0.png"},
        ),
    )
    record(
        tmp_path,
        entry("type", tier="modify", arguments={"text": "e2fsck -y /dev/vda1"}),
    )
    record(tmp_path, entry("power reset", tier="destroy", exit_code=0))

    entries = read(tmp_path)
    assert entries[0]["screenshot_sha256"] == "abc123"
    assert entries[1]["arguments"]["text"] == "e2fsck -y /dev/vda1"
    assert entries[2]["tier"] == "destroy"
    assert all(item["mode"] == "live" for item in entries)


def test_a_failed_command_records_why(tmp_path: Path) -> None:
    record(tmp_path, entry("type", exit_code=1, error="no machine is attached"))
    assert read(tmp_path)[0]["error"] == "no machine is attached"


def test_reading_an_absent_log_is_not_an_error(tmp_path: Path) -> None:
    assert read(tmp_path) == []


def test_a_corrupt_line_is_surfaced_rather_than_skipped(tmp_path: Path) -> None:
    """A log that quietly hides what it could not read is worse than none."""
    record(tmp_path, entry("screenshot"))
    with audit_path(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write("{this is not json\n")
    record(tmp_path, entry("type"))

    entries = list(iter_entries(audit_path(tmp_path)))
    assert [item["command"] for item in entries] == [
        "screenshot",
        "<unreadable>",
        "type",
    ]
    assert "could not parse" in entries[1]["error"]


def test_every_entry_is_one_line_of_json(tmp_path: Path) -> None:
    record(tmp_path, entry("type", arguments={"text": "line one\nline two"}))
    lines = audit_path(tmp_path).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["arguments"]["text"] == "line one\nline two"
