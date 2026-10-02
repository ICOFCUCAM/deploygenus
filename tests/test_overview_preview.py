"""The Overview's picture of the live site, and the numbers beside it.

docs/design/amendment-overview-preview.md.
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from deploypro.domain.models import DeploymentStatus
from deploypro.engine import previews
from deploypro.repositories.previews import SitePreview
from deploypro.web import views
from tests import fakes
from tests.test_dashboard import anon, app, client, repos  # noqa: F401

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def row(**kw) -> SitePreview:
    base = dict(
        project_id=uuid4(),
        status="captured",
        deployment_id=None,
        requested_at=NOW,
        started_at=NOW,
        captured_deployment_id=None,
        captured_at=NOW - timedelta(minutes=5),
        captured_url="https://vid.example/",
        desktop_file="vid-3-desktop.png",
        mobile_file="vid-3-mobile.png",
        error=None,
    )
    base.update(kw)
    return SitePreview(**base)


class TestPreviewView:
    def test_a_picture_says_when_and_from_which_deployment(self):
        live = fakes.deployment(number=276)
        v = views.preview(row(captured_deployment_id=live.id), live, 276, NOW)
        assert v.state == "captured" and v.caption == "captured 11:55 · #276"
        assert not v.stale and v.has_mobile

    def test_a_picture_of_an_older_deployment_is_marked_stale(self):
        live = fakes.deployment(number=277)
        v = views.preview(row(captured_deployment_id=uuid4()), live, 276, NOW)
        assert v.stale

    def test_capturing_keeps_the_old_picture(self):
        v = views.preview(row(status="capturing"), fakes.deployment(), None, NOW)
        assert v.state == "pending" and v.has_picture

    def test_a_failure_says_why(self):
        v = views.preview(
            row(status="failed", error="https://vid.example/ answered 502"),
            fakes.deployment(),
            None,
            NOW,
        )
        assert v.state == "failed" and v.problem.endswith("answered 502")

    def test_never_captured(self):
        assert views.preview(None, fakes.deployment(), None, NOW).state == "none"


class TestStats:
    def test_only_recorded_numbers_over_seven_days(self):
        def dep(days, status, build_s=240):
            created = NOW - timedelta(days=days)
            return fakes.deployment(
                status=status,
                created_at=created,
                started_at=created,
                built_at=created + timedelta(seconds=build_s),
            )

        stats = views.deploy_stats(
            [
                dep(1, DeploymentStatus.READY, 200),
                dep(2, DeploymentStatus.FAILED, 300),
                dep(3, DeploymentStatus.READY, 250),
                dep(9, DeploymentStatus.READY),  # outside the window
            ],
            NOW,
        )
        assert [s.label for s in stats] == [
            "Deployments",
            "Average build",
            "Success rate",
            "Time to live",
        ]
        assert stats[0].value == "3"
        assert stats[1].value == "4m 10s"
        assert stats[2].value == "67%"

    def test_no_deployments_is_a_dash_not_a_zero(self):
        stats = views.deploy_stats([], NOW)
        assert [s.value for s in stats[1:]] == ["—", "—", "—"]

    def test_time_to_live_is_queued_to_serving(self):
        created = NOW - timedelta(days=1)
        stats = views.deploy_stats(
            [
                fakes.deployment(
                    status=DeploymentStatus.READY,
                    created_at=created,
                    ready_at=created + timedelta(seconds=75),
                )
            ],
            NOW,
        )
        assert stats[3].label == "Time to live" and stats[3].value == "1m 15s"

    def test_each_number_is_compared_with_the_week_before(self):
        def dep(days, status, build_s):
            created = NOW - timedelta(days=days)
            return fakes.deployment(
                status=status,
                created_at=created,
                started_at=created,
                built_at=created + timedelta(seconds=build_s),
            )

        stats = views.deploy_stats(
            [
                dep(1, DeploymentStatus.READY, 30),
                dep(2, DeploymentStatus.READY, 30),
                dep(8, DeploymentStatus.READY, 60),  # the week before
                dep(9, DeploymentStatus.FAILED, 60),
                dep(15, DeploymentStatus.READY, 60),  # older: ignored
            ],
            NOW,
        )
        assert (stats[0].trend, stats[0].trend_sense) == ("→ 0%", "better")
        # Builds got faster: down is good news.
        assert (stats[1].trend, stats[1].trend_sense) == ("↓ 50%", "better")
        assert (stats[2].trend, stats[2].trend_sense) == ("↑ 50 pts", "better")

    def test_no_trend_without_a_week_before_to_compare(self):
        created = NOW - timedelta(days=1)
        stats = views.deploy_stats(
            [fakes.deployment(created_at=created, started_at=created)], NOW
        )
        assert [s.trend for s in stats] == ["", "", "", ""]


class TestAddress:
    def settings(self):
        return SimpleNamespace(
            deployment_url=lambda short: f"https://{short}.deploys.test"
        )

    def test_the_primary_verified_domain_is_what_visitors_use(self):
        domains = [
            fakes.domain("www.vid.tv", verified=True, primary=False),
            fakes.domain("vid.tv", verified=True, primary=True),
        ]
        url = previews.address(
            fakes.project(), fakes.deployment(), domains, self.settings()
        )
        assert url == "https://vid.tv/"

    def test_without_a_verified_domain_the_deployments_own_address(self):
        domains = [fakes.domain("vid.tv", verified=False, primary=True)]
        d = fakes.deployment(short_id="vid-1a2b3c4d")
        url = previews.address(fakes.project(), d, domains, self.settings())
        assert url == "https://vid-1a2b3c4d.deploys.test"


class TestCapture:
    @pytest.fixture
    def world(self, monkeypatch, tmp_path):
        live = fakes.deployment(number=3)
        project = fakes.project(production_deployment_id=live.id)
        recorded = {}

        async def get_project(_id):
            return project

        async def get_deployment(_id):
            return live

        async def no_domains(_id):
            return []

        async def failed(project_id, *, error):
            recorded["failed"] = error

        async def captured(project_id, **kw):
            recorded["captured"] = kw

        async def ensure_image(image, timeout=900):
            recorded["pulled"] = image

        shots = []

        async def screenshot(
            image, url, dest, *, width, height, mobile=False, timeout=45
        ):
            shots.append((width, mobile))
            if recorded.get("mobile_breaks") and mobile:
                raise previews.containers.DockerError("docker start failed")
            dest.write_bytes(b"\x89PNG")

        monkeypatch.setattr(previews.project_repo, "get", get_project)
        monkeypatch.setattr(previews.project_repo, "list_domains", no_domains)
        monkeypatch.setattr(previews.deployment_repo, "get", get_deployment)
        monkeypatch.setattr(previews.preview_repo, "failed", failed)
        monkeypatch.setattr(previews.preview_repo, "captured", captured)
        monkeypatch.setattr(previews.containers, "ensure_image", ensure_image)
        monkeypatch.setattr(previews.containers, "screenshot", screenshot)
        settings = SimpleNamespace(
            preview_image="zenika/alpine-chrome:latest",
            previews_dir=tmp_path,
            deployment_url=lambda short: "https://vid.deploys.test",
        )
        return SimpleNamespace(
            settings=settings,
            recorded=recorded,
            shots=shots,
            row=row(deployment_id=live.id),
        )

    def answer(self, monkeypatch, status=200, error=None):
        def handler(request):
            if error:
                raise error
            return httpx.Response(status)

        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient
        monkeypatch.setattr(
            previews.httpx,
            "AsyncClient",
            lambda **kw: real(transport=transport, **kw),
        )

    async def test_both_sizes_are_taken_and_recorded(self, world, monkeypatch):
        self.answer(monkeypatch)
        (world.settings.previews_dir / "blog-1-old-desktop.png").write_bytes(b"old")
        await previews._capture(world.settings, world.row)
        assert world.shots == [(1440, False), (390, True)]
        kw = world.recorded["captured"]
        assert kw["url"] == "https://vid.deploys.test"
        assert kw["desktop_file"].endswith("-desktop.png")
        assert kw["mobile_file"].endswith("-mobile.png")
        # Only the current pair is kept.
        names = sorted(p.name for p in world.settings.previews_dir.iterdir())
        assert names == sorted([kw["desktop_file"], kw["mobile_file"]])

    async def test_a_site_that_does_not_answer_is_not_photographed(
        self, world, monkeypatch
    ):
        self.answer(monkeypatch, error=httpx.ConnectError("refused"))
        await previews._capture(world.settings, world.row)
        assert world.shots == [] and "did not answer" in world.recorded["failed"]

    async def test_a_server_error_is_not_photographed(self, world, monkeypatch):
        self.answer(monkeypatch, status=502)
        await previews._capture(world.settings, world.row)
        assert world.shots == [] and world.recorded["failed"].endswith("answered 502")

    async def test_a_failed_mobile_picture_keeps_the_desktop_one(
        self, world, monkeypatch
    ):
        self.answer(monkeypatch)
        world.recorded["mobile_breaks"] = True
        await previews._capture(world.settings, world.row)
        kw = world.recorded["captured"]
        assert kw["desktop_file"] and kw["mobile_file"] is None

    async def test_requesting_never_raises_into_a_promotion(self, monkeypatch):
        async def broken(*a):
            raise RuntimeError("database is restarting")

        monkeypatch.setattr(previews.preview_repo, "request", broken)
        settings = SimpleNamespace(preview_image="x")
        await previews.request(settings, fakes.project(), fakes.deployment())


class TestPage:
    def serving(self, repos):
        live = repos["deployments"][0]
        repos["projects"][0] = fakes.project(production_deployment_id=live.id)
        return live

    async def test_the_picture_sits_beside_production(self, client, repos):
        live = self.serving(repos)
        repos["preview"] = row(captured_deployment_id=live.id)
        body = (await client.get("/projects/blog")).text
        assert "/projects/blog/preview/desktop.png?v=" in body
        assert "Open live site" in body and "Open in new tab" in body
        assert ">Desktop<" in body and ">Mobile<" in body

    async def test_no_picture_yet_offers_to_capture(self, client, repos):
        self.serving(repos)
        body = (await client.get("/projects/blog")).text
        assert "No preview yet." in body and "Capture preview" in body

    async def test_capturing_refreshes_the_page(self, client, repos):
        live = self.serving(repos)
        repos["preview"] = row(status="capturing", captured_deployment_id=live.id)
        body = (await client.get("/projects/blog")).text
        assert "Capturing preview" in body and 'http-equiv="refresh"' in body

    async def test_refresh_asks_for_a_new_picture(self, client, repos):
        live = self.serving(repos)
        response = await client.post("/projects/blog/preview")
        assert response.headers["location"].startswith("/projects/blog?ok=")
        assert repos["preview_requests"] == [live.id]

    async def test_the_picture_is_served_only_when_it_exists(
        self, client, repos, tmp_path, monkeypatch
    ):
        from deploypro.config import get_settings
        from deploypro.deps import settings_dep

        self.serving(repos)
        (tmp_path / "vid-3-desktop.png").write_bytes(b"\x89PNG-fake")
        repos["preview"] = row()
        settings = replace(get_settings(), previews_dir=tmp_path)
        from deploypro.main import app as application

        application.dependency_overrides[settings_dep] = lambda: settings
        try:
            ok = await client.get("/projects/blog/preview/desktop.png")
            missing = await client.get("/projects/blog/preview/mobile.png")
            other = await client.get("/projects/blog/preview/passwd.png")
        finally:
            application.dependency_overrides.pop(settings_dep, None)
        assert ok.status_code == 200 and ok.content == b"\x89PNG-fake"
        assert missing.status_code == 404
        assert other.status_code == 404

    async def test_open_appears_only_for_running_deployments(self, client, repos):
        """A stopped deployment's address serves nothing (amendment §2)."""
        self.serving(repos)
        repos["deployments"].append(
            fakes.deployment(number=11, short_id="blog-0000aaaa", container_id=None)
        )
        body = (await client.get("/projects/blog")).text
        table = body.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        rows = table.split("<tr")[1:]
        opens = {
            r.split('href="/deployments/', 1)[1].split('"', 1)[0]
            for r in rows
            if ">Open " in r
        }
        assert opens == {repos["deployments"][0].short_id}
