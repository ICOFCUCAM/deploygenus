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

from deploypro.adapters import containers
from deploypro.config import Settings
from deploypro.domain.retention import images_to_keep, images_to_remove
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import processes as process_repo
from deploypro.repositories import projects as project_repo

logger = logging.getLogger("deploypro.housekeeping")

#: BuildKit cache untouched for a week goes. Caches in use stay warm however
#: old they are, because "until" counts from last use.
BUILD_CACHE_HOURS = 24 * 7


async def run(settings: Settings) -> dict[str, object]:
    report: dict[str, object] = {}

    try:
        report["images_removed"] = await sweep_images(settings)
    except Exception as exc:  # noqa: BLE001 - each step stands alone
        logger.exception("image sweep failed")
        report["images_error"] = str(exc)

    try:
        report["build_cache"] = await containers.prune_build_cache(
            older_than_hours=BUILD_CACHE_HOURS
        )
    except Exception as exc:  # noqa: BLE001 - each step stands alone
        logger.exception("build cache prune failed")
        report["build_cache_error"] = str(exc)

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
