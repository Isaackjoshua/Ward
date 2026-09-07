"""Reading QEMU's PPM screendumps and re-encoding them as PNG."""

from __future__ import annotations

import pytest

from ward.ppm import PpmError, parse_ppm, ppm_to_png


def make_ppm(width: int, height: int, header_sep: bytes = b"\n") -> bytes:
    pixels = bytes(range(256)) * ((width * height * 3) // 256 + 1)
    body = pixels[: width * height * 3]
    return b"P6" + header_sep + f"{width} {height}".encode() + b"\n255\n" + body


def test_a_plain_ppm_round_trips_to_png() -> None:
    png, width, height = ppm_to_png(make_ppm(8, 4))
    assert (width, height) == (8, 4)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert png.endswith(b"IEND\xae\x42\x60\x82")


def test_pixels_survive_the_conversion() -> None:
    """A screenshot that quietly loses pixels is worse than no screenshot."""
    _, _, pixels = parse_ppm(make_ppm(3, 2))
    assert pixels == bytes(range(18))


def test_comments_in_the_header_are_skipped() -> None:
    data = b"P6\n# written by qemu\n4 2\n255\n" + bytes(24)
    width, height, pixels = parse_ppm(data)
    assert (width, height) == (4, 2)
    assert len(pixels) == 24


def test_a_png_is_not_silently_treated_as_a_ppm() -> None:
    with pytest.raises(PpmError, match="binary P6"):
        parse_ppm(b"\x89PNG\r\n\x1a\n" + bytes(64))


def test_a_truncated_screendump_is_reported_not_padded() -> None:
    """Half a screenshot must never be handed over as if it were whole."""
    with pytest.raises(PpmError, match="truncated"):
        parse_ppm(b"P6\n64 64\n255\n" + bytes(100))


def test_sixteen_bit_ppm_is_refused_rather_than_misread() -> None:
    with pytest.raises(PpmError, match="maxval"):
        parse_ppm(b"P6\n2 2\n65535\n" + bytes(24))
