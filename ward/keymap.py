"""Turning text and key names into QEMU key codes.

QEMU's ``sendkey`` speaks *qcodes*: physical key names like ``bracket_left``,
not characters. Typing ``$`` means holding shift and pressing the ``4`` key.
Ward does that translation here, in one place, so no driver has to.

This is a US layout. A machine set to another layout will receive the wrong
characters — that is a property of typing on a real keyboard too, and the fix
is to know the target's layout, not to guess.
"""

from __future__ import annotations

from ward.errors import WardError

#: Characters that live on an unshifted key of their own.
_UNSHIFTED: dict[str, str] = {
    " ": "spc",
    "\n": "ret",
    "\t": "tab",
    "`": "grave_accent",
    "-": "minus",
    "=": "equal",
    "[": "bracket_left",
    "]": "bracket_right",
    "\\": "backslash",
    ";": "semicolon",
    "'": "apostrophe",
    ",": "comma",
    ".": "dot",
    "/": "slash",
}

#: Characters reached by holding shift, and the key they share.
_SHIFTED: dict[str, str] = {
    "~": "grave_accent",
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "minus",
    "+": "equal",
    "{": "bracket_left",
    "}": "bracket_right",
    "|": "backslash",
    ":": "semicolon",
    '"': "apostrophe",
    "<": "comma",
    ">": "dot",
    "?": "slash",
}

#: What people call keys, and what QEMU calls them.
_ALIASES: dict[str, str] = {
    "enter": "ret",
    "return": "ret",
    "escape": "esc",
    "space": "spc",
    "del": "delete",
    "ins": "insert",
    "pageup": "pgup",
    "page_up": "pgup",
    "pagedown": "pgdn",
    "page_down": "pgdn",
    "control": "ctrl",
    "ctl": "ctrl",
    "windows": "meta_l",
    "super": "meta_l",
    "cmd": "meta_l",
    "backspace": "backspace",
    "period": "dot",
    "dash": "minus",
    "hyphen": "minus",
    "plus": "equal",
}


class UntypeableCharacter(WardError):
    """A character has no key on the layout Ward knows."""


def key_for_char(char: str) -> list[str]:
    """Return the qcodes that type one character.

    Two codes means shift is held: QEMU presses them together and releases
    them together, which is what a person does.
    """
    if len(char) != 1:
        raise ValueError(f"expected one character, got {char!r}")
    if not char.isascii():
        # 'é'.islower() is True, so this check has to come first or an accent
        # would be sent as a keycode QEMU has never heard of.
        raise UntypeableCharacter(
            f"cannot type {char!r}: Ward's keyboard is US ASCII. Getting "
            "other characters onto a machine needs its own layout, which is "
            "a decision about the target, not something to guess at."
        )
    if char.islower() or char.isdigit():
        return [char]
    if char.isupper():
        return ["shift", char.lower()]
    if char in _UNSHIFTED:
        return [_UNSHIFTED[char]]
    if char in _SHIFTED:
        return ["shift", _SHIFTED[char]]
    raise UntypeableCharacter(
        f"cannot type {char!r}: it has no key on a US layout. Ward types what "
        "a keyboard can type; anything else has to get onto the machine "
        "another way."
    )


def keys_for_text(text: str) -> list[list[str]]:
    """Return one qcode group per character of ``text``."""
    return [key_for_char(char) for char in text]


def normalise_key(name: str) -> str:
    """Return the qcode for a key someone named in English."""
    key = name.strip().lower()
    if not key:
        raise ValueError("a key name cannot be empty")
    key = _ALIASES.get(key, key)
    if len(key) == 1:
        return key_for_char(key)[-1]
    return key


def parse_combo(combo: str | list[str]) -> list[str]:
    """Return the qcodes for a chord.

    Accepts either a list — ``["ctrl", "alt", "f2"]`` — or the shorthand
    people actually type at a keyboard: ``"ctrl-alt-f2"``.
    """
    if isinstance(combo, str):
        parts = [part for part in combo.replace("+", "-").split("-") if part]
    else:
        parts = list(combo)
    if not parts:
        raise ValueError("a key combination needs at least one key")
    return [normalise_key(part) for part in parts]
