"""Tests for the ward CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ward.cli import main


@pytest.fixture(autouse=True)
def scratch_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never read or write the developer's real session file."""
    monkeypatch.setenv("WARD_STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def test_describe_prints_the_fake_targets_description_as_json(capsys):
    exit_code = main(["--target", "fake", "describe"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["description"] == {
        "transport": "fake",
        "mode": "live",
        "os_family": "unknown",
        "bandwidth_class": "fast",
        "name": "fake-target",
    }
    assert payload["capabilities"]["can_screenshot"] is True
    assert payload["capabilities"]["can_exec"] is False
    assert payload["capabilities"]["max_tier"] == "modify"


def test_describe_narrates_to_stderr_in_plain_english(capsys):
    main(["--target", "fake", "describe"])

    err = capsys.readouterr().err
    assert "fake-target" in err
    assert "live" in err


def test_json_flag_is_accepted(capsys):
    assert main(["--target", "fake", "describe", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["description"]["name"] == "fake-target"


def test_with_nothing_attached_ward_refuses_rather_than_faking_it(capsys):
    """The fake target must never stand in for a patient by accident.

    An agent that believes it is looking at a broken machine while actually
    looking at a canned image would do real damage with total confidence.
    """
    assert main(["describe"]) == 1
    err = capsys.readouterr().err
    assert "no machine is attached" in err
    assert "--target fake" in err


def test_unknown_target_fails_loudly():
    with pytest.raises(SystemExit) as excinfo:
        main(["--target", "hardware", "describe"])

    assert "unknown target" in str(excinfo.value)


def test_screenshot_writes_a_png_where_it_says_it_did(capsys, tmp_path: Path):
    out = tmp_path / "shots" / "screen.png"
    assert main(["--target", "fake", "screenshot", "--out", str(out)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == str(out)
    assert out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (payload["width"], payload["height"]) == (640, 480)


def test_type_records_what_it_typed(capsys):
    assert main(["--target", "fake", "type", "e2fsck -y /dev/vda1", "--enter"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["typed"].endswith("\n")
    assert payload["characters"] == len("e2fsck -y /dev/vda1") + 1


def test_key_can_repeat(capsys):
    assert main(["--target", "fake", "key", "down", "--repeat", "3"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"combo": "down", "repeat": 3}


def test_detach_with_nothing_attached_is_not_an_error(capsys):
    assert main(["detach"]) == 0
    assert "nothing was attached" in capsys.readouterr().err


def test_no_subcommand_is_an_error():
    with pytest.raises(SystemExit):
        main([])
