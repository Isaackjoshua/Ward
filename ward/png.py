"""A minimal PNG encoder, standard library only.

Ward has no image dependency in phase one. Two things need one: the canned
screenshots :class:`ward.fake.FakeTarget` hands out, and real screenshots
from QEMU, which arrive as PPM on older QEMU builds and have to be re-encoded
before anything can look at them.

8-bit truecolour RGB, no filtering, is enough for both.
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


def encode_rgb(width: int, height: int, pixels: bytes) -> bytes:
    """Return PNG bytes for ``width`` x ``height`` packed RGB triples.

    ``pixels`` is one flat run of ``R, G, B`` bytes, top row first, with no
    padding — which is exactly what a binary PPM body is.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"image must have positive dimensions, got {width}x{height}")
    expected = width * height * 3
    if len(pixels) != expected:
        raise ValueError(
            f"expected {expected} bytes of RGB for {width}x{height}, got {len(pixels)}"
        )

    # Each scanline is a filter byte (0 = no filter) followed by RGB triples.
    stride = width * 3
    raw = b"".join(
        b"\x00" + pixels[offset : offset + stride]
        for offset in range(0, expected, stride)
    )

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


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Return PNG bytes for a ``width`` x ``height`` image of one colour."""
    if width <= 0 or height <= 0:
        raise ValueError(f"image must have positive dimensions, got {width}x{height}")
    return encode_rgb(width, height, bytes(rgb) * (width * height))
