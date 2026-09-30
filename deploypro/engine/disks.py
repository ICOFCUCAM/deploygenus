"""The disks DeployPro fills: the one holding the build root, and the one
holding Docker's images, which is a different disk once the image store has
been moved to its own volume.

Regression (September 2026): after BalanceVid's host moved Docker's images to
a Hetzner Volume, the disk alert and the check before each build still read
only the build root, on the server's own disk. The Volume, where every image
and layer now lands, could have filled with no alert and no check.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from deploypro.config import Settings
from deploypro.domain.maintenance import Disk


def _measure(label: str, path: Path) -> tuple[int, Disk] | None:
    try:
        device = os.stat(path).st_dev
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return device, Disk(label, usage.total, usage.used, usage.free)


def measure(settings: Settings) -> list[Disk]:
    """One entry per distinct disk: `Disk` alone on a single-disk host,
    `Server disk` and `Image storage` when the images live elsewhere."""
    server = _measure("Server disk", settings.build_root)
    images = (
        _measure("Image storage", settings.image_store_dir)
        if settings.image_store_dir is not None
        else None
    )
    if server and images and server[0] != images[0]:
        return [server[1], images[1]]
    only = server or images
    if only is None:
        return []
    disk = only[1]
    return [Disk("Disk", disk.total, disk.used, disk.free)]


def paths(settings: Settings) -> list[tuple[str, Path]]:
    """(name, a path on it) for each distinct disk a build writes to, the
    server's first. For the pre-build check and the disk alert."""
    found = [("the server's disk", Path(settings.build_root))]
    store = settings.image_store_dir
    if store is not None:
        try:
            if os.stat(store).st_dev != os.stat(settings.build_root).st_dev:
                found.append(("the image storage disk", Path(store)))
        except OSError:
            pass
    return found
