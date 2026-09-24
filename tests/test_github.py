# ruff: noqa: F811 - the dashboard fixtures are imported, then used as arguments
"""The GitHub App: the manifest, the JWT, the tokens git uses, the webhook,
and the dashboard pages that connect it."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from deploypro.domain import github as gh
from deploypro.engine import github as engine
from deploypro.repositories.github import App, Installation
from tests import fakes
from tests.test_dashboard import _async, anon, app, client, repos  # noqa: F401

SECRET = "the-apps-webhook-secret"


@pytest.fixture(scope="module")
def rsa_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return key, pem


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class TestJWT:
    def test_it_is_rs256_signed_by_the_apps_key(self, rsa_key):
        key, pem = rsa_key
        token = gh.app_jwt(12345, pem, now=1_000_000)
        header, payload, signature = token.split(".")
        key.public_key().verify(
            _b64decode(signature),
            f"{header}.{payload}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        assert json.loads(_b64decode(header)) == {"alg": "RS256", "typ": "JWT"}

    def test_it_names_the_app_and_lasts_ten_minutes_from_a_minute_ago(self, rsa_key):
        _, pem = rsa_key
        payload = json.loads(_b64decode(gh.app_jwt(7, pem, now=1_000_000).split(".")[1]))
        assert payload == {"iat": 999_940, "exp": 1_000_540, "iss": "7"}

    def test_a_key_that_is_not_rsa_is_refused(self):
        from cryptography.hazmat.primitives.asymmetric import ec

        pem = (
            ec.generate_private_key(ec.SECP256R1())
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
        with pytest.raises(ValueError):
            gh.app_jwt(1, pem)


class TestManifest:
    def test_the_app_only_reads_and_only_hears_pushes(self):
        made = gh.manifest(
            dashboard_url="https://deploypro.example.com", deploy_domain="example.com"
        )
        assert made["default_permissions"] == {"contents": "read", "metadata": "read"}
        assert made["default_events"] == ["push"]

    def test_it_is_private_so_strangers_cannot_install_it(self):
        made = gh.manifest(dashboard_url="https://d.example", deploy_domain="example")
        assert made["public"] is False

    def test_every_url_points_back_at_this_dashboard(self):
        made = gh.manifest(
            dashboard_url="https://deploypro.example.com", deploy_domain="example.com"
        )
        assert made["hook_attributes"]["url"] == (
            "https://deploypro.example.com/github/webhook"
        )
        assert made["redirect_url"] == "https://deploypro.example.com/github/created"
        assert made["setup_url"] == "https://deploypro.example.com/github/installed"

    def test_the_name_fits_githubs_limit(self):
        assert len(gh.app_name("a-very-long-deploy-domain.example.com")) <= 34


class TestRepoFromURL:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/ICOFCUCAM/balancevid.git",
            "https://github.com/ICOFCUCAM/balancevid",
            "https://github.com/ICOFCUCAM/balancevid/",
            "git@github.com:ICOFCUCAM/balancevid.git",
            "ssh://git@github.com/ICOFCUCAM/balancevid.git",
        ],
    )
    def test_every_form_github_offers(self, url):
        assert gh.repo_from_url(url) == "ICOFCUCAM/balancevid"

    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.com/you/app.git",
            "http://github.com/you/app.git",
            "https://github.com/you",
            "https://github.com/you/app/tree/main",
            "git@gitlab.com:you/app.git",
            "",
        ],
    )
    def test_anything_else_is_not_a_github_repository(self, url):
        assert gh.repo_from_url(url) is None

    def test_an_enterprise_host_is_recognised_when_configured(self):
        assert (
            gh.repo_from_url(
                "https://git.corp.example/team/app.git",
                github_url="https://git.corp.example",
            )
            == "team/app"
        )


class TestGitAuth:
    def test_the_token_is_a_header_never_part_of_the_url(self):
        env = gh.git_auth_env("https://github.com/you/app.git", "ghs_secret")
        assert "ghs_secret" not in json.dumps(
            {k: v for k, v in env.items() if "KEY" in k}
        )
        value = env["GIT_CONFIG_VALUE_0"]
        scheme, encoded = value.removeprefix("Authorization: ").split(" ")
        assert scheme == "Basic"
        assert base64.b64decode(encoded).decode() == "x-access-token:ghs_secret"

    def test_the_header_is_scoped_to_the_repositorys_host(self):
        env = gh.git_auth_env("https://github.com/you/app.git", "t")
        assert env["GIT_CONFIG_COUNT"] == "1"
        assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"

    async def test_a_linked_project_clones_with_a_token_for_that_repository_only(
        self, monkeypatch, tmp_path
    ):
        from deploypro.engine import gitaccess

        asked = []

        async def token(settings, installation_id, repo_name=None):
            asked.append((installation_id, repo_name))
            return "ghs_scoped"

        monkeypatch.setattr(engine, "installation_token", token)
        project = fakes.project(
            repo_url="https://github.com/you/blog.git",
            github_installation_id=42,
            github_repo="you/blog",
        )
        settings = SimpleNamespace(build_root=tmp_path)
        async with gitaccess.git_env(project, settings) as env:
            assert "GIT_CONFIG_VALUE_0" in env
        assert asked == [(42, "blog")]


class TestTokens:
    async def test_a_token_is_reused_until_it_nears_expiry(self, monkeypatch):
        engine._TOKENS.clear()
        made = []

        async def create_token(api_url, jwt, installation_id, *, repositories=None):
            made.append(repositories)
            return f"t{len(made)}", datetime.now(UTC) + timedelta(hours=1)

        monkeypatch.setattr(engine.api, "create_token", create_token)
        monkeypatch.setattr(engine, "require_app", lambda: _async(object()))
        monkeypatch.setattr(engine, "_jwt", lambda app, settings: "jwt")
        settings = SimpleNamespace(github_api_url="https://api.github.test")

        assert await engine.installation_token(settings, 1, "blog") == "t1"
        assert await engine.installation_token(settings, 1, "blog") == "t1"
        assert await engine.installation_token(settings, 1) == "t2"
        assert made == [["blog"], None]

        engine._TOKENS[(1, "blog")] = ("old", datetime.now(UTC) + timedelta(minutes=2))
        assert await engine.installation_token(settings, 1, "blog") == "t3"
        engine._TOKENS.clear()


# ---------------------------------------------------------------------------
# The webhook
# ---------------------------------------------------------------------------


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def connected(monkeypatch, repos):
    fake_app = App(
        app_id=1,
        slug="deploypro-example",
        name="DeployPro example",
        html_url="https://github.com/apps/deploypro-example",
        owner_login="you",
        private_key_encrypted=b"",
        webhook_secret_encrypted=b"",
    )
    repos["github_app"] = fake_app
    repos["installations"] = [Installation(42, "you", "User")]
    monkeypatch.setattr(engine, "webhook_secret", lambda app, settings: SECRET)
    return fake_app


class TestWebhook:
    async def post(self, anon, event, payload, signature=None):
        body = json.dumps(payload).encode()
        async with anon as http:
            return await http.post(
                "/github/webhook",
                content=body,
                headers={
                    "X-GitHub-Event": event,
                    "X-Hub-Signature-256": signature or sign(body),
                    "Content-Type": "application/json",
                },
            )

    async def test_without_an_app_there_is_no_webhook(self, anon, repos):
        response = await self.post(anon, "ping", {})
        assert response.status_code == 404

    async def test_a_forged_signature_is_refused(self, anon, connected):
        response = await self.post(anon, "ping", {}, signature=sign(b"{}", "wrong"))
        assert response.status_code == 401

    async def test_a_push_deploys_every_project_linked_to_the_repository(
        self, anon, connected, monkeypatch
    ):
        blog = fakes.project(github_repo="you/blog", github_installation_id=42)
        docs = fakes.project(
            slug="docs", github_repo="you/blog", github_installation_id=42
        )
        asked = []

        async def projects_for_repo(full_name):
            asked.append(full_name)
            return [blog, docs]

        queued = []

        async def handle_push(project, payload, settings):
            queued.append(project.slug)
            return SimpleNamespace(
                ignored=None, deployment=SimpleNamespace(short_id=f"{project.slug}-1")
            )

        monkeypatch.setattr(
            "deploypro.routers.github.github_repo.projects_for_repo", projects_for_repo
        )
        monkeypatch.setattr("deploypro.routers.github._handle_push", handle_push)

        response = await self.post(
            anon,
            "push",
            {"ref": "refs/heads/main", "repository": {"full_name": "you/blog"}},
        )
        assert response.status_code == 202
        assert asked == ["you/blog"]
        assert queued == ["blog", "docs"]
        assert response.json()["projects"][1] == {
            "project": "docs",
            "deployment": "docs-1",
        }

    async def test_a_push_to_a_repository_nothing_uses_is_ignored(
        self, anon, connected, monkeypatch
    ):
        monkeypatch.setattr(
            "deploypro.routers.github.github_repo.projects_for_repo",
            lambda full_name: _async([]),
        )
        response = await self.post(
            anon, "push", {"ref": "refs/heads/main", "repository": {"full_name": "a/b"}}
        )
        assert "no project" in response.json()["ignored"]

    async def test_an_uninstall_is_remembered(self, anon, connected, monkeypatch):
        removed = []

        async def delete_installation(installation_id):
            removed.append(installation_id)

        monkeypatch.setattr(
            "deploypro.routers.github.github_repo.delete_installation",
            delete_installation,
        )
        await self.post(
            anon,
            "installation",
            {
                "action": "deleted",
                "installation": {"id": 42, "account": {"login": "you"}},
            },
        )
        assert removed == [42]


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------


class TestConnecting:
    async def test_without_an_app_new_project_offers_to_connect(self, client, repos):
        response = await client.get("/projects/new")
        assert response.status_code == 200
        assert 'href="/github/connect"' in response.text
        assert "Deploy from a Git URL" in response.text

    async def test_the_connect_page_posts_the_manifest_to_github_with_a_state(
        self, client, repos
    ):
        response = await client.get("/github/connect")
        assert response.status_code == 200
        state = response.cookies["deploypro_github_state"]
        assert f"https://github.com/settings/apps/new?state={state}" in response.text
        assert "/github/webhook" in response.text

    async def test_it_moves_to_the_address_github_will_come_back_to(
        self, app, repos, monkeypatch
    ):
        import httpx

        from deploypro.domain import session

        monkeypatch.setenv("DEPLOYPRO_DASHBOARD_DOMAIN", "deploypro.example.com")
        from deploypro.config import get_settings

        get_settings.cache_clear()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://deploypro.deploys.example.com",
                cookies={session.COOKIE_NAME: session.issue(token="a-test-api-token")},
            ) as http:
                response = await http.get("/github/connect")
        finally:
            get_settings.cache_clear()
        assert response.headers["location"] == (
            "http://deploypro.example.com/github/connect"
        )

    async def test_an_organisation_gets_githubs_organisation_page(self, client, repos):
        response = await client.get("/github/connect?org=my-team")
        assert "https://github.com/organizations/my-team/settings/apps/new" in (
            response.text
        )

    async def test_a_code_without_the_matching_state_is_refused(
        self, client, repos, monkeypatch
    ):
        called = []
        monkeypatch.setattr(
            engine, "complete_manifest", lambda code, s: called.append(code)
        )
        client.cookies.set("deploypro_github_state", "the-real-state", path="/github")
        response = await client.get("/github/created?code=abc&state=forged")
        assert response.status_code == 303
        assert "/github/connect" in response.headers["location"]
        assert called == []

    async def test_the_matching_state_creates_the_app_and_goes_to_install_it(
        self, client, repos, monkeypatch
    ):
        made = App(1, "dp", "DP", "https://github.com/apps/dp", "you", b"", b"")
        codes = []

        async def complete(code, settings):
            codes.append(code)
            return made

        monkeypatch.setattr(engine, "complete_manifest", complete)
        client.cookies.set("deploypro_github_state", "s3", path="/github")
        response = await client.get("/github/created?code=abc&state=s3")
        assert codes == ["abc"]
        assert response.headers["location"] == (
            "https://github.com/apps/dp/installations/new"
        )

    async def test_an_installation_request_that_was_not_granted_says_so(
        self, client, connected
    ):
        response = await client.get("/github/installed?setup_action=request")
        location = response.headers["location"]
        assert (
            "err=" in location
            and "approve" in parse_qs(urlsplit(location).query)["err"][0]
        )


class TestImporting:
    async def test_new_project_lists_repositories_with_import_buttons(
        self, client, connected, monkeypatch
    ):
        monkeypatch.setattr(
            engine,
            "list_repositories",
            lambda settings: _async(
                [
                    engine.Repository("you/shop", True, "main", "2026-09-20", "", 42),
                    engine.Repository(
                        "you/blog", False, "main", "2026-09-01", "", 42, "blog"
                    ),
                ]
            ),
        )
        response = await client.get("/projects/new")
        assert "you/shop" in response.text and "private" in response.text
        assert "/projects/new/github?repo=you/shop&installation=42" in response.text
        # Already imported: opened, not imported twice.
        assert 'href="/projects/blog"' in response.text

    async def test_the_search_box_filters_the_list(self, client, connected, monkeypatch):
        monkeypatch.setattr(
            engine,
            "list_repositories",
            lambda settings: _async(
                [
                    engine.Repository("you/shop", True, "main", "", "", 42),
                    engine.Repository("you/notes", False, "main", "", "", 42),
                ]
            ),
        )
        response = await client.get("/projects/new?q=sho")
        assert "you/shop" in response.text and "you/notes" not in response.text

    async def test_importing_links_the_project_and_deploys_it(
        self, client, connected, monkeypatch
    ):
        made = fakes.project(
            slug="shop", github_repo="you/shop", github_installation_id=42
        )
        seen = {}

        async def import_repository(settings, **kwargs):
            seen.update(kwargs)
            return made

        async def queue_deploy(project, **kwargs):
            return fakes.deployment(short_id="shop-1234abcd")

        monkeypatch.setattr(engine, "import_repository", import_repository)
        monkeypatch.setattr("deploypro.web.github.service.queue_deploy", queue_deploy)
        response = await client.post(
            "/projects/new/github",
            data={"repo": "you/shop", "installation": "42", "name": "Shop"},
        )
        assert seen["full_name"] == "you/shop" and seen["installation_id"] == 42
        assert response.headers["location"] == "/deployments/shop-1234abcd"

    async def test_a_linked_project_page_says_pushes_deploy(self, client, repos):
        repos["projects"][0] = fakes.project(
            production_deployment_id=repos["deployments"][0].id,
            github_repo="you/blog",
            github_installation_id=42,
        )
        response = await client.get("/projects/blog/config/repository")
        assert "through the GitHub App" in response.text
        # Its own webhook and deploy key are beside the point now.
        assert "Make a deploy key" not in response.text

    async def test_an_unlinked_github_project_offers_to_link(self, client, connected):
        response = await client.get("/projects/blog/config/repository")
        assert "/projects/blog/github/link" in response.text


class TestPageTitle:
    """Regression: adding the GitHub App card to New project inserted it before
    every `{% endblock %}` in the template, including the title block's, so the
    browser tab showed the card's raw HTML once an app was connected."""

    @staticmethod
    def title(html: str) -> str:
        return html.split("<title>", 1)[1].split("</title>", 1)[0]

    async def test_the_title_is_plain_text_with_an_app_connected(
        self, client, connected, monkeypatch
    ):
        monkeypatch.setattr(engine, "list_repositories", lambda settings: _async([]))
        response = await client.get("/projects/new")
        assert self.title(response.text) == "New project — DeployPro"
        # The app's name still renders, in the page.
        assert response.text.count("<title>") == 1
        assert "Choose some on GitHub" in response.text

    async def test_disconnect_lives_on_system_settings(
        self, client, connected, monkeypatch
    ):
        """Phase 2 decision 1: disconnecting is an installation-wide act."""
        monkeypatch.setattr(engine, "list_repositories", lambda settings: _async([]))
        new_project = (await client.get("/projects/new")).text
        assert "/github/disconnect" not in new_project
        system = (await client.get("/system")).text
        assert system.count("Disconnect</button>") == 1

    async def test_the_title_is_plain_text_without_an_app(self, client, repos):
        response = await client.get("/projects/new")
        assert self.title(response.text) == "New project — DeployPro"
