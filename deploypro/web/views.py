"""What the dashboard says, derived from what the engine records.

Every state a page shows is computed here, from rows the page already loaded,
by functions with no I/O. The templates only lay the results out. That keeps
the rules in docs/design (Phase 3's states and action matrix) in one place
where a test can hold them to the letter, instead of scattered through Jinja
conditionals where a missing attribute quietly reads as false.

Nothing here invents a state the engine does not record: when a fact needs an
engine change first (worker running counts, when production moved, whether an
image still exists), the function returns the documented fallback rather than
a guess.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from deploypro.domain.models import (
    Deployment,
    DeploymentStatus,
    Domain,
    EnvTarget,
    EnvVar,
    JobRun,
    JobStatus,
    Process,
    ProcessType,
    Project,
)

# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------

#: The seven shapes, and the colour family each one always carries. Shape is
#: what tells states apart (colour is supplementary, Phase 4 §4–5), so a shape
#: never changes family: every "ready" mark is green, every "caution" amber.
TONES = {
    "queued": "neutral",
    "building": "neutral",
    "deploying": "neutral",
    "ready": "success",
    "failed": "failure",
    "cancelled": "neutral",
    "caution": "caution",
}


@dataclass(frozen=True, slots=True)
class Mark:
    shape: str
    word: str

    @property
    def tone(self) -> str:
        return TONES[self.shape]


DEPLOYMENT_MARKS = {
    DeploymentStatus.QUEUED: Mark("queued", "Queued"),
    DeploymentStatus.BUILDING: Mark("building", "Building"),
    DeploymentStatus.DEPLOYING: Mark("deploying", "Deploying"),
    DeploymentStatus.READY: Mark("ready", "Ready"),
    DeploymentStatus.FAILED: Mark("failed", "Failed"),
    DeploymentStatus.CANCELLED: Mark("cancelled", "Cancelled"),
}

JOB_MARKS = {
    JobStatus.PENDING: Mark("queued", "Pending"),
    JobStatus.RUNNING: Mark("building", "Running"),
    JobStatus.SUCCEEDED: Mark("ready", "Succeeded"),
    JobStatus.FAILED: Mark("failed", "Failed"),
    JobStatus.TIMED_OUT: Mark("caution", "Timed out"),
    JobStatus.SKIPPED: Mark("cancelled", "Skipped"),
}

IN_PROGRESS = {
    DeploymentStatus.QUEUED,
    DeploymentStatus.BUILDING,
    DeploymentStatus.DEPLOYING,
}


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def between(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return ""
    return duration((end - start).total_seconds())


def ago(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return ""
    seconds = (now - moment).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def clock(moment: datetime | None, now: datetime | None = None) -> str:
    """An exact time: 14:02 today, `Wed 18:40` this week, `3 Sep 14:02` before."""
    if moment is None:
        return ""
    if now is None or moment.date() == now.date():
        return moment.strftime("%H:%M")
    if now - moment < timedelta(days=6):
        return moment.strftime("%a %H:%M")
    return moment.strftime("%-d %b %H:%M")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Action:
    """One control. `method` is post for a form button, get for a link."""

    label: str
    url: str
    method: str = "post"
    #: Shown under the control: Q-S1's "already building" note, a rollback's
    #: speed. Never the only place a fact appears.
    note: str = ""


def deploy_action(project: Project, deployments: Sequence[Deployment]) -> Action:
    """`Deploy {branch}`, with Phase 3 Q-S1's note when one is on its way."""
    note = ""
    for d in deployments:
        if d.git_ref == project.production_branch and d.status in IN_PROGRESS:
            word = "queued" if d.status is DeploymentStatus.QUEUED else "building"
            note = (
                f"#{d.number} is already {word} — this will wait and build "
                "again after it."
            )
            break
    return Action(
        f"Deploy {project.production_branch}",
        f"/projects/{project.slug}/deploy",
        note=note,
    )


def split_label(text: str, tail: int = 12) -> tuple[str, str]:
    """Head and tail of a long label, for shortening it in the middle.

    Phase 2 §2I: long identifiers are shortened in the middle so the part that
    tells two branches apart survives. The template puts the head in a span
    that ellipsises and the tail in one that never shrinks; the button's text
    is still the whole string, so its accessible name is complete.
    """
    if len(text) <= tail + 8:
        return text, ""
    return text[:-tail], text[-tail:]


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


