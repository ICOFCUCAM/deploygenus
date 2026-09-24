"""The dashboard renders, and refuses to render for a stranger."""

from __future__ import annotations

import os

import httpx
import pytest

from deploypro.domain import session
from deploypro.domain.models import DeploymentStatus, LogStream
from tests import fakes

TOKEN = "a-test-api-token"


@pytest.fixture(scope="module")
def app():
    os.environ.update(
        {
            "DATABASE_URL": "postgresql://unused/unused",
            "DEPLOYPRO_MASTER_KEY": "unused",
            "DEPLOYPRO_API_TOKEN": TOKEN,
            "DEPLOYPRO_DEPLOY_DOMAIN": "deploys.example.com",
            "ENVIRONMENT": "development",
        }
    )
    from deploypro.config import get_settings
    from deploypro.main import app as application

    get_settings.cache_clear()
    return application


@pytest.fixture
def repos(monkeypatch):
    """Serve the whole dashboard from fixtures instead of Postgres."""
    live = fakes.deployment()
    proj = fakes.project(production_deployment_id=live.id)
    state = {
        "projects": [proj],
        "deployments": [
            live,
            fakes.deployment(
                number=13,
                short_id="blog-9d1e0f44",
                status=DeploymentStatus.FAILED,
                error="The container never became healthy: it exited before it "
                "served a request",
            ),
            fakes.deployment(
                number=12,
                short_id="blog-11c9e2a7",
                status=DeploymentStatus.BUILDING,
                container_id=None,
            ),
        ],
        "domains": [
            fakes.domain("example.com", verified=True, primary=True),
            fakes.domain("www.example.com", verified=False, primary=False),
        ],
        "env": [fakes.env_var("DATABASE_URL"), fakes.env_var("STRIPE_KEY")],
        "processes": [fakes.process(), fakes.cron()],
        "volumes": [fakes.volume()],
        "runs": [
            fakes.job_run(),
            fakes.job_run(
                status=fakes.JobStatus.FAILED,
                exit_code=1,
                detail="Exited 1",
                output="Error: connection refused",
            ),
        ],
        "logs": [
            fakes.log_line(
                1, "deploying Blog #14 — main at 4f2a9c1e (push)", LogStream.SYSTEM
            ),
            fakes.log_line(
                2,
                "Next.js with output: 'standalone' — serving the traced server bundle",
                LogStream.SYSTEM,
            ),
            fakes.log_line(3, "#8 [build 4/4] RUN npm run build"),
            fakes.log_line(
                4, "healthy after 1.4s (4 attempts) — answered 200 on /", LogStream.SYSTEM
            ),
        ],
    }

    async def by_slug(slug):
        return state["projects"][0]

    async def by_id(_id):
        return state["projects"][0]

    async def dep_get(_id):
        for d in state["deployments"]:
            if d.id == _id:
                return d
        return state["deployments"][0]

    async def dep_by_short(short_id):
        for d in state["deployments"]:
            if d.short_id == short_id:
                return d
        raise AssertionError(short_id)

    monkeypatch.setattr(
        "deploypro.web.routes.project_repo.list_all", lambda: _async(state["projects"])
    )
    monkeypatch.setattr("deploypro.web.routes.project_repo.get_by_slug", by_slug)
    monkeypatch.setattr("deploypro.web.routes.project_repo.get", by_id)
    monkeypatch.setattr(
        "deploypro.web.routes.project_repo.list_domains",
        lambda _id: _async(state["domains"]),
    )
    monkeypatch.setattr(
        "deploypro.web.routes.project_repo.list_env", lambda _id: _async(state["env"])
    )
    monkeypatch.setattr(
        "deploypro.web.routes.volume_repo.list_for_project",
        lambda _id: _async(state["volumes"]),
    )
    monkeypatch.setattr("deploypro.web.routes.deployment_repo.get", dep_get)
    monkeypatch.setattr(
        "deploypro.web.routes.deployment_repo.get_by_short_id", dep_by_short
    )
    monkeypatch.setattr(
        "deploypro.web.routes.deployment_repo.list_for_project",
        lambda _id, limit=25: _async(state["deployments"]),
    )
    monkeypatch.setattr(
        "deploypro.web.routes.deployment_repo.read_logs",
        lambda _id, limit=0, after=0: _async(state["logs"]),
    )

    async def process_by_name(_project_id, name):
        for proc in state["processes"]:
            if proc.name == name:
                return proc
        raise AssertionError(name)

    monkeypatch.setattr(
        "deploypro.web.routes.process_repo.list_for_project",
        lambda _id: _async(state["processes"]),
    )
    monkeypatch.setattr("deploypro.web.routes.process_repo.get_by_name", process_by_name)
    monkeypatch.setattr(
        "deploypro.web.routes.process_repo.list_runs",
        lambda _id, limit=25: _async(state["runs"]),
    )
    monkeypatch.setattr(
        "deploypro.web.routes.process_repo.last_run", lambda _id: _async(state["runs"][0])
    )
    # No GitHub App unless a test connects one.
    monkeypatch.setattr(
        "deploypro.repositories.github.get_app", lambda: _async(state.get("github_app"))
    )
    monkeypatch.setattr(
        "deploypro.repositories.github.list_installations",
        lambda: _async(state.get("installations", [])),
    )
    return state


