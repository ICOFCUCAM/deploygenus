"""The hourly tidy-up that keeps a single host from filling its own disk.

Three things grow without bound on a platform that deploys often: images (one
per commit), BuildKit's cache, and build logs. Each is cleared on its own
rule, and each step is independent, so one failing — Docker briefly busy, a
lock on the logs table — does not stop the others.

Run on the deploy loop, between deploys. That is what makes the image sweep
safe on a one-worker host: no build of this worker's can be half-finished
while it runs, and a build on another worker is protected by deriving its
image name from the queued row (see deploypro.domain.retention).
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from deploypro.adapters import containers
from deploypro.config import Settings
from deploypro.domain.retention import images_to_keep, images_to_remove
from deploypro.engine import disks
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import processes as process_repo
from deploypro.repositories import projects as project_repo

logger = logging.getLogger("deploypro.housekeeping")

#: BuildKit cache untouched for a week goes. Caches in use stay warm however
#: old they are, because "until" counts from last use.
#:
#: Age is only half the rule. A cache that grows twelve gigabytes in a day —
#: one project with a 4.5 GB image, deployed a few times — contains nothing a
#: week old, so this filter alone removes none of it and the disk fills
#: regardless. `settings.build_cache_max_gb` is the other half.
BUILD_CACHE_HOURS = 24 * 7

#: A build directory is removed when its deploy ends. One left behind means
#: the worker was stopped mid-build; after this long nothing is using it.
STALE_BUILD_DIR_SECONDS = 3600

#: Job working directories hold only the env file a job starts with.
STALE_JOB_DIR_SECONDS = 24 * 3600

GB = 1024**3


async def run(settings: Settings) -> dict[str, object]:
    report: dict[str, object] = {}

    try:
        report["images_removed"] = await sweep_images(settings)
    except Exception as exc:  # noqa: BLE001 - each step stands alone
        logger.exception("image sweep failed")
        report["images_error"] = str(exc)

    try:
        report["build_cache"] = await containers.prune_build_cache(
            older_than_hours=BUILD_CACHE_HOURS,
            max_bytes=settings.build_cache_max_gb * 1024**3,
        )
    except Exception as exc:  # noqa: BLE001 - each step stands alone
        logger.exception("build cache prune failed")
        report["build_cache_error"] = str(exc)

    try:
        report["build_dirs_removed"] = sweep_build_dirs(settings.build_root)
    except Exception as exc:  # noqa: BLE001 - each step stands alone
        logger.exception("build directory sweep failed")
        report["build_dirs_error"] = str(exc)

    try:
        report["log_lines_deleted"] = await deployment_repo.delete_old_logs(
            older_than_days=settings.log_retention_days
        )
        report["job_runs_deleted"] = await process_repo.delete_old_runs(
            older_than_days=settings.log_retention_days
        )
    except Exception as exc:  # noqa: BLE001 - each step stands alone
        logger.exception("log retention failed")
        report["retention_error"] = str(exc)

    return report


async def sweep_images(settings: Settings) -> list[str]:
    keep: dict[str, set[str]] = {}
    for project in await project_repo.list_all():
        deployments = await deployment_repo.list_for_project(project.id, limit=10_000)
        keep[project.slug] = images_to_keep(
            project, deployments, keep=settings.keep_images
        )

    removed = []
    present = await containers.list_deploypro_images(settings.network)
    for tag in images_to_remove(present, keep):
        # Refused when a container still uses it — a draining worker on its
        # way out, say. That is correct; the next sweep gets it.
        if await containers.prune_image(tag):
            removed.append(tag)
    return removed


def sweep_build_dirs(build_root: Path, *, now: float | None = None) -> list[str]:
    """Remove build and job directories a stopped worker left behind.

    Each deploy deletes its own directory when it ends, success or failure;
    only a worker killed mid-build (a restart, an upgrade) leaves one. Age is
    the test rather than the deployment's state, because a promotion from the
    dashboard writes into the same directory while no build is running.
    """
    now = time.time() if now is None else now
    removed = []
    if not build_root.is_dir():
        return removed
    candidates = [
        (entry, STALE_BUILD_DIR_SECONDS)
        for entry in build_root.iterdir()
        if entry.is_dir() and entry.name != "jobs"
    ]
    jobs = build_root / "jobs"
    if jobs.is_dir():
        candidates += [
            (entry, STALE_JOB_DIR_SECONDS) for entry in jobs.iterdir() if entry.is_dir()
        ]
    for entry, age in candidates:
        if now - entry.stat().st_mtime > age:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(str(entry.relative_to(build_root)))
    return sorted(removed)


def free_gb(path: Path) -> float | None:
    try:
        return shutil.disk_usage(path).free / GB
    except OSError:
        return None


async def ensure_room(settings: Settings) -> str | None:
    """Make room for a build, or say why there is none.

    A build that fills the disk takes the database with it: Postgres cannot
    write, drops its connections, and every deploy after fails in seconds
    with an error about the database rather than the disk. So a build does
    not start below `min_free_gb`: old images and cache are cleared first,
    and if that is not enough the reason is returned, to fail the deployment
    before anything is written.

    Both disks count when the images live on their own: the build writes its
    layers there, and its working copy and the database on the server's.
    """
    if not settings.min_free_gb:
        return None
    short = _short_of_room(settings)
    if short is None:
        return None
    report = await run(settings)
    logger.info(
        "housekeeping before a build, %.1f GB free on %s: %s", short[0], short[1], report
    )
    short = _short_of_room(settings)
    if short is None:
        return None
    free, name = short
    return (
        f"The server's disk is nearly full: {free:.1f} GB free on {name}, and a "
        f"build needs at least {settings.min_free_gb} GB. Old images and build cache "
        "were cleared and it is still not enough. Free space on the server "
        "(`deploypro doctor` shows what is using it), then redeploy."
    )


def _short_of_room(settings: Settings) -> tuple[float, str] | None:
    """(free GB, which disk) for the fullest disk under the minimum, or None."""
    short = [
        (free, name)
        for name, path in disks.paths(settings)
        if (free := free_gb(path)) is not None and free < settings.min_free_gb
    ]
    return min(short) if short else None