def is_production_branch(d: Deployment, project: Project) -> bool:
    """Kind is derived from the *current* production branch. It is not stored,
    so a renamed branch changes older rows' kind; pages never claim history."""
    return d.git_ref == project.production_branch


def failed_step(d: Deployment) -> str | None:
    """Which step a failed deployment failed at, from its timestamps."""
    if d.status is not DeploymentStatus.FAILED:
        return None
    if d.started_at is None:
        return "source"
    if d.built_at is None:
        return "build"
    return "deploy"


STEP_WORDS = {"source": "Queued", "build": "Building", "deploy": "Deploying"}


@dataclass(frozen=True, slots=True)
class Node:
    key: str
    label: str
    #: complete · active · failed · pending · current · not-current ·
    #: cancelled · end
    state: str
    detail: str = ""
    extra: str = ""

    @property
    def spoken(self) -> str:
        """The node's state as text, for the ordered list screen readers get."""
        words = {
            "complete": f"completed{' in ' + self.detail if self.detail else ''}",
            "active": "in progress",
            "failed": "failed",
            "pending": "not reached",
            "current": "current production",
            "not-current": "not current production",
            "cancelled": "cancelled",
            "end": "the end of a preview: it never becomes production",
        }
        return f"{self.label}: {words[self.state]}"


def line(d: Deployment, project: Project, *, is_current: bool) -> list[Node]:
    """The deployment line: SOURCE → BUILD → DEPLOY → PRODUCTION (Phase 4 §2).

    HEALTH is drawn inside DEPLOY, PRODUCTION is a marker with no time, a
    preview ends at DEPLOY, and a cancelled deployment ends at SOURCE.
    """
    status = d.status
    source_detail = d.created_at.strftime("%H:%M:%S")
    source_extra = f"queued {between(d.created_at, d.started_at)}" if d.started_at else ""

    if status is DeploymentStatus.CANCELLED:
        return [Node("source", "Source", "cancelled", source_detail)]

    failed_at = failed_step(d)
    order = ["source", "build", "deploy"]
    if status is DeploymentStatus.QUEUED:
        reached = 0
    elif status is DeploymentStatus.BUILDING:
        reached = 1
    elif status is DeploymentStatus.DEPLOYING:
        reached = 2
    elif failed_at:
        reached = order.index(failed_at)
    else:
        reached = 3

    def state_of(index: int) -> str:
        if index < reached:
            return "complete"
        if index == reached:
            return "failed" if failed_at else "active"
        return "pending"

    deploy_end = d.ready_at or (d.finished_at if failed_at == "deploy" else None)
    nodes = [
        Node("source", "Source", state_of(0), source_detail, source_extra),
        Node(
            "build",
            "Build",
            state_of(1),
            between(d.started_at, d.built_at) if d.built_at else "",
        ),
        Node(
            "deploy",
            "Deploy",
            state_of(2),
            between(d.built_at, deploy_end) if deploy_end else "",
            "includes health check",
        ),
    ]
    if not is_production_branch(d, project):
        nodes.append(Node("preview", "Preview", "end" if reached == 3 else "pending"))
    elif status is DeploymentStatus.READY:
        nodes.append(
            Node("production", "Production", "current" if is_current else "not-current")
        )
    else:
        nodes.append(Node("production", "Production", "pending"))
    return nodes


@dataclass(frozen=True, slots=True)
class DeploymentView:
    d: Deployment
    mark: Mark
    production_branch: bool
    is_current: bool
    #: "running" / "stopped" for ready deployments, else None. "Removed" needs
    #: to know whether the image still exists ([small]); until then a removed
    #: deployment shows as stopped and the refusal comes on click.
    runtime: str | None
    failed_step: str | None
    #: `took 2m 18s` when finished; None while in progress.
    took: str
    #: When elapsed time counts from while in progress.
    since: datetime | None
    nodes: list[Node] = field(default_factory=list)

    @property
    def in_progress(self) -> bool:
        return self.d.status in IN_PROGRESS

    @property
    def failed_word(self) -> str:
        return STEP_WORDS.get(self.failed_step or "", "")


