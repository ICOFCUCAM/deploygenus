"""Clean-ups, backups and disks, as values: what the System page and the
worker agree on, with no database or Docker behind it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

GB = 1024**3

KINDS = ("cleanup", "backup")

#: A run still `running` after this long had its worker stop under it. A
#: clean-up is minutes; a backup copies every volume, so it gets longer.
STALE_AFTER = {"cleanup": timedelta(hours=2), "backup": timedelta(hours=12)}

#: A backup this recent counts as "recent". A daily backup at 03:00 that took
#: up to two hours is still recent at 04:59 the next day.
RECENT_BACKUP = timedelta(hours=26)


@dataclass(frozen=True, slots=True)
class MaintenanceRun:
    id: int
    kind: str
    origin: str
    status: str
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    worker: str | None = None
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def open(self) -> bool:
        return self.status in ("requested", "running")


@dataclass(frozen=True, slots=True)
class Disk:
    """One filesystem, as `shutil.disk_usage` saw it.

    The percentage is `df`'s: used over used plus available, so the space
    reserved for root counts as neither and the number matches what the
    owner sees on the server.
    """

    label: str
    total: int
    used: int
    free: int

    @property
    def used_percent(self) -> int:
        if self.used + self.free <= 0:
            return 0
        return round(100 * self.used / (self.used + self.free))

    @property
    def free_gb(self) -> float:
        return self.free / GB

    @property
    def total_gb(self) -> float:
        return self.total / GB


def disk_condition(disk: Disk, *, alert_percent: int, min_free_gb: int) -> str:
    """`ok`, `caution` (at the alert threshold) or `full` (builds refused)."""
    if min_free_gb and disk.free_gb < min_free_gb:
        return "full"
    if disk.used_percent >= alert_percent:
        return "caution"
    return "ok"


_SIZE = re.compile(r"([\d.]+)\s*([kKMGT]?i?B)\b")
_UNITS = {
    "B": 1,
    "kB": 1000,
    "KB": 1000,
    "KiB": 1024,
    "MB": 1000**2,
    "MiB": 1024**2,
    "GB": 1000**3,
    "GiB": 1024**3,
    "TB": 1000**4,
    "TiB": 1024**4,
}


def parse_size(text: str) -> int | None:
    """Bytes from Docker's human sizes: `1.8GB`, `512MB`, `0B`, `3.2 GiB`.

    The last size in the text, because Docker's report lines end with it
    ("Total reclaimed space: 1.8GB").
    """
    found = _SIZE.findall(text or "")
    if not found:
        return None
    number, unit = found[-1]
    try:
        return int(float(number) * _UNITS.get(unit, 1))
    except ValueError:
        return None


def size(n: int | None) -> str:
    """`1.8 GB`, `640 MB`, `0 B` — decimal units, as Docker prints them."""
    if n is None:
        return ""
    if n >= 1000**3:
        return f"{n / 1000**3:.1f} GB"
    if n >= 1000**2:
        return f"{n / 1000**2:.0f} MB"
    if n >= 1000:
        return f"{n / 1000:.0f} kB"
    return f"{n} B"


def describe_cleanup(report: dict[str, Any]) -> str:
    """One sentence for what a clean-up removed, from `housekeeping.run`.

    Errors are named, not hidden: a clean-up whose image sweep failed has not
    done its job even if the cache prune worked.
    """
    parts = []
    images = len(report.get("images_removed") or [])
    if images:
        parts.append(f"{images} image{'s' if images != 1 else ''}")
    cache = report.get("build_cache")
    if isinstance(cache, str):
        freed = sum(parse_size(line) or 0 for line in cache.split("|"))
        if freed:
            parts.append(f"{size(freed)} of build cache")
    dirs = len(report.get("build_dirs_removed") or [])
    if dirs:
        parts.append(f"{dirs} leftover build folder{'s' if dirs != 1 else ''}")
    sentence = (
        " and ".join([", ".join(parts[:-1]), parts[-1]])
        if len(parts) > 1
        else "".join(parts)
    )
    sentence = f"{sentence} removed" if sentence else "nothing to remove"
    failed = [
        name
        for key, name in (
            ("images_error", "images"),
            ("build_cache_error", "build cache"),
            ("build_dirs_error", "build folders"),
            ("retention_error", "old logs"),
        )
        if report.get(key)
    ]
    if failed:
        sentence += f"; could not clear {', '.join(failed)}"
    return sentence


def cleanup_errors(report: dict[str, Any]) -> list[str]:
    return [str(v) for k, v in report.items() if k.endswith("_error") and v]
