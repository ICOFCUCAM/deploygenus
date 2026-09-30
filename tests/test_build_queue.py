"""Production first, newer replaces older, previews optional.

docs/design/proposal-build-queue.md. The ordering and the replacing are SQL
(`claim_next`, `supersede_queued`); these tests hold the rules around them.
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

from dataclasses import replace

import pytest

from deploypro.domain.models import DeploymentStatus, DeploymentTrigger
from deploypro.engine import service
from deploypro.web import views
from tests import fakes
from tests.test_dashboard import anon, app, client, repos  # noqa: F401


class TestReplacing:
    @pytest.fixture
    def recorded(self, monkeypatch):
        calls = []

        async def create(**kwargs):
            return fakes.deployment(
                number=9, git_ref=kwargs["git_ref"], trigger=kwargs["trigger"]
            )

        async def supersede(newer):
            calls.append(newer.number)
            return [fakes.deployment(number=8, status=DeploymentStatus.CANCELLED)]

        monkeypatch.setattr(service.deployment_repo, "create", create)
        monkeypatch.setattr(service.deployment_repo, "supersede_queued", supersede)
        return calls

    @pytest.mark.parametrize(
        "trigger", [DeploymentTrigger.PUSH, DeploymentTrigger.MANUAL]
    )
    async def test_a_newer_build_of_a_branch_replaces_queued_ones(
        self, recorded, trigger
    ):
        await service.queue_deploy(
            fakes.project(), ref="main", sha="a" * 40, trigger=trigger
        )
        assert recorded == [9]

    @pytest.mark.parametrize(
        "trigger", [DeploymentTrigger.REDEPLOY, DeploymentTrigger.ROLLBACK]
    )
    async def test_a_redeploy_or_rollback_replaces_nothing(self, recorded, trigger):
        """They name a commit on purpose (§5, D2)."""
        await service.queue_deploy(
            fakes.project(), ref="main", sha="a" * 40, trigger=trigger
        )
        assert recorded == []


class TestCancelledSays:
    def test_a_replaced_build_says_by_which(self):
        cancelled = fakes.deployment(
            status=DeploymentStatus.CANCELLED,
            error="Replaced by #226, a newer commit on main, before it started building.",
        )
        v = views.view(cancelled, fakes.project())
        assert views.detail_sentence(v).startswith("Replaced by #226")

    def test_an_ordinary_cancel_keeps_phase_3s_sentence(self):
        cancelled = fakes.deployment(status=DeploymentStatus.CANCELLED, error=None)
        v = views.view(cancelled, fakes.project())
        assert views.detail_sentence(v) == "Cancelled before it started building."


class TestPages:
    async def test_the_build_page_offers_the_switch(self, client, repos):
        body = (await client.get("/projects/blog/config/build")).text
        assert 'name="preview_deploys"' in body and "checked" in body

    @pytest.mark.parametrize(
        ("posted", "expected"), [({"preview_deploys": "1"}, True), ({}, False)]
    )
    async def test_saving_the_build_page_sets_it(
        self, client, repos, monkeypatch, posted, expected
    ):
        saved = {}

        async def update(_id, changes):
            saved.update(changes)

        monkeypatch.setattr("deploypro.web.routes.project_repo.update", update)
        await client.post("/projects/blog/config/build", data=posted)
        assert saved["preview_deploys"] is expected

    async def test_the_repository_page_says_what_pushes_do(self, client, repos):
        repos["projects"][0] = replace(
            repos["projects"][0], github_repo="you/blog", github_installation_id=42
        )
        on = (await client.get("/projects/blog/config/repository")).text
        assert "a push to any other branch deploys a preview" in on

        repos["projects"][0] = replace(repos["projects"][0], preview_deploys=False)
        off = (await client.get("/projects/blog/config/repository")).text
        assert "pushes to other branches are ignored (previews are off)" in off
