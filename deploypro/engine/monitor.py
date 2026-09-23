"""Watching what is supposed to be running, once a minute.

Three questions, each turned into an alert that fires when the answer goes
bad and resolves when it comes back:

- Does each production site answer HTTP? Not "is its container running": a
  container crash-looping under its restart policy is running most of the
  time and serving none of it.
- Is every worker running? A render worker that exits on start is invisible
  from the website, which keeps working while nothing gets rendered.
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
from deploypro.domain.buildplan import DEFAULT_PORT
from deploypro.engine import health
from deploypro.engine.alerts import Alerts
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import processes as process_repo
from deploypro.repositories import projects as project_repo

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
        await self._check_disk()
        return {"down": down}

    async def _check_site(self, project) -> int:
        deployment = await deployment_repo.get(project.production_deployment_id)
        key = f"site:{project.slug}"
        problem = (
            "it has no container"
            if not deployment.container_id
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

    async def _check_disk(self) -> None:
        percent = disk_used_percent(self.settings.build_root)
        if percent is None:
            return
        limit = self.settings.disk_alert_percent
        if percent >= limit:
            await self.alerts.fire(
                "disk",
                title=f"Disk {percent}% full",
                detail=(
                    f"Over the {limit}% threshold. Builds will start failing "
                    "when it fills. `deploypro doctor` shows what is using it; old "
                    "images and build cache are cleared hourly."
                ),
            )
        elif percent < limit - 5:
            # A little below the threshold before resolving, so a disk hovering
            # at the line does not alert and resolve every minute.
            await self.alerts.resolve("disk", title=f"Disk back to {percent}% full")

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
