"""Background services: projects that run with no website.

docs/design/plan-replace-render.md, G2 — Render's `type: worker`, which is
what dispatch-worker and cineforge-worker are: a queue consumer from its own
Dockerfile, with no port, no domains and no previews.
"""

# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from deploypro.domain.errors import DeployFailed
from deploypro.domain.models import EnvTarget
from deploypro.engine import launch as launch_module
from deploypro.engine import promote as promote_module
from deploypro.routers import webhooks
from tests import fakes
from tests.test_dashboard import anon, app, client, repos  # noqa: F401

SETTINGS = SimpleNamespace(
    network="deploypro",
    master_key="unused",
    health_path="/",
    health_timeout_seconds=30,
    cert_resolver="",
    deployment_url=lambda short: f"https://{short}.example",
    deployment_host=lambda short: f"{short}.example",
)


def background(**kw):
    return fakes.project(kind="background", **kw)


class Log:
    def __init__(self):
        self.lines: list[str] = []

    async def system(self, line):
        self.lines.append(line)

    async def write(self, line, stream=None):
        self.lines.append(line)

    async def flush(self):
        pass

    def sink(self, stream):
        async def out(line):
            self.lines.append(line)

        return out


@pytest.fixture
def docker(monkeypatch):
    """The container calls launch makes, with a scripted lifetime."""
    seen = SimpleNamespace(spec=None, routed=False, removed=[], running=True, restarts=0)

    async def run_worker(spec):
        seen.spec = spec
        return "cid-1"

    async def run(spec, log=None):
        seen.routed = True
        return "cid-web"

    async def is_running(cid):
        return seen.running

    async def restart_count(cid):
        return seen.restarts

    async def logs(cid, tail=100):
        return "Error: Cannot find module 'bullmq'"

    async def remove(cid, force=False):
        seen.removed.append(cid)

    async def nothing(*a, **k):
        return None

    async def env(*a, **k):
        return SimpleNamespace(inline={}, __len__=lambda self: 0)

    from deploypro.adapters import containers

    monkeypatch.setattr(containers, "run_worker", run_worker)
    monkeypatch.setattr(containers, "run", run)
    monkeypatch.setattr(containers, "is_running", is_running)
    monkeypatch.setattr(containers, "restart_count", restart_count)
    monkeypatch.setattr(containers, "logs", logs)
    monkeypatch.setattr(containers, "remove", remove)
    monkeypatch.setattr(containers, "remove_by_name", nothing)
    monkeypatch.setattr(containers, "image_exists", lambda tag: _true())
    monkeypatch.setattr(launch_module.environment, "collect", lambda *a, **k: _env())
    monkeypatch.setattr(
        launch_module.environment, "write_runtime_env_file", lambda env, path: path
    )
    monkeypatch.setattr(launch_module.storage, "mounts_for", lambda *a, **k: _empty())
    monkeypatch.setattr(launch_module.deployment_repo, "set_container", nothing)
    monkeypatch.setattr(launch_module, "_sleep", nothing)
    return seen


async def _true():
    return True


async def _empty():
    return ()


class _Env(dict):
    inline: dict = {}


async def _env():
    return _Env()


class TestLaunch:
    async def test_it_runs_with_no_route_and_no_port(self, docker, tmp_path):
        cid = await launch_module.launch(
            background(),
            fakes.deployment(image_tag="deploypro/blog:abc"),
            settings=SETTINGS,
            log=Log(),
            target=EnvTarget.PRODUCTION,
            workdir=tmp_path,
        )
        assert cid == "cid-1" and not docker.routed
        assert docker.spec.command is None  # the image's own CMD
        assert not any(k.startswith("traefik") for k in docker.spec.labels)

    async def test_one_that_exits_while_settling_fails_with_its_output(
        self, docker, tmp_path
    ):
        docker.running = False
        log = Log()
        with pytest.raises(DeployFailed, match="exited within 15s"):
            await launch_module.launch(
                background(),
                fakes.deployment(image_tag="deploypro/blog:abc"),
                settings=SETTINGS,
                log=log,
                target=EnvTarget.PRODUCTION,
                workdir=tmp_path,
            )
        assert "Error: Cannot find module 'bullmq'" in log.lines
        assert docker.removed == ["cid-1"]

    async def test_one_that_crash_loops_fails_though_it_reads_running(
        self, docker, tmp_path
    ):
        """`--restart unless-stopped` brings a crashing service straight back,
        so `Running` is true most of the time — seen with real Docker."""
        docker.restarts = 3
        with pytest.raises(DeployFailed, match="kept restarting"):
            await launch_module.launch(
                background(),
                fakes.deployment(image_tag="deploypro/blog:abc"),
                settings=SETTINGS,
                log=Log(),
                target=EnvTarget.PRODUCTION,
                workdir=tmp_path,
            )
        assert docker.removed == ["cid-1"]

    async def test_a_website_still_takes_the_routed_path(
        self, docker, tmp_path, monkeypatch
    ):
        async def healthy(**kw):
            return SimpleNamespace(
                healthy=True, detail="answered 200", attempts=1, elapsed_seconds=0.1
            )

        monkeypatch.setattr(launch_module.health, "wait_until_healthy", healthy)
        await launch_module.launch(
            fakes.project(),
            fakes.deployment(image_tag="deploypro/blog:abc"),
            settings=SETTINGS,
            log=Log(),
            target=EnvTarget.PRODUCTION,
            workdir=tmp_path,
        )
        assert docker.routed and docker.spec is None


