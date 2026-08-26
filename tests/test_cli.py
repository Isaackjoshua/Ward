"""Tests for the ward CLI."""

import json

import pytest

from ward.cli import main


def test_describe_prints_the_fake_targets_description_as_json(capsys):
    exit_code = main(["describe"])

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
    main(["describe"])

    err = capsys.readouterr().err
    assert "fake-target" in err
    assert "live" in err


def test_json_flag_is_accepted(capsys):
    assert main(["describe", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["description"]["name"] == "fake-target"


def test_unknown_target_fails_loudly():
    with pytest.raises(SystemExit) as excinfo:
        main(["--target", "qemu", "describe"])

    assert "unknown target" in str(excinfo.value)


def test_no_subcommand_is_an_error():
    with pytest.raises(SystemExit):
        main([])