async def _async(value):
    return value


@pytest.fixture
def anon(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport, base_url="http://deploypro.test", follow_redirects=False
    )


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    cookie = session.issue(token=TOKEN)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://deploypro.test",
        cookies={session.COOKIE_NAME: cookie},
        follow_redirects=False,
    ) as http:
        yield http


class TestAccess:
    @pytest.mark.parametrize(
        "path", ["/", "/projects/blog", "/projects/new", "/deployments/blog-3f9a2c71"]
    )
    async def test_a_stranger_is_sent_to_sign_in_not_given_json(self, anon, path):
        async with anon as http:
            response = await http.get(path)
        assert response.status_code == 303
        location = response.headers["location"]
        assert (
            location == "/login" if path == "/" else location.startswith("/login?next=")
        )

    async def test_signing_in_returns_to_the_page_that_was_asked_for(self, anon):
        async with anon as http:
            response = await http.post(
                "/login",
                data={"token": TOKEN, "next": "/github/installed?installation_id=7"},
            )
        assert response.headers["location"] == "/github/installed?installation_id=7"

    @pytest.mark.parametrize(
        "target", ["//evil.example", "https://evil.example", "/\\evil"]
    )
    async def test_signing_in_never_leaves_the_dashboard(self, anon, target):
        async with anon as http:
            response = await http.post("/login", data={"token": TOKEN, "next": target})
        assert response.headers["location"] == "/"

    async def test_the_sign_in_page_is_public(self, anon):
        async with anon as http:
            response = await http.get("/login")
        assert response.status_code == 200
        assert "API token" in response.text

    async def test_a_wrong_token_does_not_set_a_cookie(self, anon):
        async with anon as http:
            response = await http.post("/login", data={"token": "wrong"})
        assert response.status_code == 303
        assert "/login" in response.headers["location"]
        assert session.COOKIE_NAME not in response.cookies

    async def test_signing_in_sets_an_httponly_cookie(self, anon):
        async with anon as http:
            response = await http.post("/login", data={"token": TOKEN})
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert "httponly" in response.headers["set-cookie"].lower()

    async def test_a_tampered_cookie_is_rejected(self, app):
        transport = httpx.ASGITransport(app=app)
        forged = session.issue(token="some-other-token")
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://deploypro.test",
            cookies={session.COOKIE_NAME: forged},
            follow_redirects=False,
        ) as http:
            response = await http.get("/")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


