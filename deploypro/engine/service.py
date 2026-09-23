"""Operations the API, the webhook and the CLI all need.

Queueing a deploy is the same act whether a push, a person or a script asked
for it, and only the recorded trigger differs. Keeping that in one place is
what stops the webhook and the CLI drifting into two subtly different ideas of
what a deployment is.
"""

from __future__ import annotations

import logging
from uuid import UUID

from deploypro.adapters import containers, edge, source
from deploypro.config import Settings, get_settings
from deploypro.domain import naming
from deploypro.domain.errors import InvalidRequest
from deploypro.domain.models import Deployment, DeploymentTrigger, Project
from deploypro.domain.repo_url import is_ssh_url
from deploypro.engine import gitaccess
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import projects as project_repo

logger = logging.getLogger("deploypro.service")


async def queue_deploy(
    project: Project,
    *,
    ref: str | None = None,
    sha: str | None = None,
    trigger: DeploymentTrigger = DeploymentTrigger.MANUAL,
    message: str | None = None,
    author: str | None = None,
    rolled_back_from: UUID | None = None,
) -> Deployment:
    """Create a queued deployment, resolving the branch to a commit first.

    Resolving up front is what makes the history honest. If the sha were
    resolved by the worker instead, two deploys queued a minute apart would
    both say "main" and there would be no record of which commit each one
    actually built.
    """
    reference = ref or project.production_branch
    commit = sha
    if commit is None:
        async with gitaccess.git_env(project, get_settings()) as env:
            try:
                commit = await source.resolve_head(project.repo_url, reference, env=env)
            except RuntimeError as exc:
                raise InvalidRequest(_unreadable(project, str(exc))) from exc

    return await deployment_repo.create(
        project_id=project.id,
        short_id=naming.deployment_short_id(project.slug),
        git_sha=commit,
        git_ref=reference,
        trigger=trigger,
        git_message=message,
        git_author=author,
        rolled_back_from=rolled_back_from,
    )


def _unreadable(project: Project, detail: str) -> str:
    """Why the repository could not be read, and what to do about it."""
    hint = ""
    lowered = detail.lower()
    unreadable = (
        "permission denied",
        "could not read from remote",
        "could not read username",
        "authentication failed",
        "repository not found",
    )
    if project.github_installation_id is not None and any(
        s in lowered for s in unreadable
    ):
        hint = (
            " The GitHub App could not read it: check that the repository is "
            "still included in the app's installation on GitHub (Settings → "
            "Applications → the DeployPro app → Configure)."
        )
    elif any(s in lowered for s in unreadable):
        hint = (
            " If the repository is private, give DeployPro read access: "
            f"`deploypro project key {project.slug}` prints a deploy key to add "
            "to it."
            if is_ssh_url(project.repo_url)
            else " If it is private, connect the GitHub App (New project → "
            f"Connect GitHub) and run `deploypro github link {project.slug}`, or "
            "switch the project to the repository's SSH URL and add a deploy key."
        )
    elif "host key" in lowered:
        hint = (
            " The server's SSH identity is not the one DeployPro knows, so it "
            "refused to connect. That is what an impostor looks like; if the "
            "server really changed its key, remove its line from the known_hosts "
            "file under the build root."
        )
    # All of git's message, on one line: its useful part ("Permission denied
    # (publickey)") is rarely the last line, which is usually boilerplate.
    said = " ".join(line.strip() for line in detail.splitlines() if line.strip())
    return f"Could not read the repository: {said[:500]}.{hint}"


async def redeploy(project: Project, deployment: Deployment) -> Deployment:
    """Build the same commit again.

    The common reason is a changed environment variable: variables are read at
    build time as well as run time, so changing one only takes effect on a new
    build. The alternative — restarting the container — would pick up the new
    runtime value and keep the old baked-in one, which is the worse kind of
    half-applied change.
    """
    return await queue_deploy(
        project,
        ref=deployment.git_ref,
        sha=deployment.git_sha,
        trigger=DeploymentTrigger.REDEPLOY,
        message=deployment.git_message,
        author=deployment.git_author,
    )


async def sweep_draining() -> tuple[int, int]:
    """Clear away containers that were asked to stop. (finished, killed)."""
    return await containers.sweep_draining()


async def delete_project(project: Project, settings: Settings) -> int:
    """Delete a project and stop everything it was running. Returns how many
    containers were removed.

    Routing first, so nothing is sent to the project while it goes; then the
    rows, so no promotion or scheduler can start anything new for it; then its
    containers: the web deployments (which would otherwise stay reachable on
    their own addresses), workers, draining workers and job runs. Removed, not
    drained: deleting a project is the decision that its work stops.

    Its volumes are kept, as always. DeployPro never deletes data.
    """
    edge.clear_route(project.slug, directory=settings.router_config_dir)
    await project_repo.delete(project.id)
    return await _remove_containers(
        [
            name
            for name, slug in await containers.list_installation_containers(
                settings.network
            )
            if slug == project.slug
        ]
    )


async def remove_orphans(settings: Settings) -> int:
    """Remove containers whose project no longer exists. Returns how many.

    The backstop for `delete_project`: a deploy that was already building when
    its project was deleted starts its container afterwards, and a removal
    that failed halfway leaves the rest behind. Run periodically by the worker.

    Containers are listed before projects on purpose. A project created in
    between is then in the project list, so its brand new container is never
    mistaken for an orphan; the reverse order could remove it.
    """
    found = await containers.list_installation_containers(settings.network)
    existing = {project.slug for project in await project_repo.list_all()}
    return await _remove_containers(
        [name for name, slug in found if slug and slug not in existing]
    )


async def _remove_containers(names: list[str]) -> int:
    removed = 0
    for name in names:
        try:
            await containers.remove(name, force=True)
            removed += 1
        except containers.DockerError as exc:
            # Already gone is the goal reached. Anything else is left for the
            # next orphan sweep rather than failing the whole deletion.
            if not containers.is_missing(exc):
                logger.warning("could not remove container %s: %s", name, exc)
    return removed


async def reconcile(settings: Settings) -> dict[str, int]:
    """Make the database agree with the Docker daemon.

    Run at worker startup. The two drift for ordinary reasons — the host
    rebooted, a container was OOM-killed, someone ran `docker rm` — and the
    cost of not noticing is a project whose production deployment is listed as
    serving while its domain returns 502.

    Docker is treated as the truth about containers and the database as the
    truth about intent, so a container that is gone is recorded as gone, and a
    deployment whose worker died is failed rather than left building forever.
    """
    await containers.ensure_network(settings.network)

    detached = 0
    restored = 0
    for project in await project_repo.list_all():
        for deployment in await deployment_repo.list_for_project(project.id):
            if deployment.container_id is None:
                continue
            if await containers.is_running(deployment.container_id):
                continue
            await deployment_repo.set_container(deployment.id, None)
            detached += 1
            if project.production_deployment_id == deployment.id:
                # Production pointing at a dead container is the one case worth
                # acting on rather than just recording: the site is down.
                restored += 1

    abandoned = await deployment_repo.reclaim_abandoned(
        older_than_seconds=settings.build_timeout_seconds + 120
    )
    return {
        "detached": detached,
        "production_down": restored,
        "abandoned": len(abandoned),
        "orphans": await remove_orphans(settings),
    }