def view(d: Deployment, project: Project, *, with_line: bool = False) -> DeploymentView:
    is_current = project.production_deployment_id == d.id
    runtime = None
    if d.status is DeploymentStatus.READY:
        runtime = "running" if d.container_id else "stopped"
    since = {
        DeploymentStatus.QUEUED: d.created_at,
        DeploymentStatus.BUILDING: d.started_at,
        DeploymentStatus.DEPLOYING: d.built_at,
    }.get(d.status)
    return DeploymentView(
        d=d,
        mark=DEPLOYMENT_MARKS[d.status],
        production_branch=is_production_branch(d, project),
        is_current=is_current,
        runtime=runtime,
        failed_step=failed_step(d),
        took=between(d.started_at, d.finished_at) if d.status.is_terminal else "",
        since=since,
        nodes=line(d, project, is_current=is_current) if with_line else [],
    )


@dataclass(frozen=True, slots=True)
class DetailActions:
    primary: Action | None
    secondary: list[Action]
    #: Whether the deployment's own address is shown: only while it answers.
    show_address: bool


def detail_actions(
    d: Deployment, project: Project, production: Deployment | None
) -> DetailActions:
    """Phase 3 §5.1's action matrix, exactly. One primary action per view."""
    base = f"/deployments/{d.short_id}"
    redeploy = Action("Redeploy", f"{base}/redeploy")
    status = d.status
    if status is DeploymentStatus.QUEUED:
        return DetailActions(Action("Cancel", f"{base}/cancel"), [], False)
    if status in (DeploymentStatus.BUILDING, DeploymentStatus.DEPLOYING):
        return DetailActions(None, [], False)
    if status in (DeploymentStatus.FAILED, DeploymentStatus.CANCELLED):
        return DetailActions(redeploy, [], False)

    running = d.container_id is not None
    if project.production_deployment_id == d.id:
        return DetailActions(redeploy, [], running)

    speed = "instant" if running else "restarts in a few seconds"
    if production is not None and d.number < production.number:
        promote = Action(f"Roll back to #{d.number}", f"{base}/promote", note=speed)
    else:
        promote = Action("Make production", f"{base}/promote", note=speed)
    return DetailActions(promote, [redeploy], running)


def detail_sentence(v: DeploymentView) -> str:
    """Phase 3 §5.1's body sentence for the state, when it has one."""
    status = v.d.status
    if status is DeploymentStatus.DEPLOYING:
        return "Starting the container and checking it answers."
    if status is DeploymentStatus.CANCELLED:
        return "Cancelled before it started building."
    if status is DeploymentStatus.FAILED:
        if v.production_branch:
            return "Production was not touched."
        return "This was a preview; production was not involved."
    if status is DeploymentStatus.READY and not v.is_current:
        if v.runtime == "running":
            return "Running — making it production is instant."
        return (
            "Stopped to free memory. Making it production restarts it from its "
            "image in a few seconds."
        )
    return ""


# ---------------------------------------------------------------------------
# A project's production, rollback and derived state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Condition:
    """One reason a project needs attention (Phase 3 §6)."""

    mark: Mark
    text: str
    url: str


def rollback_target(
    project: Project, production: Deployment | None, deployments: Sequence[Deployment]
) -> tuple[Deployment, str] | None:
    """The newest older ready deployment of the production branch, and how fast
    making it production would be. None when there is no candidate: the
    control is then absent, not disabled."""
    if production is None:
        return None
    for d in deployments:
        if (
            d.status is DeploymentStatus.READY
            and d.number < production.number
            and d.git_ref == project.production_branch
        ):
            return d, ("instant" if d.container_id else "restarts in a few seconds")
    return None


def _newest_on_branch(project: Project, deployments: Sequence[Deployment]):
    for d in deployments:
        if d.git_ref == project.production_branch:
            return d
    return None


def conditions(
    project: Project,
    production: Deployment | None,
    deployments: Sequence[Deployment],
    jobs: Iterable[tuple[Process, JobRun | None]],
) -> list[Condition]:
    """Why the project needs attention, most serious first.

    Four recorded conditions (Phase 3 §6). Workers below their wanted count is
    the fifth once running counts can be read ([small]); it is not guessed.
    """
    found: list[Condition] = []
    overview = f"/projects/{project.slug}"
    if production is not None and production.container_id is None:
        found.append(
            Condition(
                Mark("failed", "Production is down"),
                "Production is down: its container is gone.",
                overview,
            )
        )
    for process, run in jobs:
        if run is None or run.status not in (JobStatus.FAILED, JobStatus.TIMED_OUT):
            continue
        word = "timed out" if run.status is JobStatus.TIMED_OUT else "failed"
        found.append(
            Condition(
                JOB_MARKS[run.status],
                f"{process.name} {word} at {run.scheduled_for.strftime('%H:%M')}.",
                f"/projects/{project.slug}/runtime/jobs/{process.name}",
            )
        )
    newest = _newest_on_branch(project, deployments)
    if (
        production is not None
        and production.container_id is not None
        and newest is not None
        and newest.status is DeploymentStatus.FAILED
        and newest.number > production.number
    ):
        found.append(
            Condition(
                DEPLOYMENT_MARKS[DeploymentStatus.FAILED],
                f"#{newest.number} failed. Production is still #{production.number}.",
                f"/deployments/{newest.short_id}",
            )
        )
    return found


