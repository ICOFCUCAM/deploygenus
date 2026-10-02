"""Clean-ups and backups as recorded runs.

The worker runs both on its own schedule, and on request from the System
page. Every run, whoever started it, is a row in `maintenance_runs`, so the
page can say when each last happened and what it did. What only the worker
can see — the backup directory, the build cache's size — it copies into
`system_state` for the dashboard to read.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from deploypro.adapters import containers
from deploypro.config import Settings
from deploypro.domain.maintenance import (
    MaintenanceRun,
    cleanup_errors,
    describe_cleanup,
    size,
)
from deploypro.engine import backup, housekeeping, offsite
from deploypro.repositories import maintenance as maintenance_repo

logger = logging.getLogger("deploypro.maintenance")

#: Images, build cache, build folders, logs: see `housekeeping.run`.
CLEANUP_STEPS = 4


async def run_cleanup(settings: Settings, run: MaintenanceRun) -> dict[str, object]:
    """Clear old images, build cache, build folders and logs; record it."""
    try:
        report = await housekeeping.run(settings)
        cache = await containers.build_cache_bytes()
        if cache is not None:
            await maintenance_repo.set_state("build_cache", {"bytes": cache})
        await maintenance_repo.delete_old()
    except Exception as exc:
        await maintenance_repo.fail(run.id, error=f"{type(exc).__name__}: {exc}")
        raise
    errors = cleanup_errors(report)
    if len(errors) == CLEANUP_STEPS:
        # Nothing worked at all: a failure, not a clean-up that found nothing.
        await maintenance_repo.fail(run.id, error="; ".join(errors))
    else:
        await maintenance_repo.finish(
            run.id,
            summary=describe_cleanup(report),
            detail={**_jsonable(report), "cache_bytes": cache},
        )
    return report


async def run_backup(settings: Settings, run: MaintenanceRun) -> None:
    """Take a backup exactly as the daily one does; record it."""
    if settings.backup_dir is None:
        await maintenance_repo.fail(
            run.id, error="Backups are off: DEPLOYPRO_BACKUP_DIR is not set."
        )
        return
    try:
        result = await backup.take(
            settings, settings.backup_dir, keep=settings.backup_keep
        )
    except backup.BackupSkipped:
        await maintenance_repo.fail(run.id, error="Another backup was already running.")
        return
    except Exception as exc:
        await maintenance_repo.fail(run.id, error=f"{type(exc).__name__}: {exc}")
        raise
    await maintenance_repo.finish(
        run.id,
        summary=f"{size(result.bytes)} · {len(result.volumes)} volume(s)",
        detail={
            "path": str(result.path),
            "bytes": result.bytes,
            "volumes": result.volumes,
            "removed": [p.name for p in result.removed],
        },
    )
    await publish_backups(settings)
    # Off the server, when a target is set. Raises offsite.CopyFailed after
    # recording it: the backup itself stands either way.
    await offsite.copy(settings, result.path, keep=settings.backup_keep)


async def publish_backups(settings: Settings) -> None:
    """Copy what is in the backup directory into `system_state`, for a
    dashboard that does not mount it. Also covers backups taken before runs
    were recorded, and ones copied in or deleted by hand."""
    if settings.backup_dir is None:
        await maintenance_repo.set_state("backups", {"enabled": False})
        return
    dest = Path(settings.backup_dir)
    newest = backup.latest(dest) if dest.is_dir() else None
    count = (
        sum(
            1
            for p in dest.glob(f"{backup.PREFIX}*")
            if p.is_dir() and not p.name.endswith(backup.PARTIAL)
        )
        if dest.is_dir()
        else 0
    )
    fact: dict[str, object] = {"enabled": True, "dir": str(dest), "count": count}
    if newest is not None:
        fact["newest"] = newest.name
        taken = _taken_at(newest.name)
        if taken is not None:
            fact["newest_at"] = taken.isoformat()
        fact["newest_bytes"] = sum(
            f.stat().st_size for f in newest.rglob("*") if f.is_file()
        )
    await maintenance_repo.set_state("backups", fact)


def _taken_at(name: str) -> datetime | None:
    try:
        return datetime.strptime(
            name.removeprefix(backup.PREFIX), "%Y%m%dT%H%M%SZ"
        ).replace(tzinfo=UTC)
    except ValueError:
        return None


def _jsonable(report: dict[str, object]) -> dict[str, object]:
    return {
        k: (v if isinstance(v, (str, int, float, bool, list, type(None))) else str(v))
        for k, v in report.items()
    }
