"""The read-only base image every fault VM is built from.

Ward downloads the official Debian cloud image rather than building a root
filesystem with ``debootstrap``, because debootstrap needs root on the host
and Ward must not. See D1 in ``docs/DECISIONS.md``.

The file is fetched once, checked against Debian's published SHA512SUMS, and
then made read-only. Nothing in the lab ever writes to it; fault VMs are
qcow2 overlays on top.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ward.errors import WardError
from ward.lab.paths import images_dir

_MIRROR = "https://cloud.debian.org/images/cloud/bookworm/latest"

# Read in chunks so a 400 MB image never lands in memory all at once.
_CHUNK = 1 << 20


@dataclass(frozen=True)
class BaseImage:
    """A base image Ward knows how to fetch and verify."""

    name: str
    filename: str
    url: str
    checksums_url: str
    # The login the guest's cloud-init creates. The lab needs it to talk to a
    # healthy machine; broken ones never get this far.
    default_user: str

    @property
    def path(self) -> Path:
        """Where this image lives once fetched."""
        return images_dir() / self.filename


DEBIAN_12 = BaseImage(
    name="debian-12",
    filename="debian-12-genericcloud-amd64.qcow2",
    url=f"{_MIRROR}/debian-12-genericcloud-amd64.qcow2",
    checksums_url=f"{_MIRROR}/SHA512SUMS",
    default_user="debian",
)

BASE_IMAGES: dict[str, BaseImage] = {DEBIAN_12.name: DEBIAN_12}

DEFAULT_BASE = DEBIAN_12


class BaseImageError(WardError):
    """The base image could not be fetched or did not match its checksum."""


def sha512_of(path: Path) -> str:
    """Return the hex SHA-512 of a file, read in chunks."""
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def published_checksum(image: BaseImage, *, timeout: float = 60.0) -> str:
    """Return the checksum Debian publishes for this image.

    SHA512SUMS is a plain ``<hex>  <filename>`` list covering every image in
    the directory, so we pick out our own line.
    """
    with urllib.request.urlopen(image.checksums_url, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    for line in body.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == image.filename:
            return parts[0]
    raise BaseImageError(
        f"{image.filename} is not listed in {image.checksums_url}; "
        "the mirror layout may have changed"
    )


def ensure_base_image(
    image: BaseImage = DEFAULT_BASE,
    *,
    timeout: float = 900.0,
    verify: bool = True,
) -> Path:
    """Return the path to the base image, downloading it if it is missing.

    Downloads land in a ``.part`` file first, so an interrupted run never
    leaves behind a truncated image that looks complete.
    """
    destination = image.path
    if destination.exists():
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")

    try:
        with urllib.request.urlopen(image.url, timeout=timeout) as response:
            with partial.open("wb") as handle:
                shutil.copyfileobj(response, handle, _CHUNK)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise BaseImageError(f"could not download {image.url}: {exc}") from exc

    if verify:
        expected = published_checksum(image)
        actual = sha512_of(partial)
        if actual != expected:
            partial.unlink(missing_ok=True)
            raise BaseImageError(
                f"{image.filename} failed its checksum: expected {expected}, "
                f"got {actual}. The download is corrupt; nothing was kept."
            )

    partial.rename(destination)
    # Read-only, so a stray write to the base cannot silently poison every
    # fault VM built on top of it.
    destination.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return destination
