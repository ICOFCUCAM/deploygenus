"""Watching what is supposed to be running, once a minute.

Three questions, each turned into an alert that fires when the answer goes
bad and resolves when it comes back:

- Does each production site answer HTTP? Not "is its container running": a
  container crash-looping under its restart policy is running most of the
  time and serving none of it.
- Is every worker running? A render worker that exits on start is invisible
  from the website, which keeps working while nothing gets rendered.
- Is each project's Redis up, and not nearly full? A stopped one is started
  again here (docs/design/plan-replace-render.md §3.4); with noeviction, a full
  one refuses new jobs, so it warns at 90% before that happens.
- Is the disk nearly full? Every other failure on a single host follows it.

A site or worker must fail two checks in a row before it alerts. One failure
is usually a restart in progress — the policy restarting a crashed process,
or a promotion replacing a worker — and an alert that cries wolf on every
deploy gets muted.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from deploypro.adapters import containers
from deploypro.config import Settings
from deploypro.domain import naming
from deploypro.domain import redis as redis_rules
from deploypro.domain.buildplan import DEFAULT_PORT
from deploypro.engine import disks, health
from deploypro.engine import redis as redis_engine
from deploypro.engine.alerts import Alerts
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import processes as process_repo
from deploypro.repositories import projects as project_repo
from deploypro.repositories import redis as redis_repo

#: Consecutive failed checks before a site or worker is reported down.
STRIKES = 2


@dataclass
class Monitor:
    settings: Settings
    alerts: Alerts
    _strikes: dict[str, int] = field(default_factory=dict)

    async def check(self) -> dict[str, int]:
        """One pass over everything. Returns counts, for the log."""
        down = 0
        for project in await project_repo.list_all():
            if project.production_deployment_id is None:
                continue
            down += await self._check_site(project)
            down += await self._check_workers(project)
        down += await self._check_redis()
        await self._check_disk()
        return {"down": down}

    async def _check_site(self, project) -> int:
        deployment = await deployment_repo.get(project.production_deployment_id)
        key = f"site:{project.slug}"
        problem = (
            "it has no container"
            if not deployment.container_id
            else await self._service_problem(deployment.container_id)
            if project.is_background
            else await health.probe(
                container_id=deployment.container_id,
                network=self.settings.network,
                port=deployment.internal_port or DEFAULT_PORT,
                path=self.settings.health_path,
            )
        )
        return await self._record(
            key,
            problem,
            down_title=f"{project.name} is down",
            down_detail=(
                f"Production (deployment #{deployment.number}) is not serving: "
                f"{problem}. Check `deploypro logs {deployment.short_id}` and the "
                "container's own output; roll back with "
                f"`deploypro promote {project.slug} '#<number>'`."
            ),
            up_title=f"{project.name} is serving again",
            project=project.slug,
        )

    async def _service_problem(self, container_id: str) -> str | None:
        """A background service has no port: running is the whole check."""
        status = await containers.state(container_id)
        return None if status == "running" else f"its container is {status or 'gone'}"

    async def _check_workers(self, project) -> int:
        down = 0
        for process in await process_repo.list_long_running(project.id):
            for replica in range(process.replicas):
                name = naming.worker_container_name(project.slug, process.name, replica)
                status = await containers.state(name)
                if status is None:
                    # Never started: added since the last promotion, which is
                    # when workers start. Not down, just not yet up.
                    continue
                down += await self._record(
                    f"worker:{name}",
                    None if status == "running" else f"its container is {status}",
                    down_title=f"{project.name}: worker {process.name} is not running",
                    down_detail=(
                        f"{name} has stopped, so nothing is consuming its work. "
                        f"`docker logs {name}` says why."
                    ),
                    up_title=f"{project.name}: worker {process.name} is running again",
                    project=project.slug,
                )
        return down

    async def _check_redis(self) -> int:
        """Keep each Redis running, then ask it PING. Independent of
        production: a project's Redis runs whether or not anything is
        deployed, so the queue is ready before the first worker."""
        down = 0
        for config in await redis_repo.list_all():
            project = await project_repo.get(config.project_id)
            start_failed = None
            try:
                await redis_engine.ensure_running(project, config, settings=self.settings)
            except Exception as exc:  # noqa: BLE001 - becomes the alert below
                start_failed = f"starting it failed ({type(exc).__name__})"
            problem = await redis_engine.ping(project)
            if problem and start_failed:
                problem = f"{problem}; {start_failed}"
            name = redis_rules.container_name(project.slug)
            down += await self._record(
                f"redis:{project.slug}",
                problem,
                down_title=f"{project.name}: Redis is down",
                down_detail=(
                    f"Redis is not answering: {problem}. Queued jobs wait until it "
                    "is back; its data is on the volume. DeployPro tries to start "
                    f"it every minute. `docker logs {name}` says why it stopped."
                ),
                up_title=f"{project.name}: Redis is running again",
                project=project.slug,
            )
            if problem is None:
                await self._check_redis_memory(project, config)
        return down

    async def _check_redis_memory(self, project, config) -> None:
        status = await redis_engine.status(project, config)
        percent = status.used_percent
        if percent is None:
            return
        key = f"redis-memory:{project.slug}"
        if percent >= redis_rules.WARN_PERCENT:
            await self.alerts.fire(
                key,
                title=f"{project.name}: Redis {percent}% full",
                detail=(
                    f"Redis is using {percent}% of its memory. "
                    + (
                        "It never evicts, so when full it refuses new jobs with an "
                        "OOM error. Raise its memory on the Redis page."
                        if config.policy == "noeviction"
                        else "It will start evicting keys. Raise its memory on the "
                        "Redis page if those keys matter."
                    )
                ),
                project=project.slug,
            )
        elif percent < redis_rules.WARN_PERCENT - 5:
            await self.alerts.resolve(
                key,
                title=f"{project.name}: Redis back to {percent}% full",
                project=project.slug,
            )

    async def _check_disk(self) -> None:
        """Each disk a build writes to, alerted separately: the images' own
        disk filling is as fatal to the next build as the server's."""
        limit = self.settings.disk_alert_percent
        for index, (name, path) in enumerate(disks.paths(self.settings)):
            percent = disk_used_percent(path)
            if percent is None:
                continue
            key = "disk" if index == 0 else "disk:images"
            which = "Disk" if index == 0 else "Image storage disk"
            if percent >= limit:
                await self.alerts.fire(
                    key,
                    title=f"{which} {percent}% full",
                    detail=(
                        f"{name[0].upper()}{name[1:]} is over the {limit}% threshold. "
                        "Builds will start failing when it fills. `deploypro doctor` "
                        "shows what is using it; old images and build cache are "
                        "cleared hourly."
                    ),
                )
            elif percent < limit - 5:
                # A little below the threshold before resolving, so a disk
                # hovering at the line does not alert and resolve every minute.
                await self.alerts.resolve(key, title=f"{which} back to {percent}% full")

    async def _record(
        self,
        key: str,
        problem: str | None,
        *,
        down_title: str,
        down_detail: str,
        up_title: str,
        **fields: str,
    ) -> int:
        if problem is None:
            self._strikes.pop(key, None)
            await self.alerts.resolve(key, title=up_title, **fields)
            return 0
        self._strikes[key] = self._strikes.get(key, 0) + 1
        if self._strikes[key] >= STRIKES:
            await self.alerts.fire(key, title=down_title, detail=down_detail, **fields)
        return 1


def disk_used_percent(path: Path) -> int | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return round(usage.used * 100 / usage.total) if usage.total else None