def project_mark(project: Project, found: Sequence[Condition]) -> Mark:
    """Healthy · Needs attention · Not deployed. *Down* is not available: it
    needs stored health results, and the monitor's are not stored."""
    if found:
        return Mark("caution", "Needs attention")
    if project.production_deployment_id is None:
        return Mark("queued", "Not deployed")
    return Mark("ready", "Healthy")


@dataclass(frozen=True, slots=True)
class Production:
    """The Overview's production block (Phase 3 §3), and its one primary action."""

    #: empty · first-running · first-failed · serving · missing
    state: str
    mark: Mark | None
    headline: str
    primary: Action
    secondary: list[Action]
    #: A newer production-branch deployment in progress or failed.
    newer: Deployment | None = None
    #: "Rolled back to #N · from #M": production is older than a ready one.
    rolled_back_from: Deployment | None = None
    rollback: tuple[Deployment, str] | None = None


def production_block(
    project: Project, production: Deployment | None, deployments: Sequence[Deployment]
) -> Production:
    deploy = deploy_action(project, deployments)
    on_branch = [d for d in deployments if d.git_ref == project.production_branch]
    newest = on_branch[0] if on_branch else None

    if production is None:
        if newest is not None and newest.status in IN_PROGRESS:
            return Production(
                "first-running",
                DEPLOYMENT_MARKS[newest.status],
                f"#{newest.number} is {newest.status.value}.",
                Action("View deployment", f"/deployments/{newest.short_id}", "get"),
                [deploy],
                newer=newest,
            )
        if newest is not None and newest.status is DeploymentStatus.FAILED:
            return Production(
                "first-failed",
                DEPLOYMENT_MARKS[DeploymentStatus.FAILED],
                "The first deployment failed.",
                Action("View deployment", f"/deployments/{newest.short_id}", "get"),
                [Action("Deploy again", deploy.url, note=deploy.note)],
                newer=newest,
            )
        return Production("empty", None, "Nothing is deployed yet.", deploy, [])

    rollback = rollback_target(project, production, deployments)
    if production.container_id is None:
        if rollback is not None:
            target, speed = rollback
            primary = Action(
                f"Roll back to #{target.number}",
                f"/deployments/{target.short_id}/promote",
                note=speed,
            )
        else:
            primary = Action(
                f"Redeploy #{production.number}",
                f"/deployments/{production.short_id}/redeploy",
            )
        return Production(
            "missing",
            Mark("failed", "Down"),
            "Production is down: its container is gone.",
            primary,
            [],
        )

    newer = None
    if (
        newest is not None
        and newest.number > production.number
        and newest.status in IN_PROGRESS | {DeploymentStatus.FAILED}
    ):
        newer = newest
    rolled_back_from = next(
        (
            d
            for d in on_branch
            if d.number > production.number and d.status is DeploymentStatus.READY
        ),
        None,
    )
    if newer is not None:
        primary = Action(f"View #{newer.number}", f"/deployments/{newer.short_id}", "get")
        secondary = [deploy]
    else:
        primary, secondary = deploy, []
    return Production(
        "serving",
        Mark("ready", "Serving"),
        "Serving",
        primary,
        secondary,
        newer=newer,
        rolled_back_from=rolled_back_from,
        rollback=rollback,
    )


# ---------------------------------------------------------------------------
# Configuration, domains, runtime
# ---------------------------------------------------------------------------


def changed_since_production(
    variables: Iterable[EnvVar], production: Deployment | None
) -> list[EnvVar]:
    """Variables production has not picked up yet (Phase 3 Q-S3).

    A variable reaches a deployment when it is built, so anything that affects
    production and changed after production's build started is not in it.
    Accepted: a change that was later reverted still shows.
    """
    if production is None or production.started_at is None:
        return []
    return [
        v
        for v in variables
        if v.target in (EnvTarget.ALL, EnvTarget.PRODUCTION)
        and v.updated_at > production.started_at
    ]


