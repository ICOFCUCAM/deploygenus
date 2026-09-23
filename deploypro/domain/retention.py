"""Which images a host can afford to forget.

Every deploy leaves an image behind, and a busy project fills a disk with them
in weeks. Deleting one is safe only if nothing will ask for it in a hurry, so
this errs towards keeping: an image is removed only when it belongs to no
deployment that is serving, warm, being built, or among the newest rollback
targets. A deployment whose image is gone is still listed and can still be
redeployed — that rebuilds it, which takes minutes instead of a second, and is
the whole cost of forgetting it.

Pure, so the rule can be tested without a daemon: a bug here deletes the image
someone is about to roll back to.
"""

from __future__ import annotations

from collections.abc import Iterable

from deploypro.domain import naming
from deploypro.domain.models import Deployment, DeploymentStatus, Project


def images_to_keep(
    project: Project, deployments: Iterable[Deployment], *, keep: int
) -> set[str]:
    """Every image tag of this project that must survive a sweep."""
    kept: set[str] = set()
    ready: list[Deployment] = []
    for deployment in deployments:
        if (
            deployment.status in {DeploymentStatus.QUEUED}
            or deployment.status.is_in_flight
        ):
            # Not yet recorded on the row: the tag is written by mark_built,
            # after the build. Derived from the commit so a sweep between the
            # build finishing and the row being updated cannot delete it.
            kept.add(naming.image_tag(project.slug, deployment.git_sha))
            continue
        if not deployment.image_tag:
            continue
        if (
            deployment.id == project.production_deployment_id
            or deployment.container_id is not None
        ):
            kept.add(deployment.image_tag)
        if deployment.status is DeploymentStatus.READY:
            ready.append(deployment)
    ready.sort(key=lambda d: d.number, reverse=True)
    kept.update(d.image_tag for d in ready[:keep] if d.image_tag)
    return kept


def images_to_remove(
    present: Iterable[str], keep_by_project: dict[str, set[str]]
) -> list[str]:
    """Tags on the host that no project needs.

    `keep_by_project` is keyed by slug. An image whose project no longer exists
    is removed whole: nothing can roll back to a deployment of a deleted
    project.
    """
    doomed = []
    for tag in present:
        repository, _, _ = tag.partition(":")
        slug = repository.removeprefix("deploypro/")
        if tag not in keep_by_project.get(slug, set()):
            doomed.append(tag)
    return sorted(doomed)