class TestProjects:
    async def test_the_project_list_shows_state_production_and_address(
        self, client, repos
    ):
        response = await client.get("/")
        assert response.status_code == 200
        body = response.text
        assert "Blog" in body
        assert "example.com" in body
        assert "Production #14" in body

    async def test_a_domain_is_not_shown_before_anything_serves_it(
        self, client, repos, monkeypatch
    ):
        """A project can have a verified domain and no production deployment.
        Listing the domain then points at a hostname that answers with the
        router's default page."""
        undeployed = fakes.project(
            slug="api", name="Orders API", production_deployment_id=None
        )
        repos["projects"][0] = undeployed
        repos["deployments"][:] = []
        response = await client.get("/")
        assert "Not deployed yet" in response.text
        assert "No permanent address" in response.text
        assert 'href="https://example.com"' not in response.text

    async def test_a_failed_newer_deployment_makes_the_project_need_attention(
        self, client, repos
    ):
        body = (await client.get("/")).text
        assert "Healthy" in body
        repos["deployments"].insert(
            0,
            fakes.deployment(
                number=15,
                short_id="blog-15151515",
                status=DeploymentStatus.FAILED,
                container_id=None,
                ready_at=None,
                error="npm ERR! missing script: build",
            ),
        )
        body = (await client.get("/")).text
        assert "Needs attention" in body

    async def test_the_activity_feed_lists_what_happened(self, client, repos):
        body = (await client.get("/")).text
        assert "Activity" in body
        assert "#14 went live" in body

    async def test_nothing_deployed_offers_one_way_in(self, client, repos):
        repos["projects"][:] = []
        body = (await client.get("/")).text
        assert "Nothing is deployed here yet." in body
        assert "Connect GitHub" in body


