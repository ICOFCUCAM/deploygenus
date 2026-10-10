"""Starting a deployment's container and proving it serves.

Shared by the deploy pipeline and by promotion, because they need exactly the
same thing for different reasons: the pipeline has just built an image, and
promotion has found an old deployment whose container was reclaimed. Both end
with "a container for this image, on the network, answering HTTP".
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from deploypro.adapters import containers
from deploypro.config import Settings
from deploypro.domain import naming
from deploypro.domain.buildplan import DEFAULT_PORT
from deploypro.domain.errors import Conflict, DeployFailed
from deploypro.domain.models import Deployment, EnvTarget, LogStream, Project
from deploypro.engine import environment, health, storage
from deploypro.engine import redis as redis_engine
from deploypro.engine.logs import LogWriter
from deploypro.repositories import deployments as deployment_repo
from deploypro.repositories import volumes as volume_repo


async def launch(
    project: Project,
    deployment: Deployment,
    *,
    settings: Settings,
    log: LogWriter,
    target: EnvTarget,
    workdir: Path,
) -> str:
    """Run the deployment's image and return the container id once healthy.

    On any failure the container is destroyed before the error is raised. A
    half-started deployment left behind would hold its name, its memory and —
    because its Traefik labels are already in place — a route to a process
    that does not answer.
    """
    if not deployment.image_tag:
        raise Conflict("This deployment has no image to run")
    if not await containers.image_exists(deployment.image_tag):
        raise Conflict(
            f"The image for deployment #{deployment.number} is no longer on "
            "this host, so it cannot be started without rebuilding. "
            "Redeploy the commit instead."
        )

    port = deployment.internal_port or DEFAULT_PORT
    url = settings.deployment_url(deployment.short_id)

    env = await environment.collect(
        project.id,
        target=target,
        key=settings.master_key,
        injected={
            **await redis_engine.variables(project, target=target, settings=settings),
            **environment.platform_variables(
                short_id=deployment.short_id,
                git_sha=deployment.git_sha,
                url=url,
                target=target,
            ),
        },
    )
    env_file = environment.write_runtime_env_file(env, workdir / "runtime.env")

    name = naming.container_name(deployment.short_id)
    # A previous attempt that died after `docker run` but before the database
    # write leaves this name taken; without clearing it the retry fails on a
    # name conflict that reads like a Docker problem rather than a stale one.
    await containers.remove_by_name(name)

    await log.system(
        f"starting container on port {port} with {len(env)} environment "
        f"variable{'' if len(env) == 1 else 's'}"
    )

    mounts = await storage.mounts_for(project, target=target)
    if mounts:
        await log.system(f"mounting {storage.describe(mounts)}")
    elif target is EnvTarget.PREVIEW and await volume_repo.list_for_project(project.id):
        await log.system(
            "this is a preview, so the project's volumes are not mounted — "
            "anything it writes is discarded with its container"
        )

    if project.is_background:
        return await _launch_background(
            project,
            deployment,
            name=name,
            env=env,
            env_file=env_file,
            mounts=mounts,
            settings=settings,
            log=log,
        )

    spec = containers.RunSpec(
        image=deployment.image_tag,
        name=name,
        network=settings.network,
        host=settings.deployment_host(deployment.short_id),
        port=port,
        router=naming.router_id(deployment.short_id),
        memory_mb=project.memory_mb,
        cpu_shares=project.cpu_shares,
        cert_resolver=settings.cert_resolver,
        stop_timeout=project.stop_timeout_seconds,
        volumes=mounts,
        env_file=env_file,
        inline_env=env.inline,
        labels={
            containers.PROJECT_LABEL: project.slug,
            containers.DEPLOYMENT_LABEL: deployment.short_id,
        },
    )

    container_id = await containers.run(spec, log=log.sink(LogStream.SYSTEM))

    result = await health.wait_until_healthy(
        container_id=container_id,
        network=settings.network,
        port=port,
        path=settings.health_path,
        timeout=settings.health_timeout_seconds,
    )

    if not result.healthy:
        # The container's own output is the only thing that explains this, and
        # it is about to be destroyed, so it is copied into the deploy log
        # first. Without this the failure reads "unhealthy" and nothing else.
        tail = await containers.logs(container_id, tail=100)
        await log.system(f"health check failed: {result.detail}")
        await log.system("--- last 100 lines from the container ---")
        lines = tail.splitlines()
        for line in lines:
            await log.write(line, stream=LogStream.RUN)
        if not lines:
            # An empty section under that heading reads as "the platform is
            # broken", and is indistinguishable from a log that was captured
            # and dropped. Say which it is.
            await log.system(
                "(the container produced no output at all — it was killed "
                "before it wrote anything, or its entrypoint is wrong)"
            )
        await containers.remove(container_id, force=True)
        await log.flush()
        raise DeployFailed(f"The container never became healthy: {result.detail}")

    await log.system(
        f"healthy after {result.elapsed_seconds:.1f}s "
        f"({result.attempts} attempt{'' if result.attempts == 1 else 's'}) — "
        f"{result.detail}"
    )
    await deployment_repo.set_container(deployment.id, container_id)
    return container_id


#: How long a background service must keep running to count as started. It has
#: no port to answer on, so "still running after it settled" is the check: long
#: enough for a missing variable or a bad import to crash it, short enough that
#: a deploy does not stall on it.
BACKGROUND_SETTLE_SECONDS = 15


async def _settles(container_id: str) -> bool:
    """Still up, and never restarted, after the settle time.

    The container runs under `--restart unless-stopped`, so one that crashes
    on start is brought back at once and reads as running most of the time:
    the restart count is what gives a crash loop away.
    """
    for _ in range(BACKGROUND_SETTLE_SECONDS):
        await _sleep(1)
        if not await containers.is_running(container_id):
            return False
        if await containers.restart_count(container_id) > 0:
            return False
    return True


async def _launch_background(
    project: Project,
    deployment: Deployment,
    *,
    name: str,
    env,
    env_file: Path,
    mounts,
    settings: Settings,
    log: LogWriter,
) -> str:
    """A container with no address: no router labels, no port, no domains.

    The same image, environment and volumes as a website, started from the
    image's own CMD. docs/design/plan-replace-render.md, G2.
    """
    await log.system(
        "background service — no web address; healthy if still running after "
        f"{BACKGROUND_SETTLE_SECONDS}s"
    )
    container_id = await containers.run_worker(
        containers.TaskSpec(
            image=deployment.image_tag,
            name=name,
            network=settings.network,
            command=None,
            memory_mb=project.memory_mb,
            env_file=env_file,
            inline_env=env.inline,
            volumes=mounts,
            stop_timeout=project.stop_timeout_seconds,
            labels={
                containers.OWNER_LABEL: containers.OWNER_VALUE,
                containers.PROJECT_LABEL: project.slug,
                containers.DEPLOYMENT_LABEL: deployment.short_id,
                containers.ROLE_LABEL: "service",
            },
        )
    )
    if not await _settles(container_id):
        tail = await containers.logs(container_id, tail=100)
        await log.system(
            "the service stopped or restarted within "
            f"{BACKGROUND_SETTLE_SECONDS}s of starting"
        )
        await log.system("--- last 100 lines from the container ---")
        lines = tail.splitlines()
        for line in lines:
            await log.write(line, stream=LogStream.RUN)
        if not lines:
            await log.system("(the container produced no output at all)")
        await containers.remove(container_id, force=True)
        await log.flush()
        raise DeployFailed(
            f"The service exited within {BACKGROUND_SETTLE_SECONDS}s of starting "
            "(or kept restarting)."
        )
    await log.system(f"running after {BACKGROUND_SETTLE_SECONDS}s")
    await deployment_repo.set_container(deployment.id, container_id)
    return container_id


async def _sleep(seconds: float) -> None:
    """Separate so tests do not wait the settle time out."""
    await asyncio.sleep(seconds)


async def ensure_serving(
    project: Project,
    deployment: Deployment,
    *,
    settings: Settings,
    log: LogWriter,
    workdir: Path,
) -> str:
    """The container for this deployment, started if it is not already.

    Rollback goes through here. A deployment kept warm answers instantly; one
    whose container was reclaimed to free memory is restarted from its image,
    which costs seconds rather than a rebuild.
    """
    if deployment.container_id and await containers.is_running(deployment.container_id):
        return deployment.container_id

    await log.system(
        f"deployment #{deployment.number} is not resident — restarting it from its image"
    )
    return await launch(
        project,
        deployment,
        settings=settings,
        log=log,
        target=EnvTarget.PRODUCTION,
        workdir=workdir,
    )
