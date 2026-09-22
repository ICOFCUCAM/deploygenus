"""Turning a project's volumes into mounts for a container about to start.

One function, called by everything that starts a container, so the rule about
previews is stated once: production gets the project's volumes, a preview gets
none. A branch deploy writing into the production recordings directory is the
storage equivalent of a preview holding the production database password.
"""

from __future__ import annotations

from forge.adapters import containers
from forge.domain.models import EnvTarget, Project
from forge.domain.storage import Mount, docker_volume_name
from forge.repositories import volumes as volume_repo

VOLUME_LABEL = "forge.volume"


async def mounts_for(project: Project, *, target: EnvTarget) -> tuple[Mount, ...]:
    """The mounts a container of this project gets, creating volumes as needed.

    Volumes are created here rather than when they are configured, because
    configuration is a database write and Docker may not be reachable from
    wherever it happened; the first container to need one is always on the
    host that will hold it.
    """
    if target is not EnvTarget.PRODUCTION:
        return ()
    mounts = []
    for volume in await volume_repo.list_for_project(project.id):
        name = docker_volume_name(project.slug, volume.name)
        await containers.ensure_volume(
            name,
            labels={
                containers.OWNER_LABEL: containers.OWNER_VALUE,
                containers.PROJECT_LABEL: project.slug,
                VOLUME_LABEL: volume.name,
            },
        )
        mounts.append(Mount(volume=name, path=volume.mount_path))
    return tuple(mounts)


def describe(mounts: tuple[Mount, ...]) -> str:
    return ", ".join(f"{mount.volume} at {mount.path}" for mount in mounts)