def domain_mark(
    domain: Domain, *, has_production: bool, primary_host: str | None
) -> Mark:
    if not domain.is_verified:
        return Mark("caution", "Waiting for DNS")
    if not has_production:
        return Mark("ready", "Verified, not yet serving")
    if primary_host and domain.host != primary_host:
        return Mark("ready", f"Verified, redirects to {primary_host}")
    return Mark("ready", "Verified")


def worker_state(
    process: Process, production: Deployment | None
) -> tuple[Mark | None, str]:
    """A worker's state, without a running count until one can be read.

    Pausing and resuming a worker takes effect at the next deploy (Phase 3
    Q-S4): the dashboard only flips the definition, and workers are reconciled
    when production changes.
    """
    if production is None:
        return (
            Mark("queued", "Not started"),
            "Workers run the production deployment. Nothing is in production yet.",
        )
    if not process.enabled:
        return (
            Mark("cancelled", "Paused"),
            "Takes effect at the next deploy: a running worker keeps running until then.",
        )
    # No mark: a green one would claim it is running, which only a Docker
    # read can say.
    return None, f"Follows production #{production.number}."


def job_state(process: Process, run: JobRun | None) -> Mark | None:
    if not process.enabled:
        return Mark("cancelled", "Paused")
    if run is None:
        return None
    return JOB_MARKS[run.status]


# ---------------------------------------------------------------------------
# Activity (Projects home, Phase 3 Q-S6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Event:
    at: datetime
    project: Project
    mark: Mark
    text: str
    url: str


def activity(
    deployments: Iterable[tuple[Project, Deployment]],
    runs: Iterable[tuple[Project, Process, JobRun]],
    *,
    limit: int = 10,
) -> list[Event]:
    """Deploys that went live, failures, and job failures, newest first.

    Rollbacks are absent: a rollback moves the production pointer without a
    row or a timestamp, so there is no event to show until promotions are
    recorded ([small]).
    """
    events: list[Event] = []
    for project, d in deployments:
        if (
            d.status is DeploymentStatus.READY
            and d.ready_at
            and is_production_branch(d, project)
        ):
            events.append(
                Event(
                    d.ready_at,
                    project,
                    DEPLOYMENT_MARKS[d.status],
                    f"#{d.number} went live",
                    f"/deployments/{d.short_id}",
                )
            )
        elif d.status is DeploymentStatus.FAILED and d.finished_at:
            events.append(
                Event(
                    d.finished_at,
                    project,
                    DEPLOYMENT_MARKS[d.status],
                    f"#{d.number} failed at {STEP_WORDS[failed_step(d) or 'deploy']}",
                    f"/deployments/{d.short_id}",
                )
            )
    for project, process, run in runs:
        if run.status in (JobStatus.FAILED, JobStatus.TIMED_OUT):
            word = "timed out" if run.status is JobStatus.TIMED_OUT else "failed"
            events.append(
                Event(
                    run.finished_at or run.scheduled_for,
                    project,
                    JOB_MARKS[run.status],
                    f"job {process.name} {word}",
                    f"/projects/{project.slug}/runtime/jobs/{process.name}",
                )
            )
    events.sort(key=lambda e: e.at, reverse=True)
    return events[:limit]


def anything_in_progress(
    deployments: Iterable[Deployment], runs: Iterable[JobRun | None] = ()
) -> bool:
    """Whether a page should refresh itself (Phase 3 Q-S2)."""
    return any(d.status in IN_PROGRESS for d in deployments) or any(
        r is not None and r.status in (JobStatus.PENDING, JobStatus.RUNNING) for r in runs
    )


def jobs_summary(jobs: Sequence[tuple[Process, JobRun | None]]) -> str:
    """The worst recent run state, in words (Phase 3 §2, the Projects record)."""
    if not jobs:
        return "no jobs"
    failing = sum(
        1
        for _p, run in jobs
        if run is not None and run.status in (JobStatus.FAILED, JobStatus.TIMED_OUT)
    )
    if failing:
        return f"jobs: {failing} failing"
    if all(run is None for _p, run in jobs):
        return "jobs: not run yet"
    return "jobs: all succeeding"


def workers(processes: Iterable[Process]) -> list[Process]:
    return [p for p in processes if p.type is ProcessType.WORKER]


def jobs(processes: Iterable[Process]) -> list[Process]:
    return [p for p in processes if p.type is ProcessType.CRON]
