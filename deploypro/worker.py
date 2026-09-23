"""The worker: one deploy loop, one job loop, one scheduler sweep.

Deploys run strictly one at a time. A build saturates CPU and disk, and two
concurrent ones on a single host make both slower than running them in
sequence while also letting either starve the other of memory.

Scheduled jobs run in their own loop, concurrently with deploys and with each
other up to a small limit. They belong on a different loop because the worker
process is only *waiting* on them — the work happens inside another container
— and because a fifteen-minute nightly job sharing the deploy loop would block
every deploy for fifteen minutes.

Scaling up means running a second worker process. The leases in the database
already make that safe for both loops.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import time
from datetime import UTC, datetime

from deploypro.adapters import db
from deploypro.config import get_settings
from deploypro.engine import backup, housekeeping, pipeline, processes, scheduler, service
from deploypro.engine.alerts import Alerts
from deploypro.engine.monitor import Monitor
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import processes as process_repo
from deploypro.repositories import projects as project_repo

logger = logging.getLogger("deploypro.worker")

#: How long to wait when a queue is empty. Short enough that a deploy feels
#: immediate, long enough that an idle platform is not querying constantly.
IDLE_POLL_SECONDS = 2.0

#: How often to look for work whose worker died.
SWEEP_INTERVAL_SECONDS = 60.0

#: How often to turn due schedules into rows. Well under a minute, because a
#: schedule's smallest unit is a minute and a sweep that ran every 90 seconds
#: would run some minutes' jobs late and others not at all.
SCHEDULE_INTERVAL_SECONDS = 20.0

#: How often to look at containers that were asked to stop. Short, because
#: this is what enforces a stop timeout: a container still running at its
#: deadline is killed at the first sweep after it, so the sweep interval is
#: how late a deadline can be. It is one `docker ps`, which is cheap.
DRAIN_SWEEP_INTERVAL_SECONDS = 5.0

#: How often images, build cache and old logs are cleared.
HOUSEKEEPING_INTERVAL_SECONDS = 3600.0

#: How often to ask "is it the backup hour, and is today's backup missing?".
BACKUP_CHECK_INTERVAL_SECONDS = 60.0

#: Concurrent scheduled jobs per worker. Three rather than one so a slow
#: nightly job does not delay every other project's, and rather than many
#: because each one is a container competing for the same host.
JOB_CONCURRENCY = 3


def worker_id() -> str:
    """Host and PID — what someone debugging a stuck deployment needs in order
    to go and look at it."""
    return f"{socket.gethostname()}/{os.getpid()}"


class Worker:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._id = worker_id()
        self._stopping = asyncio.Event()
        self._last_reclaim = 0.0
        self._last_schedule = 0.0
        self._last_drain_sweep = 0.0
        self._last_monitor = 0.0
        self._last_backup_check = 0.0
        # Not zero: the first tidy-up waits an hour, so a worker restarted in
        # a loop is not also sweeping images in a loop.
        self._last_housekeeping = time.monotonic()
        self._jobs: set[asyncio.Task] = set()
        self._background: dict[str, asyncio.Task] = {}
        self._alerts = Alerts(self._settings.alert_webhook_url)
        self._monitor = Monitor(self._settings, self._alerts)

    def request_stop(self) -> None:
        """Finish what is in hand, then exit.

        Not a cancellation. Killing a build halfway leaves a half-written
        image and a deployment stuck in `building`; letting it finish costs at
        most one build's time and leaves the database consistent.
        """
        if not self._stopping.is_set():
            logger.info("shutdown requested — finishing work in hand")
            self._stopping.set()

    async def run(self) -> None:
        settings = self._settings
        await db.open_pool(
            settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
        )
        try:
            await self._startup_reconcile()
            logger.info("worker %s ready", self._id)
            await asyncio.gather(self._deploy_loop(), self._job_loop())
        finally:
            await self._drain_jobs()
            for task in self._background.values():
                task.cancel()
            await asyncio.gather(*self._background.values(), return_exceptions=True)
            await db.close_pool()
            logger.info("worker %s stopped", self._id)

    async def _startup_reconcile(self) -> None:
        report = await service.reconcile(self._settings)
        logger.info(
            "reconciled with docker: %s container(s) gone, %s abandoned "
            "deployment(s) failed, %s project(s) with production down, "
            "%s container(s) of deleted projects removed",
            report["detached"],
            report["abandoned"],
            report["production_down"],
            report["orphans"],
        )
        if report["production_down"]:
            logger.warning(
                "%s project(s) have a production deployment whose container is "
                "missing — roll back or redeploy them",
                report["production_down"],
            )

    # -- deploys ------------------------------------------------------------

    async def _deploy_loop(self) -> None:
        while not self._stopping.is_set():
            await self._reclaim()
            deployment = await deployment_repo.claim_next(self._id)
            if deployment is None:
                await self._idle()
                continue

            logger.info(
                "building %s #%s (%s)",
                deployment.short_id,
                deployment.number,
                deployment.git_sha[:8],
            )
            started = time.monotonic()
            finished = await pipeline.run_deployment(deployment, settings=self._settings)
            await self._alert_on_deploy(finished)
            logger.info(
                "%s #%s finished as %s in %.1fs",
                finished.short_id,
                finished.number,
                finished.status.value,
                time.monotonic() - started,
            )

            await self._housekeeping()

    async def _alert_on_deploy(self, deployment) -> None:
        """A failed deploy of the production branch is worth a message: it is
        what someone pushed expecting it to go live. A failed preview is not —
        its author is usually watching the log."""
        if deployment.status.value != "failed":
            return
        try:
            project = await project_repo.get(deployment.project_id)
        except Exception:
            return
        if deployment.git_ref != project.production_branch:
            return
        await self._alerts.event(
            title=f"{project.name}: deploy #{deployment.number} failed",
            detail=(
                f"{deployment.error or 'no error recorded'}\n"
                f"Production was not touched. `deploypro logs {deployment.short_id}`"
            ),
            level="critical",
            project=project.slug,
        )

    async def _housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_housekeeping < HOUSEKEEPING_INTERVAL_SECONDS:
            return
        self._last_housekeeping = now
        report = await housekeeping.run(self._settings)
        logger.info("housekeeping: %s", report)

    # -- scheduled jobs -----------------------------------------------------

    async def _job_loop(self) -> None:
        while not self._stopping.is_set():
            await self._schedule()
            await self._sweep_draining()
            self._every(
                self._settings.monitor_interval_seconds,
                "_last_monitor",
                "monitor",
                self._check,
            )
            self._every(
                BACKUP_CHECK_INTERVAL_SECONDS,
                "_last_backup_check",
                "backup",
                self._backup,
            )

            if len(self._jobs) >= JOB_CONCURRENCY:
                await self._idle()
                continue

            run = await process_repo.claim_next_run(self._id)
            if run is None:
                await self._idle()
                continue

            task = asyncio.create_task(self._execute(run))
            self._jobs.add(task)
            task.add_done_callback(self._jobs.discard)

    async def _execute(self, run) -> None:
        try:
            process = await process_repo.get(run.process_id)
        except Exception:
            logger.exception("job run %s has no process", run.id)
            return

        logger.info(
            "running job %s for slot %s", process.name, run.scheduled_for.isoformat()
        )
        started = time.monotonic()
        finished = await processes.run_job(run, process, settings=self._settings)
        await self._alert_on_job(process, finished)
        level = logging.INFO if finished.status.value == "succeeded" else logging.WARNING
        logger.log(
            level,
            "job %s finished as %s in %.1fs",
            process.name,
            finished.status.value,
            time.monotonic() - started,
        )

    async def _alert_on_job(self, process, run) -> None:
        """Fires on the first failure and resolves on the next success, so a
        job failing every minute sends two messages, not one a minute."""
        key = f"job:{process.id}"
        try:
            project = await project_repo.get(process.project_id)
        except Exception:
            return
        if run.status.value in {"failed", "timed_out"}:
            outcome = run.status.value.replace("_", " ")
            await self._alerts.fire(
                key,
                title=f"{project.name}: scheduled job {process.name} {outcome}",
                detail=(
                    f"{run.detail or ''}\n"
                    f"`deploypro runs {project.slug} {process.name} --output`"
                ).strip(),
                project=project.slug,
            )
        elif run.status.value == "succeeded":
            await self._alerts.resolve(
                key,
                title=f"{project.name}: scheduled job {process.name} is succeeding again",
                project=project.slug,
            )

    # -- background checks --------------------------------------------------

    def _every(self, interval: float, stamp: str, name: str, work) -> None:
        """Start `work` in the background if it is due and not still running.

        Background, because both of these can be slow — a probe waits up to
        ten seconds for a site that is down, a backup copies gigabytes — and
        the loop that calls this also claims scheduled jobs, which must not
        wait on either.
        """
        now = time.monotonic()
        if now - getattr(self, stamp) < interval:
            return
        running = self._background.get(name)
        if running is not None and not running.done():
            return
        setattr(self, stamp, now)
        self._background[name] = asyncio.create_task(self._guarded(name, work))

    async def _guarded(self, name: str, work) -> None:
        try:
            await work()
        except Exception:
            logger.exception("%s failed", name)

    async def _check(self) -> None:
        report = await self._monitor.check()
        if report["down"]:
            logger.warning("monitor: %s site(s) or worker(s) failing", report["down"])

    async def _backup(self) -> None:
        settings = self._settings
        if settings.backup_dir is None:
            return
        now = datetime.now(UTC)
        if now.hour != settings.backup_hour or backup.taken_today(settings.backup_dir):
            return
        logger.info("taking the daily backup into %s", settings.backup_dir)
        try:
            result = await backup.take(
                settings, settings.backup_dir, keep=settings.backup_keep
            )
        except backup.BackupSkipped:
            return
        except Exception as exc:
            logger.exception("backup failed")
            await self._alerts.event(
                title="Backup failed",
                detail=f"{type(exc).__name__}: {exc}",
                level="critical",
            )
            return
        logger.info(
            "backup written: %s (%s volume(s), %.1f MB, %s old removed)",
            result.path.name,
            len(result.volumes),
            result.bytes / 1e6,
            len(result.removed),
        )

    async def _drain_jobs(self) -> None:
        """Let running jobs finish before the pool closes under them."""
        if not self._jobs:
            return
        logger.info("waiting for %s job(s) to finish", len(self._jobs))
        await asyncio.gather(*list(self._jobs), return_exceptions=True)

    # -- periodic -----------------------------------------------------------

    async def _schedule(self) -> None:
        now = time.monotonic()
        if now - self._last_schedule < SCHEDULE_INTERVAL_SECONDS:
            return
        self._last_schedule = now
        try:
            await scheduler.sweep()
        except Exception:
            # A broken sweep must not end the loop that also executes jobs.
            logger.exception("schedule sweep failed")

    async def _sweep_draining(self) -> None:
        """Remove replaced containers that finished; kill ones out of time.

        On the job loop, not the deploy loop: the deploy loop is busy for the
        whole of a build, and a stop timeout that could be overshot by the
        length of someone else's build would not be a timeout.
        """
        now = time.monotonic()
        if now - self._last_drain_sweep < DRAIN_SWEEP_INTERVAL_SECONDS:
            return
        self._last_drain_sweep = now
        try:
            finished, killed = await service.sweep_draining()
        except Exception:
            logger.exception("draining sweep failed")
            return
        if finished:
            logger.info("removed %s container(s) that finished draining", finished)
        if killed:
            logger.warning(
                "killed %s container(s) still running at the end of their stop timeout",
                killed,
            )
            await self._alerts.event(
                title=f"{killed} container(s) killed at the end of their stop timeout",
                detail=(
                    "A replaced worker was still busy when its time ran out, so "
                    "whatever it was doing was cut off. If that was a render, raise "
                    "the project's stop timeout: `deploypro project set <project> "
                    "--stop-timeout <seconds>`."
                ),
            )

    async def _reclaim(self) -> None:
        now = time.monotonic()
        if now - self._last_reclaim < SWEEP_INTERVAL_SECONDS:
            return
        self._last_reclaim = now

        for deployment in await deployment_repo.reclaim_abandoned(
            older_than_seconds=self._settings.build_timeout_seconds + 120
        ):
            logger.warning(
                "failed abandoned deployment %s #%s",
                deployment.short_id,
                deployment.number,
            )
        # Generous, because the bound is per job and the longest legitimate
        # timeout is the one configured on the slowest process.
        for run in await process_repo.reclaim_abandoned_runs(older_than_seconds=7200):
            logger.warning("failed abandoned job run %s", run.id)
        try:
            removed = await service.remove_orphans(self._settings)
        except Exception:  # noqa: BLE001 - a Docker hiccup must not stop deploys
            logger.exception("could not sweep containers of deleted projects")
        else:
            if removed:
                logger.info("removed %s container(s) of deleted projects", removed)

    async def _idle(self) -> None:
        """Sleep, but wake immediately on shutdown.

        A plain sleep would make SIGTERM take up to the poll interval to be
        noticed, turning every restart into a needless pause.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=IDLE_POLL_SECONDS)


async def amain() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    # httpx logs every request at INFO, which with the monitor is a line per
    # site per minute saying nothing. Failures are logged by the callers.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    worker = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)
    await worker.run()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
