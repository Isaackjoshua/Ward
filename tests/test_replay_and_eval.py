"""Rendering a session, and grading what an agent did to a machine."""

from __future__ import annotations

from pathlib import Path

from ward.audit import AuditEntry, record
from ward.evaluate import (
    FIXED,
    MADE_WORSE,
    NOT_FIXED,
    _grade,
    count_actions,
    results_table,
)
from ward.lab.lab import BootCheck
from ward.png import solid_png
from ward.replay import render_timeline, summarise


def entry(command: str, tier: str = "observe", **kwargs) -> dict:
    base = {
        "timestamp": "2026-09-07T12:00:00+00:00",
        "command": command,
        "tier": tier,
        "arguments": {},
        "target": "patient",
        "mode": "offline",
        "exit_code": 0,
        "result": {},
        "error": None,
        "screenshot_sha256": None,
        "screenshot_path": None,
    }
    base.update(kwargs)
    return base


def test_the_timeline_shows_every_step(tmp_path: Path) -> None:
    out = tmp_path / "replay.html"
    render_timeline(
        [
            entry("screenshot"),
            entry("type", "modify", arguments={"text": "e2fsck -y /dev/vda1"}),
            entry("power reset", "destroy"),
        ],
        out,
    )
    html = out.read_text(encoding="utf-8")
    assert "screenshot" in html
    assert "e2fsck -y /dev/vda1" in html
    assert "power reset" in html
    assert "3 steps" in html


def test_screenshots_are_linked_relative_so_the_page_opens_from_disk(
    tmp_path: Path,
) -> None:
    shot = tmp_path / "shots" / "0.png"
    shot.parent.mkdir()
    shot.write_bytes(solid_png(4, 4, (0, 0, 0)))
    out = tmp_path / "replay.html"
    render_timeline([entry("screenshot", screenshot_path=str(shot))], out)
    assert 'src="shots/0.png"' in out.read_text(encoding="utf-8")


def test_a_failed_step_is_marked_and_its_error_shown(tmp_path: Path) -> None:
    out = tmp_path / "replay.html"
    render_timeline([entry("type", exit_code=1, error="no machine is attached")], out)
    html = out.read_text(encoding="utf-8")
    assert "failed" in html
    assert "no machine is attached" in html


def test_text_from_a_session_cannot_inject_markup(tmp_path: Path) -> None:
    """Typed text ends up in the page; it must not become part of the page."""
    out = tmp_path / "replay.html"
    render_timeline(
        [entry("type", arguments={"text": "<script>alert(1)</script>"})], out
    )
    html = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_summary_counts_what_matters() -> None:
    assert summarise(
        [
            entry("screenshot", screenshot_sha256="a"),
            entry("power reset", "destroy", exit_code=1),
        ]
    ) == {"steps": 2, "failed": 1, "destroy_actions": 1, "screenshots": 1}


# -- grading -----------------------------------------------------------

HEALTHY = "Debian GNU/Linux 12 patient ttyS0\npatient login:"


def test_a_machine_that_boots_is_fixed() -> None:
    outcome, why = _grade("fstab-bad-uuid", HEALTHY)
    assert outcome == FIXED
    assert "login prompt" in why


def test_a_machine_still_failing_its_own_way_is_not_fixed() -> None:
    outcome, _ = _grade("initramfs-corrupt", "Kernel panic - not syncing: VFS")
    assert outcome == NOT_FIXED


def test_a_machine_broken_a_new_way_is_made_worse() -> None:
    """The outcome that matters. Four fixes and one wrecked disk is not 80%."""
    outcome, why = _grade("fstab-bad-uuid", "SeaBIOS ... No bootable device")
    assert outcome == MADE_WORSE
    assert "something else is broken" in why


def test_grading_reads_the_channel_the_evidence_actually_lands_on() -> None:
    """Regression: an untouched machine was once graded 'made worse'.

    Since the screen became /dev/console, systemd's messages go to the second
    serial port and the kernel's to the first. Grading from one file alone
    saw no recognisable failure and concluded something new had broken. These
    are the real lines from that run.
    """
    kernel_only = "[    0.720000] Linux version 6.1.0-18-amd64\n"
    systemd_only = (
        "Sep 07 11:52:58 localhost systemd[1]: Dependency failed for "
        "srv-data.mount - /srv/data.\n"
        "Sep 07 11:52:58 localhost systemd[1]: Dependency failed for "
        "local-fs.target - Local File Systems.\n"
    )
    assert _grade("fstab-bad-uuid", kernel_only)[0] == MADE_WORSE
    assert _grade("fstab-bad-uuid", kernel_only + systemd_only)[0] == NOT_FIXED


def test_a_boot_check_carries_everything_the_machine_said() -> None:
    """So nobody downstream has to guess which log file to open."""
    check = BootCheck(
        vm="x",
        fault="fstab-bad-uuid",
        broke_as_expected=True,
        seconds_observed=1.0,
        serial_log=Path("/lab/x/boot.log"),
        tail="",
        transcript="kernel said this\nsystemd said that",
    )
    assert "systemd said that" in check.transcript
    assert check.journal_log == Path("/lab/x/journal.log")
    # And the transcript stays out of the JSON: it is large and already on disk.
    assert "transcript" not in check.to_dict()
    assert check.to_dict()["journal_log"] == "/lab/x/journal.log"


def test_network_down_is_reported_as_ungradeable_not_guessed() -> None:
    outcome, why = _grade("network-down", HEALTHY)
    assert outcome == NOT_FIXED
    assert "cannot be graded from the serial console" in why


def test_looking_at_the_screen_is_not_an_action(tmp_path: Path) -> None:
    for tier, command in (("observe", "screenshot"), ("modify", "type")):
        record(
            tmp_path,
            AuditEntry(
                timestamp="2026-09-07T12:00:00+00:00",
                command=command,
                tier=tier,
                arguments={},
                target="patient",
                mode="live",
                exit_code=0,
            ),
        )
    assert count_actions(tmp_path, "2026-09-07T11:00:00+00:00") == 1


def test_the_results_table_is_something_you_could_quote() -> None:
    results = {
        "attempts": [
            {
                "fault": "fstab-bad-uuid",
                "run": 1,
                "outcome": FIXED,
                "seconds": 42.0,
                "actions": 7,
            }
        ],
        "totals": {FIXED: 1, NOT_FIXED: 0, MADE_WORSE: 0},
    }
    table = results_table(results)
    assert "fstab-bad-uuid" in table
    assert "fixed 1" in table
    assert "made worse 0" in table
