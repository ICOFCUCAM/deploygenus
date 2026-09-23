"""The image sweep: what it may delete, and above all what it may not."""

from __future__ import annotations

from uuid import uuid4

from forge.domain import naming
from forge.domain.models import DeploymentStatus
from forge.domain.retention import images_to_keep, images_to_remove
from tests import fakes


def sha(number: int) -> str:
    return f"{number:012x}" + "0" * 28


def history(n: int, **overrides):
    """n ready deployments, newest first, none running."""
    return [
        fakes.deployment(
            id=uuid4(),
            number=number,
            git_sha=sha(number),
            image_tag=naming.image_tag("blog", sha(number)),
            container_id=None,
            **overrides,
        )
        for number in range(n, 0, -1)
    ]


class TestKeep:
    def test_keeps_the_newest_rollback_targets(self):
        deployments = history(15)
        kept = images_to_keep(fakes.project(), deployments, keep=3)
        assert kept == {f"forge/blog:{n:012x}" for n in (15, 14, 13)}

    def test_always_keeps_production_however_old(self):
        deployments = history(15)
        oldest = deployments[-1]
        project = fakes.project(production_deployment_id=oldest.id)
        assert oldest.image_tag in images_to_keep(project, deployments, keep=3)

    def test_keeps_anything_with_a_container(self):
        deployments = history(15)
        deployments[-2] = fakes.deployment(
            id=uuid4(), number=2, image_tag="forge/blog:warm", container_id="c1"
        )
        assert "forge/blog:warm" in images_to_keep(fakes.project(), deployments, keep=1)

    def test_keeps_a_build_whose_row_does_not_name_its_image_yet(self):
        building = fakes.deployment(
            id=uuid4(),
            number=16,
            status=DeploymentStatus.BUILDING,
            git_sha="ab" * 20,
            image_tag=None,
            container_id=None,
        )
        kept = images_to_keep(fakes.project(), [building, *history(3)], keep=0)
        assert naming.image_tag("blog", "ab" * 20) in kept

    def test_keeps_the_image_a_queued_redeploy_will_reuse(self):
        queued = fakes.deployment(
            id=uuid4(),
            number=16,
            status=DeploymentStatus.QUEUED,
            git_sha=sha(1),
            image_tag=None,
            container_id=None,
        )
        kept = images_to_keep(fakes.project(), [queued, *history(15)], keep=0)
        assert "forge/blog:000000000001" in kept

    def test_failed_deployments_are_not_rollback_targets(self):
        failed = history(3, status=DeploymentStatus.FAILED)
        assert images_to_keep(fakes.project(), failed, keep=10) == set()

    def test_keep_zero_still_keeps_production(self):
        deployments = history(5)
        project = fakes.project(production_deployment_id=deployments[0].id)
        assert images_to_keep(project, deployments, keep=0) == {deployments[0].image_tag}


class TestRemove:
    def test_removes_only_what_is_not_kept(self):
        present = ["forge/blog:a", "forge/blog:b", "forge/shop:c"]
        keep = {"blog": {"forge/blog:a"}, "shop": {"forge/shop:c"}}
        assert images_to_remove(present, keep) == ["forge/blog:b"]

    def test_a_deleted_projects_images_all_go(self):
        present = ["forge/gone:a", "forge/gone:b", "forge/blog:a"]
        keep = {"blog": {"forge/blog:a"}}
        assert images_to_remove(present, keep) == ["forge/gone:a", "forge/gone:b"]
