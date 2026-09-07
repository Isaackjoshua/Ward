"""Typing is the hands half of the contract; getting it wrong is silent.

A wrong keycode does not raise — it types the wrong character into a
bootloader and the agent spends the rest of the run confused.
"""

from __future__ import annotations

import pytest

from ward.keymap import (
    UntypeableCharacter,
    key_for_char,
    keys_for_text,
    normalise_key,
    parse_combo,
)


@pytest.mark.parametrize(
    ("char", "expected"),
    [
        ("a", ["a"]),
        ("z", ["z"]),
        ("7", ["7"]),
        ("A", ["shift", "a"]),
        (" ", ["spc"]),
        ("\n", ["ret"]),
        ("\t", ["tab"]),
        ("-", ["minus"]),
        ("_", ["shift", "minus"]),
        ("/", ["slash"]),
        ("?", ["shift", "slash"]),
        ("=", ["equal"]),
        ("$", ["shift", "4"]),
        ("|", ["shift", "backslash"]),
        (":", ["shift", "semicolon"]),
        ('"', ["shift", "apostrophe"]),
    ],
)
def test_characters_map_to_the_keys_that_produce_them(
    char: str, expected: list[str]
) -> None:
    assert key_for_char(char) == expected


def test_a_realistic_repair_command_is_typeable() -> None:
    """The exact shape of thing an agent will type at a rescue shell."""
    text = 'mount -o rw,remount / && sed -i "s/UUID=bad/UUID=good/" /etc/fstab\n'
    groups = keys_for_text(text)
    assert len(groups) == len(text)
    assert groups[-1] == ["ret"]


def test_a_character_with_no_key_says_so_instead_of_guessing() -> None:
    with pytest.raises(UntypeableCharacter, match="US ASCII"):
        key_for_char("é")

    with pytest.raises(UntypeableCharacter, match="US layout"):
        key_for_char("\x00")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("enter", "ret"),
        ("Return", "ret"),
        ("ESCAPE", "esc"),
        ("space", "spc"),
        ("ctrl", "ctrl"),
        ("control", "ctrl"),
        ("f2", "f2"),
        ("a", "a"),
        ("A", "a"),
    ],
)
def test_key_names_people_use_reach_the_keys_qemu_knows(
    name: str, expected: str
) -> None:
    assert normalise_key(name) == expected


def test_combo_accepts_both_shapes() -> None:
    assert parse_combo(["ctrl", "alt", "f2"]) == ["ctrl", "alt", "f2"]
    assert parse_combo("ctrl-alt-f2") == ["ctrl", "alt", "f2"]
    assert parse_combo("ctrl+alt+delete") == ["ctrl", "alt", "delete"]


def test_an_empty_combo_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one key"):
        parse_combo([])