class TestPromote:
    async def test_no_route_no_picture_and_nothing_kept_warm(self, monkeypatch, tmp_path):
        project = background(keep_warm=2)
        deployment = fakes.deployment()
        calls = {"publish": 0, "picture": 0, "keep": None}

        async def ensure_promotable(did, pid):
            return deployment

        async def ensure_serving(*a, **k):
            return "cid"

        async def publish(*a, **k):
            calls["publish"] += 1
            return "routed"

        async def picture(*a, **k):
            calls["picture"] += 1

        async def reclaimable(pid, *, keep, protect):
            calls["keep"] = keep
            return []

        async def nothing(*a, **k):
            return None

        async def get(pid):
            return project

        monkeypatch.setattr(
            promote_module.deployment_repo, "ensure_promotable", ensure_promotable
        )
        monkeypatch.setattr(promote_module, "ensure_serving", ensure_serving)
        monkeypatch.setattr(promote_module.routing, "publish", publish)
        monkeypatch.setattr(promote_module.previews, "request", picture)
        monkeypatch.setattr(promote_module.deployment_repo, "reclaimable", reclaimable)
        monkeypatch.setattr(promote_module.project_repo, "set_production", nothing)
        monkeypatch.setattr(promote_module.project_repo, "get", get)
        monkeypatch.setattr(promote_module.processes, "reconcile_workers", nothing)
        await promote_module.promote(
            project, deployment, settings=SETTINGS, log=Log(), workdir=tmp_path
        )
        assert calls == {"publish": 0, "picture": 0, "keep": 0}


class TestOnlyProduction:
    @pytest.fixture
    def queued(self, monkeypatch):
        calls = []

        async def fake_queue(project, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(short_id="worker-abc12345", number=3)

        monkeypatch.setattr(webhooks.service, "queue_deploy", fake_queue)
        monkeypatch.setattr(webhooks, "_out", lambda *a, **k: None)
        return calls

    def push(self, branch):
        return {"ref": f"refs/heads/{branch}", "after": "d" * 40, "head_commit": {}}

    def project(self):
        # Previews on: a background service still takes none.
        return SimpleNamespace(
            slug="worker",
            production_branch="main",
            preview_deploys=True,
            is_background=True,
        )

    async def test_a_push_to_another_branch_is_ignored(self, queued):
        answer = await webhooks._handle_push(self.project(), self.push("feature"), None)
        assert queued == [] and "no previews" in answer.ignored

    async def test_production_deploys(self, queued):
        await webhooks._handle_push(self.project(), self.push("main"), None)
        assert [c["ref"] for c in queued] == ["main"]


class TestPages:
    async def test_the_build_page_has_the_switch(self, client, repos):
        repos["projects"][0] = replace(repos["projects"][0], kind="background")
        body = (await client.get("/projects/blog/config/build")).text
        assert 'name="kind" value="background"' in body and "checked" in body

    @pytest.mark.parametrize(
        ("posted", "kind"), [({"kind": "background"}, "background"), ({}, "web")]
    )
    async def test_saving_sets_it(self, client, repos, monkeypatch, posted, kind):
        saved = {}

        async def update(_id, changes):
            saved.update(changes)

        monkeypatch.setattr("deploypro.web.routes.project_repo.update", update)
        await client.post("/projects/blog/config/build", data=posted)
        assert saved["kind"] == kind

    async def test_a_background_service_takes_no_domain(self, client, repos):
        repos["projects"][0] = replace(repos["projects"][0], kind="background")
        response = await client.post(
            "/projects/blog/domains", data={"host": "x.example.com"}
        )
        assert (
            "no+domains" in response.headers["location"]
            or "no%20domains" in response.headers["location"]
        )

    async def test_the_overview_has_no_live_site_button(self, client, repos):
        repos["projects"][0] = replace(repos["projects"][0], kind="background")
        body = (await client.get("/projects/blog")).text
        assert "Open live site" not in body
        assert "Background service · no web address" in body