class TestOverview:
    async def test_production_is_the_first_thing_and_carries_the_deploy_action(
        self, client, repos
    ):
        body = (await client.get("/projects/blog")).text
        assert "Serving" in body
        assert body.index("Serving") < body.index("Recent deployments")
        assert "Deploy main" in body

    async def test_a_newer_deploy_in_progress_becomes_the_primary_action(
        self, client, repos
    ):
        repos["deployments"].insert(
            0,
            fakes.deployment(
                number=15,
                short_id="blog-15151515",
                status=DeploymentStatus.BUILDING,
                container_id=None,
                built_at=None,
                ready_at=None,
                finished_at=None,
            ),
        )
        body = (await client.get("/projects/blog")).text
        assert "View #15" in body
        assert "production switches when it passes its health check" in body
        # Q-S1: Deploy is still offered, and says what it will do.
        assert "#15 is already building" in body

    async def test_it_refreshes_itself_only_while_something_is_in_progress(
        self, client, repos
    ):
        """Q-S2. The fixture has #12 building."""
        body = (await client.get("/projects/blog")).text
        assert 'http-equiv="refresh"' in body
        repos["deployments"][:] = repos["deployments"][:2]
        body = (await client.get("/projects/blog")).text
        assert 'http-equiv="refresh"' not in body

    async def test_it_holds_no_configuration_form(self, client, repos):
        """Phase 2 §2C: configuration lives on Configuration's pages."""
        body = (await client.get("/projects/blog")).text
        assert 'action="/projects/blog/env"' not in body
        assert 'action="/projects/blog/config/build"' not in body
        assert "/projects/blog/config/environment" in body

    async def test_production_down_names_its_rollback_target(self, client, repos):
        down = fakes.deployment(container_id=None)
        older = fakes.deployment(number=11, short_id="blog-0a1b2c3d")
        repos["deployments"][:] = [down, older]
        repos["projects"][0] = fakes.project(production_deployment_id=down.id)
        body = (await client.get("/projects/blog")).text
        assert "Production is down: its container is gone." in body
        assert "Roll back to #11" in body

    async def test_a_flash_message_cannot_inject_markup(self, client, repos):
        response = await client.get("/projects/blog?err=<script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in response.text

    async def test_it_lists_workers_and_jobs(self, client, repos):
        body = (await client.get("/projects/blog")).text
        assert "Workers" in body
        assert "nightly" in body


class TestDeployment:
    async def test_it_renders_its_log(self, client, repos):
        response = await client.get("/deployments/blog-3f9a2c71")
        assert response.status_code == 200
        assert "npm run build" in response.text
        assert "healthy after 1.4s" in response.text

    async def test_the_line_is_an_ordered_list_that_states_itself(self, client, repos):
        body = (await client.get("/deployments/blog-3f9a2c71")).text
        assert '<ol class="line" aria-label="Deployment lifecycle">' in body
        assert "Production: current production" in body
        assert "includes health check" in body

    async def test_current_production_offers_redeploy_and_no_rollback(
        self, client, repos
    ):
        body = (await client.get("/deployments/blog-3f9a2c71")).text
        assert "Current production" in body
        assert ">Redeploy<" in body
        assert "Roll back" not in body

    async def test_a_failed_deployment_says_where_it_failed_and_shows_its_error(
        self, client, repos
    ):
        body = (await client.get("/deployments/blog-9d1e0f44")).text
        assert "never became healthy" in body
        assert "Production was not touched." in body

    async def test_an_in_flight_deployment_offers_no_action(self, client, repos):
        body = (await client.get("/deployments/blog-11c9e2a7")).text
        assert "Make production" not in body
        assert ">Redeploy<" not in body
        assert 'aria-current="step"' in body

    async def test_without_javascript_it_reloads_only_while_in_progress(
        self, client, repos
    ):
        """The meta refresh sits inside <noscript>: with scripting on it never
        fires, so it cannot restart the live log."""
        building = (await client.get("/deployments/blog-11c9e2a7")).text
        assert '<noscript><meta http-equiv="refresh"' in building
        finished = (await client.get("/deployments/blog-3f9a2c71")).text
        assert 'http-equiv="refresh"' not in finished

    async def test_an_older_ready_deployment_names_itself_as_the_target(
        self, client, repos
    ):
        older = fakes.deployment(number=11, short_id="blog-0a1b2c3d")
        repos["deployments"].append(older)
        body = (await client.get("/deployments/blog-0a1b2c3d")).text
        assert "Roll back to #11" in body

    async def test_a_missing_commit_message_leaves_no_empty_slot(self, client, repos):
        """Dashboard, CLI and import deploys store no message (Phase 3 §4)."""
        repos["deployments"][0] = fakes.deployment(git_message=None)
        body = (await client.get("/deployments/blog-3f9a2c71")).text
        assert "dhead-message" not in body
        assert "—" not in body.split('class="dhead-identity"')[1].split("</p>")[0]

    async def test_a_log_line_cannot_inject_markup(self, client, repos, monkeypatch):
        """Build output is attacker-influenced: it contains whatever a
        dependency printed."""
        monkeypatch.setattr(
            "deploypro.web.routes.deployment_repo.read_logs",
            lambda _id, limit=0, after=0: _async(
                [fakes.log_line(1, "<img src=x onerror=alert(1)>")]
            ),
        )
        response = await client.get("/deployments/blog-3f9a2c71")
        assert "<img src=x" not in response.text
        assert "&lt;img src=x" in response.text

    async def test_the_deployments_list_marks_kind_and_runtime(self, client, repos):
        body = (await client.get("/projects/blog/deployments")).text
        assert "Production branch" in body
        assert "Running" in body
        assert "Failed at" in body


class TestDeployButton:
    async def test_it_carries_the_full_branch_name(self, client, repos):
        branch = "claude/adoring-mayer-yfghmg"
        repos["projects"][0] = fakes.project(
            production_branch=branch,
            production_deployment_id=repos["deployments"][0].id,
        )
        body = (await client.get("/projects/blog")).text
        # Shortened in the middle for the eye; whole in the button's text.
        assert '<span class="middle-head">Deploy claude/adoring-</span>' in body
        assert '<span class="middle-tail">mayer-yfghmg</span>' in body
        assert f'production branch <span class="branch mono">{branch}</span>' in body


class TestRuntime:
    async def test_the_runtime_page_summarises_web_workers_and_jobs(self, client, repos):
        body = (await client.get("/projects/blog/runtime")).text
        assert "Website" in body
        assert "mailer" in body
        assert "nightly" in body

    async def test_workers_never_claim_a_running_count(self, client, repos):
        """Running counts need a Docker read that is not built yet."""
        body = (await client.get("/projects/blog/runtime/workers")).text
        assert "mailer" in body
        assert "Follows production #14." in body
        assert "1/1" not in body

    async def test_a_paused_worker_says_when_it_takes_effect(self, client, repos):
        repos["processes"][0] = fakes.process(enabled=False)
        body = (await client.get("/projects/blog/runtime/workers")).text
        assert "Paused" in body
        assert "next deploy" in body

    async def test_a_job_page_shows_its_schedule_in_words_and_its_runs(
        self, client, repos
    ):
        response = await client.get("/projects/blog/runtime/jobs/nightly")
        assert response.status_code == 200
        assert "every day at 03:00 UTC" in response.text
        assert "Run now" in response.text
        assert "Succeeded" in response.text

    async def test_a_worker_page_offers_no_run_now_button(self, client, repos):
        """There is nothing to trigger: it is already running."""
        response = await client.get("/projects/blog/runtime/workers/mailer")
        assert response.status_code == 200
        assert "Run now" not in response.text

    async def test_a_worker_page_says_where_its_output_goes(self, client, repos):
        """Workers stream to the container log rather than the database —
        an always-on process would otherwise write an unbounded log table."""
        response = await client.get("/projects/blog/runtime/workers/mailer")
        assert "docker logs" in response.text

    async def test_job_output_cannot_inject_markup(self, client, repos, monkeypatch):
        """A job's output is whatever the customer's own code printed."""
        monkeypatch.setattr(
            "deploypro.web.routes.process_repo.list_runs",
            lambda _id, limit=25: _async(
                [fakes.job_run(output="<img src=x onerror=alert(1)>")]
            ),
        )
        response = await client.get("/projects/blog/runtime/jobs/nightly")
        assert "<img src=x" not in response.text
        assert "&lt;img src=x" in response.text

    @pytest.mark.parametrize(
        ("name", "where"), [("mailer", "workers"), ("nightly", "jobs")]
    )
    async def test_the_old_address_still_works(self, client, repos, name, where):
        """Older alerts and CLI output print /projects/{p}/processes/{name}."""
        response = await client.get(f"/projects/blog/processes/{name}")
        assert response.status_code == 301
        assert response.headers["location"] == f"/projects/blog/runtime/{where}/{name}"


class TestConfiguration:
    async def test_the_index_has_one_line_per_area_and_delete_at_the_bottom(
        self, client, repos
    ):
        body = (await client.get("/projects/blog/config")).text
        for area in ("Environment", "Domains", "Storage", "Build", "Repository"):
            assert f"/projects/blog/config/{area.lower()}" in body
        assert body.index("Repository") < body.index("Delete project")
        assert "Its stored files are kept on the server." in body
        assert 'name="confirm"' in body

    async def test_configuration_pages_never_refresh_themselves(self, client, repos):
        """Q-S2: a refresh would discard what is being typed. The fixture has
        a deployment building, which would make other pages refresh."""
        for page in ("", "/environment", "/domains", "/storage", "/build", "/repository"):
            body = (await client.get(f"/projects/blog/config{page}")).text
            assert 'http-equiv="refresh"' not in body, page

    async def test_variables_are_listed_by_scope_and_never_by_value(self, client, repos):
        body = (await client.get("/projects/blog/config/environment")).text
        assert "DATABASE_URL" in body and "STRIPE_KEY" in body
        assert "Production only" in body
        assert "ciphertext" not in body

    async def test_a_domain_waiting_for_dns_says_what_to_add(self, client, repos):
        body = (await client.get("/projects/blog/config/domains")).text
        assert "www.example.com" in body
        assert "Waiting for DNS" in body
        assert "deploys.example.com" in body

    async def test_storage_lists_mounts_with_their_docker_names(self, client, repos):
        body = (await client.get("/projects/blog/config/storage")).text
        assert "/data" in body
        assert "deploypro_blog_recordings" in body

    async def test_build_exposes_every_stored_setting(self, client, repos):
        body = (await client.get("/projects/blog/config/build")).text
        for field in (
            "install_command",
            "build_command",
            "start_command",
            "port",
            "memory_mb",
            "cpu_shares",
            "keep_warm",
            "stop_timeout_seconds",
        ):
            assert f'name="{field}"' in body, field
        assert "Graceful shutdown time" in body

    async def test_the_webhook_secret_is_folded_away_until_asked_for(self, client, repos):
        body = (await client.get("/projects/blog/config/repository")).text
        secret = repos["projects"][0].webhook_secret
        before = body.split(secret)[0]
        assert before.rstrip().endswith('<span class="mono" id="hook-secret">')
        assert "<summary" in before.split("<details>")[-1]


class TestDeployKey:
    async def test_a_project_without_a_key_offers_to_make_one(self, client, repos):
        body = (await client.get("/projects/blog/config/repository")).text
        assert "Make a deploy key" in body

    async def test_the_public_key_is_shown_and_the_private_one_never(self, client, repos):
        repos["projects"][0] = fakes.project(
            repo_url="git@github.com:you/blog.git",
            deploy_key_public="ssh-ed25519 AAAAC3Nzapublic deploypro@blog",
        )
        body = (await client.get("/projects/blog/config/repository")).text
        assert "ssh-ed25519 AAAAC3Nzapublic deploypro@blog" in body
        assert "Replace key" in body
        assert "PRIVATE KEY" not in body


class TestSystem:
    async def test_settings_are_read_only_and_say_where_they_change(self, client, repos):
        body = (await client.get("/system")).text
        assert "/opt/deploypro/.env" in body
        assert "DEPLOYPRO_KEEP_IMAGES" in body
        assert "<input" not in body

    async def test_without_an_app_it_offers_to_connect_one(self, client, repos):
        body = (await client.get("/system")).text
        assert "Connect GitHub" in body
        assert "Disconnect" not in body


class TestForms:
    """Every form returns to the page it was posted from (Phase 3 §12)."""

    async def test_saving_build_settings_leaves_the_branch_alone(
        self, client, repos, monkeypatch
    ):
        """Regression: one shared settings handler read an absent field as
        "clear it", so a form without the build overrides wiped them."""
        saved = {}

        async def update(_id, changes):
            saved.update(changes)

        monkeypatch.setattr("deploypro.web.routes.project_repo.update", update)
        response = await client.post(
            "/projects/blog/config/build",
            data={"port": "8080", "cpu_shares": "0.5", "memory_mb": "1024"},
        )
        assert response.headers["location"].startswith("/projects/blog/config/build?ok=")
        assert saved["port"] == 8080
        assert saved["cpu_shares"] == 0.5
        assert "production_branch" not in saved
        assert "name" not in saved

    async def test_saving_the_repository_leaves_build_overrides_alone(
        self, client, repos, monkeypatch
    ):
        saved = {}

        async def update(_id, changes):
            saved.update(changes)

        monkeypatch.setattr("deploypro.web.routes.project_repo.update", update)
        response = await client.post(
            "/projects/blog/config/repository", data={"production_branch": "release"}
        )
        assert response.headers["location"].startswith(
            "/projects/blog/config/repository?ok="
        )
        assert saved == {"name": "Blog", "production_branch": "release"}

    @pytest.mark.parametrize("port", ["0", "70000", "http"])
    async def test_an_impossible_port_is_refused_on_the_build_page(
        self, client, repos, port
    ):
        response = await client.post("/projects/blog/config/build", data={"port": port})
        assert response.headers["location"].startswith("/projects/blog/config/build?err=")

    async def test_deleting_needs_the_project_name_typed(
        self, client, repos, monkeypatch
    ):
        deleted = []

        async def delete(project, settings):
            deleted.append(project.slug)

        monkeypatch.setattr("deploypro.web.routes.service.delete_project", delete)
        refused = await client.post("/projects/blog/delete", data={"confirm": "blog"})
        assert refused.headers["location"].startswith("/projects/blog/config?err=")
        assert deleted == []
        done = await client.post("/projects/blog/delete", data={"confirm": "Blog"})
        assert done.headers["location"].startswith("/?ok=")
        assert deleted == ["blog"]

    async def test_a_variable_returns_to_the_environment_page(
        self, client, repos, monkeypatch
    ):
        async def set_env(*_args):
            return None

        monkeypatch.setattr("deploypro.web.routes.project_repo.set_env", set_env)
        monkeypatch.setattr(
            "deploypro.web.routes.crypto.encrypt", lambda value, key: b"x"
        )
        response = await client.post(
            "/projects/blog/env", data={"key": "API_KEY", "value": "v", "target": "all"}
        )
        assert response.headers["location"].startswith(
            "/projects/blog/config/environment?ok="
        )

    async def test_pausing_a_worker_says_it_takes_effect_at_the_next_deploy(
        self, client, repos, monkeypatch
    ):
        """Q-S4: the old message said "mailer paused." while it kept running."""

        async def update(_id, changes):
            return None

        monkeypatch.setattr("deploypro.web.routes.process_repo.update", update)
        response = await client.post("/projects/blog/processes/mailer/toggle")
        location = response.headers["location"]
        assert location.startswith("/projects/blog/runtime/workers?ok=")
        assert "next%20deploy" in location

    async def test_a_form_can_only_return_to_this_dashboard(
        self, client, repos, monkeypatch
    ):
        async def update(_id, changes):
            return None

        monkeypatch.setattr("deploypro.web.routes.process_repo.update", update)
        response = await client.post(
            "/projects/blog/processes/mailer/toggle",
            data={"back": "https://evil.example/"},
        )
        assert response.headers["location"].startswith("/projects/blog/runtime/workers")


def test_every_static_file_a_page_refers_to_exists():
    """The rename to DeployPro changed `/static/forge.css` to
    `/static/deploypro.css` in the templates but not the file's name, and the
    first real install served every page unstyled."""
    import re
    from pathlib import Path

    web = Path(__file__).parent.parent / "deploypro" / "web"
    referenced = set()
    for template in (web / "templates").glob("*.html"):
        referenced.update(re.findall(r"/static/([\w./-]+)", template.read_text()))
    stylesheet = (web / "static" / "deploypro.css").read_text()
    referenced.update(re.findall(r'url\("([\w./-]+)"\)', stylesheet))
    assert referenced, "no template refers to a static file — the check found nothing"
    missing = sorted(name for name in referenced if not (web / "static" / name).is_file())
    assert missing == []


async def test_the_stylesheet_is_actually_served(anon):
    response = await anon.get("/static/deploypro.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_a_real_install_carries_the_templates_and_static_files():
    """Without package-data, a non-editable install has only .py files. The
    image hid it by starting the web server from the source tree."""
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(
        (Path(__file__).parent.parent / "pyproject.toml").read_text()
    )
    shipped = pyproject["tool"]["setuptools"]["package-data"]["deploypro"]
    assert "web/templates/*.html" in shipped
    assert "web/static/*" in shipped
    # `web/static/*` does not reach into a subfolder.
    assert "web/static/fonts/*" in shipped


def test_the_fonts_ship_with_their_licence():
    """IBM Plex is under the SIL Open Font License, which travels with it."""
    from pathlib import Path

    fonts = Path(__file__).parent.parent / "deploypro" / "web" / "static" / "fonts"
    assert len(list(fonts.glob("*.woff2"))) == 4
    for licence in ("OFL-IBM-Plex-Sans.txt", "OFL-IBM-Plex-Mono.txt"):
        assert "SIL Open Font License" in (fonts / licence).read_text()


class TestProductionMarker:
    """The deployment list marks the one deployment serving production.

    Regression: the row macro read `d.is_production`, which the Deployment
    model does not have. Jinja treats a missing attribute as false, so the
    marker never rendered on the project page, for any deployment.
    """

    @staticmethod
    def rows(html: str) -> dict[str, str]:
        """Each deployment row's markup, keyed by the deployment it links to."""
        found = {}
        for chunk in html.split('<li class="drow')[1:]:
            row = chunk.split("</li>", 1)[0]
            short_id = row.split('href="/deployments/', 1)[1].split('"', 1)[0]
            found[short_id] = row
        return found

    def marked(self, html: str) -> list[str]:
        return [sid for sid, row in self.rows(html).items() if "tag-current" in row]

    async def test_the_serving_deployment_is_marked_and_only_it(self, client, repos):
        live = repos["deployments"][0]
        response = await client.get("/projects/blog/deployments")
        assert self.marked(response.text) == [live.short_id]

    async def test_after_a_rollback_the_older_deployment_carries_the_marker(
        self, client, repos
    ):
        older = fakes.deployment(number=11, short_id="blog-0a1b2c3d")
        repos["deployments"].append(older)
        repos["projects"][0] = fakes.project(production_deployment_id=older.id)
        response = await client.get("/projects/blog/deployments")
        assert self.marked(response.text) == [older.short_id]

    async def test_before_anything_is_in_production_nothing_is_marked(
        self, client, repos
    ):
        repos["projects"][0] = fakes.project(production_deployment_id=None)
        response = await client.get("/projects/blog/deployments")
        assert self.marked(response.text) == []
        assert "Current production" not in response.text
