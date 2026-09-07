"""Reading the binary PPM files QEMU's ``screendump`` writes.

QEMU 7.1 and later can write PNG directly. Older builds only write P6 PPM,
and Ward has to work on whatever QEMU the machine in front of it has, so it
converts. PPM is about as simple as an image format gets: a text header, then
raw RGB.
"""

from __future__ import annotations

from ward.errors import WardError
from ward.png import encode_rgb


class PpmError(WardError):
    """The file QEMU wrote is not a PPM we can read."""


def _next_token(data: bytes, offset: int) -> tuple[bytes, int]:
    """Return the next header token and the offset just past it.

    PPM headers separate fields with any whitespace and allow ``#`` comments
    to the end of the line, anywhere.
    """
    while offset < len(data):
        if data[offset : offset + 1].isspace():
            offset += 1
        elif data[offset : offset + 1] == b"#":
            while offset < len(data):
                if data[offset : offset + 1] in (b"\n", b"\r"):
                    break
                offset += 1
        else:
            break
    start = offset
    while offset < len(data) and not data[offset : offset + 1].isspace():
        offset += 1
    if start == offset:
        raise PpmError("PPM header ended before all of its fields were read")
    return data[start:offset], offset


def parse_ppm(data: bytes) -> tuple[int, int, bytes]:
    """Return ``(width, height, rgb_bytes)`` from a binary P6 PPM."""
    if not data.startswith(b"P6"):
        magic = data[:2].decode("ascii", "replace")
        raise PpmError(f"expected a binary P6 PPM, got magic {magic!r}")

    offset = 2
    fields = []
    for _ in range(3):
        token, offset = _next_token(data, offset)
        try:
            fields.append(int(token))
        except ValueError as exc:
            raise PpmError(f"bad PPM header field {token!r}") from exc
    width, height, maxval = fields

    if maxval != 255:
        raise PpmError(f"only 8-bit PPM is supported, this one has maxval {maxval}")
    if width <= 0 or height <= 0:
        raise PpmError(f"PPM has no pixels: {width}x{height}")

    # Exactly one whitespace byte separates the header from the pixel data.
    body = data[offset + 1 :]
    expected = width * height * 3
    if len(body) < expected:
        raise PpmError(
            f"PPM is truncated: {width}x{height} needs {expected} bytes of "
            f"pixel data, the file has {len(body)}"
        )
    return width, height, body[:expected]


def ppm_to_png(data: bytes) -> tuple[bytes, int, int]:
    """Return ``(png_bytes, width, height)`` for a binary P6 PPM."""
    width, height, pixels = parse_ppm(data)
    return encode_rgb(width, height, pixels), width, height
