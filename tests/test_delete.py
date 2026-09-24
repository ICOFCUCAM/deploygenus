# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments
"""Deleting a project stops everything it was running.

Regression: both delete paths removed the rows and the route file and left
every container running. The code said "the worker's next reconciliation"
would clean them up; reconciliation never looked for them. A deleted project's
deployments stayed reachable on their own addresses and its workers kept
running, with nothing in the database to show they existed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deploypro.adapters import containers
from deploypro.engine import service
from tests import fakes
from tests.test_containers import FakeDocker
from tests.test_dashboard import TOKEN, _async, app, client, repos  # noqa: F401

NETWORK = "deploypro"

#: What `docker ps` would report for this installation: two projects, with a
#: web deployment, a worker, a worker still finishing its work, a job run and
#: a stopped (reclaimed) deployment for `blog`.
RUNNING = [
    ("deploypro-blog-3f9a2c71", "blog"),
    ("deploypro-blog-11c9e2a7", "blog"),
    ("deploypro-blog-render-0", "blog"),
    ("deploypro-blog-render-0.draining.1790000000", "blog"),
    ("deploypro-job-blog-tidy-202609221430", "blog"),
    ("deploypro-shop-9d1e0f44", "shop"),
    ("deploypro-shop-mailer-0", "shop"),
]


@pytest.fixture
def settings(tmp_path):
    return SimpleNamespace(network=NETWORK, router_config_dir=tmp_path / "router")


@pytest.fixture
def world(monkeypatch):
    """Docker and the database, recorded instead of touched."""
    state = SimpleNamespace(
        containers=list(RUNNING),
        projects=[fakes.project(slug="blog"), fakes.project(slug="shop")],
        events=[],
        failing=set(),
    )

    async def list_installation_containers(network):
        state.events.append(("list containers", network))
        return list(state.containers)

    async def remove(name, *, force=True):
        state.events.append(("remove", name))
        if name in state.failing:
            raise containers.DockerError("Error response from daemon: busy")
        state.containers = [c for c in state.containers if c[0] != name]

    async def delete(project_id):
        state.events.append(("delete rows", project_id))
        state.projects = [p for p in state.projects if p.id != project_id]

    async def list_all():
        state.events.append(("list projects",))
        return list(state.projects)

    monkeypatch.setattr(
        service.containers, "list_installation_containers", list_installation_containers
    )
    monkeypatch.setattr(service.containers, "remove", remove)
    monkeypatch.setattr(service.project_repo, "delete", delete)
    monkeypatch.setattr(service.project_repo, "list_all", list_all)
    return state


def names(state, slug):
    return [name for name, owner in state.containers if owner == slug]


class TestDeleteProject:
    async def test_every_container_of_the_project_is_removed(self, world, settings):
        blog = world.projects[0]
        removed = await service.delete_project(blog, settings)
        assert removed == 5
        assert names(world, "blog") == []

    async def test_other_projects_are_untouched(self, world, settings):
        await service.delete_project(world.projects[0], settings)
        assert names(world, "shop") == [
            "deploypro-shop-9d1e0f44",
            "deploypro-shop-mailer-0",
        ]

    async def test_routing_goes_first_and_rows_before_containers(
        self, world, settings, monkeypatch
    ):
        """Nothing is routed to the project while it goes, and nothing can
        start a new container for it once its containers are being removed."""
        cleared = []
        monkeypatch.setattr(
            service.edge,
            "clear_route",
            lambda slug, directory: cleared.append(len(world.events)),
        )
        blog = world.projects[0]
        await service.delete_project(blog, settings)
        assert cleared == [0]
        kinds = [event[0] for event in world.events]
        assert kinds.index("delete rows") < kinds.index("remove")

    async def test_the_route_file_is_deleted(self, world, settings):
        settings.router_config_dir.mkdir()
        route = settings.router_config_dir / "project-blog.yml"
        route.write_text("{}")
        await service.delete_project(world.projects[0], settings)
        assert not route.exists()

    async def test_only_this_installations_network_is_asked(self, world, settings):
        await service.delete_project(world.projects[0], settings)
        assert ("list containers", NETWORK) in world.events

    async def test_one_container_that_will_not_go_does_not_stop_the_rest(
        self, world, settings
    ):
        world.failing.add("deploypro-blog-render-0")
        removed = await service.delete_project(world.projects[0], settings)
        assert removed == 4
        assert names(world, "blog") == ["deploypro-blog-render-0"]


class TestOrphanSweep:
    async def test_containers_of_a_deleted_project_are_removed(self, world, settings):
        """The backstop: a deploy still building when its project was deleted
        starts its container afterwards."""
        world.projects = [p for p in world.projects if p.slug != "blog"]
        removed = await service.remove_orphans(settings)
        assert removed == 5
        assert names(world, "blog") == []
        assert len(names(world, "shop")) == 2

    async def test_nothing_is_removed_while_every_project_exists(self, world, settings):
        assert await service.remove_orphans(settings) == 0
        assert world.containers == RUNNING

    async def test_containers_are_listed_before_projects(self, world, settings):
        """So a project created between the two reads is known by the time its
        new container is judged, and never mistaken for an orphan."""
        await service.remove_orphans(settings)
        kinds = [event[0] for event in world.events]
        assert kinds.index("list containers") < kinds.index("list projects")

    async def test_a_container_without_a_project_label_is_left_alone(
        self, world, settings
    ):
        world.containers.append(("deploypro-something-else", ""))
        await service.remove_orphans(settings)
        assert ("remove", "deploypro-something-else") not in world.events


class TestListingIsScopedToThisInstallation:
    async def test_the_owner_label_and_the_network_both_filter(self, monkeypatch):
        """Another DeployPro on the same daemon (the end-to-end run, a staging
        copy) labels its containers the same way. Only the network tells them
        apart, and a sweep without it would delete the other one's sites."""
        docker = FakeDocker()
        docker.responses["ps"] = "deploypro-blog-3f9a2c71\tblog\ndeploypro-job-x\t\n"
        monkeypatch.setattr(containers, "_capture", docker.capture)

        found = await containers.list_installation_containers("deploypro-e2e")

        (call,) = docker.called("ps")
        assert "--all" in call
        assert f"label={containers.OWNER_LABEL}={containers.OWNER_VALUE}" in call
        assert "network=deploypro-e2e" in call
        assert found == [("deploypro-blog-3f9a2c71", "blog"), ("deploypro-job-x", "")]


class TestBothDeletePathsUseIt:
    @pytest.fixture
    def deleted(self, monkeypatch):
        calls = []

        async def delete_project(project, settings):
            calls.append(project.slug)
            return 0

        monkeypatch.setattr(service, "delete_project", delete_project)
        return calls

    async def test_the_dashboard(self, client, repos, deleted):
        response = await client.post("/projects/blog/delete", data={"confirm": "Blog"})
        assert response.status_code == 303
        assert deleted == ["blog"]

    async def test_the_api(self, client, repos, deleted, monkeypatch):
        monkeypatch.setattr(
            "deploypro.deps.project_repo.resolve",
            lambda ref: _async(repos["projects"][0]),
        )
        response = await client.delete(
            "/api/projects/blog", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status_code == 204
        assert deleted == ["blog"]
