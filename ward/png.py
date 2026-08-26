"""A minimal PNG encoder, standard library only.

Ward has no image dependency in phase one. The only picture it needs to
manufacture is a canned screenshot for :class:`ward.fake.FakeTarget`, so a
flat-colour 8-bit RGB encoder is enough. Real drivers get their PNG bytes
from the transport and never come through here.
"""

from __future__ import annotations

import struct
import zlib


def _chunk(kind: bytes, payload: bytes) -> bytes:
    """Return one length-prefixed, CRC-suffixed PNG chunk."""
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Return PNG bytes for a ``width`` x ``height`` image of one colour."""
    if width <= 0 or height <= 0:
        raise ValueError(f"image must have positive dimensions, got {width}x{height}")

    # Each scanline is a filter byte (0 = no filter) followed by RGB triples.
    row = bytes([0]) + bytes(rgb) * width
    raw = row * height

    header = struct.pack(
        ">IIBBBBB",
        width,
        height,
        8,  # bit depth
        2,  # colour type: truecolour RGB
        0,  # compression: deflate
        0,  # filter method
        0,  # interlace: none
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
