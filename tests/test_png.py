"""Tests for the stdlib PNG encoder."""

import struct

import pytest

from ward.png import solid_png

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_solid_png_has_png_magic_and_declared_size():
    png = solid_png(64, 32, (1, 2, 3))

    assert png.startswith(PNG_MAGIC)
    # IHDR payload starts 16 bytes in: magic (8) + length (4) + b"IHDR" (4).
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (64, 32)


def test_solid_png_ends_with_iend():
    assert solid_png(2, 2, (0, 0, 0)).endswith(b"IEND\xae\x42\x60\x82")


@pytest.mark.parametrize(("width", "height"), [(0, 10), (10, 0), (-1, 4)])
def test_solid_png_rejects_non_positive_dimensions(width, height):
    with pytest.raises(ValueError):
        solid_png(width, height, (0, 0, 0))
